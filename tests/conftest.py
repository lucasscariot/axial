from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import pytest

from axial.bq import JobFailed, JobState, SubmissionUncertain
from axial.config import load_config

PROJECT = Path(__file__).resolve().parent / "project"


class FakeWarehouse:
    """In-memory stand-in for BigQuery that records every submitted job."""

    project = "test-project"
    location = "EU"

    def __init__(self, rows=None, delay=0.0):
        self.rows = rows if rows is not None else [{"day": "2026-11-01", "bookings": 3, "gross_value": 10.0}]
        self.delay = delay
        self.submitted: list[dict] = []
        self.jobs: dict[str, JobState] = {}
        self.fail_submit_uncertain = 0  # number of uncertain failures before success
        self.uncertain_but_created = False  # uncertain failure where the job did get created
        self.timeout = False
        self.fail_job = None
        self.bytes_billed = 10 * 2**20
        self._lock = threading.Lock()

    def submit(self, *, job_id, sql, params, labels, use_query_cache, maximum_bytes_billed):
        with self._lock:
            if self.fail_submit_uncertain:
                self.fail_submit_uncertain -= 1
                if self.uncertain_but_created:
                    self._create(job_id, sql, params, labels, use_query_cache, maximum_bytes_billed)
                raise SubmissionUncertain("read timeout")
            self._create(job_id, sql, params, labels, use_query_cache, maximum_bytes_billed)

    def _create(self, job_id, sql, params, labels, use_query_cache, maximum_bytes_billed):
        if job_id in self.jobs:
            return
        self.submitted.append(dict(job_id=job_id, sql=sql, params=params, labels=labels,
                                   use_query_cache=use_query_cache, maximum_bytes_billed=maximum_bytes_billed))
        now = int(time.time() * 1000)
        self.jobs[job_id] = JobState(job_id, done=False, created_ms=now)

    def _finish(self, job_id):
        st = self.jobs[job_id]
        if not st.done:
            st.done = True
            st.started_ms = st.created_ms + 5
            st.ended_ms = st.started_ms + 120
            st.outcome = "error" if self.fail_job else "success"
            st.error = self.fail_job
            st.cache_hit = False
            st.bytes_processed = self.bytes_billed
            st.bytes_billed = self.bytes_billed
            st.slot_ms = 4000
        return st

    def get_job(self, job_id):
        st = self.jobs.get(job_id)
        if st is None:
            return None
        if not self.timeout:
            self._finish(job_id)
        return st

    def list_datasets(self, project=None):
        return [{"id": "test-project.analytics"}]

    def list_tables(self, dataset):
        return [{"id": f"{dataset}.fct_bookings", "type": "TABLE",
                 "partitioning": {"type": "DAY", "field": "created_at"}, "clustering": ["region"]}]

    def get_table(self, table):
        return {"id": table, "type": "TABLE", "description": "One row per booking", "num_rows": 10, "num_bytes": 2048,
                "partitioning": {"type": "DAY", "field": "created_at"}, "require_partition_filter": False,
                "clustering": ["region"], "view_query": None,
                "columns": [{"name": "created_at", "type": "TIMESTAMP", "mode": "NULLABLE", "description": None},
                            {"name": "region", "type": "STRING", "mode": "NULLABLE", "description": "Region"}]}

    def dry_run(self, sql, params):
        from axial.bq import DryRun

        return DryRun(self.bytes_billed, "SELECT", ["test-project.analytics.fct_bookings"])

    def wait_and_fetch(self, job_id, timeout, max_rows):
        if self.delay:
            time.sleep(self.delay)
        if self.timeout:
            raise TimeoutError("still running")
        st = self._finish(job_id)
        if st.outcome == "error":
            raise JobFailed(st)
        return st, self.rows[:max_rows], len(self.rows)


@pytest.fixture
def repo(tmp_path):
    """A copy of the test project so tests can edit queries freely."""
    shutil.copytree(PROJECT, tmp_path, dirs_exist_ok=True)
    return tmp_path


@pytest.fixture
def cfg(repo):
    return load_config(repo, environ={})
