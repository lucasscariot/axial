"""Shared SQL: a `{{ name }}` line in a query is replaced by snippets/<name>.sql."""

import asyncio
import datetime as dt
import os

import pytest
from click.testing import CliRunner

from axial.checks import run_checks
from axial.cli import main
from axial.config import load_config
from axial.registry import Registry, expand_sql

RANGE = "FROM `analytics.fct_bookings`\nWHERE DATE(created_at) >= @start_date\n  AND DATE(created_at) < @end_date\n"


def issues(repo, dashboard=None):
    found = run_checks(Registry(load_config(repo, environ={})), dashboard)
    return [f"{i.level} {i.where}: {i.message}" for i in found]


def test_include_is_replaced_by_the_snippet_text(cfg):
    reg = Registry(cfg)
    daily, rate = reg.queries["bookings_daily"], reg.queries["cancellation_rate"]
    assert RANGE in daily.sql and RANGE in rate.sql
    assert "{{" not in daily.sql and daily.snippets == ("bookings_in_range",)
    assert daily.sql.endswith("  AND DATE(created_at) < @end_date\nGROUP BY day\nORDER BY day\n")
    assert reg.queries["booking_exceptions"].snippets == ()


def test_expansion_is_textual_and_keeps_every_other_byte():
    sql, used = expand_sql("WITH a AS (\n  {{ base }}  \n)\n{{base}}\nSELECT '{{ not_one }}' -- {{ nor_this }}\n",
                           {"base": "SELECT 1\n", "unused": "SELECT 2\n"})
    assert sql == "WITH a AS (\nSELECT 1\n)\nSELECT 1\nSELECT '{{ not_one }}' -- {{ nor_this }}\n"
    assert used == ("base",)
    assert expand_sql("{{ a }}\n{{ b }}", {"a": "SELECT 1,", "b": "2"}) == ("SELECT 1,\n2", ("a", "b"))


@pytest.mark.parametrize("sql, message", [
    ("{{ missing }}\nSELECT 1", r"snippets/missing\.sql does not exist \(known: base, nested\)"),
    ("{{ nested }}", "snippets cannot include snippets"),
    ("{{ ../secrets }}", "is not an include"),
    ("{{ base(1) }}", "is not an include"),
    ("{{ base start=@start_date }}", "is not an include"),
    ("{{ }}", "is not an include"),
    ("SELECT {{ base }} AS x", "must be alone on its line"),
    ("SELECT 1 WHERE d > {{ start_date }}", "pass values as @params"),
    ("{% if x %}\n{{ base }}\n{% endif %}\nSELECT }} 1", "must be alone on its line"),
])
def test_anything_but_a_whole_line_include_is_rejected(sql, message):
    with pytest.raises(ValueError, match=message):
        expand_sql(sql, {"base": "SELECT 1\n", "nested": "{{ base }}\n"})


def test_sql_hash_and_reload_follow_the_snippet(cfg):
    reg = Registry(cfg)
    before = reg.queries["bookings_daily"].sql_hash
    untouched = reg.queries["booking_exceptions"].sql_hash
    path = cfg.snippets_dir / "bookings_in_range.sql"
    path.write_text(path.read_text() + "  AND status != 'test'\n")
    os.utime(path, ns=(1, 1))  # a new mtime even on coarse filesystems
    reg.reload_if_changed()
    assert "status != 'test'" in reg.queries["bookings_daily"].sql
    assert reg.queries["bookings_daily"].sql_hash != before
    assert reg.queries["booking_exceptions"].sql_hash == untouched
    # a snippet that appears later is picked up too
    (cfg.snippets_dir / "new_one.sql").write_text("SELECT 1\n")
    reg.reload_if_changed()
    assert "new_one" in reg.snippets


def test_check_runs_on_the_expanded_sql(repo):
    assert issues(repo) == []
    snippet = repo / "snippets" / "bookings_in_range.sql"
    snippet.write_text(RANGE + "  AND region = @region\n")
    assert issues(repo) == [
        "error queries/bookings_daily: SQL uses @region but it is not declared under params",
        "error queries/cancellation_rate: SQL uses @region but it is not declared under params",
    ]
    snippet.write_text(RANGE + ";\nDROP TABLE `analytics.fct_bookings`\n")
    found = issues(repo, "booking_ops")
    assert "error queries/bookings_daily: SQL must be a single statement (found ';')" in found
    assert "error queries/bookings_daily: SQL contains a non-SELECT statement keyword: DROP" in found
    # params declared only for the snippet count as used
    snippet.write_text(RANGE)
    (repo / "queries" / "bookings_daily.sql").write_text("SELECT 1 AS day, 2 AS bookings, 3 AS gross_value\n{{ bookings_in_range }}\n")
    assert issues(repo) == []


def test_check_reports_broken_includes(repo):
    sql = repo / "queries" / "bookings_daily.sql"
    sql.write_text(sql.read_text().replace("{{ bookings_in_range }}", "{{ bookings_range }}"))
    # one error, on the file to fix; tiles that use the query do not repeat it
    assert issues(repo) == ["error queries/bookings_daily.sql: {{ bookings_range }}: snippets/bookings_range.sql "
                            "does not exist (known: bookings_in_range)"]
    # the query is not loaded, so it can never reach the warehouse with the include unresolved
    assert "bookings_daily" not in Registry(load_config(repo, environ={})).queries

    (repo / "snippets" / "bookings_range.sql").write_text("{{ bookings_in_range }}\n")
    assert issues(repo) == ["error queries/bookings_daily.sql: {{ bookings_range }}: snippets/bookings_range.sql "
                            "includes another snippet; snippets cannot include snippets"]


def test_check_reports_snippet_files_that_cannot_be_used(repo):
    folder = repo / "snippets"
    (folder / "unused.sql").write_text("SELECT 1\n")
    (folder / "empty.sql").write_text("\n")
    (folder / "bad-name.sql").write_text("SELECT 1\n")
    (folder / "notes.md").write_text("why\n")
    (folder / "crm").mkdir()
    (folder / "crm" / "base.sql").write_text("SELECT 1\n")
    assert issues(repo) == [
        "error snippets/bad-name.sql: a snippet name may hold only letters, digits and underscores, and cannot "
        "start with a digit; rename it so a query can include it",
        "error snippets/crm/base.sql: files in subfolders are not loaded; move it to snippets/",
        "error snippets/empty.sql: snippet is empty",
        "error snippets/notes.md: snippets/ holds only .sql files; this file is not loaded",
        "warning snippets/unused.sql: no query includes it; add a {{ unused }} line to a query's SQL or delete the file",
    ]
    # a single-dashboard check stays about that dashboard
    assert issues(repo, "booking_ops") == []


def test_sql_command_prints_the_expanded_sql(repo, monkeypatch):
    monkeypatch.chdir(repo)
    r = CliRunner().invoke(main, ["sql", "cancellation_rate"])
    assert r.exit_code == 0 and r.output == Registry(load_config(repo, environ={})).queries["cancellation_rate"].sql
    assert RANGE in r.output and "{{" not in r.output
    (repo / "snippets" / "bookings_in_range.sql").unlink()
    r = CliRunner().invoke(main, ["sql", "cancellation_rate"])
    assert r.exit_code == 1 and "snippets/bookings_in_range.sql does not exist" in r.output
    r = CliRunner().invoke(main, ["sql", "nope"])
    assert r.exit_code == 1 and "no query 'nope'" in r.output


def test_duckdb_receives_expanded_sql_with_rewritten_params(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from axial.duck import DuckDBWarehouse
    from axial.executor import Executor
    from axial.store import SQLiteStore as Store

    db = tmp_path / "ops.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE bookings AS SELECT DATE '2026-11-01' + CAST(i % 3 AS INTEGER) AS d FROM range(9) t(i)")
    con.close()
    (tmp_path / "axial.yaml").write_text("version: 2\nwarehouse: duckdb\nduckdb: {path: ops.duckdb}\n")
    (tmp_path / "queries").mkdir()
    (tmp_path / "snippets").mkdir()
    (tmp_path / "snippets" / "in_range.sql").write_text("WHERE d >= @start_date AND d < @end_date\n")
    (tmp_path / "queries" / "n.yaml").write_text("params: {start_date: DATE, end_date: DATE}\ncolumns: {n: INT64}\n")
    (tmp_path / "queries" / "n.sql").write_text("SELECT COUNT(*) AS n FROM bookings\n{{ in_range }}\n")
    cfg = load_config(tmp_path, environ={})
    q = Registry(cfg).queries["n"]
    exe = Executor(cfg, Store(":memory:"), DuckDBWarehouse(db))
    served = asyncio.run(exe.run(q, {"start_date": dt.date(2026, 11, 1), "end_date": dt.date(2026, 11, 3)}))
    assert served.result.rows == [{"n": 6}]
