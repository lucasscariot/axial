# Commands

**Free:** these never start a billed query job.

| command | |
|---|---|
| `axial check [dashboard] [--json]` | validate queries and dashboards |
| `axial preview <dashboard> --fixture --screenshot out.png` | render with representative data |
| `axial preview <dashboard> --fixture --pdf out.pdf` | A4 landscape PDF, as the app's Export PDF prints it |
| `axial dry-run <query> [--start --end] [--json]` | byte estimate, est. cost, tables read |
| `axial tables [project.dataset] [--json]` | list datasets / tables (metadata only) |
| `axial schema <project.dataset.table> [--json]` | columns, partitioning, clustering |
| `axial usage [query] [--days N] [--json]` | recorded requests, jobs, cost, SQL versions |
| `axial guide [topic]` | this reference, matching the installed engine |
| `axial agents sync [--check]` | refresh AGENTS.md, CLAUDE.md and skills |

**Billed:** these run BigQuery jobs. Check `dry-run` first.
With `warehouse: duckdb` they run against the local file and cost nothing; `dry-run` then
checks the query plan instead of estimating bytes.

| command | |
|---|---|
| `axial run <query> [--start --end] [--param K=V] [--json]` | run once, print rows (uses the app cache rules) |
| `axial bench <query> [--start --end] [--runs N] [--json]` | run with both caches bypassed and record it |
| `axial up` | launch the app; every dashboard load can run jobs |

`--start` is inclusive and `--end` is exclusive (YYYY-MM-DD). They default to the last 7 days.
