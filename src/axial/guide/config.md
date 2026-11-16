# axial.yaml

```yaml
version: 2                     # config format version
engine: ">=0.1,<0.2"           # axial versions this project works with
bigquery:
  billing_project: my-project  # runs and pays for queries
  location: EU                 # job location; must match the data
  datasets: [my-project.analytics]   # where agents look first (axial tables)
  maximum_bytes_billed: 10737418240  # per job; larger jobs fail instead of billing
  wait_timeout_seconds: 90
billing: {mode: on_demand, price_per_tib_usd: 6.25}   # or mode: reservation
execution: {default_cache_ttl_seconds: 120, max_concurrent_jobs: 4, row_cap: 2000}
display: {title: null, locale: en-US, currency: USD, brand_color: null, logo: null, slow_tile_ms: 3000}
server: {port: 8765}
storage: {backend: sqlite, path: .axial/history.sqlite3}
```

## Local DuckDB

Point a project at a DuckDB file instead of BigQuery. The `bigquery` and `billing`
sections are then ignored: nothing is billed, so there are no bytes, slots or costs.

```yaml
warehouse: duckdb              # bigquery (default) | duckdb
duckdb:
  path: ../warehouse/analytics.duckdb   # relative to the project root
  datasets: [analytics.main]   # schemas `axial tables` lists by default
  allow_external_access: false # true lets SQL read files and URLs (read_parquet, httpfs)
  threads: null                # optional DuckDB settings
  memory_limit: null           # e.g. "4GB"
```

The file is opened read-only, once per query, so a rebuilt database (written elsewhere and
moved into place) is picked up by the next query. External access is off by default and
cannot be switched back on from SQL.

Override any value with `AXIAL_<SECTION>_<KEY>`, for example
`AXIAL_BIGQUERY_BILLING_PROJECT=other`. Values are parsed as YAML scalars.
