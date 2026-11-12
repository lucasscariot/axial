# Working on the bqdash engine

This repo is the **engine** only. It must never contain business queries, project IDs, table
names or other data from a real dashboard project. Projects live in their own repos, created
with `bqdash init`. The guide for authoring dashboards ships to projects as
`src/bqdash/template/AGENTS.md`.

- **Python:** `src/bqdash/`, which holds the FastAPI app, executor, `HistoryStore` and the
  CLI. **Frontend:** `web/` (React + ECharts).
- `src/bqdash/web_dist/` is the committed UI build. After any change in `web/`, run
  `make web` and commit the result. CI fails if it's stale.
- `src/bqdash/template/` is what `bqdash init` copies. The template's `.gitignore` is stored
  as `gitignore.txt`. Keep the example passing `bqdash check`; `tests/test_cli.py` enforces it.
- Tests run against `tests/project/` and a fake warehouse (`tests/conftest.py`). Run them
  with `make test`.
- Config format changes: bump `SCHEMA_VERSION` in `config.py` and describe the migration in
  `CHANGELOG.md`. History schema changes: bump `SCHEMA_VERSION` in `store.py`.
- Use Conventional Commits.
