# bqdash

The engine for local, Git-backed BigQuery dashboards. bqdash holds no business data. Each
dashboard project is its own repo, containing saved SQL, small YAML dashboard definitions and
a `bqdash.yaml`. bqdash checks the project, runs its queries against BigQuery, renders its
dashboards, and records what each one costs to use.

```
my-ops/                 a dashboard project (its own private repo)
  bqdash.yaml           engine version range, billing project, location, caps, display
  queries/<id>.sql      one read-only SELECT with @params
  queries/<id>.yaml     typed params, output columns, formats, cache TTL
  dashboards/<id>.yaml  a date range and a 12-column grid of tiles
  CONTEXT.md            your trusted tables, metric definitions, conventions (for agents)
  AGENTS.md, CLAUDE.md  agent rules (managed by bqdash)
  .agents/skills/       skills for Codex           (managed by bqdash)
  .claude/skills/       skills for Claude Code     (managed by bqdash)
  .bqdash/              local execution history (gitignored)
```

## Install

```sh
uv tool install 'git+ssh://git@github.com/lucasscariot/bqdash.git@v0.1.0'
# screenshots for `bqdash preview --screenshot` (optional):
uv tool install --with playwright 'git+ssh://git@github.com/lucasscariot/bqdash.git@v0.1.0'
playwright install chromium
```

The built UI ships inside the package, so installing needs no Node.

## Create and run a project

```sh
bqdash init my-ops --billing-project my-gcp-project --location EU
cd my-ops
bqdash up --fixture                         # sample data, no BigQuery
gcloud auth application-default login
bqdash up                                   # live
```

`bqdash up` does the following:
1. Checks that the installed engine is within the project's `engine:` range.
2. Checks that a billing project and Google credentials exist, with fix-it messages if not.
3. Runs `bqdash check` and refuses to start on errors.
4. Serves on `server.port`, or the next free port, and opens the browser.

Several projects can run at once, each on its own port with its own history.

Queries run as your Application Default Credentials. The app accepts only one `SELECT` or
`WITH` statement per query, but that check only catches mistakes. Your BigQuery permissions
are the real limit on what dashboards can read.

## Commands

**Free:** these start no billed query jobs.

| Command | |
|---|---|
| `bqdash init <dir>` | create a project (skeleton, one example, agent files) |
| `bqdash up [--fixture] [--port N] [--no-open]` | check and launch the project (live loads run jobs) |
| `bqdash check [dashboard] [--json]` | query references, tile fields, filter bindings, non-SELECT SQL |
| `bqdash preview <dashboard> --fixture [--screenshot out.png]` | render one dashboard |
| `bqdash dry-run <query> [--start --end] [--json]` | byte estimate vs. the cap, tables read |
| `bqdash tables [project.dataset] [--json]` | list datasets and tables (metadata API) |
| `bqdash schema <table> [--json]` | columns, partitioning, clustering |
| `bqdash usage [query] [--days N] [--json]` | recorded requests, jobs, cost, SQL versions |
| `bqdash guide [topic]` | reference for the installed version: queries, dashboards, config, cli |
| `bqdash agents sync [--check] [--force]` | refresh AGENTS.md, CLAUDE.md and skills |

**Billed:**

| Command | |
|---|---|
| `bqdash run <query> [--start --end] [--json]` | run once and print rows |
| `bqdash bench <query> [--start --end] [--runs N] [--json]` | run with both caches bypassed; compare SQL versions |
| `bqdash reconcile` | fetch final stats for jobs the app stopped waiting for (metadata only) |

## Coding agents

`bqdash init` sets a project up for Codex and Claude Code:

- **`AGENTS.md`:** short rules. Which commands are free and which are billed, the SQL
  rules, and what "done" means. Codex reads it directly.
- **`CLAUDE.md`:** imports `AGENTS.md`, because Claude Code reads `CLAUDE.md`.
- **Three skills,** written to `.agents/skills/` (Codex) and `.claude/skills/` (Claude Code):
  - `build-dashboard`: request → tables → SQL → dry-run → tiles → check → screenshot
  - `optimize-query`: usage → hypothesis → bench before and after → identical results
  - `rebuild-dashboard`: inventory an existing dashboard → build → verify numbers per metric
- **`CONTEXT.md`:** yours. Trusted tables, metric definitions and conventions. Agents read
  it first, and bqdash never touches it.

The skills stay short and call `bqdash guide <topic>` for reference, so the details always
match the installed engine. Generated files carry a `bqdash:managed` marker. After upgrading
the engine, run `bqdash agents sync`; `bqdash up` warns when they're out of date. Files
without the marker are left alone unless you pass `--force`. Set `bigquery.datasets` so
`bqdash tables` shows agents the right datasets first.

## Project configuration

`bqdash.yaml` is committed with the project. None of its values are secrets. Any value can be
overridden with an environment variable named `BQDASH_<SECTION>_<KEY>`, for example
`BQDASH_BIGQUERY_BILLING_PROJECT=other-project` or `BQDASH_SERVER_PORT=9000`. Values are
parsed as YAML scalars. `version:` is the config format version (currently 1).

| Section | Keys |
|---|---|
| `bigquery` | `billing_project`, `location`, `maximum_bytes_billed`, `wait_timeout_seconds` |
| `billing` | `mode` (`on_demand` \| `reservation`), `price_per_tib_usd` |
| `execution` | `default_cache_ttl_seconds`, `max_concurrent_jobs`, `row_cap`, `reconcile_interval_seconds` |
| `display` | `title`, `locale`, `currency`, `brand_color` (`#rrggbb`), `logo` (path in the project), `slow_tile_ms` |
| `server` | `port` |
| `storage` | `backend` (`sqlite`), `path` |

Customization is config only. Projects choose tiles, spans, formats and these display tokens,
and never run their own code in the engine, so every project stays upgradeable.

## How a dataset is served

1. **Validate.** The browser sends `dashboard_id`, `query_id` and filter values. The server
   checks that the query is on that dashboard, validates types and the date range
   (`max_days`, exclusive end date), and binds native BigQuery parameters.
2. **Reuse.** The cache key is query ID + SQL hash + normalized params. Concurrent requests
   for the same key share one in-flight job, and each dataset renders as it arrives.
3. **Limit.** Each query has a cache TTL (default 120 s). At most 4 jobs run at once, results
   are capped at 2,000 rows, and every job carries `maximum_bytes_billed`.
4. **Trace.**
   - Each job's ID is generated by the app and saved before submission, along with project,
     location, SQL text and hash, project commit and engine version.
   - If a submit times out, the app looks up that same job ID before retrying.
   - If the dashboard stops waiting, a background reconciler records the final job stats later.

Execution history goes through a `HistoryStore` interface. The only backend today is SQLite,
in `.bqdash/history.sqlite3` with two tables:
- **`query_requests`:** one row per dataset request, with serve time and source (`bigquery`,
  `shared` or `app_cache`).
- **`bigquery_jobs`:** one row per actual job, with bytes processed and billed, slot-ms,
  queue and execution time, BigQuery cache hit and outcome.

`/usage` groups this by saved query and SQL version. **Inspect** on any tile opens the run
behind it.

## Developing the engine

```sh
make install    # uv sync --all-extras + npm ci
make test       # pytest + TypeScript type-check
make web        # rebuild the UI into src/bqdash/web_dist (commit the result)
make preview    # render tests/project with fixture data
```

`tests/project/` is the throwaway project the tests use. CI rejects a pull request whose
committed UI build is stale.

### Releasing

1. Bump `version` in `pyproject.toml` and move the CHANGELOG entry out of *unreleased*,
   adding **Upgrade notes** if projects must change.
2. Merge, then tag the merge commit `vX.Y.Z` and push the tag. CI verifies that the tag
   matches the package version.
3. In each project, raise `engine:` in `bqdash.yaml` when you adopt the new range, and
   reinstall the tool at that tag.
