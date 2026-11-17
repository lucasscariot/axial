"""The history backends: same behaviour, same row shapes, safe to share between instances."""

import json
import sqlite3
import sys
import threading

import pytest

from axial import store as store_mod
from axial.config import ConfigError, load_config
from axial.store import SQLiteStore, postgres_conninfo
from axial.usage import query_runs, summarize

JOB = dict(job_id="axial_q_1", project="p", location="EU", query_id="q", sql_hash="h1", sql_text="SELECT 1",
           params_json='{"d": 1}', app_revision="abc", engine_version="0.0.0", use_query_cache=1,
           recorded_at=1000.0)


def fill(s):
    """The same history in any store: a finished job, a running one and three requests."""
    s.insert_job(**JOB)
    s.update_job("axial_q_1", status="done", outcome="success", cache_hit=0, bytes_processed=5 * 2**40,
                 bytes_billed=5 * 2**40, slot_ms=4000, created_ms=1_000_000_000_000, started_ms=1_000_000_000_005,
                 ended_ms=1_000_000_000_125, queue_ms=5, exec_ms=120, reconciled_at=1001.5)
    s.insert_job(**{**JOB, "job_id": "axial_q_2", "use_query_cache": 0, "recorded_at": 1002.0})
    s.insert_request(requested_at=1000.25, query_id="q", dashboard_id="d", params_json='{"d": 1}', sql_hash="h1",
                     source="bigquery", job_id="axial_q_1", bypass_cache=False, outcome="ok", app_ms=12.5,
                     row_count=3, truncated=True)
    s.insert_request(requested_at=1001.0, query_id="q", dashboard_id="d", params_json='{"d": 1}', sql_hash="h1",
                     source="app_cache", job_id="axial_q_1", bypass_cache=0, outcome="ok", app_ms=1.0,
                     row_count=3, truncated=False)
    return s.insert_request(requested_at=1002.5, query_id="q", dashboard_id=None, params_json='{"d": 2}',
                            sql_hash="h1", source="bigquery", job_id="axial_q_2", bypass_cache=True,
                            outcome="timeout", error="still running", app_ms=90000.0, user_email="a@example.com")


def test_rows_keep_their_python_shapes(store):
    rid = fill(store)
    job = store.get_job("axial_q_1")
    assert job["bytes_billed"] == 5 * 2**40  # past 32 bits
    assert type(job["recorded_at"]) is float and type(job["reconciled_at"]) is float
    assert type(job["cache_hit"]) is int and type(job["use_query_cache"]) is int and type(job["exec_ms"]) is int
    assert store.get_job("axial_q_2")["cache_hit"] is None
    req = store.get_request(rid)
    assert type(req["id"]) is int and req["id"] == rid
    assert req["bypass_cache"] == 1 and type(req["bypass_cache"]) is int and req["truncated"] == 0
    assert type(req["requested_at"]) is float and type(req["app_ms"]) is float and req["row_count"] is None
    assert list(req) == [c[0] for c in store_mod.TABLES["query_requests"]]
    assert list(job) == [c[0] for c in store_mod.TABLES["bigquery_jobs"]]


def test_backends_return_identical_history(store, cfg):
    reference = SQLiteStore(":memory:")
    fill(store), fill(reference)
    for s in (store, reference):
        assert [r["job_id"] for r in s.unfinished_jobs()] == ["axial_q_2"]
    dump = lambda s: json.dumps([s.requests_between(0, 2000), s.jobs_between(0, 2000, "q"),  # noqa: E731
                                 s.latest_request("q", '{"d": 1}'), s.latest_request("q", '{"nope": 0}'),
                                 summarize(s, cfg, 0, 2000), query_runs(s, cfg, "q", 0, 2000)])
    assert dump(store) == dump(reference)
    reference.close()


def test_user_totals_include_a_job_started_before_the_range(store, cfg):
    fill(store)
    # axial_q_2 was submitted at 1002.0; the request that started it returned at 1002.5.
    store.update_job("axial_q_2", status="done", outcome="success", bytes_billed=2**40, slot_ms=1000)
    assert summarize(store, cfg, 0, 1002.25)["users"] == []
    (user,) = summarize(store, cfg, 1002.25, 2000)["users"]
    assert user["user_email"] == "a@example.com" and user["jobs"] == 1 and user["bytes_billed"] == 2**40
    (both,) = summarize(store, cfg, 0, 2000)["users"]
    assert both["jobs"] == 1 and both["bytes_billed"] == 2**40


def test_finished_jobs_are_never_moved_back(store):
    store.insert_job(**JOB)
    store.update_job("axial_q_1", status="done", outcome="success", bytes_billed=10)
    # A late writer that last saw the job running (another instance's reconciler, say).
    store.update_job("axial_q_1", status="running", bytes_billed=None)
    assert store.get_job("axial_q_1")["status"] == "done" and store.get_job("axial_q_1")["bytes_billed"] == 10
    assert store.unfinished_jobs() == []
    with pytest.raises(ValueError, match="unknown job fields"):
        store.update_job("axial_q_1", sql_text="DROP")
    with pytest.raises(ValueError, match="unknown query_requests fields"):
        store.insert_request(query_id="q", nope=1)


def test_instances_share_history_without_collisions(backend):
    a, b = backend.open(), backend.open()
    ids: list[int] = []

    def write(s, n):
        for i in range(25):
            ids.append(s.insert_request(query_id="q", params_json="{}", sql_hash="h", source="bigquery",
                                        outcome="ok", requested_at=float(i)))
        s.insert_job(**{**JOB, "job_id": f"job_{n}"})

    threads = [threading.Thread(target=write, args=(s, n)) for n, s in enumerate((a, b, a, b))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ids) == len(set(ids)) == 100
    assert len(a.requests_between(0, 100)) == len(b.requests_between(0, 100)) == 100
    assert {j["job_id"] for j in b.jobs_between(0, 2000)} == {"job_0", "job_1", "job_2", "job_3"}
    with pytest.raises(Exception):  # job IDs are unique across instances
        b.insert_job(**{**JOB, "job_id": "job_0"})


def test_instances_starting_together_create_the_schema_once(backend):
    if backend.name != "postgres":
        pytest.skip("only postgres is shared by instances; sqlite cannot switch to WAL concurrently")
    errors = []

    def start():
        try:
            backend.open()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=start) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    backend.open().insert_job(**JOB)


def _set_version(backend, version):
    if backend.name == "sqlite":
        with sqlite3.connect(backend.path) as db:
            db.execute(f"PRAGMA user_version = {version}")
    else:
        import psycopg

        with psycopg.connect(backend.url, autocommit=True) as conn:
            conn.execute("UPDATE history_schema SET version = %s", (version,))


def test_refuses_a_newer_history_schema(backend):
    backend.open().insert_job(**JOB)
    _set_version(backend, store_mod.SCHEMA_VERSION + 1)
    with pytest.raises(RuntimeError, match="newer than this engine supports"):
        backend.open()


def test_reopening_adds_new_columns_and_keeps_history(backend, monkeypatch):
    backend.open().insert_job(**JOB)
    tables = dict(store_mod.TABLES)
    tables["bigquery_jobs"] += (("note", "text", ""),)
    monkeypatch.setattr(store_mod, "TABLES", tables)
    monkeypatch.setattr(store_mod, "COLUMNS", {t: {c[0] for c in cols} for t, cols in tables.items()})
    monkeypatch.setattr(store_mod, "SCHEMA_VERSION", store_mod.SCHEMA_VERSION + 1)
    s = backend.open()
    assert s.get_job("axial_q_1")["note"] is None
    s.insert_job(**{**JOB, "job_id": "axial_q_9", "note": "new"})
    assert s.get_job("axial_q_9")["note"] == "new"
    backend.open()  # idempotent once upgraded


def _as_version_1(monkeypatch):
    """Make this engine behave like one with history schema 1, before query_requests.user_email."""
    tables = {**store_mod.TABLES,
              "query_requests": tuple(c for c in store_mod.TABLES["query_requests"] if c[0] != "user_email")}
    monkeypatch.setattr(store_mod, "TABLES", tables)
    monkeypatch.setattr(store_mod, "COLUMNS", {t: {c[0] for c in cols} for t, cols in tables.items()})
    monkeypatch.setattr(store_mod, "SCHEMA_VERSION", 1)


def test_version_1_history_gains_user_email_and_old_engines_refuse_it(backend):
    with pytest.MonkeyPatch.context() as mp:
        _as_version_1(mp)
        old = backend.open()
        rid = old.insert_request(query_id="q", params_json="{}", sql_hash="h", source="bigquery", outcome="ok")
        assert "user_email" not in old.get_request(rid)
        old.close()
    s = backend.open()
    assert s.get_request(rid)["user_email"] is None
    new = s.insert_request(query_id="q", params_json="{}", sql_hash="h", source="app_cache", outcome="ok",
                           user_email="a@example.com")
    assert [r["user_email"] for r in s.requests_between(0, 1e12)] == ["a@example.com", None]
    with pytest.MonkeyPatch.context() as mp:
        _as_version_1(mp)
        with pytest.raises(RuntimeError, match="schema 2, newer than this engine supports \\(1\\)"):
            backend.open()
    assert backend.open().get_request(new)["user_email"] == "a@example.com"


def test_postgres_recovers_from_dropped_connections(backend):
    if backend.name != "postgres":
        pytest.skip("connection pooling is postgres-only")
    import psycopg

    s = backend.open()
    s.insert_job(**JOB)
    # What Cloud SQL does to connections of an instance that sat idle.
    with psycopg.connect(backend.url, autocommit=True) as conn:
        killed = conn.execute("SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
                              "WHERE application_name = 'axial' AND pid <> pg_backend_pid()").fetchone()[0]
    assert killed >= 1
    assert s.get_job("axial_q_1")["job_id"] == "axial_q_1"


# --- configuration and secrets ----------------------------------------------

SECRET = "s3cr#t: p@ss"


def test_storage_url_comes_from_the_environment_verbatim(repo):
    url = f"host=/cloudsql/proj:europe-west1:hist dbname=axial user=axial password='{SECRET}'"
    cfg = load_config(repo, environ={"AXIAL_STORAGE_BACKEND": "postgres", "AXIAL_STORAGE_URL": url,
                                     "AXIAL_STORAGE_POOL_SIZE": "2"})
    assert cfg.storage_backend == "postgres" and cfg.storage_url == url and cfg.storage_pool_size == 2
    assert SECRET not in repr(cfg) and SECRET not in json.dumps(cfg.display())
    with pytest.raises(ConfigError, match="storage.backend 'mysql' is not supported"):
        load_config(repo, environ={"AXIAL_STORAGE_BACKEND": "mysql"})


def test_postgres_descriptions_and_errors_never_show_the_password(monkeypatch):
    pytest.importorskip("psycopg")
    for name in ("PGHOST", "PGPORT", "PGUSER", "PGDATABASE", "PGPASSWORD"):
        monkeypatch.delenv(name, raising=False)
    conninfo, label = postgres_conninfo(f"host=/cloudsql/proj:europe-west1:hist dbname=axial user=app "
                                        f"password='{SECRET}'")
    assert label == "postgres app@/cloudsql/proj:europe-west1:hist/axial" and "connect_timeout" in conninfo
    _, label = postgres_conninfo("postgresql://app:pw@db.internal:6543/hist")
    assert label == "postgres app@db.internal:6543/hist"
    with pytest.raises(ConfigError) as bad:
        postgres_conninfo(f"host=x password {SECRET}")
    assert SECRET not in str(bad.value) and "storage.url" in str(bad.value)
    with pytest.raises(ConfigError) as down:
        store_mod.PostgresStore(f"host=127.0.0.1 port=1 user=app password='{SECRET}' connect_timeout=2")
    assert SECRET not in str(down.value) and "cannot open the history database" in str(down.value)


def test_missing_driver_says_how_to_install_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "psycopg", None)
    with pytest.raises(ConfigError, match=r"axial\[postgres\]"):
        store_mod.PostgresStore("host=localhost")
