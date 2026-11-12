"""Execution history: one row per dataset request, one row per BigQuery job.

`HistoryStore` is the interface the rest of the engine uses. `SQLiteStore` is the local
implementation; a hosted deployment can add another backend behind the same interface.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .config import Config

SCHEMA_VERSION = 1


class HistoryStore(Protocol):
    def insert_job(self, **row: Any) -> None: ...
    def update_job(self, job_id: str, **fields: Any) -> None: ...
    def get_job(self, job_id: str) -> dict | None: ...
    def unfinished_jobs(self, older_than_s: float = 0) -> list[dict]: ...
    def insert_request(self, **row: Any) -> int: ...
    def get_request(self, request_id: int) -> dict | None: ...
    def latest_request(self, query_id: str, params_json: str | None = None) -> dict | None: ...
    def requests_between(self, start: float, end: float, query_id: str | None = None) -> list[dict]: ...
    def jobs_between(self, start: float, end: float, query_id: str | None = None) -> list[dict]: ...

SCHEMA = """
CREATE TABLE IF NOT EXISTS query_requests (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  requested_at  REAL NOT NULL,          -- unix seconds
  query_id      TEXT NOT NULL,
  dashboard_id  TEXT,
  params_json   TEXT NOT NULL,
  sql_hash      TEXT NOT NULL,
  source        TEXT NOT NULL,          -- bigquery | app_cache | shared | error
  job_id        TEXT,                   -- job whose result served this request
  bypass_cache  INTEGER NOT NULL DEFAULT 0,
  outcome       TEXT NOT NULL,          -- ok | error | timeout
  error         TEXT,
  app_ms        REAL,                   -- time the app took to serve the dataset
  row_count     INTEGER,
  truncated     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS query_requests_q ON query_requests (query_id, requested_at);

CREATE TABLE IF NOT EXISTS bigquery_jobs (
  job_id          TEXT PRIMARY KEY,
  project         TEXT,
  location        TEXT,
  query_id        TEXT NOT NULL,
  sql_hash        TEXT NOT NULL,
  sql_text        TEXT NOT NULL,
  params_json     TEXT NOT NULL,
  app_revision    TEXT,                 -- git commit of the project, '+dirty' when queries/ is modified
  engine_version  TEXT,
  use_query_cache INTEGER NOT NULL DEFAULT 1,
  recorded_at     REAL NOT NULL,        -- persisted before submission
  status          TEXT NOT NULL,        -- pending | running | done | not_found
  outcome         TEXT,                 -- success | error
  error           TEXT,
  cache_hit       INTEGER,              -- BigQuery result cache
  bytes_processed INTEGER,
  bytes_billed    INTEGER,
  slot_ms         INTEGER,
  created_ms      INTEGER,              -- BigQuery job timestamps, epoch ms
  started_ms      INTEGER,
  ended_ms        INTEGER,
  queue_ms        INTEGER,
  exec_ms         INTEGER,
  reconciled_at   REAL
);
CREATE INDEX IF NOT EXISTS bigquery_jobs_q ON bigquery_jobs (query_id, recorded_at);
CREATE INDEX IF NOT EXISTS bigquery_jobs_status ON bigquery_jobs (status);
"""

JOB_STAT_FIELDS = (
    "status", "outcome", "error", "cache_hit", "bytes_processed", "bytes_billed", "slot_ms",
    "created_ms", "started_ms", "ended_ms", "queue_ms", "exec_ms", "reconciled_at",
)


class SQLiteStore:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        with self._lock:
            found = self._db.execute("PRAGMA user_version").fetchone()[0]
            if found > SCHEMA_VERSION:
                raise RuntimeError(f"history database {path} has schema {found}, newer than this engine "
                                   f"supports ({SCHEMA_VERSION}); upgrade bqdash")
            self._db.executescript(SCHEMA)
            self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _exec(self, sql: str, args: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def _all(self, sql: str, args: tuple | dict = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    # --- jobs ---------------------------------------------------------------

    def insert_job(self, **row: Any) -> None:
        row.setdefault("recorded_at", time.time())
        row.setdefault("status", "pending")
        cols = ",".join(row)
        marks = ",".join(f":{k}" for k in row)
        self._exec(f"INSERT INTO bigquery_jobs ({cols}) VALUES ({marks})", row)

    def update_job(self, job_id: str, **fields: Any) -> None:
        bad = set(fields) - set(JOB_STAT_FIELDS)
        if bad:
            raise ValueError(f"unknown job fields {bad}")
        if not fields:
            return
        sets = ",".join(f"{k}=:{k}" for k in fields)
        self._exec(f"UPDATE bigquery_jobs SET {sets} WHERE job_id=:job_id", {**fields, "job_id": job_id})

    def get_job(self, job_id: str) -> dict | None:
        rows = self._all("SELECT * FROM bigquery_jobs WHERE job_id=?", (job_id,))
        return rows[0] if rows else None

    def unfinished_jobs(self, older_than_s: float = 0) -> list[dict]:
        return self._all(
            "SELECT * FROM bigquery_jobs WHERE status IN ('pending','running') AND recorded_at <= ?",
            (time.time() - older_than_s,),
        )

    # --- requests -----------------------------------------------------------

    def insert_request(self, **row: Any) -> int:
        row.setdefault("requested_at", time.time())
        for k in ("bypass_cache", "truncated"):
            if k in row:
                row[k] = int(bool(row[k]))
        cols = ",".join(row)
        marks = ",".join(f":{k}" for k in row)
        return int(self._exec(f"INSERT INTO query_requests ({cols}) VALUES ({marks})", row).lastrowid)

    def get_request(self, request_id: int) -> dict | None:
        rows = self._all("SELECT * FROM query_requests WHERE id=?", (request_id,))
        return rows[0] if rows else None

    def latest_request(self, query_id: str, params_json: str | None = None) -> dict | None:
        """Most recent request for a query, preferring one with exactly these params."""
        if params_json is not None:
            rows = self._all(
                "SELECT * FROM query_requests WHERE query_id=? AND params_json=? ORDER BY id DESC LIMIT 1",
                (query_id, params_json),
            )
            if rows:
                return rows[0]
        rows = self._all("SELECT * FROM query_requests WHERE query_id=? ORDER BY id DESC LIMIT 1", (query_id,))
        return rows[0] if rows else None

    def requests_between(self, start: float, end: float, query_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM query_requests WHERE requested_at >= ? AND requested_at < ?"
        args: list = [start, end]
        if query_id:
            sql += " AND query_id = ?"
            args.append(query_id)
        return self._all(sql + " ORDER BY id DESC", tuple(args))

    def jobs_between(self, start: float, end: float, query_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM bigquery_jobs WHERE recorded_at >= ? AND recorded_at < ?"
        args: list = [start, end]
        if query_id:
            sql += " AND query_id = ?"
            args.append(query_id)
        return self._all(sql + " ORDER BY recorded_at DESC", tuple(args))


def open_store(cfg: "Config") -> HistoryStore:
    if cfg.storage_backend == "sqlite":
        return SQLiteStore(cfg.history_path)
    raise ValueError(f"unsupported storage backend {cfg.storage_backend!r}")
