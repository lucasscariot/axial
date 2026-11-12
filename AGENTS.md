# Working on the bqdash engine

This repo is the **engine** only. It must never contain business queries, project IDs, table
names or other data from a real dashboard project. Projects live in their own repos, created
with `bqdash init`. The guide for agents authoring dashboards ships to projects
from `src/bqdash/template/agents/`.

- **Python:** `src/bqdash/`, which holds the FastAPI app, executor, `HistoryStore` and the
  CLI. **Frontend:** `web/` (React + ECharts).
- `src/bqdash/web_dist/` is the committed UI build. After any change in `web/`, run
  `make web` and commit the result. CI fails if it's stale.
- `src/bqdash/template/project/` is what `bqdash init` copies. The template's `.gitignore`
  is stored as `gitignore.txt`. Keep the example passing `bqdash check`; `tests/test_cli.py`
  enforces it.
- `src/bqdash/template/agents/` holds the managed agent files: `AGENTS.md`, `CLAUDE.md` and
  `skills/<name>/SKILL.md`. Each skill is copied to both `.agents/skills/` (Codex) and
  `.claude/skills/` (Claude Code).
  - Keep skills short; put reference detail in `src/bqdash/guide/*.md`, which `bqdash guide`
    prints.
  - Tests fail if a skill mentions a command that doesn't exist, or the guide misses a tile
    type, reducer or format.
- Tests run against `tests/project/` and a fake warehouse (`tests/conftest.py`). Run them
  with `make test`.
- Config format changes: bump `SCHEMA_VERSION` in `config.py` and describe the migration in
  `CHANGELOG.md`. History schema changes: bump `SCHEMA_VERSION` in `store.py`.
- Use Conventional Commits.
