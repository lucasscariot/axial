# Working on the Axial engine

This repo is the **engine** only. It must never contain business queries, project IDs, table
names or other data from a real dashboard project. Projects live in their own repos, created
with `axial init`. The guide for agents authoring dashboards ships to projects
from `src/axial/template/agents/`.

- **Python:** `src/axial/`, which holds the FastAPI app, executor, `HistoryStore` and the
  CLI. **Frontend:** `web/` (React + ECharts).
- `src/axial/web_dist/` is the committed UI build. After any change in `web/`, run
  `make web` and commit the result. CI fails if it's stale.
- `src/axial/template/project/` is what `axial init` copies. The template's `.gitignore`
  is stored as `gitignore.txt`. Keep the example passing `axial check`; `tests/test_cli.py`
  enforces it.
- `src/axial/template/agents/` holds the managed agent files: `AGENTS.md`, `CLAUDE.md` and
  `skills/<name>/SKILL.md`. Each skill is copied to both `.agents/skills/` (Codex) and
  `.claude/skills/` (Claude Code).
  - Keep skills short; put reference detail in `src/axial/guide/*.md`, which `axial guide`
    prints.
  - Tests fail if a skill mentions a command that doesn't exist, or the guide misses a tile
    type, reducer or format.
- Tests run against `tests/project/` and a fake warehouse (`tests/conftest.py`). Run them
  with `make test`.
- Config format changes: bump `SCHEMA_VERSION` in `config.py` and describe the migration in
  `CHANGELOG.md`. History schema changes: bump `SCHEMA_VERSION` in `store.py`.
- Use Conventional Commits.
