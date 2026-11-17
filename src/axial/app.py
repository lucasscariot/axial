"""FastAPI app: serves the React build and the query API on 127.0.0.1."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .checks import run_checks
from .config import Config, engine_version
from .executor import Executor, QueryError, QueryTimeout, git_revision
from .fixtures import fixture_rows
from .registry import ParamError, Registry, normalize_params, resolve_params
from .store import HistoryStore, open_store
from .usage import job_detail, query_runs, summarize

WEB_DIST = Path(__file__).parent / "web_dist"
# Set by Identity-Aware Proxy on every request it lets through, as
# `accounts.google.com:user@example.com`. Read only when server.trust_iap_user is on.
IAP_USER_HEADER = "x-goog-authenticated-user-email"
EMAIL = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
MAX_EMAIL = 254


class DatasetRequest(BaseModel):
    dashboard_id: str
    query_id: str
    filters: dict[str, Any] = Field(default_factory=dict)
    bypass_cache: bool = False


def iap_user(value: str | None) -> str | None:
    """The lowercased email in an IAP identity header, or None for anything else."""
    if not value or len(value) > 2 * MAX_EMAIL:
        return None
    email = value.rpartition(":")[2].strip().lower()  # drop the accounts.google.com: namespace
    return email if len(email) <= MAX_EMAIL and EMAIL.match(email) else None


def _day_bounds(start: str | None, end: str | None) -> tuple[float, float]:
    """Local-date range [start, end) as unix seconds; default last 7 days."""
    today = dt.date.today()
    e = dt.date.fromisoformat(end) if end else today + dt.timedelta(days=1)
    s = dt.date.fromisoformat(start) if start else e - dt.timedelta(days=7)
    to_ts = lambda d: dt.datetime.combine(d, dt.time()).timestamp()  # noqa: E731
    return to_ts(s), to_ts(e)


def create_app(cfg: Config, *, mode: str = "live", warehouse=None, store: HistoryStore | None = None) -> FastAPI:
    assert mode in ("live", "fixture")
    registry = Registry(cfg)
    state: dict[str, Any] = {"executor": None}
    owned_store = None

    if mode == "live":
        if store is None:
            store = owned_store = open_store(cfg)
        if warehouse is None:
            from .warehouses import make_warehouse

            warehouse = make_warehouse(cfg)
        state["executor"] = Executor(cfg, store, warehouse, app_revision=git_revision(cfg.root))

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = None
        if state["executor"] is not None:
            task = asyncio.create_task(state["executor"].reconcile_forever())
        yield
        if task:
            task.cancel()
        if owned_store is not None:
            owned_store.close()

    app = FastAPI(title="axial", lifespan=lifespan)
    app.state.registry = registry
    app.state.executor = state["executor"]

    def ex() -> Executor:
        if state["executor"] is None:
            raise HTTPException(409, "fixture mode: no execution history")
        return state["executor"]

    def dashboard(dashboard_id: str):
        registry.reload_if_changed()
        d = registry.dashboards.get(dashboard_id)
        if d is None:
            raise HTTPException(404, f"no dashboard {dashboard_id!r}")
        return d

    # --- metadata ---------------------------------------------------------

    @app.get("/api/meta")
    def meta():
        return {"mode": mode, "display": cfg.display(), "engine_version": engine_version(),
                "project": cfg.root.name}

    @app.get("/api/brand/logo", include_in_schema=False)
    def logo():
        if cfg.logo is None or not cfg.logo.exists():
            raise HTTPException(404)
        return FileResponse(cfg.logo)

    @app.get("/api/dashboards")
    def list_dashboards():
        registry.reload_if_changed()
        return [{"id": d.id, "title": d.title, "description": d.description, "tiles": len(d.tiles)}
                for d in registry.dashboards.values()]

    @app.get("/api/dashboards/{dashboard_id}")
    def get_dashboard(dashboard_id: str):
        d = dashboard(dashboard_id)
        dr = None
        if d.date_range:
            s, e = d.date_range.default()
            dr = {"default_start": s.isoformat(), "default_end": e.isoformat(), "max_days": d.date_range.max_days,
                  "start_param": d.date_range.start_param, "end_param": d.date_range.end_param}
        issues = [i for i in run_checks(registry, d.id) if i.level == "error"]
        return {
            "id": d.id, "title": d.title, "description": d.description, "date_range": dr,
            "tiles": [{**t.raw, "type": t.type, "query": t.query, "span": t.span} for t in d.tiles],
            "queries": {q: registry.queries[q].meta() for q in d.query_ids if q in registry.queries},
            "errors": [str(i) for i in issues],
        }

    # --- datasets ---------------------------------------------------------

    @app.post("/api/datasets")
    async def dataset(req: DatasetRequest, request: Request):
        d = dashboard(req.dashboard_id)
        if req.query_id not in d.query_ids:
            raise HTTPException(400, f"query {req.query_id!r} is not on dashboard {d.id!r}")
        q = registry.queries.get(req.query_id)
        if q is None:
            raise HTTPException(404, f"no query {req.query_id!r}")
        allowed = {d.date_range.start_param, d.date_range.end_param} if d.date_range else set()
        extra = set(req.filters) - allowed
        if extra:
            raise HTTPException(400, f"unknown filters: {', '.join(sorted(extra))}")
        try:
            values = resolve_params(q, {k: v for k, v in req.filters.items() if k in q.params}, d.date_range)
        except ParamError as e:
            raise HTTPException(400, str(e)) from None

        if mode == "fixture":
            t0 = time.perf_counter()
            kw = {"start_param": d.date_range.start_param, "end_param": d.date_range.end_param} if d.date_range else {}
            rows = fixture_rows(cfg, q, values, **kw)
            return {"query_id": q.id, "rows": rows[: cfg.row_cap], "truncated": len(rows) > cfg.row_cap,
                    "total_rows": len(rows), "source": "fixture", "request_id": None, "job_id": None,
                    "sql_hash": q.sql_hash, "app_ms": (time.perf_counter() - t0) * 1000, "params": json.loads(normalize_params(values))}

        try:
            user = iap_user(request.headers.get(IAP_USER_HEADER)) if cfg.trust_iap_user else None
            served = await ex().run(q, values, dashboard_id=d.id, bypass_cache=req.bypass_cache, user_email=user)
        except QueryTimeout as e:
            raise HTTPException(504, {"message": str(e), "job_id": e.job_id, "request_id": e.request_id}) from None
        except QueryError as e:
            raise HTTPException(502, {"message": str(e), "job_id": e.job_id, "request_id": e.request_id}) from None
        r = served.result
        return {"query_id": q.id, "rows": r.rows, "truncated": r.truncated, "total_rows": r.total_rows,
                "source": served.source, "request_id": served.request_id, "job_id": r.job_id,
                "sql_hash": r.sql_hash, "app_ms": served.app_ms, "bq_cache_hit": r.cache_hit,
                "params": json.loads(r.params_json)}

    # --- inspection -------------------------------------------------------

    def request_detail(r: dict) -> dict:
        store_ = ex().store
        job = store_.get_job(r["job_id"]) if r.get("job_id") else None
        q = registry.queries.get(r["query_id"])
        return {
            "request": {**r, "params": json.loads(r["params_json"])},
            "job": job_detail(cfg, job) if job else None,
            "current_sql_hash": q.sql_hash if q else None,
            "sql_changed_since": bool(q and q.sql_hash != r["sql_hash"]),
        }

    @app.get("/api/inspect")
    def inspect(dashboard_id: str, query_id: str, filters: str = "{}"):
        d = dashboard(dashboard_id)
        q = registry.queries.get(query_id)
        if q is None:
            raise HTTPException(404, f"no query {query_id!r}")
        params_json = None
        try:
            raw = json.loads(filters)
            params_json = normalize_params(resolve_params(q, {k: v for k, v in raw.items() if k in q.params}, d.date_range))
        except (ValueError, ParamError):
            pass
        r = ex().store.latest_request(query_id, params_json)
        if r is None:
            raise HTTPException(404, "this query has not run yet")
        out = request_detail(r)
        out["matches_selected_filters"] = r["params_json"] == params_json
        return out

    @app.get("/api/requests/{request_id}")
    def get_request(request_id: int):
        r = ex().store.get_request(request_id)
        if r is None:
            raise HTTPException(404, "no such request")
        return request_detail(r)

    @app.get("/api/usage")
    def usage(start: str | None = None, end: str | None = None):
        s, e = _day_bounds(start, end)
        return summarize(ex().store, cfg, s, e)

    @app.get("/api/usage/{query_id}")
    def usage_query(query_id: str, start: str | None = None, end: str | None = None):
        s, e = _day_bounds(start, end)
        out = query_runs(ex().store, cfg, query_id, s, e)
        q = registry.queries.get(query_id)
        out["title"] = q.title if q else query_id
        out["current_sql_hash"] = q.sql_hash if q else None
        return out

    # --- SPA --------------------------------------------------------------

    if (WEB_DIST / "assets").exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        index = WEB_DIST / "index.html"
        if index.exists():
            return FileResponse(index)
        return HTMLResponse("<p>Frontend not built. In the engine repo, run <code>make web</code>.</p>", status_code=503)

    return app
