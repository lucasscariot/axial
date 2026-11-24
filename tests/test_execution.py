import asyncio
import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from axial.app import create_app
from axial.config import load_config
from axial.executor import Executor, QueryError, QueryTimeout
from axial.registry import Registry

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
def client(cfg, wh, store):
    app = create_app(cfg, mode="live", warehouse=wh, store=store)
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
    q = Registry(cfg).queries["bookings_daily"]
    # history keeps the SQL the warehouse ran: the file with its snippet included
    source = (cfg.queries_dir / "bookings_daily.sql").read_text()
    snippet = (cfg.snippets_dir / "bookings_in_range.sql").read_text()
    assert job["sql_text"] == q.sql == source.replace("{{ bookings_in_range }}\n", snippet)
    assert job["sql_hash"] == q.sql_hash
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


def test_row_cap_truncates(cfg, wh, store):
    cfg.row_cap = 2
    wh.rows = [{"day": f"2026-11-0{i}", "bookings": i, "gross_value": 1.0} for i in range(1, 6)]
    app = create_app(cfg, mode="live", warehouse=wh, store=store)
    with TestClient(app) as c:
        r = c.post("/api/datasets", json={"dashboard_id": "booking_ops", "query_id": "bookings_daily",
                                          "filters": FILTERS}).json()
    assert len(r["rows"]) == 2 and r["truncated"] and r["total_rows"] == 5


# --- executor-level behaviour ------------------------------------------------


def make_exec(cfg, wh, store, **over):
    for k, v in over.items():
        setattr(cfg, k, v)
    return Executor(cfg, store, wh)


def q_and_values(cfg):
    q = Registry(cfg).queries["bookings_daily"]
    return q, {"start_date": dt.date(2026, 11, 1), "end_date": dt.date(2026, 11, 8)}


def test_concurrent_identical_requests_share_one_job(cfg, store):
    wh = FakeWarehouse(delay=0.2)
    ex = make_exec(cfg, wh, store)
    q, v = q_and_values(cfg)

    async def go():
        return await asyncio.gather(*[ex.run(q, v) for _ in range(5)])

    served = asyncio.run(go())
    assert len(wh.submitted) == 1
    assert sorted(s.source for s in served) == ["bigquery"] + ["shared"] * 4


def test_concurrency_is_bounded(cfg, store):
    wh = FakeWarehouse(delay=0.1)
    ex = make_exec(cfg, wh, store, max_concurrent_jobs=2)
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


def test_uncertain_submission_looks_up_same_job_before_retrying(cfg, store, monkeypatch):
    monkeypatch.setattr("axial.executor.time.sleep", lambda s: None)
    wh = FakeWarehouse()
    wh.fail_submit_uncertain = 1
    wh.uncertain_but_created = True
    ex = make_exec(cfg, wh, store)
    q, v = q_and_values(cfg)
    s = asyncio.run(ex.run(q, v))
    assert len(wh.submitted) == 1 and s.result.job_id == wh.submitted[0]["job_id"]

    wh2 = FakeWarehouse()
    wh2.fail_submit_uncertain = 2  # job never landed: retried with the same ID
    ex2 = make_exec(cfg, wh2, store)
    s2 = asyncio.run(ex2.run(q, v))
    assert len(wh2.submitted) == 1 and s2.result.job_id == wh2.submitted[0]["job_id"]


def test_job_recorded_before_submission(cfg, store):
    wh = FakeWarehouse()
    ex = make_exec(cfg, wh, store)
    seen = {}
    orig = wh.submit

    def spy(**kw):
        seen["row"] = ex.store.get_job(kw["job_id"])
        return orig(**kw)

    wh.submit = spy
    q, v = q_and_values(cfg)
    asyncio.run(ex.run(q, v))
    assert seen["row"]["status"] == "pending" and seen["row"]["location"] == "EU"


def test_timeout_then_reconcile(cfg, store):
    wh = FakeWarehouse()
    wh.timeout = True
    ex = make_exec(cfg, wh, store)
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


def test_failed_job_records_error(cfg, store):
    wh = FakeWarehouse()
    wh.fail_job = "Query exceeded limit for bytes billed"
    ex = make_exec(cfg, wh, store)
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


def test_instances_sharing_history_reconcile_each_others_jobs(cfg, backend):
    wh = FakeWarehouse()
    wh.timeout = True
    a, b = Executor(cfg, backend.open(), wh), Executor(cfg, backend.open(), wh)
    q, v = q_and_values(cfg)
    with pytest.raises(QueryTimeout) as ei:
        asyncio.run(a.run(q, v))
    wh.timeout = False
    a.store._exec("UPDATE bigquery_jobs SET recorded_at = recorded_at - 60")
    assert a.reconcile_once() + b.reconcile_once() == 1  # the second instance finds nothing left
    a.store.update_job(ei.value.job_id, status="running")  # a stale writer cannot reopen it
    job = b.store.get_job(ei.value.job_id)
    assert job["status"] == "done" and job["bytes_billed"] == wh.bytes_billed
    assert len(b.store.jobs_between(0, 1e12)) == 1 and b.store.get_request(ei.value.request_id)["outcome"] == "timeout"


# --- signed-in users (server.trust_iap_user) ---------------------------------


def as_user(client, header, query_id="bookings_daily", bypass=False):
    r = client.post("/api/datasets", headers={"X-Goog-Authenticated-User-Email": header} if header else {},
                    json={"dashboard_id": "booking_ops", "query_id": query_id, "filters": FILTERS,
                          "bypass_cache": bypass})
    assert r.status_code == 200, r.text
    return r.json()["request_id"]


def test_iap_header_is_ignored_unless_trusted(client, store):
    rid = as_user(client, "accounts.google.com:alice@example.com")
    assert store.get_request(rid)["user_email"] is None
    u = client.get("/api/usage").json()
    assert u["users"] == []
    assert client.get(f"/api/requests/{rid}").json()["request"]["user_email"] is None


def test_each_request_records_its_own_user(cfg, wh, store):
    cfg.trust_iap_user = True
    with TestClient(create_app(cfg, mode="live", warehouse=wh, store=store)) as c:
        first = as_user(c, "accounts.google.com:Alice@Example.com")
        cached = as_user(c, "accounts.google.com:bob@example.com")  # served from alice's result
        anonymous = as_user(c, None)
        assert len(wh.submitted) == 1
        rows = {rid: store.get_request(rid) for rid in (first, cached, anonymous)}
        assert [(r["source"], r["user_email"]) for r in rows.values()] == [
            ("bigquery", "alice@example.com"), ("app_cache", "bob@example.com"), ("app_cache", None)]
        assert all(s["labels"].keys() == {"app", "query_id", "sql_hash", "dashboard"} for s in wh.submitted)
        assert not any("example.com" in s["job_id"] for s in wh.submitted)

        detail = c.get(f"/api/requests/{cached}").json()
        assert detail["request"]["user_email"] == "bob@example.com"
        insp = c.get("/api/inspect", params={"dashboard_id": "booking_ops", "query_id": "bookings_daily",
                                             "filters": json.dumps(FILTERS)}).json()
        assert insp["request"]["user_email"] is None  # the latest request was anonymous
        runs = c.get("/api/usage/bookings_daily").json()["runs"]
        assert [r["user_email"] for r in runs] == [None, "bob@example.com", "alice@example.com"]

        users = c.get("/api/usage").json()["users"]
        assert [(u["user_email"], u["requests"], u["jobs"]) for u in users] == [
            ("alice@example.com", 1, 1), ("bob@example.com", 1, 0)]
        assert users[0]["bytes_billed"] == wh.bytes_billed and users[1]["bytes_billed"] == 0


@pytest.mark.parametrize("header", [
    "", "accounts.google.com:", "accounts.google.com:not-an-email", "accounts.google.com:a@b@c",
    "accounts.google.com:al ice@example.com", "accounts.google.com:<script>@x.com",
    "accounts.google.com:" + "a" * 250 + "@example.com", "x" * 5000, "accounts.google.com:élodie@example.com",
])
def test_malformed_iap_headers_record_no_user_and_never_fail(cfg, wh, store, header):
    cfg.trust_iap_user = True
    with TestClient(create_app(cfg, mode="live", warehouse=wh, store=store)) as c:
        r = c.post("/api/datasets", headers={"X-Goog-Authenticated-User-Email": header.encode()},
                   json={"dashboard_id": "booking_ops", "query_id": "bookings_daily", "filters": FILTERS})
    assert r.status_code == 200
    assert store.get_request(r.json()["request_id"])["user_email"] is None


def test_iap_user_normalisation():
    from axial.app import iap_user

    assert iap_user("accounts.google.com:Jane.Doe+dash@Example.CO.uk") == "jane.doe+dash@example.co.uk"
    assert iap_user("  bob@example.com ") == "bob@example.com"  # no namespace prefix
    assert iap_user("securetoken.google.com/proj:ext@example.com") == "ext@example.com"
    assert iap_user(None) is None and iap_user("accounts.google.com:12345") is None


def test_shared_jobs_and_failures_are_attributed_to_each_waiting_user(cfg, store):
    wh = FakeWarehouse(delay=0.2)
    ex = make_exec(cfg, wh, store)
    q, v = q_and_values(cfg)

    async def go():
        return await asyncio.gather(*[ex.run(q, v, user_email=u) for u in ("a@x.io", "b@x.io", None)])

    served = asyncio.run(go())
    assert len(wh.submitted) == 1
    assert [(s.source, store.get_request(s.request_id)["user_email"]) for s in served] == [
        ("bigquery", "a@x.io"), ("shared", "b@x.io"), ("shared", None)]

    wh.timeout = True
    with pytest.raises(QueryTimeout) as timed_out:
        asyncio.run(ex.run(q, v, bypass_cache=True, user_email="c@x.io"))
    assert store.get_request(timed_out.value.request_id)["user_email"] == "c@x.io"
    wh.timeout, wh.fail_job = False, "boom"
    with pytest.raises(QueryError) as failed:
        asyncio.run(ex.run(q, v, bypass_cache=True, user_email="d@x.io"))
    row = store.get_request(failed.value.request_id)
    assert row["outcome"] == "error" and row["user_email"] == "d@x.io"


def test_health_touches_neither_warehouse_nor_store(cfg, wh, store):
    with TestClient(create_app(cfg, mode="live", warehouse=wh, store=store)) as c:
        assert c.get("/api/health").json() == {"status": "ok"}
    assert wh.submitted == [] and store.requests_between(0, 2**40) == []
    with TestClient(create_app(cfg, mode="fixture")) as c:
        assert c.get("/api/health").json() == {"status": "ok"}


def test_app_revision_comes_from_the_environment(cfg, wh, store, monkeypatch):
    from axial.config import load_config
    from axial.executor import git_revision

    monkeypatch.setenv("AXIAL_APP_REVISION", "abc123def456")
    assert git_revision(cfg.root) == "abc123def456"
    # not a config section: AXIAL_<SECTION>_<KEY> parsing leaves it alone
    assert load_config(cfg.root, environ={"AXIAL_APP_REVISION": "x"}) == load_config(cfg.root, environ={})
    with TestClient(create_app(cfg, mode="live", warehouse=wh, store=store)) as c:
        load_dashboard(c)
        job_id = c.get("/api/usage/bookings_daily").json()["runs"][0]["job_id"]
        assert c.app.state.executor.store.get_job(job_id)["app_revision"] == "abc123def456"
    monkeypatch.setenv("AXIAL_APP_REVISION", "")
    assert git_revision(cfg.root) is None  # the test project copy is not a git checkout


def test_fixture_mode_makes_up_one_row_for_a_single_value_kpi(repo):
    """A summary may return a label or a date beside its numbers; a `single` KPI still needs one row."""
    (repo / "queries" / "region_summary.sql").write_text(
        "SELECT 'North' AS region, @start_date AS since, 3 AS bookings")
    (repo / "queries" / "region_summary.yaml").write_text(
        "params: {start_date: DATE, end_date: DATE}\n"
        "columns: {region: STRING, since: DATE, bookings: INT64}\n")
    (repo / "queries" / "region_list.sql").write_text("SELECT 'North' AS region, 3 AS bookings")
    (repo / "queries" / "region_list.yaml").write_text("columns: {region: STRING, bookings: INT64}\n")
    (repo / "dashboards" / "regions.yaml").write_text(
        "title: Regions\n"
        "date_range: {default_days: 7, max_days: 31}\n"
        "tiles:\n"
        "  - {type: kpi, query: region_summary, value: bookings}\n"
        "  - {type: table, query: region_summary}\n"
        "  - {type: kpi, query: region_list, value: bookings, reduce: sum}\n"
        "  - {type: kpi, query: bookings_daily, value: bookings, reduce: last}\n")

    def rows(client, query):
        r = client.post("/api/datasets", json={"dashboard_id": "regions", "query_id": query, "filters": FILTERS})
        assert r.status_code == 200, r.text
        return r.json()["rows"]

    with TestClient(create_app(load_config(repo, environ={}), mode="fixture")) as c:
        summary = rows(c, "region_summary")
        assert len(summary) == 1 and set(summary[0]) == {"region", "since", "bookings"}
        assert len(rows(c, "region_list")) == 25  # reduced over many rows: unchanged
        assert len(rows(c, "bookings_daily")) == 7  # a series, even though a KPI reads its last value
        # a saved fixture still wins
        (repo / "fixtures").mkdir()
        (repo / "fixtures" / "region_summary.json").write_text(
            '[{"region": "A", "since": "2026-11-01", "bookings": 1}, {"region": "B", "since": "2026-11-01", "bookings": 2}]')
        assert len(rows(c, "region_summary")) == 2


def test_fixture_mode_draws_readings_for_time_series_tiles(cfg):
    """A tile with a TIMESTAMP x and a facet gets ordered readings per facet value, with BOOL
    columns true in contiguous runs and step columns held, instead of scattered random rows."""
    with TestClient(create_app(cfg, mode="fixture")) as c:
        rows = c.post("/api/datasets", json={"dashboard_id": "service_health", "query_id": "service_readings",
                                             "filters": FILTERS}).json()["rows"]
        exceptions = c.post("/api/datasets", json={"dashboard_id": "booking_ops", "query_id": "booking_exceptions",
                                                   "filters": FILTERS}).json()["rows"]
    assert len(exceptions) == 25  # a TIMESTAMP column no chart plots keeps the old shape
    services = list(dict.fromkeys(r["service"] for r in rows))
    assert len(services) == 8 and [r["service"] for r in rows] == sorted(r["service"] for r in rows)
    for s in services:
        mine = [r for r in rows if r["service"] == s]
        times = [dt.datetime.fromisoformat(r["read_at"]) for r in mine]
        assert times == sorted(times) and len(mine) > 40
        assert dt.datetime(2026, 11, 1) <= times[0] and times[-1] < dt.datetime(2026, 11, 8)
        flags = [r["incident"] for r in mine]
        runs = sum(1 for i, f in enumerate(flags) if f and (i == 0 or not flags[i - 1]))
        assert 1 <= runs < sum(flags) + 1 and not all(flags)
        held = sum(1 for a, b in zip(mine, mine[1:]) if a["replicas"] == b["replicas"])
        assert held > len(mine) * 0.7  # a step column changes now and then, not every reading
        assert all(0 <= r["error_rate"] < 1 and isinstance(r["replicas"], int) for r in mine)
