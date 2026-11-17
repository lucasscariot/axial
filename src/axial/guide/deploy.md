# Deploying to Cloud Run

`axial serve` runs a project as a container-ready server: it checks the project, binds
`0.0.0.0` and listens on `$PORT` (else `server.port`). It opens no browser and logs each
request. `axial init` writes a `Dockerfile` and `.dockerignore` for it. This is one process
serving one project; there is no hosted mode with accounts or sharing built into Axial.

**Never deploy it publicly.** Axial has no login of its own. Anyone who can reach the
service can load dashboards, which run billed BigQuery jobs as the service account.
Put it behind Identity-Aware Proxy (IAP) or Cloud Run IAM, and deploy with
`--no-allow-unauthenticated`.

## Build

```sh
docker build --build-arg APP_REVISION=$(git rev-parse --short=12 HEAD) -t my-dashboards .
docker run --rm -p 8080:8080 my-dashboards axial serve --fixture   # try it locally
```

- The image installs the axial version in `AXIAL_REF` (the engine release that created the
  project) with its locked dependencies, from the GitHub source archive. Pass
  `--build-arg AXIAL_REF=v0.3.0` or a commit SHA to change it; keep it inside the
  project's `engine:` range. The build fails if that ref does not install an `axial` command.
- `APP_REVISION` sets `AXIAL_APP_REVISION`, which Axial records as each job's project
  commit. The image has no `.git`, so without it the commit is blank.
- `.dockerignore` keeps `.axial/` history, `.git/`, local DuckDB files, `.env` files and
  credential JSON out of the image. A DuckDB project needs its file at runtime: remove the
  `*.duckdb` line only if the file is meant to ship in the image.
- The container runs as an unprivileged `axial` user and starts `axial serve` on port 8080.

`GET /api/health` returns `{"status": "ok"}` without touching the warehouse or history,
for startup and liveness probes. It is behind the same authentication as the app.

## Deploy

```sh
gcloud artifacts repositories create dashboards --repository-format=docker --location=europe-west2
gcloud builds submit --tag europe-west2-docker.pkg.dev/my-project/dashboards/my-dashboards
gcloud run deploy my-dashboards \
  --image europe-west2-docker.pkg.dev/my-project/dashboards/my-dashboards \
  --region europe-west2 \
  --service-account axial-dashboards@my-project.iam.gserviceaccount.com \
  --no-allow-unauthenticated \
  --max-instances=1
```

`gcloud builds submit` builds without `--build-arg`; to stamp the revision, build and push
with `docker build --build-arg APP_REVISION=…` instead, or set
`--set-env-vars AXIAL_APP_REVISION=…` on deploy.

- `--max-instances=1`: the app cache and the sharing of in-flight jobs are per process.
  More instances would each submit their own jobs for the same tile, and spread history
  across containers.
- `--no-allow-unauthenticated`: only principals with `roles/run.invoker` can call the
  service. For browser access, enable IAP on the service (`gcloud run deploy … --iap`, or
  a load balancer with IAP), grant viewers `roles/iap.httpsResourceAccessor`, and let the
  IAP service agent invoke the service (`roles/run.invoker`).

## Service account

Queries run with the service's identity through Application Default Credentials; no key
file is needed. Give a dedicated service account:

- `roles/bigquery.jobUser` on the billing project (`bigquery.billing_project`), to run jobs;
- `roles/bigquery.dataViewer` on each dataset the queries read.

Every viewer now reads data with the service account's permissions, not their own. Anyone
allowed through IAP sees everything the dashboards can query; grant data access with that
in mind. `maximum_bytes_billed`, the row cap and the concurrency limit still apply per job.

## Configuration

Set `axial.yaml` values per environment with `AXIAL_<SECTION>_<KEY>` variables, e.g.
`--set-env-vars AXIAL_BIGQUERY_BILLING_PROJECT=prod-project,AXIAL_DISPLAY_TITLE=Ops`.
Cloud Run sets `PORT`; `axial serve --port` overrides it. The project is copied to `/app`, so
set `display.title`; otherwise the top bar shows `app`. `AXIAL_APP_REVISION` is not a
config key and is not parsed as one.

## History is ephemeral by default

History (`storage.path`, default `.axial/history.sqlite3`) is written to the container's
filesystem, which on Cloud Run is in memory. It is lost when the instance is replaced or
scaled to zero, and counts against the instance's memory, so Query usage and Inspect only
cover the current instance's lifetime.

To keep it, use the PostgreSQL backend (`storage.backend: postgres`; see `axial guide config`)
and add `--extra postgres` to the `uv sync` line in the Dockerfile.

## Who ran what

Behind IAP, set `AXIAL_SERVER_TRUST_IAP_USER=true` to record the signed-in user's email on
each request and show it in Query usage and Inspect. See "Signed-in users" in
`axial guide config` for what that does and does not guarantee.

The background reconciler that records final stats for jobs the dashboard stopped waiting
for only advances while the instance has CPU. With default request-based billing, CPU is
throttled between requests, so those stats may be recorded late or not at all. Use
`--no-cpu-throttling` if that matters; the BigQuery jobs themselves run regardless.
