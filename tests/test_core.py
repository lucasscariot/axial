import datetime as dt

import pytest

from bqdash.checks import run_checks
from bqdash.dbt import DbtIndex
from bqdash.registry import DateRange, ParamError, Registry, normalize_params, resolve_params
from bqdash.sqlcheck import check_read_only_select, referenced_params


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "with a as (select 1) select * from a;",
    "-- comment; with semicolon\nSELECT ';' AS x, `delete from` FROM t",
    "(SELECT 1) UNION ALL (SELECT 2)",
    "SELECT created_at FROM t WHERE updated = 1",
])
def test_read_only_accepts(sql):
    assert check_read_only_select(sql) == []


@pytest.mark.parametrize("sql", [
    "DELETE FROM t WHERE 1=1",
    "SELECT 1; DROP TABLE t",
    "DECLARE x INT64; SELECT x",
    "CREATE TABLE t AS SELECT 1",
    "",
])
def test_read_only_rejects(sql):
    assert check_read_only_select(sql)


def test_referenced_params_ignores_strings_and_comments():
    sql = "SELECT '@nope' -- @also_nope\nFROM t WHERE d >= @start_date AND d < @end_date"
    assert referenced_params(sql) == {"start_date", "end_date"}


def test_example_repo_checks_clean(cfg):
    reg = Registry(cfg)
    issues = run_checks(reg, DbtIndex.load(cfg))
    assert [str(i) for i in issues if i.level == "error"] == []


def test_resolve_params_types_and_range(cfg):
    q = Registry(cfg).queries["booking_exceptions"]
    dr = DateRange(max_days=31)
    v = resolve_params(q, {"start_date": "2026-11-01", "end_date": "2026-11-08"}, dr)
    assert v == {"start_date": dt.date(2026, 11, 1), "end_date": dt.date(2026, 11, 8), "limit": 500}
    assert normalize_params(v) == '{"end_date":"2026-11-08","limit":500,"start_date":"2026-11-01"}'
    with pytest.raises(ParamError, match="at most 31"):
        resolve_params(q, {"start_date": "2026-01-01", "end_date": "2026-03-01"}, dr)
    with pytest.raises(ParamError, match="must be after"):
        resolve_params(q, {"start_date": "2026-11-08", "end_date": "2026-11-08"}, dr)
    with pytest.raises(ParamError, match="expected DATE"):
        resolve_params(q, {"start_date": "yesterday", "end_date": "2026-11-08"}, dr)
    with pytest.raises(ParamError, match="unknown"):
        resolve_params(q, {"start_date": "2026-11-01", "end_date": "2026-11-08", "x": 1}, dr)
    with pytest.raises(ParamError, match="expected INT64"):
        resolve_params(q, {"start_date": "2026-11-01", "end_date": "2026-11-08", "limit": "1; DROP"}, dr)


def test_default_range_is_exclusive_end():
    s, e = DateRange(default_days=7).default(today=dt.date(2026, 11, 12))
    assert (s, e) == (dt.date(2026, 11, 6), dt.date(2026, 11, 13))


def test_check_reports_broken_references(cfg):
    (cfg.dashboards_dir / "broken.yaml").write_text(
        "title: Broken\n"
        "date_range: {default_days: 7, max_days: 31}\n"
        "tiles:\n"
        "  - {type: kpi, query: bookings_daily, value: nope, reduce: avg, span: 3}\n"
        "  - {type: line, query: missing_query, x: day, y: bookings}\n"
        "  - {type: line, query: bookings_daily, x: day, y: day, span: 13}\n"
        "  - {type: pie, query: bookings_daily}\n"
    )
    (cfg.queries_dir / "bad_model.yaml").write_text(
        "title: Bad\ndbt_models: [model.analytics.fct_bookingz]\nparams: {start_date: DATE, region: STRING}\n"
        "columns: {n: INT64}\n"
    )
    (cfg.queries_dir / "bad_model.sql").write_text("SELECT COUNT(*) AS n FROM t WHERE d >= @start_date AND d < @end_date")
    (cfg.dashboards_dir / "binds.yaml").write_text(
        "title: Binds\ndate_range: {default_days: 7}\ntiles:\n  - {type: table, query: bad_model}\n"
    )
    reg = Registry(cfg)
    msgs = [str(i) for i in run_checks(reg, DbtIndex.load(cfg))]
    text = "\n".join(msgs)
    assert "column 'nope' is not in query bookings_daily" in text
    assert "reduce 'avg' not supported" in text
    assert "query 'missing_query' does not exist" in text
    assert "column 'day' is DATE, expected a numeric type" in text
    assert "span must be between 1 and 12" in text
    assert "unknown tile type 'pie'" in text
    assert "fct_bookingz is not in the manifest (did you mean model.analytics.fct_bookings" in text
    assert "SQL uses @end_date but it is not declared" in text
    assert "required param region is not bound" in text
    assert "binds only one of start_date/end_date" in text


def test_dbt_search_prefers_marts(cfg):
    dbt = DbtIndex.load(cfg)
    hits = [m.name for _, m in dbt.search("bookings")]
    assert hits[0] == "fct_bookings"
    assert hits.index("fct_bookings") < hits.index("stg_bookings")
    m = dbt.models["model.analytics.fct_bookings"]
    assert m.columns["gross_value"].type == "NUMERIC"  # from catalog.json
    assert dbt.revision.startswith("sample-0000")
