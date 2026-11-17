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
cache_ttl_seconds: 120                          # optional, default from axial.yaml
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
- `columns` must list every output column, with the type BigQuery returns. `axial check`
  verifies that the declared params match the `@params` the SQL uses.
- **Rates and ratios:** compute them over the whole period in SQL from the numerator and the
  denominator (`SAFE_DIVIDE(COUNTIF(cancelled), COUNT(*))`), returning one row. Never average
  per-day rates.
- Results are capped at the configured row cap (2,000 by default). Aggregate in BigQuery,
  and use `ORDER BY ... LIMIT @limit` for detail tables.
- **Cost:** filter on the table's partition column (see `axial schema`) so BigQuery can
  skip partitions, and select only the columns you need. Compare `axial dry-run` bytes
  for a 1-day and a 7-day range: if they're the same, the filter isn't pruning partitions.

- Keep `queries/` flat and use `.yaml`. `axial check` reports files that would not be loaded:
  a `.yml` file, a file in a subfolder, or a `.sql` without its `.yaml`. It also rejects any
  key not shown above.

## Sharing SQL between queries

When several queries need the same SQL, such as a chain of CTEs, write it once in
`snippets/<name>.sql` and include it with a line that holds only `{{ name }}`:

```sql
-- snippets/completed_orders.sql
WITH completed AS (
  SELECT order_id, DATE(created_at) AS day, amount
  FROM `project.dataset.orders`
  WHERE DATE(created_at) >= @start_date AND DATE(created_at) < @end_date
    AND status = 'completed'
)
```

```sql
-- queries/orders_daily.sql
{{ completed_orders }}
SELECT day, COUNT(*) AS orders, SUM(amount) AS revenue
FROM completed
GROUP BY day
ORDER BY day
```

The line is replaced by the file's text, exactly as written, before anything else reads the
query. `axial check`, `dry-run`, the warehouse and the SQL version in Query usage all see the
resulting SQL, so editing a snippet gives every query that includes it a new SQL version.
`axial sql <query>` prints that SQL; warehouse error messages give line numbers in it.

- Inclusion is the only feature. There are no arguments, variables, conditions or loops, and
  `{{ }}` anywhere else in a query is an error. Values still go through `@params`.
- A snippet may use `@params`. Each query that includes it must declare them under `params`,
  as for its own SQL; `axial check` reports the ones that are missing. Shared params are
  repeated in each query's YAML.
- A snippet can be any run of whole lines: leading CTEs, CTEs that continue a `WITH`
  (starting with `, name AS (`), or an expression such as a `CASE`. A query can include
  several snippets, and the same one more than once.
- Snippets cannot include other snippets. When queries need different parts of a long
  fragment, split it into several snippets and list them in each query.
- Keep `snippets/` flat, with `.sql` files named with letters, digits and underscores.
  `axial check` reports an include with no file, a file that cannot be included, and (as a
  warning) a snippet no query includes.
- A snippet has no YAML file and is not a query: dashboards cannot refer to it.

## Fixtures

`--fixture` serves `fixtures/<id>.json` for a query when that file exists, and otherwise
makes up rows from `columns`. A fixture is a JSON list of rows:

```json
[{"day": "2026-11-01", "orders": 412, "revenue": 10315.5}]
```

`axial check` validates every fixture against its query:

- each row has exactly the declared `columns`;
- values match the column type: a JSON integer for INT64, a number or numeric string for
  FLOAT64, NUMERIC and BIGNUMERIC, `true`/`false` for BOOL, a string for STRING, and an ISO
  string for DATE, DATETIME and TIMESTAMP. `null` is allowed anywhere;
- the file is directly inside `fixtures/` and named after an existing query.

Fixtures are not filtered by the date range.

## DuckDB projects

With `warehouse: duckdb`, write DuckDB SQL but keep `@params`: axial rewrites them to
DuckDB's `$params`. Declare `columns` with the BigQuery type names: `INT64` for
INTEGER/BIGINT/HUGEINT, `FLOAT64` for DOUBLE, `NUMERIC` for DECIMAL, `STRING` for VARCHAR,
`BOOL` for BOOLEAN, and `DATE`/`TIMESTAMP` as is. `axial dry-run` plans the query (catching
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
