# Dashboards

A dashboard is `dashboards/<id>.yaml`: an optional date filter and a 12-column grid of tiles.
Tiles fill rows left to right. The engine owns spacing, colours, fonts and formatting; you
choose tiles, sizes and order.

```yaml
title: Booking operations
description: optional
date_range: {default_days: 7, max_days: 31}   # binds start_date / end_date params
tiles:
  - {type: text, markdown: "Excludes test accounts. Owner: ops-analytics.", span: 12}
  - {type: kpi, query: bookings_daily, value: bookings, reduce: sum, span: 3}
  - {type: kpi, query: cancellation_rate, value: cancellation_rate, title: Cancellation rate, span: 3}
  - {type: line, query: bookings_daily, x: day, y: bookings, span: 9}
  - {type: bar, query: bookings_by_region, x: region, y: bookings, span: 3}
  - {type: table, query: booking_exceptions, span: 12, height: 400}
```

## Tile types

| type | required | optional |
|---|---|---|
| `kpi` | `query`, `value` (numeric column) | `reduce`: `single` (default; the query returns 1 row), `sum`, `min`, `max`, `first`, `last`, `count`; `format` |
| `line` | `query`, `x`, `y` (numeric column or list) | `series` (a column to split by, with a single `y`), `format` |
| `bar` | same as `line` | same as `line` |
| `funnel` | `query`, `x` (stage column), `y` (one numeric column) | `format` |
| `table` | `query` | `columns` (subset and order), `formats` (per column) |
| `text` | `markdown` (headings, lists, **bold**, *italic*, `code`, https links) | |

Every tile also takes `span` (1–12, default 12), `title`, and `height` (px; defaults are
kpi 64, charts 200, funnel 260, table 330).

## Layout guidance

- Put the headline KPIs on the first row, 3–4 per row (`span: 3` or `4`).
- Put trends (`line`) wide (`span: 8–12`) and breakdowns (`bar`) narrower.
- A `funnel` draws one row per stage, in the order the query returns them, so end the SQL
  with an `ORDER BY` on the step. Rows that repeat a stage are summed. Each stage shows its
  share of the first stage, and the tooltip adds the share of the previous one.
- Put detail tables last, at `span: 12`.
- Tiles that use the same query share one dataset and one BigQuery job, so reuse a query
  instead of writing near-duplicates.
- There is no `avg` reducer. Averages and rates belong in SQL.
- `line`/`bar` with `series` shows at most 8 series and folds the rest into "Other".

## The date filter

- `date_range` binds to each query's `start_date` and `end_date` params. Use
  `start_param`/`end_param` to rename them.
- Every query on the dashboard must declare both params, or neither.
- Any other params a query declares need a default.
- `default_days: 7` means the last 7 days including today.
