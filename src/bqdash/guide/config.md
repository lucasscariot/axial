# bqdash.yaml

```yaml
version: 1                     # config format version
engine: ">=0.1,<0.2"           # bqdash versions this project works with
bigquery:
  billing_project: my-project  # runs and pays for queries
  location: EU                 # job location; must match the data
  datasets: [my-project.analytics]   # where agents look first (bqdash tables)
  maximum_bytes_billed: 10737418240  # per job; larger jobs fail instead of billing
  wait_timeout_seconds: 90
billing: {mode: on_demand, price_per_tib_usd: 6.25}   # or mode: reservation
execution: {default_cache_ttl_seconds: 120, max_concurrent_jobs: 4, row_cap: 2000}
display: {title: null, locale: en-US, currency: USD, brand_color: null, logo: null, slow_tile_ms: 3000}
server: {port: 8765}
storage: {backend: sqlite, path: .bqdash/history.sqlite3}
```

Override any value with `BQDASH_<SECTION>_<KEY>`, for example
`BQDASH_BIGQUERY_BILLING_PROJECT=other`. Values are parsed as YAML scalars.
