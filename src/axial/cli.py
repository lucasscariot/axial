"""`axial` command line: create a project, launch it, and the authoring loop."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import click

from .checks import Issue, run_checks
from .config import CONFIG_FILE, Config, ConfigError, engine_version, load_config
from .registry import DateRange, ParamError, Registry, normalize_params, resolve_params

TEMPLATE_DIR = Path(__file__).parent / "template" / "project"
GUIDE_DIR = Path(__file__).parent / "guide"
PORT_SEARCH = 20
# the template stores its dotfiles under plain names; init writes them back under their real ones
TEMPLATE_RENAMES = {"gitignore.txt": ".gitignore", "dockerignore.txt": ".dockerignore", "github": ".github"}


def _template_target(relative: Path) -> Path:
    return Path(*(TEMPLATE_RENAMES.get(part, part) for part in relative.parts))


class Group(click.Group):
    """Turns configuration problems into one-line errors instead of tracebacks."""

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except ConfigError as e:
            raise click.ClickException(str(e)) from None


def _project() -> tuple[Config, Registry]:
    cfg = load_config()
    return cfg, Registry(cfg)


def _fmt_bytes(n: float | None) -> str:
    if n is None:
        return "-"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return str(n)


def _json(obj) -> None:
    click.echo(json.dumps(obj, indent=2, default=str))


def _date_window(days: int) -> tuple[str, str]:
    import datetime as dt

    end = dt.date.today() + dt.timedelta(days=1)
    return (end - dt.timedelta(days=days)).isoformat(), end.isoformat()


def _print_issues(issues: list[Issue]) -> int:
    for i in issues:
        click.secho(str(i), fg="red" if i.level == "error" else "yellow", err=True)
    return sum(1 for i in issues if i.level == "error")


def _query_values(reg: Registry, query_id: str, start: str | None, end: str | None, params: tuple[str, ...]):
    q = reg.queries.get(query_id)
    if q is None:
        raise click.ClickException(f"no query {query_id!r} (known: {', '.join(sorted(reg.queries)) or 'none'})")
    raw: dict = {}
    if "start_date" in q.params or "end_date" in q.params:
        ds, de = DateRange().default()
        raw["start_date"] = start or ds.isoformat()
        raw["end_date"] = end or de.isoformat()
    for kv in params:
        k, _, v = kv.partition("=")
        raw[k] = v
    try:
        # CLI runs are bounded by the widest dashboard that uses this query
        ranges = [d.date_range for d in reg.dashboards.values() if d.date_range and query_id in d.query_ids]
        dr = max(ranges, key=lambda r: r.max_days) if ranges else DateRange(max_days=366)
        return q, resolve_params(q, raw, dr)
    except ParamError as e:
        raise click.ClickException(str(e)) from None


def _require_bigquery(cfg: Config) -> None:
    """Fail fast, with a fix-it message, when a live BigQuery session cannot work."""
    if not cfg.billing_project:
        raise click.ClickException(
            f"bigquery.billing_project is not set. Add it to {CONFIG_FILE} "
            "or set AXIAL_BIGQUERY_BILLING_PROJECT. (Use --fixture to run without BigQuery.)")
    import google.auth
    from google.auth.exceptions import DefaultCredentialsError

    try:
        google.auth.default(scopes=["https://www.googleapis.com/auth/bigquery"])
    except DefaultCredentialsError:
        raise click.ClickException(
            "no Google credentials found. Run `gcloud auth application-default login` "
            "(or use --fixture to run without BigQuery).") from None


def _warehouse(cfg: Config):
    from .warehouses import make_warehouse

    if cfg.warehouse == "bigquery":
        _require_bigquery(cfg)
    try:
        return make_warehouse(cfg)
    except ConfigError as e:
        raise click.ClickException(str(e)) from None


def _target(cfg: Config) -> str | None:
    """What live queries run against, for banners: the billing project or the DuckDB file."""
    return f"duckdb {cfg.duckdb_path.name}" if cfg.local and cfg.duckdb_path else cfg.billing_project


def _free_port(preferred: int) -> int:
    for port in range(preferred, preferred + PORT_SEARCH):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise click.ClickException(f"no free port in {preferred}-{preferred + PORT_SEARCH - 1}; pass --port")


def _server(cfg: Config, fixture: bool, port: int, host: str = "127.0.0.1", log_level: str = "warning"):
    import uvicorn

    from .app import create_app

    warehouse = None if fixture else _warehouse(cfg)
    app = create_app(cfg, mode="fixture" if fixture else "live", warehouse=warehouse)
    return uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level=log_level))


def _start_in_thread(server) -> threading.Thread:
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        if not t.is_alive():
            raise click.ClickException("the server failed to start")
        time.sleep(0.05)
    return t


@click.group(cls=Group)
@click.version_option(engine_version(), prog_name="axial")
def main():
    """Local, Git-backed BigQuery dashboards."""


# --- project lifecycle -------------------------------------------------------


def _engine_range(v: str) -> str:
    parts = [int(x) for x in v.split(".")[:2]]
    major, minor = (parts + [0, 0])[:2]
    return f">={major}.{minor},<{major}.{minor + 1}" if major == 0 else f">={major}.{minor},<{major + 1}"


def _duckdb_config(text: str, path: str) -> str:
    """Swap the template's bigquery and billing sections for a duckdb section."""
    start, end = text.index("bigquery:\n"), text.index("execution:\n")
    block = (
        "warehouse: duckdb             # bigquery | duckdb\n\n"
        "duckdb:\n"
        f"  path: {path}               # database file, relative to this project\n"
        "  datasets: []                # schemas agents should explore first, e.g. [mydb.main]\n"
        "  allow_external_access: false  # true lets queries read files and URLs (read_parquet etc.)\n\n"
    )
    return text[:start] + block + text[end:]


@main.command()
@click.argument("directory", type=click.Path(file_okay=False, path_type=Path))
@click.option("--billing-project", help="GCP project that runs and pays for queries")
@click.option("--location", help="BigQuery job location, e.g. EU, US, europe-west2")
@click.option("--duckdb", "duckdb_path", help="query a local DuckDB file instead of BigQuery (path relative to DIRECTORY)")
def init(directory: Path, billing_project, location, duckdb_path):
    """Create a dashboard project in DIRECTORY."""
    directory = directory.resolve()
    files = [p for p in TEMPLATE_DIR.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    targets = {p: directory / _template_target(p.relative_to(TEMPLATE_DIR)) for p in files}
    clashes = [t for t in targets.values() if t.exists()]
    if clashes:
        raise click.ClickException("refusing to overwrite: " + ", ".join(str(c.relative_to(directory)) for c in clashes))
    for src, dst in targets.items():
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.name == CONFIG_FILE:
            text = src.read_text()
            text = text.replace("__ENGINE_RANGE__", _engine_range(engine_version()))
            text = text.replace("__BILLING_PROJECT__", billing_project or "null  # set me")
            text = text.replace("__LOCATION__", location or "US")
            if duckdb_path:
                text = _duckdb_config(text, duckdb_path)
            dst.write_text(text)
        elif src.name == "Dockerfile":
            dst.write_text(src.read_text().replace("__AXIAL_REF__", f"v{engine_version()}"))
        else:
            shutil.copyfile(src, dst)
    for d in ("queries", "dashboards"):
        (directory / d).mkdir(exist_ok=True)
    from . import agents

    agents.apply(directory, agents.plan(directory))
    click.echo(f"Created axial project in {directory}")
    click.echo("  agent files: AGENTS.md, CLAUDE.md, .agents/skills/ (Codex), .claude/skills/ (Claude Code)")
    click.echo("  hosting: Dockerfile, .dockerignore and .github/workflows/check.yml (see `axial guide deploy`)")
    click.echo("  fill in CONTEXT.md so agents know your tables and metric definitions\n")
    click.echo("Next:")
    click.echo(f"  cd {directory}")
    click.echo("  axial up --fixture        # see the example dashboard with sample data")
    if duckdb_path:
        click.echo(f"  axial up                  # run against {duckdb_path}")
        return
    if not billing_project:
        click.echo(f"  # set bigquery.billing_project and location in {CONFIG_FILE}")
    click.echo("  gcloud auth application-default login")
    click.echo("  axial up                  # run against BigQuery")


@main.command()
@click.option("--fixture", is_flag=True, help="serve representative data instead of querying BigQuery")
@click.option("--port", type=int, help="port to use (default: server.port, or the next free one)")
@click.option("--no-open", is_flag=True, help="do not open a browser")
def up(fixture, port, no_open):
    """Check the project and launch its dashboards on 127.0.0.1."""
    cfg, reg = _project()
    errors = _print_issues(run_checks(reg))
    if errors:
        raise click.ClickException(f"{errors} error(s) in dashboards or queries; fix them or run `axial check`")
    if cfg.trust_iap_user:
        # Nothing sits in front of 127.0.0.1, so any local client could claim to be anyone.
        click.secho("server.trust_iap_user is ignored by axial up: requests are not recorded per user",
                    fg="yellow", err=True)
        cfg.trust_iap_user = False
    server = _server(cfg, fixture, port or _free_port(cfg.port))
    port = server.config.port
    url = f"http://127.0.0.1:{port}/"
    if len(reg.dashboards) == 1:
        url += f"d/{next(iter(reg.dashboards))}"
    from . import agents

    if (cfg.root / "AGENTS.md").exists() and agents.stale(cfg.root):
        click.secho("agent files differ from this engine version; run `axial agents sync`", fg="yellow", err=True)
    click.echo(f"axial {engine_version()} · {cfg.root.name} · {'fixture data' if fixture else _target(cfg)}")
    click.echo(url)
    if not no_open:
        def open_when_ready():
            while not server.started and not server.should_exit:
                time.sleep(0.05)
            if server.started:
                webbrowser.open(url)

        threading.Thread(target=open_when_ready, daemon=True).start()
    server.run()


@main.command()
@click.option("--fixture", is_flag=True, help="serve representative data instead of querying BigQuery")
@click.option("--host", default="0.0.0.0", show_default=True, help="interface to bind")
@click.option("--port", type=int, help="port to use (default: $PORT, else server.port)")
def serve(fixture, host, port):
    """Check the project and serve its dashboards for a container or hosted deployment."""
    cfg, reg = _project()
    errors = _print_issues(run_checks(reg))
    if errors:
        raise click.ClickException(f"{errors} error(s) in dashboards or queries; fix them or run `axial check`")
    # Cloud Run and most container platforms pass the port to listen on in $PORT
    env_port = os.environ.get("PORT")
    if port is None and env_port:
        try:
            port = int(env_port)
        except ValueError:
            raise click.ClickException(f"PORT={env_port!r} is not a number") from None
    server = _server(cfg, fixture, port or cfg.port, host=host, log_level="info")
    click.echo(f"axial {engine_version()} · {cfg.root.name} · {'fixture data' if fixture else _target(cfg)}")
    click.echo(f"serving on {host}:{server.config.port}")
    server.run()


@main.command("deploy")
@click.option("--dry-run", is_flag=True, help="print every command without changing anything")
@click.option("--skip-build", is_flag=True, help="reuse the image already built for this commit")
@click.option("--account", help="gcloud account to act as (default: the active one)")
def deploy_command(dry_run, skip_build, account):
    """Build this commit's image and run it on Cloud Run behind IAP, as `deploy:` in axial.yaml describes."""
    from .deploy import deploy

    cfg, reg = _project()
    errors = _print_issues(run_checks(reg))
    if errors:
        raise click.ClickException(f"{errors} error(s) in dashboards or queries; fix them or run `axial check`")
    out = deploy(cfg, account=account, skip_build=skip_build, dry_run=dry_run)
    if dry_run:
        click.echo(f"dry run: nothing was changed. Would deploy {out['image']}")
        return
    _json(out)
    click.echo("Open the URL signed out and signed in as a viewer before sharing it: a signed-out visit "
               "must redirect to Google sign-in.", err=True)


# --- authoring ---------------------------------------------------------------


@main.command()
@click.argument("dashboard", required=False)
@click.option("--json", "as_json", is_flag=True)
def check(dashboard, as_json):
    """Validate a dashboard (or everything): keys, query refs, tile fields, filter bindings, SQL, fixtures."""
    _, reg = _project()
    issues = run_checks(reg, dashboard)
    errors = sum(1 for i in issues if i.level == "error")
    if as_json:
        _json({"errors": errors, "warnings": len(issues) - errors,
               "issues": [{"level": i.level, "where": i.where, "message": i.message} for i in issues]})
    else:
        _print_issues(issues)
        click.echo(f"{errors} error(s), {len(issues) - errors} warning(s)")
    sys.exit(1 if errors else 0)


@main.command()
@click.argument("dashboard")
@click.option("--fixture", is_flag=True, help="use representative data; no warehouse query")
@click.option("--port", type=int, help="port to use (default: next free after server.port + 1)")
@click.option("--screenshot", type=click.Path(dir_okay=False), help="save a PNG and exit (needs playwright)")
@click.option("--pdf", type=click.Path(dir_okay=False), help="save an A4 landscape PDF and exit (needs playwright)")
@click.option("--width", default=1440, show_default=True, help="viewport width for --screenshot")
@click.option("--start", help="start_date for the screenshot or PDF")
@click.option("--end", help="end_date (exclusive) for the screenshot or PDF")
def preview(dashboard, fixture, port, screenshot, pdf, width, start, end):
    """Render one dashboard, optionally to a PNG or a PDF to share."""
    if screenshot and pdf:
        raise click.UsageError("pass --screenshot or --pdf, not both")
    cfg, reg = _project()
    _print_issues(run_checks(reg, dashboard))
    if dashboard not in reg.dashboards:
        sys.exit(1)
    out = screenshot or pdf
    if out:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise click.ClickException("screenshots and PDFs need playwright: install axial with the [screenshot] "
                                       "extra, then run `playwright install chromium`") from None
    server = _server(cfg, fixture, port or _free_port(cfg.port + 1))
    params = [(k, v) for k, v in (("start", start), ("end", end), ("print", "1" if pdf else None)) if v]
    qs = "&".join(f"{k}={v}" for k, v in params)
    url = f"http://127.0.0.1:{server.config.port}/d/{dashboard}" + (f"?{qs}" if qs else "")
    if not out:
        click.echo(url)
        server.run()
        return
    t = _start_in_thread(server)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            # ?print=1 lays the page out at A4 landscape's printable width (see web/src/print.ts)
            page = browser.new_page(viewport={"width": 1030 if pdf else width, "height": 900})
            page.goto(url)
            page.wait_for_selector("[data-dashboard-ready='true']", timeout=120_000)
            if pdf:
                page.wait_for_selector("[data-print-ready='true']", timeout=30_000)
                page.pdf(path=pdf, print_background=True, prefer_css_page_size=True)
            else:
                page.screenshot(path=screenshot, full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        t.join(timeout=5)
    click.echo(f"wrote {out}")


@main.command("dry-run")
@click.argument("query_id")
@click.option("--start", help="start_date (inclusive), default 7 days ago")
@click.option("--end", help="end_date (exclusive), default tomorrow")
@click.option("--param", "params", multiple=True, help="extra NAME=VALUE")
@click.option("--json", "as_json", is_flag=True)
def dry_run(query_id, start, end, params, as_json):
    """Estimate bytes for a saved query with BigQuery's dry run (free)."""
    from .executor import bq_params
    from .sqlcheck import check_read_only_select

    cfg, reg = _project()
    q, values = _query_values(reg, query_id, start, end, params)
    problems = check_read_only_select(q.sql)
    if problems:
        raise click.ClickException("; ".join(problems))
    wh = _warehouse(cfg)
    try:
        r = wh.dry_run(q.sql, bq_params(q, values))
    except ValueError as e:
        raise click.ClickException(f"{q.id}: {e}") from None
    if cfg.local:
        if as_json:
            _json({"query": q.id, "sql_hash": q.sql_hash, "params": {k: str(v) for k, v in values.items()},
                   "warehouse": cfg.warehouse, "statement_type": r.statement_type, "plan_ok": True,
                   "referenced_tables": r.referenced_tables})
            return
        click.echo(f"query:          {q.id}  sql {q.sql_hash[:12]}")
        click.echo(f"params:         {json.dumps({k: str(v) for k, v in values.items()})}")
        click.echo(f"plan:           ok ({cfg.warehouse}, nothing billed)")
        click.echo(f"tables:         {', '.join(r.referenced_tables) or '-'}")
        return
    over_cap = bool(cfg.maximum_bytes_billed and r.bytes_processed > cfg.maximum_bytes_billed)
    not_select = bool(r.statement_type and r.statement_type != "SELECT")
    if as_json:
        _json({"query": q.id, "sql_hash": q.sql_hash, "params": {k: str(v) for k, v in values.items()},
               "statement_type": r.statement_type, "bytes_processed": r.bytes_processed,
               "est_cost_usd": (r.bytes_processed / 2**40 * cfg.price_per_tib_usd)
               if cfg.billing_mode == "on_demand" else None,
               "maximum_bytes_billed": cfg.maximum_bytes_billed, "within_cap": not over_cap,
               "referenced_tables": r.referenced_tables})
        sys.exit(1 if over_cap or not_select else 0)
    click.echo(f"query:          {q.id}  sql {q.sql_hash[:12]}")
    click.echo(f"params:         {json.dumps({k: str(v) for k, v in values.items()})}")
    click.echo(f"statement type: {r.statement_type}")
    click.echo(f"bytes estimate: {_fmt_bytes(r.bytes_processed)}")
    if cfg.billing_mode == "on_demand":
        click.echo(f"est. cost:      ${r.bytes_processed / 2**40 * cfg.price_per_tib_usd:.4f} "
                   f"at ${cfg.price_per_tib_usd}/TiB")
    if cfg.maximum_bytes_billed:
        pct = r.bytes_processed / cfg.maximum_bytes_billed * 100
        click.echo(f"byte cap:       {_fmt_bytes(cfg.maximum_bytes_billed)} ({pct:.0f}% used)")
    click.echo(f"tables:         {', '.join(r.referenced_tables) or '-'}")
    click.echo("note: estimates on clustered tables can be conservative; compare with `axial bench`.")
    if not_select:
        raise click.ClickException(f"statement type {r.statement_type} is not allowed")
    if over_cap:
        raise click.ClickException("estimate exceeds maximum_bytes_billed; the job would fail")


@main.command()
@click.argument("query_id")
@click.option("--start", help="start_date (inclusive), default 7 days ago")
@click.option("--end", help="end_date (exclusive), default tomorrow")
@click.option("--param", "params", multiple=True, help="extra NAME=VALUE")
@click.option("--runs", default=1, show_default=True)
@click.option("--json", "as_json", is_flag=True)
def bench(query_id, start, end, params, runs, as_json):
    """Run a query with both caches bypassed and record it, for before/after comparisons."""
    from .executor import Executor, git_revision
    from .store import open_store
    from .usage import query_runs

    cfg, reg = _project()
    q, values = _query_values(reg, query_id, start, end, params)
    store = open_store(cfg)
    exe = Executor(cfg, store, _warehouse(cfg), app_revision=git_revision(cfg.root))

    jobs: list[dict] = []

    async def go():
        for i in range(runs):
            s = await exe.run(q, values, dashboard_id=None, bypass_cache=True)
            j = store.get_job(s.result.job_id)
            jobs.append({k: j[k] for k in ("job_id", "exec_ms", "queue_ms", "bytes_billed", "slot_ms", "cache_hit")})
            if as_json:
                continue
            cost = "" if cfg.local else f"billed {_fmt_bytes(j['bytes_billed'])}  slot {j['slot_ms']} ms  "
            click.echo(f"run {i + 1}: job {j['job_id']}  exec {j['exec_ms']} ms  queue {j['queue_ms']} ms  "
                       f"{cost}rows {len(s.result.rows)}")

    asyncio.run(go())
    out = query_runs(store, cfg, q.id, 0, time.time() + 1)
    pj = json.loads(normalize_params(values))
    versions = [v for v in out["versions"] if v["bq_cache_bypassed"] and v["params"] == pj]
    if as_json:
        _json({"query": q.id, "sql_hash": q.sql_hash, "params": pj, "runs": jobs,
               "versions": [{**v, "current": v["sql_hash"] == q.sql_hash} for v in versions]})
        return
    click.echo("\nSQL versions for these params, BigQuery cache bypassed:")
    for v in versions:
        mark = "  <- current" if v["sql_hash"] == q.sql_hash else ""
        click.echo(f"  {v['sql_hash'][:12]}  runs {v['runs']:>3}  median exec {v['median_exec_ms']} ms  "
                   f"median billed {_fmt_bytes(v['median_bytes_billed'] or 0)}  "
                   f"median slot {v['median_slot_ms']} ms{mark}")


@main.command()
@click.argument("query_id")
def sql(query_id):
    """Print a saved query's SQL as the warehouse receives it, with snippets included (free)."""
    _, reg = _project()
    q = reg.queries.get(query_id)
    if q is None:
        for e in reg.errors:
            if e.path.parent == reg.cfg.queries_dir and e.path.stem == query_id:
                raise click.ClickException(str(e))
        raise click.ClickException(f"no query {query_id!r} (known: {', '.join(sorted(reg.queries)) or 'none'})")
    click.echo(q.sql, nl=not q.sql.endswith("\n"))


@main.command()
@click.argument("query_id")
@click.option("--start", help="start_date (inclusive), default 7 days ago")
@click.option("--end", help="end_date (exclusive), default tomorrow")
@click.option("--param", "params", multiple=True, help="extra NAME=VALUE")
@click.option("--json", "as_json", is_flag=True)
def run(query_id, start, end, params, as_json):
    """Run a saved query once and print its rows (billed; recorded in history)."""
    from .executor import Executor, git_revision
    from .store import open_store

    cfg, reg = _project()
    q, values = _query_values(reg, query_id, start, end, params)
    store = open_store(cfg)
    exe = Executor(cfg, store, _warehouse(cfg), app_revision=git_revision(cfg.root))
    served = asyncio.run(exe.run(q, values, dashboard_id=None))
    r = served.result
    job = store.get_job(r.job_id) if r.job_id else None
    if as_json:
        _json({"query": q.id, "params": json.loads(r.params_json), "job_id": r.job_id,
               "bytes_billed": job and job["bytes_billed"], "exec_ms": job and job["exec_ms"],
               "bq_cache_hit": r.cache_hit, "truncated": r.truncated, "total_rows": r.total_rows, "rows": r.rows})
        return
    cols = list(q.columns)
    click.echo("\t".join(cols))
    for row in r.rows:
        click.echo("\t".join("" if row.get(c) is None else str(row.get(c)) for c in cols))
    cost = f"exec {job and job['exec_ms']} ms" if cfg.local else f"billed {_fmt_bytes(job and job['bytes_billed'])}"
    click.echo(f"-- {len(r.rows)} of {r.total_rows} rows · job {r.job_id} · {cost}", err=True)


@main.command()
@click.argument("dataset", required=False)
@click.option("--json", "as_json", is_flag=True)
def tables(dataset, as_json):
    """List tables in DATASET, or in bigquery.datasets, or the billing project's datasets (free)."""
    cfg, _ = _project()
    wh = _warehouse(cfg)
    targets = [dataset] if dataset else cfg.datasets
    if not targets:
        rows = wh.list_datasets()
        if as_json:
            _json({"datasets": rows, "hint": "set bigquery.datasets in axial.yaml to list their tables by default"})
        else:
            for d in rows:
                click.echo(d["id"])
            click.echo("(datasets in the billing project; set bigquery.datasets to list tables)", err=True)
        return
    out = {}
    for ds in targets:
        ref = ds if "." in ds else f"{wh.project}.{ds}"
        out[ref] = wh.list_tables(ref)
    if as_json:
        _json({"tables": out})
        return
    for ref, ts in out.items():
        for t in ts:
            part = f" partitioned by {t['partitioning']['field'] or '_PARTITIONTIME'}" if t["partitioning"] else ""
            clus = f" clustered by {', '.join(t['clustering'])}" if t["clustering"] else ""
            click.echo(f"{t['id']}  {t['type']}{part}{clus}")


@main.command()
@click.argument("table")
@click.option("--json", "as_json", is_flag=True)
def schema(table, as_json):
    """Show a table's columns, partitioning and clustering (free)."""
    cfg, _ = _project()
    wh = _warehouse(cfg)
    ref = table if table.count(".") == 2 else f"{wh.project}.{table}"
    t = wh.get_table(ref)
    if as_json:
        _json(t)
        return
    click.secho(f"{t['id']}  {t['type']}", bold=True)
    if t["description"]:
        click.echo(f"  {t['description']}")
    if t["num_rows"] is not None:
        click.echo(f"  {t['num_rows']:,} rows · {_fmt_bytes(t['num_bytes'])}")
    if t["partitioning"]:
        req = " (partition filter required)" if t["require_partition_filter"] else ""
        click.echo(f"  partitioned by {t['partitioning']['field']} ({t['partitioning']['type']}){req}")
    if t["clustering"]:
        click.echo(f"  clustered by {', '.join(t['clustering'])}")

    def show(cols, indent="    "):
        for c in cols:
            desc = f"  {c['description']}" if c.get("description") else ""
            mode = "" if c.get("mode") in (None, "NULLABLE") else f" {c['mode']}"
            click.echo(f"{indent}{c['name']:<32} {c['type']}{mode}{desc}")
            if c.get("fields"):
                show(c["fields"], indent + "  ")

    show(t["columns"])


@main.command()
@click.argument("query_id", required=False)
@click.option("--days", default=7, show_default=True, help="window ending today")
@click.option("--json", "as_json", is_flag=True)
def usage(query_id, days, as_json):
    """Recorded requests, jobs and cost per saved query, or runs and SQL versions for one (free)."""
    import datetime as dt

    from .store import open_store
    from .usage import query_runs, summarize

    cfg, _ = _project()
    s, e = _date_window(days)
    ts = lambda d: dt.datetime.combine(dt.date.fromisoformat(d), dt.time()).timestamp()  # noqa: E731
    store = open_store(cfg)
    if query_id:
        out = query_runs(store, cfg, query_id, ts(s), ts(e), limit=50)
        if as_json:
            _json(out)
            return
        click.echo(f"{query_id}: {out['totals']['jobs']} jobs, {_fmt_bytes(out['totals']['bytes_billed'])} billed "
                   f"in the last {days} days")
        for v in out["versions"]:
            click.echo(f"  sql {v['sql_hash'][:12]}  {'bypassed' if v['bq_cache_bypassed'] else 'cached '}  "
                       f"runs {v['runs']:>3}  median exec {v['median_exec_ms']} ms  "
                       f"median billed {_fmt_bytes(v['median_bytes_billed'])}  {v['params']}")
        return
    out = summarize(store, cfg, ts(s), ts(e))
    if as_json:
        _json(out)
        return
    click.echo(f"{'query':<28}{'requests':>9}{'jobs':>6}{'app hit':>9}{'p95 app':>10}{'p95 bq':>10}{'billed':>12}{'cost':>10}")
    for r in out["queries"]:
        pct = "-" if r["app_cache_hit_rate"] is None else f"{r['app_cache_hit_rate']:.0%}"
        ms = lambda v: "-" if v is None else f"{v:.0f}ms"  # noqa: E731
        cost = "-" if r["est_cost_usd"] is None else f"${r['est_cost_usd']:.4f}"
        click.echo(f"{r['query_id']:<28}{r['requests']:>9}{r['jobs']:>6}{pct:>9}{ms(r['p95_app_ms']):>10}"
                   f"{ms(r['p95_bq_exec_ms']):>10}{_fmt_bytes(r['bytes_billed']):>12}{cost:>10}")


@main.command()
@click.argument("topic", required=False)
def guide(topic):
    """Print the reference: queries, dashboards, config, cli, deploy, migration."""
    topics = sorted(p.stem for p in GUIDE_DIR.glob("*.md"))
    if topic is None:
        click.echo(f"axial {engine_version()} reference. Topics: {', '.join(topics)}\n"
                   "Run `axial guide <topic>`.")
        return
    if topic not in topics:
        raise click.ClickException(f"unknown topic {topic!r} (topics: {', '.join(topics)})")
    click.echo((GUIDE_DIR / f"{topic}.md").read_text())


@main.group()
def agents():
    """Manage the agent files (AGENTS.md, CLAUDE.md, skills) in this project."""


@agents.command("sync")
@click.option("--check", "check_only", is_flag=True,
              help="exit 1 if files are out of date or hand-written; change nothing")
@click.option("--force", is_flag=True, help="also overwrite agent files that are not marked as managed")
def agents_sync(check_only, force):
    """Write or refresh agent files from the installed engine."""
    from . import agents as ag

    cfg = load_config(check_version=False)
    actions = ag.plan(cfg.root, force=force)
    changes = [a for a in actions if a.status in ("create", "update", "remove")]
    skipped = [a for a in actions if a.status == "skip"]
    for a in actions:
        if a.status != "unchanged":
            note = " (not managed by axial; use --force to replace)" if a.status == "skip" else ""
            click.echo(f"{a.status:<8} {a.path}{note}")
    if check_only:
        # A hand-written file in a managed path means agents are not reading the engine's guide.
        parts = ([f"{len(changes)} file(s) out of date"] if changes else []) + \
                ([f"{len(skipped)} file(s) not managed by axial"] if skipped else [])
        click.echo("; ".join(parts) or "up to date")
        sys.exit(1 if parts else 0)
    ag.apply(cfg.root, changes)
    click.echo(f"{len(changes)} file(s) written" if changes else "up to date")


@main.command()
def reconcile():
    """Fetch final stats for jobs the app stopped waiting for."""
    from .executor import Executor
    from .store import open_store

    cfg = load_config()
    n = Executor(cfg, open_store(cfg), _warehouse(cfg)).reconcile_once()
    click.echo(f"updated {n} job(s)")


if __name__ == "__main__":
    main()
