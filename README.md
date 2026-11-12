# Local BigQuery dashboards

A Git-backed dashboard app. Saved SQL and small YAML dashboard definitions live in this
repo, a shared React renderer handles layout and formatting, and one local FastAPI process
runs the queries against BigQuery and records what each dashboard costs to use.

```
queries/<id>.sql + <id>.yaml   saved queries (typed params, output columns, dbt models)
dashboards/<id>.yaml           dashboards: a date range and a 12-column grid of tiles
dbt/manifest.json, catalog.json  production dbt artifacts (samples included)
dash.yaml                      billing project, location, caps, pricing
src/bqdash/                    FastAPI app, executor, history store, CLI
web/                           React + ECharts renderer (built into src/bqdash/web_dist)
```

## Run it

```sh
make install                 # uv sync + npm ci
make web                     # build the frontend
gcloud auth application-default login
uv run dash serve            # http://127.0.0.1:8765
uv run dash preview booking_ops --fixture   # no BigQuery needed
```

Set `bigquery.billing_project` and `location` in `dash.yaml` first (or `DASH_BILLING_PROJECT`).
Use Application Default Credentials for a principal that can only run jobs in the billing
project and read the approved analytics datasets or views. That grant is the real
read-only boundary. The app's own SQL check (one `SELECT`/`WITH` statement) only catches
mistakes.

## How a dataset is served

1. The browser sends `dashboard_id`, `query_id` and filter values. The server checks that
   the query is on that dashboard, validates types and the date range (`max_days`, end
   date exclusive), and binds native BigQuery parameters.
2. The cache key is query ID + SQL hash + normalized params. Concurrent requests for one
   key share a single in-flight job. Each dataset renders as it arrives.
3. Limits: TTL per query (default 120 s), 4 concurrent jobs, a 2,000-row cap per result,
   and `maximum_bytes_billed` on every job.
4. Each job gets a client-generated ID, persisted with project, location, SQL text/hash,
   dbt models and revision **before** submission. An uncertain submit (timeout) looks
   the same ID up before retrying. If the dashboard stops waiting (`wait_timeout_seconds`),
   a background reconciler (and `dash reconcile`) records the final job stats later.

History lives in SQLite (`.dash/history.sqlite3`): `query_requests` (one row per dataset
request, with the app's serve time and source: `bigquery`, `shared` or `app_cache`) and
`bigquery_jobs` (one row per actual job: bytes processed/billed, slot-ms, queue and
execution time, BigQuery cache hit, outcome). Cache hits reuse the original job, so spend
is never counted twice.

## Query usage and Inspect

`/usage` groups requests by saved query and shows requests, jobs, app and BigQuery cache
hit rates, p95 response time, p95 BigQuery execution time, bytes billed with a gross
on-demand cost estimate (`billing.price_per_tib_usd`), or slot-hours when
`billing.mode: reservation`. Each query drills down to individual runs and a
*SQL versions* comparison table.

On any tile, **Inspect** opens the run behind it: filters, timing breakdown, BigQuery
console link, SQL version and text, repo commit, dbt revision, and dbt model links (set
`dbt.docs_url`).

## CLI

| Command | |
|---|---|
| `dash models search bookings` | dbt models with descriptions, columns and types, relations, upstream deps |
| `dash check [dashboard]` | invalid model IDs, query references, tile fields, filter bindings, non-SELECT SQL |
| `dash preview <dashboard> --fixture [--screenshot out.png]` | render with representative data |
| `dash dry-run <query> [--start --end]` | BigQuery byte estimate vs. the cap |
| `dash bench <query> [--start --end] [--runs N]` | run with both caches bypassed; compare SQL versions |
| `dash reconcile` | fetch final stats for jobs the app stopped waiting for |
| `dash serve [--fixture]` | run the app |

Screenshots need `uv sync --extra screenshot && uv run playwright install chromium`.

## v1 acceptance test

1. Rebuild one real Metabase dashboard (date filter, KPI, trend, exceptions table) and
   point `dbt.manifest` at the production manifest. `dash check` must be clean.
2. Compare the numbers against Metabase for the same period.
3. Cold load: KPI + trend submit **one** job for their shared query. Warm reload: **zero**.
   The usage screen shows this as requests vs. jobs and app cache hit rate.
4. Trace the cold job from `/usage/<query>` to its SQL hash, repo commit and dbt model.
5. Pick an expensive query, `dash bench` it, change it, `dash bench` again with the same
   dates, and compare the two rows in *SQL versions*.

`make test` runs the automated version of steps 3–5 against a fake warehouse.
