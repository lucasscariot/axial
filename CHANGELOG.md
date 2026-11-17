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

### Features

- Homebrew installation from this repository's tap, with locked dependencies and
  DuckDB support. Release automation opens formula update PRs with verified checksums.
- `storage.backend: postgres` keeps execution history in PostgreSQL (including Cloud SQL),
  so hosted instances share it and keep it across restarts. Install `axial[postgres]` and
  pass the connection string in `AXIAL_STORAGE_URL`. Existing projects need no changes.
- `server.trust_iap_user: true` records the signed-in user that Identity-Aware Proxy reports
  on each dataset request, and shows it in Inspect and Query usage. Off by default and
  always off in `axial up`. It stores viewers' emails in history; see `axial guide config`.

**History schema 2:** request history gains a `user_email` column. The engine adds it to
SQLite and PostgreSQL history when it opens them; older rows have no user. After that,
older engines refuse the history database, so upgrade every instance that shares it.

**Upgrade notes:** run `axial guide migration`. Stop the old engine, rename
`bqdash.yaml` to `axial.yaml`, set `version: 2`, move `.bqdash/` to `.axial/` and update
`storage.path`, ignore rules, environment variables and scripts. Keep custom history
paths. The rename leaves history as it is. `axial agents sync` upgrades old managed markers
without `--force`, preserving handwritten files and `CONTEXT.md`. Release tags through
`v0.1.2` retain the old package name; the initial Homebrew formula pins the rename commit.

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
