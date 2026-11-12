# Authoring dashboards

This repo holds saved BigQuery SQL and dashboard definitions. A shared renderer owns
spacing, formatting, tables and chart behaviour. You decide **what goes on the page and
how much space it gets**, not how it looks.

## The loop

1. **Find the model:** `uv run dash models search <words>`. Prefer `fct_`/`dim_` marts. Keep
   reusable business logic in dbt; saved SQL only does the dashboard's filters, aggregation
   and output shape.
2. **Write the query** as `queries/<id>.sql` + `queries/<id>.yaml`:
   ```yaml
   title: Daily bookings
   dbt_models: [model.analytics.fct_bookings]   # unique IDs from the manifest
   params: {start_date: DATE, end_date: DATE}     # or {name: {type: INT64, default: 500}}
   columns: {day: DATE, bookings: INT64}          # exact output columns and types
   formats: {bookings: integer}                   # integer|number|money|percent|duration|date|text
   cache_ttl_seconds: 120
   ```
   - One read-only `SELECT`/`WITH` statement. Use `@params`, never string interpolation.
   - `end_date` is **exclusive**: `WHERE d >= @start_date AND d < @end_date`.
   - Rates (cancellation %, conversion) are computed in SQL over the whole period from
     numerator and denominator. Never average daily rates; there is no `avg` reducer.
   - `percent` values are fractions (0.042 → 4.2%). `duration` values are seconds.
   - Results are capped at 2,000 rows: aggregate in BigQuery.
3. **Dry-run:** `uv run dash dry-run <id> [--start YYYY-MM-DD --end YYYY-MM-DD]` shows the
   byte estimate against the configured cap (estimates are conservative on clustered tables).
4. **Add tiles** to `dashboards/<id>.yaml`, then `uv run dash check <dashboard>` until clean.
   ```yaml
   title: Booking operations
   date_range: {default_days: 7, max_days: 31}
   tiles:
     - {type: kpi, query: bookings_daily, value: bookings, reduce: sum, span: 3}
     - {type: line, query: bookings_daily, x: day, y: bookings, span: 9}
     - {type: table, query: booking_exceptions, span: 12}
   ```
   Tile types: `kpi` (`value`, `reduce`: single|sum|min|max|first|last|count, `format`),
   `line`/`bar` (`x`, `y` as a column or list, or one `y` + a `series` column),
   `table` (optional `columns`, `formats`). All take `span` (1–12), `title`, `height` (px).
   Tiles that use the same query share one dataset and one BigQuery job.
5. **Look at it:** `uv run dash preview <dashboard> --fixture --screenshot /tmp/x.png`
   renders with representative data (from `fixtures/<query>.json` if present, else
   synthesized from `columns`), with no warehouse query. Check density and layout.

## Measuring a change

The Query usage screen (`/usage`) groups jobs by saved query and SQL version. To compare an
expensive query before and after a change, run `uv run dash bench <id> --start … --end …`
on each version: it bypasses both caches and records the job, and the drill-down's
*SQL versions* table compares median execution time, bytes billed and slot-ms.
