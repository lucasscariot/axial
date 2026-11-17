# Changelog

Projects pin a compatible range in `axial.yaml` (`engine: ">=0.1,<0.2"`). While the engine
is 0.x, a minor bump (0.1 → 0.2) may need project changes. Each release lists them under
**Upgrade notes**.

## Unreleased

### Breaking changes

- Rename bqdash to **Axial**, including the `axial` CLI and Python package, public
  repository, UI text, BigQuery job labels and generated project/agent files.
- Config format version **2** uses `axial.yaml`, `AXIAL_` environment overrides and
  `.axial/history.sqlite3` as the default history path.
- Stricter project checks. An unknown key in `axial.yaml` (or set through
  `AXIAL_<SECTION>_<KEY>`) stops the engine from loading the project. `axial check`, and so
  `axial up` and `axial serve`, reject unknown keys in queries, dashboards and tiles,
  fixtures that do not match their query's `columns`, `.yml` files, files in subfolders of
  `queries/`, `dashboards/` and `fixtures/`, a `.sql` with no `.yaml` and a fixture with no
  query. `axial agents sync --check` exits 1 when a managed path holds a hand-written file.
  The config format stays at version **2**: a file that used only documented keys loads as
  before.

### Features

- The dashboard date filter offers calendar periods (Today, This week, Last week, Month to
  date, Last month, Quarter to date, Year to date) in place of the rolling `7d`, `14d` and
  `28d` buttons. Weeks start on Monday. `date_range.default` opens a dashboard on one of
  them; `default_days` still works. Existing dashboards need no change.

- Homebrew installation from this repository's tap, with locked dependencies and
  DuckDB support. Release automation opens formula update PRs with verified checksums.
- `storage.backend: postgres` keeps execution history in PostgreSQL (including Cloud SQL),
  so hosted instances share it and keep it across restarts. Install `axial[postgres]` and
  pass the connection string in `AXIAL_STORAGE_URL`. Existing projects need no changes.
- `server.trust_iap_user: true` records the signed-in user that Identity-Aware Proxy reports
  on each dataset request, and shows it in Inspect and Query usage. Off by default and
  always off in `axial up`. It stores viewers' emails in history; see `axial guide config`.
- `axial deploy` publishes a project on Cloud Run behind IAP, described by a new optional
  `deploy:` section in `axial.yaml` (`service`, `region`, `project`, `viewers`,
  `record_viewers`). `--dry-run` prints the gcloud commands and changes nothing. The config
  format stays at version **2**: existing projects need no changes, and a project that adds
  `deploy:` should require this engine in its `engine:` range, because earlier releases
  ignore the section and have no `axial deploy`.
- `axial init` now writes an allowlist `.dockerignore`, so the image and the upload to Cloud
  Build hold only `axial.yaml`, `dashboards/`, `queries/` and `snippets/`, and
  `.github/workflows/check.yml`, which builds the image and uses it to run `axial check`,
  `axial agents sync --check` and a fixture-mode `axial serve` probe on every pull request,
  with no Google credentials.
- `axial check` treats the Dockerfile's `AXIAL_REF` as the project's engine pin: a release
  pinned there that falls outside `engine:` in `axial.yaml` is an error, and a ref that is
  not a release tag is a warning.
- Queries share SQL through **snippets**: a line holding only `{{ name }}` in a query's SQL is
  replaced by `snippets/<name>.sql`. Inclusion is textual and one level deep, with no
  arguments or logic; values still go through `@params`. `axial check`, dry runs and SQL
  versions use the resulting SQL, and `axial sql <query>` prints it. Existing projects need
  no changes; a project that adds snippets should require this engine in its `engine:`
  range and add `!snippets/` and `!snippets/*.sql` to its `.dockerignore`.

### Bug Fixes

- Fixture mode makes up a single row for a query that a KPI tile reads with `reduce: single`.
  It used to make up 25 rows, or one per day, whenever such a query also returned a label or
  a date, so the tile showed "Expected 1 row" unless the project saved a fixture for it.

**History schema 2:** request history gains a `user_email` column. The engine adds it to
SQLite and PostgreSQL history when it opens them; older rows have no user. After that,
older engines refuse the history database, so upgrade every instance that shares it.

**Upgrade notes:** run `axial guide migration`. Stop the old engine, rename
`bqdash.yaml` to `axial.yaml`, set `version: 2`, move `.bqdash/` to `.axial/` and update
`storage.path`, ignore rules, environment variables and scripts. Keep custom history
paths. The rename leaves history as it is. `axial agents sync` upgrades old managed markers
without `--force`, preserving handwritten files and `CONTEXT.md`. Release tags through
`v0.1.2` retain the old package name; the initial Homebrew formula pins the rename commit.

**Upgrade notes for the stricter checks:** keep `version: 2`. Run `axial check` with the new
engine and fix what it reports before widening the project's `engine:` range. Each error
names the file and key and lists the known keys. Remove or correct stray keys in `axial.yaml`
and unset any `AXIAL_<SECTION>_<KEY>` variable that names no setting. Rename `.yml` files to
`.yaml`, move files out of subfolders, delete a `.sql` or fixture that has no query, and
correct fixtures whose columns or types differ from the query's `columns`. If
`axial agents sync --check` fails on a hand-written file, run `axial agents sync --force` to
replace it, or move its content to `CONTEXT.md` first.

**Upgrade notes for hosting:** nothing is required. `axial init` does not rewrite an existing
project, so to adopt the new files, run `axial init` in an empty directory and copy its
`Dockerfile`, `.dockerignore` and `.github/workflows/check.yml`, keeping your own
`AXIAL_REF`. The allowlist leaves out everything it does not name: add a `!` line for each
extra runtime file, such as a logo or a DuckDB file. If `axial check` then reports
`AXIAL_REF` outside `engine:`, change one of the two so they agree. Add `deploy:` to
`axial.yaml` only if you use `axial deploy`.

## [0.3.1](https://github.com/lucasscariot/axial/compare/v0.3.0...v0.3.1) (2026-11-17)


### Bug Fixes

* **homebrew:** drop the version line when the formula pins a release tag ([#26](https://github.com/lucasscariot/axial/issues/26)) ([58c133d](https://github.com/lucasscariot/axial/commit/58c133da2e6abc1c7b51b730f3f88ba5d1b89f3f))

## [0.3.0](https://github.com/lucasscariot/axial/compare/v0.2.0...v0.3.0) (2026-11-17)


### ⚠ BREAKING CHANGES

* **check:** projects with an unknown key in axial.yaml no longer load, and `axial check`, `axial up` and `axial serve` fail on unknown query, dashboard or tile keys, on fixtures that do not match their query, and on files the engine would ignore. `axial agents sync --check` fails on unmanaged agent files; run `axial agents sync --force` after moving project notes to CONTEXT.md.

### Features

* add axial deploy and ship CI and an allowlist image with axial init ([#21](https://github.com/lucasscariot/axial/issues/21)) ([60207c1](https://github.com/lucasscariot/axial/commit/60207c19ce7d0588d4da6da31314d5e40e1ac6b3))
* **check:** reject unknown keys, bad fixtures and files that are never loaded ([#20](https://github.com/lucasscariot/axial/issues/20)) ([0e1fe2d](https://github.com/lucasscariot/axial/commit/0e1fe2d6e6c181c0bd811757eb6e58fb14679e00))
* share SQL between queries with snippets ([#22](https://github.com/lucasscariot/axial/issues/22)) ([df8cf74](https://github.com/lucasscariot/axial/commit/df8cf7442210db0815e4b21a31a630e46103f114))


### Bug Fixes

* **fixtures:** make up one row for queries a KPI reads as a single value ([#23](https://github.com/lucasscariot/axial/issues/23)) ([b375e5d](https://github.com/lucasscariot/axial/commit/b375e5d02a8924f7367760a231f77ebab3cfb8e0))

## [0.2.0](https://github.com/lucasscariot/axial/compare/v0.1.2...v0.2.0) (2026-11-17)


### ⚠ BREAKING CHANGES

* use axial, axial.yaml config version 2, AXIAL_ environment overrides and .axial history paths. Run axial guide migration before upgrading existing projects.

### Features

* add axial serve and a container template for Cloud Run ([#16](https://github.com/lucasscariot/axial/issues/16)) ([88f5b98](https://github.com/lucasscariot/axial/commit/88f5b9822308a5214a6ea85b5d48e7661312435a))
* record the signed-in user on each request ([#18](https://github.com/lucasscariot/axial/issues/18)) ([9924fa1](https://github.com/lucasscariot/axial/commit/9924fa1835a0a2a8b6de76216795f6f2f26ed3b1))
* rename bqdash to axial and publish a Homebrew tap ([#14](https://github.com/lucasscariot/axial/issues/14)) ([93bc3a7](https://github.com/lucasscariot/axial/commit/93bc3a7c6703a9e383d43ab0400ed975f74187a0))
* **store:** add a postgres history backend ([#17](https://github.com/lucasscariot/axial/issues/17)) ([4828bc4](https://github.com/lucasscariot/axial/commit/4828bc4f9cca35ca078be45a7ff495d92466916f))

## [0.1.2](https://github.com/lucasscariot/bqdash/compare/v0.1.1...v0.1.2) (2026-11-13)


### Features

* query local duckdb files as a warehouse ([#10](https://github.com/lucasscariot/bqdash/issues/10)) ([5b07a67](https://github.com/lucasscariot/bqdash/commit/5b07a67ce00324188f9f6761462b57d9c70b9519))


### Bug Fixes

* **duckdb:** reuse the shared instance's configuration lock ([#12](https://github.com/lucasscariot/bqdash/issues/12)) ([5ddbcc4](https://github.com/lucasscariot/bqdash/commit/5ddbcc42a4c37ccb308750bfbcc9195b5432cc3b))
* **ui:** wrap tile titles instead of cropping them ([#13](https://github.com/lucasscariot/bqdash/issues/13)) ([1c5b128](https://github.com/lucasscariot/bqdash/commit/1c5b128562eedc26bf897f51ca673c7dfd8eb23a))

## [0.1.1](https://github.com/lucasscariot/bqdash/compare/v0.1.0...v0.1.1) (2026-11-12)


### Features

* **tiles:** add funnel tile type ([#7](https://github.com/lucasscariot/bqdash/issues/7)) ([2203cc5](https://github.com/lucasscariot/bqdash/commit/2203cc5232f08352fa01181e83a76fdcf57a7603))

## 0.1.0 (development baseline)

Initial development before automated releases. Release-please starts from this version
and adds new releases above; commits through `817e52c` are covered by these notes.

- `bqdash init <dir>` scaffolds a dashboard project, and `bqdash up` checks it and launches it.
- Saved queries (`queries/*.sql` + `*.yaml`) and dashboards (`dashboards/*.yaml`) with KPI,
  line, bar, table and markdown text tiles.
- App cache keyed by query, SQL hash and params; shared in-flight jobs; a concurrency limit,
  row cap and `maximum_bytes_billed`.
- Jobs are persisted before submission and reconciled after timeouts. Query usage screen,
  Inspect drawer, `bqdash bench` for before/after comparisons.
- Config format version 1; any value can be overridden with `BQDASH_<SECTION>_<KEY>`.
- Agent support:
  - `init` writes `AGENTS.md`, `CLAUDE.md`, `CONTEXT.md`, and the `build-dashboard`,
    `optimize-query` and `rebuild-dashboard` skills for Codex (`.agents/skills`) and
    Claude Code (`.claude/skills`).
  - `bqdash agents sync` keeps them current.
- Redesigned UI:
  - Geist type (bundled, works offline), neutral surfaces with hairline borders, and a
    light, dark or system theme toggle.
  - A segmented date-range control, a cache switch, and Inspect buttons that appear on hover.
  - Charts with readable legends, line-key tooltips and an area wash.
- PDF export:
  - **Export PDF** in the dashboard toolbar (and Cmd/Ctrl+P) opens the print dialog with the
    dashboard laid out for A4 landscape in the light theme. Tables print every loaded row and
    repeat their header on each page. A line records when it was generated and from what data.
  - `bqdash preview <dashboard> --pdf out.pdf` writes the same PDF headlessly (needs the
    `[screenshot]` extra).
  - Charts now render as SVG, so they stay sharp and their labels are selectable in PDFs.
- `funnel` tiles: one stage per row in query order, with each stage's share of the first
  and previous stages.
- Optional `labels:` in query YAML sets column display names for legends and table headers.
- New commands: `bqdash tables`, `schema`, `usage`, `run` and `guide`. `--json` on `check`,
  `dry-run`, `bench` and the new commands. Optional `bigquery.datasets` setting.

**Upgrade notes:** projects created before agent support was added should run
`bqdash agents sync --force` once, to replace the old hand-written `AGENTS.md` with the
managed one.
