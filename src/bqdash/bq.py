"""Thin BigQuery adapter. Everything else talks to the `Warehouse` protocol."""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import threading
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class JobState:
    job_id: str
    done: bool
    outcome: str | None = None  # success | error, once done
    error: str | None = None
    cache_hit: bool | None = None
    bytes_processed: int | None = None
    bytes_billed: int | None = None
    slot_ms: int | None = None
    created_ms: int | None = None
    started_ms: int | None = None
    ended_ms: int | None = None
    statement_type: str | None = None

    def stats(self) -> dict:
        queue = exec_ = None
        if self.created_ms is not None and self.started_ms is not None:
            queue = max(0, self.started_ms - self.created_ms)
        if self.started_ms is not None and self.ended_ms is not None:
            exec_ = max(0, self.ended_ms - self.started_ms)
        return {
            "status": "done" if self.done else "running",
            "outcome": self.outcome,
            "error": self.error,
            "cache_hit": None if self.cache_hit is None else int(self.cache_hit),
            "bytes_processed": self.bytes_processed,
            "bytes_billed": self.bytes_billed,
            "slot_ms": self.slot_ms,
            "created_ms": self.created_ms,
            "started_ms": self.started_ms,
            "ended_ms": self.ended_ms,
            "queue_ms": queue,
            "exec_ms": exec_,
        }


@dataclass
class DryRun:
    bytes_processed: int
    statement_type: str | None
    referenced_tables: list[str] = field(default_factory=list)


class SubmissionUncertain(Exception):
    """The insert call failed in a way that may or may not have created the job."""


class JobFailed(Exception):
    def __init__(self, state: JobState):
        super().__init__(state.error or "job failed")
        self.state = state


class Warehouse(Protocol):
    project: str | None
    location: str | None

    def submit(self, *, job_id: str, sql: str, params: list[tuple[str, str, Any]], labels: dict[str, str],
               use_query_cache: bool, maximum_bytes_billed: int | None) -> None: ...

    def get_job(self, job_id: str) -> JobState | None: ...

    def wait_and_fetch(self, job_id: str, timeout: float, max_rows: int) -> tuple[JobState, list[dict], int]:
        """Wait for the job and return (state, first max_rows rows, total rows).

        Raises TimeoutError if it is still running after `timeout`, JobFailed on error.
        """
        ...

    def dry_run(self, sql: str, params: list[tuple[str, str, Any]]) -> DryRun: ...


def to_json_value(v: Any) -> Any:
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, bytes):
        return base64.b64encode(v).decode()
    if isinstance(v, dict):
        return {k: to_json_value(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [to_json_value(x) for x in v]
    return v


def _ms(t: dt.datetime | None) -> int | None:
    return None if t is None else int(t.timestamp() * 1000)


class BigQueryWarehouse:
    """Uses Application Default Credentials. The principal should only be able to run
    jobs in the billing project and read the approved analytics datasets or views."""

    def __init__(self, project: str | None, location: str | None):
        from google.cloud import bigquery  # imported lazily: fixture mode never needs it

        self._bq = bigquery
        self.client = bigquery.Client(project=project, location=location)
        self.project = self.client.project
        self.location = location
        self._jobs: dict[str, Any] = {}
        self._lock = threading.Lock()

    def _params(self, params: list[tuple[str, str, Any]]) -> list:
        return [self._bq.ScalarQueryParameter(n, t, v) for n, t, v in params]

    def submit(self, *, job_id, sql, params, labels, use_query_cache, maximum_bytes_billed) -> None:
        from google.api_core import exceptions as gexc
        import requests

        cfg = self._bq.QueryJobConfig(
            query_parameters=self._params(params),
            use_query_cache=use_query_cache,
            maximum_bytes_billed=maximum_bytes_billed,
            labels=labels,
            use_legacy_sql=False,
        )
        try:
            job = self.client.query(
                sql, job_config=cfg, job_id=job_id, location=self.location,
                timeout=30, job_retry=None,
            )
        except gexc.Conflict:
            return  # an earlier attempt created it; caller looks it up by ID
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                gexc.RetryError, gexc.ServerError, gexc.DeadlineExceeded, TimeoutError) as e:
            raise SubmissionUncertain(str(e)) from e
        with self._lock:
            self._jobs[job_id] = job

    def _job(self, job_id: str):
        from google.api_core import exceptions as gexc

        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            try:
                job = self.client.get_job(job_id, project=self.project, location=self.location)
            except gexc.NotFound:
                return None
            with self._lock:
                self._jobs[job_id] = job
        return job

    def _state(self, job) -> JobState:
        done = job.state == "DONE"
        err = job.error_result
        return JobState(
            job_id=job.job_id,
            done=done,
            outcome=("error" if err else "success") if done else None,
            error=(err or {}).get("message") if err else None,
            cache_hit=job.cache_hit,
            bytes_processed=job.total_bytes_processed,
            bytes_billed=job.total_bytes_billed,
            slot_ms=job.slot_millis,
            created_ms=_ms(job.created),
            started_ms=_ms(job.started),
            ended_ms=_ms(job.ended),
            statement_type=job.statement_type,
        )

    def get_job(self, job_id: str) -> JobState | None:
        job = self._job(job_id)
        if job is None:
            return None
        if job.state != "DONE":
            job.reload()
        return self._state(job)

    def wait_and_fetch(self, job_id, timeout, max_rows):
        import concurrent.futures
        from google.api_core import exceptions as gexc

        job = self._job(job_id)
        if job is None:
            raise JobFailed(JobState(job_id, True, "error", "job not found"))
        try:
            it = job.result(timeout=timeout, max_results=max_rows, job_retry=None)
        except concurrent.futures.TimeoutError as e:
            raise TimeoutError(str(e)) from e
        except gexc.GoogleAPICallError as e:
            job.reload()
            st = self._state(job)
            if st.error is None:
                st.error = str(e)
            st.outcome, st.done = "error", True
            raise JobFailed(st) from e
        rows = []
        for i, row in enumerate(it):
            if i >= max_rows:
                break
            rows.append({k: to_json_value(v) for k, v in row.items()})
        job.reload()
        with self._lock:
            self._jobs.pop(job_id, None)
        return self._state(job), rows, int(it.total_rows or len(rows))

    def dry_run(self, sql, params) -> DryRun:
        cfg = self._bq.QueryJobConfig(
            query_parameters=self._params(params), dry_run=True, use_query_cache=False, use_legacy_sql=False,
        )
        job = self.client.query(sql, job_config=cfg, location=self.location)
        tables = [f"{t.project}.{t.dataset_id}.{t.table_id}" for t in (job.referenced_tables or [])]
        return DryRun(int(job.total_bytes_processed or 0), job.statement_type, tables)
