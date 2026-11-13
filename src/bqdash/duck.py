"""DuckDB adapter: runs saved queries against a local database file, read-only.

Saved queries keep BigQuery-style `@params`; they are rewritten to DuckDB's `$params` outside
strings and comments. Each job opens its own read-only connection, so a database rebuilt by
replacing the file is picked up by the next query.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .bq import DryRun, JobFailed, JobState, to_json_value
from .sqlcheck import _TOKEN

_PARAM = re.compile(r"@([A-Za-z_][A-Za-z0-9_]*)")


def to_duckdb_sql(sql: str) -> str:
    """Rewrite `@name` parameters to `$name`, leaving strings, quoted identifiers and comments alone."""
    out: list[str] = []
    plain: list[str] = []

    def flush() -> None:
        if plain:
            out.append(_PARAM.sub(r"$\1", "".join(plain)))
            plain.clear()

    for m in _TOKEN.finditer(sql):
        if m.lastgroup == "other":
            plain.append(m.group())
        else:
            flush()
            out.append(m.group())
    flush()
    return "".join(out)


def _json_value(v: Any) -> Any:
    if isinstance(v, dt.timedelta):
        return v.total_seconds()
    if isinstance(v, uuid.UUID):
        return str(v)
    return to_json_value(v)


def _now_ms() -> int:
    return int(time.time() * 1000)


class _Job:
    def __init__(self, job_id: str):
        self.state = JobState(job_id, done=False, created_ms=_now_ms(), cache_hit=False, statement_type="SELECT")
        self.rows: list[dict] = []
        self.total = 0
        self.thread: threading.Thread | None = None


class DuckDBWarehouse:
    """Read-only access to one DuckDB file. External file and network access is off unless enabled,
    so saved queries can only read the database itself."""

    def __init__(self, path: Path, *, allow_external_access: bool = False, threads: int | None = None,
                 memory_limit: str | None = None):
        import duckdb  # imported lazily: BigQuery projects never need it

        self._duckdb = duckdb
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"DuckDB database not found: {self.path}")
        self.config: dict[str, Any] = {"enable_external_access": bool(allow_external_access)}
        if threads:
            self.config["threads"] = int(threads)
        if memory_limit:
            self.config["memory_limit"] = str(memory_limit)
        self.project = self.path.stem  # the catalog name DuckDB gives the attached file
        self.location = str(self.path)
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.Lock()
        self._connect_lock = threading.Lock()

    def connect(self):
        # DuckDB shares one database instance between connections to the same file in a process,
        # so the configuration lock set by the first connection already applies to later ones.
        with self._connect_lock:
            con = self._duckdb.connect(str(self.path), read_only=True, config=dict(self.config))
            if not con.execute("SELECT current_setting('lock_configuration')").fetchone()[0]:
                con.execute("SET lock_configuration = true")
        return con

    @staticmethod
    def _bind(sql: str, params: list[tuple[str, str, Any]]) -> tuple[str, dict[str, Any]]:
        from .sqlcheck import referenced_params

        used = referenced_params(sql)
        return to_duckdb_sql(sql), {n: v for n, _t, v in params if n in used}

    # --- jobs -------------------------------------------------------------

    def submit(self, *, job_id, sql, params, labels, use_query_cache, maximum_bytes_billed) -> None:
        with self._lock:
            if job_id in self._jobs:
                return
            job = self._jobs[job_id] = _Job(job_id)
        text, values = self._bind(sql, params)
        job.thread = threading.Thread(target=self._run, args=(job, text, values), daemon=True)
        job.thread.start()

    def _run(self, job: _Job, sql: str, values: dict[str, Any]) -> None:
        st = job.state
        st.started_ms = _now_ms()
        try:
            con = self.connect()
            try:
                cur = con.execute(sql, values)
                names = [d[0] for d in cur.description or []]
                rows = cur.fetchall()
            finally:
                con.close()
            job.rows = [{n: _json_value(v) for n, v in zip(names, r)} for r in rows]
            job.total = len(rows)
            st.outcome = "success"
        except Exception as e:  # noqa: BLE001 - every DuckDB error becomes a failed job
            st.outcome, st.error = "error", str(e)
        st.ended_ms = _now_ms()
        st.done = True

    def get_job(self, job_id: str) -> JobState | None:
        with self._lock:
            job = self._jobs.get(job_id)
        return None if job is None else job.state

    def wait_and_fetch(self, job_id, timeout, max_rows):
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise JobFailed(JobState(job_id, True, "error", "job not found"))
        if job.thread is not None:
            job.thread.join(timeout)
        if not job.state.done:
            raise TimeoutError(f"DuckDB query {job_id} is still running")
        with self._lock:
            self._jobs.pop(job_id, None)
        if job.state.outcome == "error":
            raise JobFailed(job.state)
        return job.state, job.rows[:max_rows], job.total

    def dry_run(self, sql, params) -> DryRun:
        """Plan the query without running it: catches syntax, binding and type errors."""
        text, values = self._bind(sql, params)
        con = self.connect()
        try:
            con.execute("EXPLAIN " + text, values).fetchall()
            tables = _tables_in(con, text)
        except self._duckdb.Error as e:
            raise ValueError(str(e)) from None
        finally:
            con.close()
        return DryRun(0, "SELECT", tables)

    # --- metadata ---------------------------------------------------------

    def _split(self, ref: str, parts: int) -> list[str]:
        """Fill in catalog and schema like DuckDB does.

        parts=2 (a dataset): `schema`, `catalog` or `catalog.schema`.
        parts=3 (a table): `t`, `schema.t`, `catalog.t` or `catalog.schema.t`.
        """
        bits = ref.split(".")
        if len(bits) > parts:
            raise ValueError(f"{ref!r} has too many parts")
        if len(bits) == parts:
            return bits
        if bits[0] == self.project:  # catalog given, schema omitted
            bits = bits[1:]
            return [self.project, "main", *bits] if parts == 3 else [self.project, "main"]
        if parts == 3 and len(bits) == 1:
            return [self.project, "main", *bits]
        return [self.project, *bits]

    def list_datasets(self, project: str | None = None) -> list[dict]:
        con = self.connect()
        try:
            rows = con.execute(
                "SELECT DISTINCT database_name, schema_name FROM duckdb_tables() WHERE NOT internal "
                "UNION SELECT DISTINCT database_name, schema_name FROM duckdb_views() WHERE NOT internal "
                "ORDER BY 1, 2").fetchall()
        finally:
            con.close()
        return [{"id": f"{d}.{s}"} for d, s in rows if project in (None, d)]

    def list_tables(self, dataset: str) -> list[dict]:
        db, schema = self._split(dataset, 2)
        con = self.connect()
        try:
            rows = con.execute(
                "SELECT table_name, 'TABLE' FROM duckdb_tables() WHERE database_name = $db AND schema_name = $s "
                "UNION ALL SELECT view_name, 'VIEW' FROM duckdb_views() "
                "WHERE NOT internal AND database_name = $db AND schema_name = $s ORDER BY 1",
                {"db": db, "s": schema}).fetchall()
        finally:
            con.close()
        return [{"id": f"{db}.{schema}.{n}", "type": t, "partitioning": None, "clustering": []} for n, t in rows]

    def get_table(self, table: str) -> dict:
        db, schema, name = self._split(table, 3)
        con = self.connect()
        try:
            t = con.execute(
                "SELECT 'TABLE', comment, estimated_size, NULL FROM duckdb_tables() "
                "WHERE database_name = $db AND schema_name = $s AND table_name = $n "
                "UNION ALL SELECT 'VIEW', comment, NULL, sql FROM duckdb_views() "
                "WHERE NOT internal AND database_name = $db AND schema_name = $s AND view_name = $n",
                {"db": db, "s": schema, "n": name}).fetchone()
            if t is None:
                raise ValueError(f"no table or view {db}.{schema}.{name}")
            cols = con.execute(
                "SELECT column_name, data_type, is_nullable, NULL FROM information_schema.columns "
                "WHERE table_catalog = $db AND table_schema = $s AND table_name = $n ORDER BY ordinal_position",
                {"db": db, "s": schema, "n": name}).fetchall()
        finally:
            con.close()
        return {
            "id": f"{db}.{schema}.{name}", "type": t[0], "description": t[1], "num_rows": t[2], "num_bytes": None,
            "partitioning": None, "require_partition_filter": False, "clustering": [], "view_query": t[3],
            "columns": [{"name": c, "type": ty, "mode": "NULLABLE" if nul == "YES" else "REQUIRED", "description": d}
                        for c, ty, nul, d in cols],
        }


def _tables_in(con, sql: str) -> list[str]:
    """Best-effort list of base tables a query reads, from DuckDB's parsed statement."""
    try:
        tree = json.loads(con.execute("SELECT json_serialize_sql($q)", {"q": sql}).fetchone()[0])
    except Exception:  # noqa: BLE001
        return []
    ctes: set[str] = set()
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for cte in (node.get("cte_map") or {}).get("map", []) or []:
                if isinstance(cte, dict) and cte.get("key"):
                    ctes.add(cte["key"])
            if node.get("type") == "BASE_TABLE" and node.get("table_name"):
                ref = ".".join(x for x in (node.get("catalog_name"), node.get("schema_name"), node["table_name"]) if x)
                if node["table_name"] not in ctes and ref not in found:
                    found.append(ref)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(tree)
    return found
