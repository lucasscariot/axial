# Saved queries

A saved query is two files with the same id: `queries/<id>.sql` and `queries/<id>.yaml`.

```yaml
# queries/orders_daily.yaml
title: Daily orders
description: Orders and revenue per day.        # optional
params:
  start_date: DATE                              # bound to the dashboard date filter
  end_date: DATE                                # EXCLUSIVE end
  limit: {type: INT64, default: 500}            # params with a default need no filter
columns: {day: DATE, orders: INT64, revenue: NUMERIC}   # exact output columns and types
formats: {orders: integer, revenue: money}      # optional display formats
labels: {revenue: Revenue (GBP)}                # optional display names; default: "Revenue"
cache_ttl_seconds: 120                          # optional, default from bqdash.yaml
```

```sql
-- queries/orders_daily.sql
SELECT DATE(created_at) AS day, COUNT(*) AS orders, SUM(amount) AS revenue
FROM `project.dataset.orders`
WHERE DATE(created_at) >= @start_date AND DATE(created_at) < @end_date
GROUP BY day
ORDER BY day
```

## Rules

- Write exactly one read-only `SELECT` or `WITH` statement. Use BigQuery `@params`, never
  string formatting.
- `end_date` is **exclusive**: write `>= @start_date AND < @end_date`.
- **Param types:** DATE, INT64, FLOAT64, NUMERIC, STRING, BOOL.
- **Column types:** the param types, plus TIMESTAMP, DATETIME and BIGNUMERIC.
- `columns` must list every output column, with the type BigQuery returns. `bqdash check`
  verifies that the declared params match the `@params` the SQL uses.
- **Rates and ratios:** compute them over the whole period in SQL from the numerator and the
  denominator (`SAFE_DIVIDE(COUNTIF(cancelled), COUNT(*))`), returning one row. Never average
  per-day rates.
- Results are capped at the configured row cap (2,000 by default). Aggregate in BigQuery,
  and use `ORDER BY ... LIMIT @limit` for detail tables.
- **Cost:** filter on the table's partition column (see `bqdash schema`) so BigQuery can
  skip partitions, and select only the columns you need. Compare `bqdash dry-run` bytes
  for a 1-day and a 7-day range: if they're the same, the filter isn't pruning partitions.

## DuckDB projects

With `warehouse: duckdb`, write DuckDB SQL but keep `@params`: bqdash rewrites them to
DuckDB's `$params`. Declare `columns` with the BigQuery type names: `INT64` for
INTEGER/BIGINT/HUGEINT, `FLOAT64` for DOUBLE, `NUMERIC` for DECIMAL, `STRING` for VARCHAR,
`BOOL` for BOOLEAN, and `DATE`/`TIMESTAMP` as is. `bqdash dry-run` plans the query (catching
syntax, binding and type errors) and lists the tables it reads; `run` and `bench` cost nothing.

## Formats

| format | value | renders |
|---|---|---|
| `integer` | number | `12,345` |
| `number` | number | `12,345.67` |
| `money` | number in `display.currency` | `£12,345.67` |
| `percent` | **fraction** | `0.042` → `4.2%` |
| `duration` | **seconds** | `3725` → `1h 02m` |
| `date` | DATE or ISO date | `06 Nov 2026` |
| `text` | anything | as is |

Without a format, INT64 renders as `integer`, other numbers as `number`, and DATE as `date`.
