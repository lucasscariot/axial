---
name: build-dashboard
description: Create or extend an Axial dashboard from a request such as "a dashboard of cancellations by region" or "add a revenue trend to ops". Covers finding the right BigQuery tables, writing the saved query, checking cost, laying out tiles and checking the rendered page.
---

# Build a dashboard

1. **Understand the ask.** Restate the metrics, grain (day, week, region…), filters and
   audience in one or two lines. Read `CONTEXT.md` for metric definitions and trusted tables.
   If a metric's definition is ambiguous and `CONTEXT.md` doesn't settle it, ask. Don't guess.
2. **Find the data.** This step is free.
   - `axial tables --json` lists the configured datasets. Then
     `axial tables <project.dataset> --json`.
   - `axial schema <project.dataset.table> --json` shows the columns, descriptions, and
     partitioning and clustering.
   - Prefer the tables `CONTEXT.md` names. Note the partition column; your date filter
     should use it.
3. **Read the reference.** Run `axial guide queries` and `axial guide dashboards`.
4. **Write the queries** (`queries/<id>.sql` + `.yaml`).
   - Write one query per grain. Reuse the same query for a KPI and its trend, which keeps
     it to one job.
   - Write a separate one-row query for each rate or ratio, computed from numerator and
     denominator.
   - Declare exact `columns` and set `formats` (money, percent as a fraction, duration in seconds).
5. **Check the cost.** Run `axial dry-run <id> --json` for the default range and for the
   dashboard's `max_days`.
   - If bytes don't shrink for a shorter range, the partition filter isn't working. Fix it.
   - Report the estimated cost at `max_days`. If it's above $1, ask before going further.
6. **Lay out the dashboard** in `dashboards/<id>.yaml`. KPIs go first (span 3–4), trends
   wide, breakdowns narrower, detail tables last at span 12. Add a `text` tile only for
   caveats that change how numbers are read.
7. **Validate.** Run `axial check <dashboard> --json` until it reports 0 errors.
8. **Look at it.** Run `axial preview <dashboard> --fixture --screenshot /tmp/<dashboard>.png`
   and view the image. Fix anything cramped, empty, mislabelled or badly ordered, then
   re-render.
9. **Optional live check (billed):** only with the user's go-ahead, run
   `axial run <query> --json` for one closed period and sanity-check the magnitudes.
10. **Report:**
    - the files added or changed
    - each tile's definition, in one line
    - dry-run bytes and estimated cost
    - open questions about definitions
