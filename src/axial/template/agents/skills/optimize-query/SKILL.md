---
name: optimize-query
description: Find the most expensive or slowest saved query in this Axial project and make it cheaper or faster with a measured before/after comparison. Use when asked to reduce BigQuery cost, speed up a dashboard, or investigate a slow tile.
---

# Optimize a query

1. **Pick the target.** Run `axial usage --days 30 --json` (free).
   - Rank by `bytes_billed`, or by `slot_hours` in reservation mode, then by
     `p95_bq_exec_ms`.
   - Low `app_cache_hit_rate` with many jobs means a caching problem, not a SQL problem.
     Consider raising `cache_ttl_seconds`.
   - If the user named a query or tile, use that.
2. **Understand it.**
   - Read `queries/<id>.sql`.
   - Run `axial schema <table> --json` for each table it reads, and note the partitioning,
     clustering and `require_partition_filter`.
   - Run `axial usage <id> --json` and look at recent runs and SQL versions.
3. **Form one hypothesis at a time.** Typical wins:
   - Filter on the partition column so partitions are pruned. Wrapping the column in a
     function can defeat pruning; check with dry-run.
   - Filter or group on clustering columns.
   - Select only the needed columns. Avoid `SELECT *` in CTEs.
   - Aggregate earlier, before joins.
   - Avoid re-reading the same table in several CTEs.
   - Merge near-duplicate queries so tiles share one job.
4. **Fix the comparison window.** Pick a closed date range (e.g. the last full 7 days) and
   use the same `--start`/`--end` for every measurement.
5. **Measure before.** This step is billed.
   - Run `axial dry-run <id> --start S --end E --json` and tell the user the estimated cost.
   - Then run `axial bench <id> --start S --end E --runs 2 --json`. That bypasses both
     caches and records the SQL version.
6. **Change the SQL.** Don't change its output: same columns, same numbers.
   - Run `axial check` and `axial dry-run` again.
   - If the dry-run estimate doesn't drop and the change targets bytes, rethink before benching.
7. **Measure after.** Run the same `axial bench` command.
8. **Check the results match.** Run `axial run <id> --start S --end E --json` for the old
   and new SQL (`git stash` to get the old one) and compare the rows. Any difference is a
   bug unless the user agreed to it.
9. **Report:**
   - a before/after table of median exec ms, bytes billed, slot-ms and estimated cost per
     run, from the `bench` output (or `axial usage <id> --json` → `versions`)
   - the change
   - an estimated monthly saving, based on the job count from `usage`
