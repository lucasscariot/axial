import asyncio
import datetime as dt

import pytest
import yaml
from click.testing import CliRunner
from fastapi.testclient import TestClient

from axial.app import create_app
from axial.checks import run_checks
from axial.cli import main
from axial.config import ConfigError, load_config
from axial.duck import DuckDBWarehouse, to_duckdb_sql
from axial.executor import Executor, QueryError
from axial.registry import Registry
from axial.store import SQLiteStore as Store

duckdb = pytest.importorskip("duckdb")

BOOKINGS_SQL = """SELECT day, count(*) AS bookings, sum(amount) AS gross_value
FROM bookings
WHERE day >= @start_date AND day < @end_date
GROUP BY day
ORDER BY day
"""


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "data" / "ops.duckdb"
    path.parent.mkdir()
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE bookings AS SELECT DATE '2026-11-01' + (i % 10)::INT AS day, i::DOUBLE AS amount, "
                "CASE WHEN i % 7 = 0 THEN 'cancelled' ELSE 'ok' END AS status, "
                "CASE WHEN i % 4 = 0 THEN 'North' WHEN i % 4 = 1 THEN 'South' ELSE NULL END AS region "
                "FROM range(100) t(i)")
    con.execute("CREATE VIEW bookings_ok AS SELECT * FROM bookings WHERE status = 'ok'")
    con.close()
    return path


@pytest.fixture
def duck_repo(repo, db):
    """The test project switched to a local DuckDB file, with the bookings query rewritten for it."""
    raw = yaml.safe_load((repo / "axial.yaml").read_text())
    raw.pop("bigquery", None)
    raw["warehouse"] = "duckdb"
    raw["duckdb"] = {"path": "data/ops.duckdb", "datasets": ["ops.main"]}
    (repo / "axial.yaml").write_text(yaml.safe_dump(raw))
    (repo / "queries" / "bookings_daily.sql").write_text(BOOKINGS_SQL)
    return repo


def test_params_are_rewritten_outside_strings_and_comments():
    sql = "SELECT '@a' AS s, \"@b\" AS q, @c AS c -- @d\n/* @e */ FROM t WHERE x = @f"
    assert to_duckdb_sql(sql) == "SELECT '@a' AS s, \"@b\" AS q, $c AS c -- @d\n/* @e */ FROM t WHERE x = $f"


def test_config_resolves_path_and_turns_billing_off(duck_repo, db):
    cfg = load_config(duck_repo, environ={})
    assert cfg.warehouse == "duckdb" and cfg.local
    assert cfg.duckdb_path == db.resolve()
    assert cfg.billing_mode == "local" and cfg.billing_project is None and cfg.maximum_bytes_billed is None
    assert cfg.datasets == ["ops.main"]
    assert cfg.display()["database"] == "ops.duckdb"


def test_config_errors(duck_repo):
    raw = yaml.safe_load((duck_repo / "axial.yaml").read_text())
    raw["duckdb"] = {}
    (duck_repo / "axial.yaml").write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigError, match="duckdb.path"):
        load_config(duck_repo, environ={})
    assert load_config(duck_repo, environ={"AXIAL_DUCKDB_PATH": "data/ops.duckdb"}).duckdb_path.name == "ops.duckdb"
    raw["warehouse"] = "postgres"
    (duck_repo / "axial.yaml").write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigError, match="warehouse must be one of"):
        load_config(duck_repo, environ={})


def test_executor_runs_against_duckdb(duck_repo, db):
    cfg = load_config(duck_repo, environ={})
    store = Store(":memory:")
    reg = Registry(cfg)
    exe = Executor(cfg, store, DuckDBWarehouse(db))
    q = reg.queries["bookings_daily"]
    served = asyncio.run(exe.run(q, {"start_date": dt.date(2026, 11, 1), "end_date": dt.date(2026, 11, 4)}))
    rows = served.result.rows
    assert [r["day"] for r in rows] == ["2026-11-01", "2026-11-02", "2026-11-03"]
    assert rows[0] == {"day": "2026-11-01", "bookings": 10, "gross_value": float(sum(range(0, 100, 10)))}
    job = store.get_job(served.result.job_id)
    assert job["outcome"] == "success" and job["bytes_billed"] is None and job["exec_ms"] is not None
    assert job["project"] == "ops" and job["location"] == str(db)
    # app cache serves the second call
    again = asyncio.run(exe.run(q, {"start_date": dt.date(2026, 11, 1), "end_date": dt.date(2026, 11, 4)}))
    assert again.source == "app_cache"


def test_failed_query_is_recorded(duck_repo, db):
    cfg = load_config(duck_repo, environ={})
    (duck_repo / "queries" / "bookings_daily.sql").write_text(BOOKINGS_SQL.replace("bookings\n", "missing_table\n"))
    store = Store(":memory:")
    exe = Executor(cfg, store, DuckDBWarehouse(db))
    q = Registry(cfg).queries["bookings_daily"]
    with pytest.raises(QueryError, match="missing_table"):
        asyncio.run(exe.run(q, {"start_date": dt.date(2026, 11, 1), "end_date": dt.date(2026, 11, 4)}))


def test_database_is_read_only_and_sealed(db, tmp_path):
    wh = DuckDBWarehouse(db)
    (tmp_path / "secret.csv").write_text("a\n1\n")
    for i, sql in enumerate([f"SELECT * FROM read_csv('{tmp_path / 'secret.csv'}')",
                             "SELECT current_setting('enable_external_access') AS v FROM (SET enable_external_access = true)",
                             "CREATE TABLE x AS SELECT 1"]):
        wh.submit(job_id=f"j{i}", sql=sql, params=[], labels={}, use_query_cache=True, maximum_bytes_billed=None)
        with pytest.raises(Exception):
            wh.wait_and_fetch(f"j{i}", 10, 10)
    con = wh.connect()
    with pytest.raises(duckdb.Error):
        con.execute("SET enable_external_access = true")
    con.close()


def test_concurrent_jobs_share_the_database_instance(db):
    """Dashboards run several tiles at once; every connection after the first must still work."""
    wh = DuckDBWarehouse(db)
    held = wh.connect()  # keeps the shared instance (and its locked configuration) alive
    try:
        for i in range(8):
            wh.submit(job_id=f"c{i}", sql="SELECT count(*) AS n FROM bookings WHERE amount >= @m",
                      params=[("m", "INT64", i)], labels={}, use_query_cache=True, maximum_bytes_billed=None)
        results = [wh.wait_and_fetch(f"c{i}", 10, 10)[1][0]["n"] for i in range(8)]
    finally:
        held.close()
    assert results == [100 - i for i in range(8)]


def test_dry_run_plans_and_lists_tables(db):
    wh = DuckDBWarehouse(db)
    params = [("start_date", "DATE", dt.date(2026, 11, 1)), ("end_date", "DATE", dt.date(2026, 11, 2))]
    r = wh.dry_run("WITH b AS (SELECT * FROM bookings_ok) SELECT count(*) FROM b WHERE day >= @start_date "
                   "AND day < @end_date", params)
    assert r.statement_type == "SELECT" and r.bytes_processed == 0 and r.referenced_tables == ["bookings_ok"]
    with pytest.raises(ValueError, match="nope"):
        wh.dry_run("SELECT nope FROM bookings", [])


def test_metadata(db):
    wh = DuckDBWarehouse(db)
    assert wh.list_datasets() == [{"id": "ops.main"}]
    assert {(t["id"], t["type"]) for t in wh.list_tables("main")} == {("ops.main.bookings", "TABLE"),
                                                                       ("ops.main.bookings_ok", "VIEW")}
    t = wh.get_table("bookings")
    assert t["id"] == "ops.main.bookings" and t["num_rows"] == 100
    assert [(c["name"], c["type"]) for c in t["columns"]] == [("day", "DATE"), ("amount", "DOUBLE"), ("status", "VARCHAR"), ("region", "VARCHAR")]
    assert "status = 'ok'" in wh.get_table("ops.main.bookings_ok")["view_query"]
    with pytest.raises(ValueError):
        wh.get_table("nope")


def test_app_serves_duckdb_datasets(duck_repo):
    cfg = load_config(duck_repo, environ={})
    app = create_app(cfg, mode="live", store=Store(":memory:"))
    with TestClient(app) as c:
        assert c.get("/api/meta").json()["display"]["warehouse"] == "duckdb"
        r = c.post("/api/datasets", json={"dashboard_id": "booking_ops", "query_id": "bookings_daily",
                                          "filters": {"start_date": "2026-11-01", "end_date": "2026-11-03"}})
        assert r.status_code == 200, r.text
        assert [row["bookings"] for row in r.json()["rows"]] == [10, 10]


def test_select_filter_filters_rows_on_duckdb(duck_repo):
    """End to end on a real engine: the choices come from a query, and a null default keeps every row."""
    q = duck_repo / "queries"
    (q / "regions.sql").write_text("SELECT region FROM bookings WHERE day >= @start_date AND day < @end_date "
                                   "AND region IS NOT NULL GROUP BY region ORDER BY region")
    (q / "region_bookings.sql").write_text(
        "SELECT day, coalesce(region, 'none') AS region, count(*) AS bookings, sum(amount) AS gross_value\n"
        "FROM bookings\nWHERE day >= @start_date AND day < @end_date\n"
        "  AND (@region IS NULL OR region = @region)\n  AND (@channel IS NULL OR status = @channel)\n"
        "GROUP BY ALL\nORDER BY day, region\n")
    cfg = load_config(duck_repo, environ={})
    assert not [i for i in run_checks(Registry(cfg), "regional_bookings") if i.level == "error"]
    app = create_app(cfg, mode="live", store=Store(":memory:"))
    dates = {"start_date": "2026-11-01", "end_date": "2026-11-03"}  # 20 rows: 5 North, 5 South, 10 null

    def rows(query, **extra):
        r = c.post("/api/datasets", json={"dashboard_id": "regional_bookings", "query_id": query,
                                          "filters": {**dates, **extra}})
        assert r.status_code == 200, r.text
        return r.json()["rows"]

    def total(**extra):
        return sum(r["bookings"] for r in rows("region_bookings", **extra))

    with TestClient(app) as c:
        assert [r["region"] for r in rows("regions")] == ["North", "South"]
        assert total() == 20  # All: @region is NULL, so rows with no region count too
        assert total(region="North") == 5 and total(region="South") == 5
        assert total(region="West") == 0  # a value that is not a choice is bound as is
        assert total(region="North", channel="cancelled") == 1


def test_cli_commands(duck_repo, monkeypatch):
    monkeypatch.chdir(duck_repo)
    runner = CliRunner()
    r = runner.invoke(main, ["run", "bookings_daily", "--start", "2026-11-01", "--end", "2026-11-03", "--json"])
    assert r.exit_code == 0, r.output
    assert '"total_rows": 2' in r.output
    r = runner.invoke(main, ["dry-run", "bookings_daily", "--start", "2026-11-01", "--end", "2026-11-03"])
    assert r.exit_code == 0 and "plan:           ok" in r.output and "bookings" in r.output
    r = runner.invoke(main, ["tables"])
    assert r.exit_code == 0 and "ops.main.bookings  TABLE" in r.output
    r = runner.invoke(main, ["schema", "bookings"])
    assert r.exit_code == 0 and "amount" in r.output


def test_init_duckdb_project(tmp_path, db, monkeypatch):
    target = tmp_path / "dash"
    r = CliRunner().invoke(main, ["init", str(target), "--duckdb", "../data/ops.duckdb"])
    assert r.exit_code == 0, r.output
    raw = yaml.safe_load((target / "axial.yaml").read_text())
    assert raw["warehouse"] == "duckdb" and raw["duckdb"]["path"] == "../data/ops.duckdb"
    assert "bigquery" not in raw and "billing" not in raw
    cfg = load_config(target, environ={})
    assert cfg.duckdb_path == db.resolve()
    monkeypatch.chdir(target)
    assert CliRunner().invoke(main, ["check"]).exit_code == 0
