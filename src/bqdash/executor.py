"""Runs saved queries: app cache, in-flight sharing, concurrency bound, job tracing."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bq import JobFailed, SubmissionUncertain, Warehouse
from .config import Config
from .registry import Query, normalize_params
from .store import Store

log = logging.getLogger("bqdash")

SUBMIT_ATTEMPTS = 3
NOT_FOUND_GRACE_S = 600


class QueryTimeout(Exception):
    def __init__(self, job_id: str, request_id: int | None = None):
        super().__init__(f"BigQuery job {job_id} is still running; its stats will be reconciled later")
        self.job_id = job_id
        self.request_id = request_id


class QueryError(Exception):
    def __init__(self, message: str, job_id: str | None = None, request_id: int | None = None):
        super().__init__(message)
        self.job_id = job_id
        self.request_id = request_id


@dataclass
class Result:
    query_id: str
    sql_hash: str
    params_json: str
    job_id: str | None
    rows: list[dict]
    total_rows: int
    truncated: bool
    cache_hit: bool | None
    completed_at: float


@dataclass
class Served:
    result: Result
    source: str  # bigquery | app_cache | shared | fixture
    request_id: int | None
    app_ms: float


def git_revision(root: Path) -> str | None:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=root, capture_output=True,
                             text=True, timeout=5).stdout.strip()
        if not sha:
            return None
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "queries"], cwd=root,
                               capture_output=True, text=True, timeout=5).stdout.strip()
        return sha + ("+dirty" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return None


def bq_params(query: Query, values: dict[str, Any]) -> list[tuple[str, str, Any]]:
    return [(name, query.params[name].type, v) for name, v in values.items()]


def _label(s: str) -> str:
    out = "".join(c if c.isalnum() or c in "-_" else "_" for c in s.lower())
    return out[:63]


class Executor:
    def __init__(self, cfg: Config, store: Store, warehouse: Warehouse, dbt_revision: str | None = None,
                 app_revision: str | None = None, clock=time.time):
        self.cfg = cfg
        self.store = store
        self.wh = warehouse
        self.dbt_revision = dbt_revision
        self.app_revision = app_revision
        self.clock = clock
        self._cache: dict[tuple, Result] = {}
        self._inflight: dict[tuple, asyncio.Task] = {}
        self._sem = asyncio.Semaphore(cfg.max_concurrent_jobs)

    # --- public -----------------------------------------------------------

    async def run(self, query: Query, values: dict[str, Any], *, dashboard_id: str | None = None,
                  bypass_cache: bool = False) -> Served:
        t0 = time.perf_counter()
        params_json = normalize_params(values)
        key = (query.id, query.sql_hash, params_json)

        def record(source: str, outcome: str, result: Result | None = None, job_id: str | None = None,
                   error: str | None = None) -> tuple[int, float]:
            app_ms = (time.perf_counter() - t0) * 1000
            rid = self.store.insert_request(
                query_id=query.id, dashboard_id=dashboard_id, params_json=params_json, sql_hash=query.sql_hash,
                source=source, job_id=result.job_id if result else job_id, bypass_cache=bypass_cache,
                outcome=outcome, error=error, app_ms=app_ms,
                row_count=len(result.rows) if result else None, truncated=result.truncated if result else False,
            )
            return rid, app_ms

        if not bypass_cache:
            cached = self._cache.get(key)
            if cached and self.clock() - cached.completed_at < query.cache_ttl_seconds:
                rid, ms = record("app_cache", "ok", cached)
                return Served(cached, "app_cache", rid, ms)
            task = self._inflight.get(key)
            source = "shared"
            if task is None:
                task = asyncio.ensure_future(self._execute(query, values, params_json, dashboard_id, True))
                self._inflight[key] = task
                task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))
                task.add_done_callback(_consume_exception)
                source = "bigquery"
        else:
            task = asyncio.ensure_future(self._execute(query, values, params_json, dashboard_id, False))
            task.add_done_callback(_consume_exception)
            source = "bigquery"

        try:
            # shield: a disconnecting browser must not cancel a job others are waiting for
            result = await asyncio.shield(task)
        except QueryTimeout as e:
            rid, _ = record(source, "timeout", job_id=e.job_id, error=str(e))
            e.request_id = rid
            raise
        except QueryError as e:
            rid, _ = record(source, "error", job_id=e.job_id, error=str(e))
            e.request_id = rid
            raise
        if not bypass_cache:
            self._cache[key] = result
        rid, ms = record(source, "ok", result)
        return Served(result, source, rid, ms)

    def clear_cache(self) -> None:
        self._cache.clear()

    # --- execution --------------------------------------------------------

    async def _execute(self, query: Query, values: dict[str, Any], params_json: str, dashboard_id: str | None,
                       use_query_cache: bool) -> Result:
        async with self._sem:
            job_id = f"dash_{_label(query.id)}_{uuid.uuid4().hex}"
            # Persist before submission so the job is traceable even if we never hear back.
            self.store.insert_job(
                job_id=job_id, project=self.wh.project, location=self.wh.location, query_id=query.id,
                sql_hash=query.sql_hash, sql_text=query.sql, params_json=params_json, dbt_models=query.dbt_models,
                dbt_revision=self.dbt_revision, app_revision=self.app_revision, use_query_cache=int(use_query_cache),
            )
            labels = {"app": "bqdash", "query_id": _label(query.id), "sql_hash": query.sql_hash[:12]}
            if dashboard_id:
                labels["dashboard"] = _label(dashboard_id)
            await asyncio.to_thread(self._submit, job_id, query, values, labels, use_query_cache)
            self.store.update_job(job_id, status="running")
            try:
                state, rows, total = await asyncio.to_thread(
                    self.wh.wait_and_fetch, job_id, self.cfg.wait_timeout_seconds, self.cfg.row_cap)
            except TimeoutError:
                raise QueryTimeout(job_id) from None
            except JobFailed as e:
                self.store.update_job(job_id, **e.state.stats(), reconciled_at=self.clock())
                raise QueryError(e.state.error or "BigQuery job failed", job_id) from None
            except Exception as e:  # transport failure while waiting: leave for reconciliation
                log.warning("waiting for %s failed: %s", job_id, e)
                raise QueryError(f"lost contact with job {job_id}: {e}", job_id) from None
            self.store.update_job(job_id, **state.stats(), reconciled_at=self.clock())
        return Result(
            query_id=query.id, sql_hash=query.sql_hash, params_json=params_json, job_id=job_id, rows=rows,
            total_rows=total, truncated=total > len(rows), cache_hit=state.cache_hit, completed_at=self.clock(),
        )

    def _submit(self, job_id: str, query: Query, values: dict[str, Any], labels: dict[str, str],
                use_query_cache: bool) -> None:
        last: Exception | None = None
        for attempt in range(SUBMIT_ATTEMPTS):
            try:
                self.wh.submit(job_id=job_id, sql=query.sql, params=bq_params(query, values), labels=labels,
                               use_query_cache=use_query_cache,
                               maximum_bytes_billed=self.cfg.maximum_bytes_billed)
                return
            except SubmissionUncertain as e:
                last = e
                # The insert may have landed. Look the same job ID up before retrying.
                try:
                    if self.wh.get_job(job_id) is not None:
                        return
                except Exception as lookup_err:  # noqa: BLE001
                    log.warning("lookup of %s after uncertain submit failed: %s", job_id, lookup_err)
                time.sleep(min(2 ** attempt, 5))
            except Exception as e:
                self.store.update_job(job_id, status="done", outcome="error", error=str(e),
                                      reconciled_at=self.clock())
                raise QueryError(f"submission failed: {e}", job_id) from None
        # Leave status pending: the reconciler will find the job if it exists, or mark it not_found.
        raise QueryError(f"could not confirm submission of {job_id}: {last}", job_id)

    # --- reconciliation ---------------------------------------------------

    def reconcile_once(self) -> int:
        """Fetch final stats for jobs the app stopped waiting for. Returns jobs updated."""
        updated = 0
        for row in self.store.unfinished_jobs(older_than_s=5):
            try:
                state = self.wh.get_job(row["job_id"])
            except Exception as e:  # noqa: BLE001
                log.warning("reconcile %s: %s", row["job_id"], e)
                continue
            if state is None:
                if self.clock() - row["recorded_at"] > NOT_FOUND_GRACE_S:
                    self.store.update_job(row["job_id"], status="not_found", reconciled_at=self.clock())
                    updated += 1
                continue
            if state.done or row["status"] == "pending":
                self.store.update_job(row["job_id"], **state.stats(), reconciled_at=self.clock())
                updated += 1
        return updated

    async def reconcile_forever(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.reconcile_once)
            except Exception as e:  # noqa: BLE001
                log.warning("reconcile loop: %s", e)
            await asyncio.sleep(self.cfg.reconcile_interval_seconds)


def _consume_exception(task: asyncio.Task) -> None:
    # Waiters may all have gone away; mark the exception retrieved to avoid noisy warnings.
    if not task.cancelled():
        task.exception()
