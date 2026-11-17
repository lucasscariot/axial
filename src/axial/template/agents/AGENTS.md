# Agent guide for this Axial project

This project holds saved BigQuery SQL (`queries/`) and dashboard definitions (`dashboards/`).
The axial engine renders them and owns all styling. You decide what goes on each page and
how much space it gets.

**Read `CONTEXT.md` first.** It holds this project's trusted tables, metric definitions and
conventions, and it overrides anything generic here.

## Rules

- **Free commands:** `check`, `preview --fixture`, `dry-run`, `tables`, `schema`, `usage`
  and `guide`. Use them freely.
- **Billed commands:** `run`, `bench` and live dashboard loads run billed BigQuery jobs.
  `dry-run` first. If the estimate is above $1, or you'd run more than a handful of jobs,
  ask before running.
- **Publishing:** `deploy` changes cloud resources, IAM and who can see the data. Run it only
  when the user asks; `deploy --dry-run` is free and changes nothing.
- **SQL:** one read-only `SELECT`/`WITH` per query, with `@params`. `end_date` is exclusive.
  Compute rates in SQL over the whole period, never by averaging.
- **Output:** pass `--json` to `check`, `dry-run`, `tables`, `schema`, `usage`, `run` and
  `bench`, then parse the result rather than reading text.
- **Done means:**
  - `axial check` reports 0 errors.
  - You looked at an `axial preview <dashboard> --fixture --screenshot` image.
  - The dry-run bytes are reasonable for the date range.
- **Don't touch:** `.axial/`, the app's local history. Don't edit files marked
  `axial:managed`; `axial agents sync` overwrites them. Put project knowledge in `CONTEXT.md`.

## Reference

`axial guide <topic>` prints the reference for the installed engine version. Topics:
`queries`, `dashboards`, `config`, `cli`, `deploy`, `migration`. Read the relevant topic before writing files;
don't rely on memory.

## Skills

- `build-dashboard`: create or extend a dashboard from a request.
- `optimize-query`: find the costliest or slowest query and measure an improvement.
- `rebuild-dashboard`: recreate an existing dashboard (e.g. Metabase) and verify its numbers.
