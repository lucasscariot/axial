"""Aggregations behind the Query usage screen."""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict

from .config import Config
from .store import Store

TIB = 2 ** 40


def p95(values: list[float]) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    # nearest-rank
    return float(vals[max(0, math.ceil(0.95 * len(vals)) - 1)])


def median(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return float(statistics.median(vals)) if vals else None


def cost_usd(cfg: Config, bytes_billed: int | None) -> float | None:
    if cfg.billing_mode != "on_demand" or bytes_billed is None:
        return None
    return bytes_billed / TIB * cfg.price_per_tib_usd


def _job_totals(cfg: Config, jobs: list[dict]) -> dict:
    done = [j for j in jobs if j["status"] == "done"]
    billed = sum(j["bytes_billed"] or 0 for j in done)
    slot_ms = sum(j["slot_ms"] or 0 for j in done)
    hits = [j for j in done if j["cache_hit"] is not None]
    return {
        "jobs": len(jobs),
        "jobs_unfinished": sum(1 for j in jobs if j["status"] in ("pending", "running")),
        "jobs_failed": sum(1 for j in done if j["outcome"] == "error"),
        "bq_cache_hit_rate": (sum(1 for j in hits if j["cache_hit"]) / len(hits)) if hits else None,
        "p95_bq_exec_ms": p95([j["exec_ms"] for j in done]),
        "bytes_processed": sum(j["bytes_processed"] or 0 for j in done),
        "bytes_billed": billed,
        "slot_hours": slot_ms / 3_600_000,
        "est_cost_usd": cost_usd(cfg, billed),
    }


def summarize(store: Store, cfg: Config, start: float, end: float) -> dict:
    reqs = store.requests_between(start, end)
    jobs = store.jobs_between(start, end)
    by_q_req: dict[str, list[dict]] = defaultdict(list)
    by_q_job: dict[str, list[dict]] = defaultdict(list)
    for r in reqs:
        by_q_req[r["query_id"]].append(r)
    for j in jobs:
        by_q_job[j["query_id"]].append(j)
    rows = []
    for q in sorted(set(by_q_req) | set(by_q_job)):
        rq = by_q_req.get(q, [])
        app_hits = sum(1 for r in rq if r["source"] in ("app_cache", "shared"))
        row = {
            "query_id": q,
            "requests": len(rq),
            "errors": sum(1 for r in rq if r["outcome"] != "ok"),
            "app_cache_hit_rate": (app_hits / len(rq)) if rq else None,
            "p95_app_ms": p95([r["app_ms"] for r in rq if r["outcome"] == "ok"]),
            **_job_totals(cfg, by_q_job.get(q, [])),
        }
        rows.append(row)
    totals = _job_totals(cfg, jobs)
    totals["requests"] = len(reqs)
    rows.sort(key=lambda r: -(r["bytes_billed"] if cfg.billing_mode == "on_demand" else r["slot_hours"]))
    return {"billing_mode": cfg.billing_mode, "price_per_tib_usd": cfg.price_per_tib_usd,
            "queries": rows, "totals": totals}


def query_runs(store: Store, cfg: Config, query_id: str, start: float, end: float, limit: int = 200) -> dict:
    reqs = store.requests_between(start, end, query_id)[:limit]
    jobs = store.jobs_between(start, end, query_id)
    job_by_id = {j["job_id"]: j for j in jobs}
    missing = {r["job_id"] for r in reqs if r["job_id"] and r["job_id"] not in job_by_id}
    for jid in missing:
        j = store.get_job(jid)
        if j:
            job_by_id[jid] = j
    runs = []
    for r in reqs:
        j = job_by_id.get(r["job_id"]) if r["job_id"] else None
        runs.append({**r, "job": _job_public(cfg, j) if j else None})

    # Before/after comparison: group finished jobs by SQL version and parameters,
    # separating runs that bypassed BigQuery's cache (the fair comparison).
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for j in jobs:
        if j["status"] == "done" and j["outcome"] == "success":
            groups[(j["sql_hash"], j["params_json"], bool(j["use_query_cache"]))].append(j)
    versions = []
    for (h, params, used_cache), js in groups.items():
        versions.append({
            "sql_hash": h,
            "params": json.loads(params),
            "bq_cache_bypassed": not used_cache,
            "runs": len(js),
            "bq_cache_hits": sum(1 for j in js if j["cache_hit"]),
            "median_exec_ms": median([j["exec_ms"] for j in js]),
            "median_bytes_billed": median([j["bytes_billed"] for j in js]),
            "median_slot_ms": median([j["slot_ms"] for j in js]),
            "first_seen": min(j["recorded_at"] for j in js),
            "last_seen": max(j["recorded_at"] for j in js),
            "app_revision": js[0]["app_revision"],
        })
    versions.sort(key=lambda v: (-v["last_seen"]))
    return {"query_id": query_id, "runs": runs, "versions": versions, "totals": _job_totals(cfg, jobs)}


def bq_console_url(project: str | None, location: str | None, job_id: str) -> str | None:
    if not project:
        return None
    return (f"https://console.cloud.google.com/bigquery?project={project}"
            f"&j=bq:{location or 'US'}:{job_id}&page=queryresults")


def _job_public(cfg: Config, j: dict) -> dict:
    out = {k: v for k, v in j.items() if k != "sql_text"}
    out["console_url"] = bq_console_url(j.get("project"), j.get("location"), j["job_id"])
    out["est_cost_usd"] = cost_usd(cfg, j.get("bytes_billed"))
    out["slot_hours"] = (j["slot_ms"] or 0) / 3_600_000 if j.get("slot_ms") is not None else None
    return out


def job_detail(cfg: Config, j: dict) -> dict:
    out = _job_public(cfg, j)
    out["sql_text"] = j["sql_text"]
    return out
