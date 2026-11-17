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
storage: {backend: sqlite, path: .axial/history.sqlite3}   # or backend: postgres (below)
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

## History storage

Execution history (Query usage, the Inspect drawer, `axial usage` and `axial bench`) is kept
in `.axial/history.sqlite3` by default. A hosted instance whose disk is wiped on restart,
such as Cloud Run, should keep it in PostgreSQL instead:

```yaml
storage:
  backend: postgres            # sqlite (default) | postgres
  pool_size: 4                 # connections per instance
  # url: set AXIAL_STORAGE_URL instead, so the password stays out of this file
```

`AXIAL_STORAGE_URL` takes a `postgresql://user:password@host:5432/dbname` URL or libpq
`key=value` pairs, used verbatim. Anything it leaves out comes from the standard `PGHOST`,
`PGPORT`, `PGUSER`, `PGPASSWORD` and `PGDATABASE` variables. Axial never prints the password;
messages name the database as `postgres user@host/dbname`.

From Cloud Run to Cloud SQL, attach the instance to the service and connect through its
unix socket, with the password from Secret Manager:

```sh
AXIAL_STORAGE_BACKEND=postgres
AXIAL_STORAGE_URL="host=/cloudsql/PROJECT:REGION:INSTANCE dbname=axial user=axial"
PGPASSWORD=...                 # mounted from a secret
```

Axial creates its tables on first start and upgrades them on later ones; the database user
needs `CREATE` on the schema (the first of `search_path`, usually `public`). To keep the
tables apart, use a dedicated database, or `CREATE SCHEMA axial` and add
`options='-csearch_path=axial'` to the connection string. Several instances can share one
database: request and job IDs never collide, any instance may reconcile a job, and a late
update never reopens a finished one. The app cache stays per instance, so a new instance
runs a query once before serving it from its cache.

This needs the `postgres` extra: `pip install 'axial[postgres]'` or `uv tool install
'axial[postgres] @ git+https://github.com/lucasscariot/axial.git@main'`.

Override any value with `AXIAL_<SECTION>_<KEY>`, for example
`AXIAL_BIGQUERY_BILLING_PROJECT=other`. Values are parsed as YAML scalars.
