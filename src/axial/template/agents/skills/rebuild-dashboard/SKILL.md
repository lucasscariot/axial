---
name: rebuild-dashboard
description: Recreate an existing dashboard (Metabase, Looker Studio, a screenshot or a spec) as an Axial dashboard and verify that its numbers match the original. Use when migrating or replacing a dashboard.
---

# Rebuild an existing dashboard

1. **Inventory the original.** List every card: title, metric, chart type, grain, filters,
   date range, and any hidden logic such as excluded statuses, time zone or currency. If
   the user can share the original SQL or question definitions (Metabase lets you view a
   question's SQL), use them. They are the specification.
2. **Map to data.** For each metric, find the table and columns with `axial tables`,
   `axial schema` and `CONTEXT.md`. Record the mapping, plus any definition you had to
   infer, in a short table for the user.
3. **Build it** with the `build-dashboard` skill. Keep the original's tile order and
   emphasis, but use axial defaults for layout density. Group cards that share a grain
   into one query, so they share a job.
4. **Verify the numbers.** This step is billed; confirm the estimated cost first.
   - Pick one closed period the user can also select in the original (e.g. last full week).
   - Run `axial run <query> --start S --end E --json` for each query.
   - Produce a table of metric, axial value and original value. Leave the original value
     blank for the user to fill in if you can't read it.
   - For each mismatch, list the likely causes: time zone of the date boundary, an
     inclusive vs. exclusive end date, status filters, deduplication, currency or rounding.
5. **Compare cost.** From the `dry-run` bytes, report the estimated cost of a cold load
   (the sum over its distinct queries) and note that warm loads within the cache TTL cost
   nothing.
6. **Report:** the mapping table, the verification table, the open questions, and the
   files created.
