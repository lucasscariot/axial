import asyncio
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from axial.app import create_app
from axial.executor import Executor, QueryError, QueryTimeout
from axial.registry import Registry
from axial.store import SQLiteStore as Store

from .conftest import FakeWarehouse

FILTERS = {"start_date": "2026-11-01", "end_date": "2026-11-08"}


def load_dashboard(client, dashboard_id="booking_ops", filters=FILTERS, bypass=False):
    """What the browser does: one dataset request per tile, sharing by query in the UI."""
    d = client.get(f"/api/dashboards/{dashboard_id}").json()
    out = []
    for tile in d["tiles"]:  # deliberately per tile, so sharing is proven server-side
        r = client.post("/api/datasets", json={"dashboard_id": dashboard_id, "query_id": tile["query"],
                                               "filters": filters, "bypass_cache": bypass})
        assert r.status_code == 200, r.text
        out.append(r.json())
    return out


@pytest.fixture
def wh():
    return FakeWarehouse()


@pytest.fixture
def client(cfg, wh):
    app = create_app(cfg, mode="live", warehouse=wh, store=Store(":memory:"))
    with TestClient(app) as c:
        yield c


def test_cold_load_one_job_per_query_warm_load_zero(client, wh):
    load_dashboard(client)
    queries = [s["labels"]["query_id"] for s in wh.submitted]
    # KPIs and trend share bookings_daily: one job for it, cold
    assert sorted(queries) == ["booking_exceptions", "bookings_daily", "cancellation_rate"]
    load_dashboard(client)
    assert len(wh.submitted) == 3  # warm reload submits nothing


def test_job_parameters_are_native_and_bounded(client, wh, cfg):
    load_dashboard(client)
    job = next(s for s in wh.submitted if s["labels"]["query_id"] == "booking_exceptions")
    assert ("start_date", "DATE", dt.date(2026, 11, 1)) in job["params"]
    assert ("limit", "INT64", 500) in job["params"]
    assert "@start_date" in job["sql"] and "2026-11-01" not in job["sql"]
    assert job["maximum_bytes_billed"] == cfg.maximum_bytes_billed
    assert job["labels"]["dashboard"] == "booking_ops"


def test_usage_traces_cold_job_to_sql_version(client, wh, cfg):
    load_dashboard(client)
    load_dashboard(client)
    u = client.get("/api/usage").json()
    row = next(r for r in u["queries"] if r["query_id"] == "bookings_daily")
    # 4 tiles use bookings_daily across 2 loads = 8 requests, 1 job
    assert row["requests"] == 8 and row["jobs"] == 1
    assert row["app_cache_hit_rate"] == pytest.approx(7 / 8)
    assert row["bytes_billed"] == wh.bytes_billed
    assert row["est_cost_usd"] == pytest.approx(wh.bytes_billed / 2**40 * cfg.price_per_tib_usd)

    insp = client.get("/api/inspect", params={"dashboard_id": "booking_ops", "query_id": "bookings_daily",
                                              "filters": '{"start_date":"2026-11-01","end_date":"2026-11-08"}'}).json()
    assert insp["matches_selected_filters"]
    job = insp["job"]
    sql = (cfg.queries_dir / "bookings_daily.sql").read_text()
    assert job["sql_text"] == sql
    assert job["sql_hash"] == Registry(cfg).queries["bookings_daily"].sql_hash
    assert job["engine_version"]
    assert job["console_url"].endswith(f"j=bq:EU:{job['job_id']}&page=queryresults")
    assert job["exec_ms"] == 120 and job["queue_ms"] == 5

    runs = client.get("/api/usage/bookings_daily").json()
    sources = sorted({r["source"] for r in runs["runs"]})
    assert sources == ["app_cache", "bigquery"]
    assert all(r["job"]["job_id"] == job["job_id"] for r in runs["runs"])


def test_before_after_comparison_with_caches_bypassed(client, wh, cfg):
    load_dashboard(client, bypass=True)
    first = len(wh.submitted)
    assert all(s["use_query_cache"] is False for s in wh.submitted)
    # change the query, as you would after finding it expensive
    p = cfg.queries_dir / "bookings_daily.sql"
    p.write_text(p.read_text().replace("COUNT(*)", "COUNT(1)"))
    wh.bytes_billed = 2 * 2**20
    load_dashboard(client, bypass=True)
    assert len(wh.submitted) > first
    v = client.get("/api/usage/bookings_daily").json()["versions"]
    bypassed = [x for x in v if x["bq_cache_bypassed"]]
    assert len({x["sql_hash"] for x in bypassed}) == 2
    newest, oldest = bypassed[0], bypassed[-1]
    assert newest["median_bytes_billed"] < oldest["median_bytes_billed"]


def test_rejects_bad_filters(client):
    bad = client.post("/api/datasets", json={"dashboard_id": "booking_ops", "query_id": "bookings_daily",
                                             "filters": {"start_date": "2026-01-01", "end_date": "2026-03-01"}})
    assert bad.status_code == 400 and "at most 31" in bad.text
    other = client.post("/api/datasets", json={"dashboard_id": "booking_ops", "query_id": "bookings_daily",
                                               "filters": {**FILTERS, "limit": 5}})
    assert other.status_code == 400
    notile = client.post("/api/datasets", json={"dashboard_id": "booking_ops", "query_id": "nope", "filters": FILTERS})
    assert notile.status_code == 400


def test_row_cap_truncates(cfg, wh):
    cfg.row_cap = 2
    wh.rows = [{"day": f"2026-11-0{i}", "bookings": i, "gross_value": 1.0} for i in range(1, 6)]
    app = create_app(cfg, mode="live", warehouse=wh, store=Store(":memory:"))
    with TestClient(app) as c:
        r = c.post("/api/datasets", json={"dashboard_id": "booking_ops", "query_id": "bookings_daily",
                                          "filters": FILTERS}).json()
    assert len(r["rows"]) == 2 and r["truncated"] and r["total_rows"] == 5


# --- executor-level behaviour ------------------------------------------------


def make_exec(cfg, wh, **over):
    for k, v in over.items():
        setattr(cfg, k, v)
    return Executor(cfg, Store(":memory:"), wh)


def q_and_values(cfg):
    q = Registry(cfg).queries["bookings_daily"]
    return q, {"start_date": dt.date(2026, 11, 1), "end_date": dt.date(2026, 11, 8)}


def test_concurrent_identical_requests_share_one_job(cfg):
    wh = FakeWarehouse(delay=0.2)
    ex = make_exec(cfg, wh)
    q, v = q_and_values(cfg)

    async def go():
        return await asyncio.gather(*[ex.run(q, v) for _ in range(5)])

    served = asyncio.run(go())
    assert len(wh.submitted) == 1
    assert sorted(s.source for s in served) == ["bigquery"] + ["shared"] * 4


def test_concurrency_is_bounded(cfg):
    wh = FakeWarehouse(delay=0.1)
    ex = make_exec(cfg, wh, max_concurrent_jobs=2)
    q = Registry(cfg).queries["bookings_daily"]
    active = {"now": 0, "max": 0}
    orig = wh.wait_and_fetch

    def tracked(*a, **k):
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        try:
            return orig(*a, **k)
        finally:
            active["now"] -= 1

    wh.wait_and_fetch = tracked

    async def go():
        await asyncio.gather(*[ex.run(q, {"start_date": dt.date(2026, 11, d), "end_date": dt.date(2026, 11, 20)})
                               for d in range(1, 7)])

    asyncio.run(go())
    assert len(wh.submitted) == 6 and active["max"] <= 2


def test_uncertain_submission_looks_up_same_job_before_retrying(cfg, monkeypatch):
    monkeypatch.setattr("axial.executor.time.sleep", lambda s: None)
    wh = FakeWarehouse()
    wh.fail_submit_uncertain = 1
    wh.uncertain_but_created = True
    ex = make_exec(cfg, wh)
    q, v = q_and_values(cfg)
    s = asyncio.run(ex.run(q, v))
    assert len(wh.submitted) == 1 and s.result.job_id == wh.submitted[0]["job_id"]

    wh2 = FakeWarehouse()
    wh2.fail_submit_uncertain = 2  # job never landed: retried with the same ID
    ex2 = make_exec(cfg, wh2)
    s2 = asyncio.run(ex2.run(q, v))
    assert len(wh2.submitted) == 1 and s2.result.job_id == wh2.submitted[0]["job_id"]


def test_job_recorded_before_submission(cfg):
    wh = FakeWarehouse()
    ex = make_exec(cfg, wh)
    seen = {}
    orig = wh.submit

    def spy(**kw):
        seen["row"] = ex.store.get_job(kw["job_id"])
        return orig(**kw)

    wh.submit = spy
    q, v = q_and_values(cfg)
    asyncio.run(ex.run(q, v))
    assert seen["row"]["status"] == "pending" and seen["row"]["location"] == "EU"


def test_timeout_then_reconcile(cfg):
    wh = FakeWarehouse()
    wh.timeout = True
    ex = make_exec(cfg, wh)
    q, v = q_and_values(cfg)
    with pytest.raises(QueryTimeout) as ei:
        asyncio.run(ex.run(q, v))
    job = ex.store.get_job(ei.value.job_id)
    assert job["status"] == "running" and job["bytes_billed"] is None
    req = ex.store.get_request(ei.value.request_id)
    assert req["outcome"] == "timeout" and req["job_id"] == job["job_id"]
    # job finishes later in BigQuery; reconciliation records its final stats
    wh.timeout = False
    ex.clock = lambda: 1e12
    ex.store._exec("UPDATE bigquery_jobs SET recorded_at = recorded_at - 60")
    assert ex.reconcile_once() == 1
    job = ex.store.get_job(job["job_id"])
    assert job["status"] == "done" and job["bytes_billed"] == wh.bytes_billed


def test_failed_job_records_error(cfg):
    wh = FakeWarehouse()
    wh.fail_job = "Query exceeded limit for bytes billed"
    ex = make_exec(cfg, wh)
    q, v = q_and_values(cfg)
    with pytest.raises(QueryError, match="bytes billed"):
        asyncio.run(ex.run(q, v))
    rows = ex.store.jobs_between(0, 1e12)
    assert rows[0]["outcome"] == "error" and rows[0]["status"] == "done"


def test_fixture_mode_makes_no_queries(cfg):
    app = create_app(cfg, mode="fixture")
    with TestClient(app) as c:
        r = c.post("/api/datasets", json={"dashboard_id": "booking_ops", "query_id": "bookings_daily",
                                          "filters": FILTERS}).json()
        assert r["source"] == "fixture" and len(r["rows"]) == 7
        assert set(r["rows"][0]) == {"day", "bookings", "gross_value"}
        assert c.get("/api/usage").status_code == 409
