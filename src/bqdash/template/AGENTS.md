# Authoring dashboards in this project

This is a bqdash project: saved BigQuery SQL plus dashboard definitions. The bqdash engine
renders them and owns spacing, formatting, tables and chart behaviour. You decide **what goes
on the page and how much space it gets**, not how it looks.

## The loop

1. **Write the query** as `queries/<id>.sql` + `queries/<id>.yaml`:
   ```yaml
   title: Daily orders
   params: {start_date: DATE, end_date: DATE}     # or {name: {type: INT64, default: 500}}
   columns: {day: DATE, orders: INT64}            # exact output columns and types
   formats: {orders: integer}                     # integer|number|money|percent|duration|date|text
   cache_ttl_seconds: 120
   ```
   - One read-only `SELECT`/`WITH` statement. Use `@params`, never string interpolation.
   - `end_date` is **exclusive**: `WHERE d >= @start_date AND d < @end_date`.
   - Compute rates (cancellation %, conversion) in SQL over the whole period, from the
     numerator and denominator. Never average daily rates. There is no `avg` reducer.
   - `percent` values are fractions (0.042 → 4.2%). `duration` values are seconds.
   - Results are capped at the configured row cap (2,000 by default), so aggregate in BigQuery.
2. **Dry-run:** `bqdash dry-run <id> [--start YYYY-MM-DD --end YYYY-MM-DD]` is free. It shows
   the byte estimate and the tables read. Estimates are conservative on clustered tables.
3. **Add tiles** to `dashboards/<id>.yaml`, then run `bqdash check <dashboard>` until it's clean.
   ```yaml
   title: Operations
   date_range: {default_days: 7, max_days: 31}
   tiles:
     - {type: kpi, query: orders_daily, value: orders, reduce: sum, span: 3}
     - {type: line, query: orders_daily, x: day, y: orders, span: 9}
     - {type: table, query: order_exceptions, span: 12}
     - {type: text, markdown: "Numbers exclude test accounts.", span: 12}
   ```
   Tile types:
   - `kpi`: `value`, `reduce` (single|sum|min|max|first|last|count), `format`
   - `line` / `bar`: `x`, plus `y` as a column or list, or one `y` plus a `series` column
   - `table`: optional `columns` and `formats`
   - `text`: `markdown`

   All tiles take `span` (1–12), `title` and `height` (px). Tiles that use the same query
   share one dataset and one BigQuery job.
4. **Look at it:** `bqdash preview <dashboard> --fixture --screenshot /tmp/x.png` renders it
   with representative data and makes no BigQuery query. The data comes from
   `fixtures/<query>.json` (a list of rows) if present, otherwise it's synthesized from `columns`.

## Measuring a change

The Query usage screen (`/usage`) groups jobs by saved query and SQL version. To compare an
expensive query before and after a change, run `bqdash bench <id> --start … --end …` on each
version. It bypasses both caches and records the job. The *SQL versions* table then compares
median execution time, bytes billed and slot-ms.
