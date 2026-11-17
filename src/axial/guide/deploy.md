# Deploying to Cloud Run

`axial serve` runs a project as a container-ready server: it checks the project, binds
`0.0.0.0` and listens on `$PORT` (else `server.port`). It opens no browser and logs each
request. `axial init` writes a `Dockerfile`, a `.dockerignore` and a CI workflow for it, and
`axial deploy` publishes it. This is one process serving one project; there is no hosted
mode with accounts or sharing built into Axial.

**Never deploy it publicly.** Axial has no login of its own. Anyone who can reach the
service can load dashboards, which run billed BigQuery jobs as the service account.
Put it behind Identity-Aware Proxy (IAP) or Cloud Run IAM, and deploy with
`--no-allow-unauthenticated`.

## axial deploy

Describe the service in `axial.yaml`, commit, and run `axial deploy`:

```yaml
bigquery:
  datasets: [my-project.analytics]   # the service account gets read access to these
deploy:
  service: my-dashboards       # Cloud Run service; also names its image repository and service account
  region: europe-west2
  project: null                # defaults to bigquery.billing_project
  viewers: [user:jane@example.com, group:ops@example.com]
  record_viewers: false        # true records each viewer's email (see "Who ran what")
```

```sh
axial deploy --dry-run         # print every gcloud command; changes nothing
axial deploy                   # needs gcloud, signed in with rights to do the steps below
```

It deploys one fixed shape and has no other options. Each step is safe to repeat:

1. Enables the Cloud Run, Cloud Build, Artifact Registry and IAP APIs.
2. Creates the service account `<service>@<project>`, grants it `roles/bigquery.jobUser` on
   `bigquery.billing_project` (where its queries run, even when `deploy.project` hosts the
   service elsewhere) and READER on each of `bigquery.datasets`. No key is created.
3. Creates an Artifact Registry repository with immutable tags, and builds the image with
   Cloud Build, tagged with the Git commit. The working tree must be clean. Only the files
   `.dockerignore` allows are uploaded.
4. Deploys the service with `--iap --no-allow-unauthenticated`, one instance at most, scaled
   to zero when idle, with `/api/health` as its startup probe.
5. Lets only the IAP service agent invoke the service, and grants each of `deploy.viewers`
   `roles/iap.httpsResourceAccessor` on it.

Then it prints the URL. Open it signed out and signed in before sharing it: a signed-out
visit must redirect to Google sign-in. `--skip-build` redeploys the image already built for
this commit, and `--account` picks the gcloud account. Removing someone from `deploy.viewers`
does not revoke their access; remove the IAP binding in the console or with gcloud.

`axial deploy` covers BigQuery projects with the default SQLite history. For PostgreSQL
history or a DuckDB file, use the manual steps below.

## Continuous integration

`.github/workflows/check.yml` builds the image and uses it to run `axial check`,
`axial agents sync --check` and a fixture-mode `axial serve` probe on every pull request.
It needs no Google credentials and runs no billed query. Because CI and Cloud Run both run
the image, the Dockerfile's `AXIAL_REF` is the project's one engine pin. `axial check` fails
if a release pinned there is outside `engine:` in `axial.yaml`; local installs are held to
the same range when they start.

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
- `.dockerignore` is an allowlist: only `axial.yaml`, `dashboards/*.yaml`,
  `queries/*.sql|yaml` and `snippets/*.sql` reach the image. History, `.git/`, fixtures, `.env` files and
  credentials cannot leak in. Add a `!` line for anything else the server needs, such as
  `!assets/` and `!assets/*` for `display.logo`, or a DuckDB file that must ship in the image.
  Without fixtures, `axial serve --fixture` in the image makes up rows from `columns`.
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
