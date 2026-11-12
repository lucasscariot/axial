# Changelog

Projects pin a compatible range in `bqdash.yaml` (`engine: ">=0.1,<0.2"`). While the engine
is 0.x, a minor bump (0.1 → 0.2) may need project changes. Each release lists them under
**Upgrade notes**.

## 0.1.0 (unreleased)

First release.

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
- New commands: `bqdash tables`, `schema`, `usage`, `run` and `guide`. `--json` on `check`,
  `dry-run`, `bench` and the new commands. Optional `bigquery.datasets` setting.

**Upgrade notes:** projects created before agent support was added should run
`bqdash agents sync --force` once, to replace the old hand-written `AGENTS.md` with the
managed one.
