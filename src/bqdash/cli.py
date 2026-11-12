"""`bqdash` command line: create a project, launch it, and the authoring loop."""

from __future__ import annotations

import asyncio
import json
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

TEMPLATE_DIR = Path(__file__).parent / "template"
PORT_SEARCH = 20


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
            "or set BQDASH_BIGQUERY_BILLING_PROJECT. (Use --fixture to run without BigQuery.)")
    import google.auth
    from google.auth.exceptions import DefaultCredentialsError

    try:
        google.auth.default(scopes=["https://www.googleapis.com/auth/bigquery"])
    except DefaultCredentialsError:
        raise click.ClickException(
            "no Google credentials found. Run `gcloud auth application-default login` "
            "(or use --fixture to run without BigQuery).") from None


def _warehouse(cfg: Config):
    from .bq import BigQueryWarehouse

    _require_bigquery(cfg)
    return BigQueryWarehouse(cfg.billing_project, cfg.location)


def _free_port(preferred: int) -> int:
    for port in range(preferred, preferred + PORT_SEARCH):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise click.ClickException(f"no free port in {preferred}-{preferred + PORT_SEARCH - 1}; pass --port")


def _server(cfg: Config, fixture: bool, port: int):
    import uvicorn

    from .app import create_app

    warehouse = None if fixture else _warehouse(cfg)
    app = create_app(cfg, mode="fixture" if fixture else "live", warehouse=warehouse)
    return uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))


def _start_in_thread(server) -> threading.Thread:
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        if not t.is_alive():
            raise click.ClickException("the server failed to start")
        time.sleep(0.05)
    return t


@click.group(cls=Group)
@click.version_option(engine_version(), prog_name="bqdash")
def main():
    """Local, Git-backed BigQuery dashboards."""


# --- project lifecycle -------------------------------------------------------


def _engine_range(v: str) -> str:
    parts = [int(x) for x in v.split(".")[:2]]
    major, minor = (parts + [0, 0])[:2]
    return f">={major}.{minor},<{major}.{minor + 1}" if major == 0 else f">={major}.{minor},<{major + 1}"


@main.command()
@click.argument("directory", type=click.Path(file_okay=False, path_type=Path))
@click.option("--billing-project", help="GCP project that runs and pays for queries")
@click.option("--location", help="BigQuery job location, e.g. EU, US, europe-west2")
def init(directory: Path, billing_project, location):
    """Create a dashboard project in DIRECTORY."""
    directory = directory.resolve()
    files = [p for p in TEMPLATE_DIR.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    targets = {p: directory / str(p.relative_to(TEMPLATE_DIR)).replace("gitignore.txt", ".gitignore") for p in files}
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
            dst.write_text(text)
        else:
            shutil.copyfile(src, dst)
    for d in ("queries", "dashboards"):
        (directory / d).mkdir(exist_ok=True)
    click.echo(f"Created bqdash project in {directory}\n")
    click.echo("Next:")
    click.echo(f"  cd {directory}")
    click.echo("  bqdash up --fixture        # see the example dashboard with sample data")
    if not billing_project:
        click.echo(f"  # set bigquery.billing_project and location in {CONFIG_FILE}")
    click.echo("  gcloud auth application-default login")
    click.echo("  bqdash up                  # run against BigQuery")


@main.command()
@click.option("--fixture", is_flag=True, help="serve representative data instead of querying BigQuery")
@click.option("--port", type=int, help="port to use (default: server.port, or the next free one)")
@click.option("--no-open", is_flag=True, help="do not open a browser")
def up(fixture, port, no_open):
    """Check the project and launch its dashboards on 127.0.0.1."""
    cfg, reg = _project()
    errors = _print_issues(run_checks(reg))
    if errors:
        raise click.ClickException(f"{errors} error(s) in dashboards or queries; fix them or run `bqdash check`")
    server = _server(cfg, fixture, port or _free_port(cfg.port))
    port = server.config.port
    url = f"http://127.0.0.1:{port}/"
    if len(reg.dashboards) == 1:
        url += f"d/{next(iter(reg.dashboards))}"
    click.echo(f"bqdash {engine_version()} · {cfg.root.name} · {'fixture data' if fixture else cfg.billing_project}")
    click.echo(url)
    if not no_open:
        def open_when_ready():
            while not server.started and not server.should_exit:
                time.sleep(0.05)
            if server.started:
                webbrowser.open(url)

        threading.Thread(target=open_when_ready, daemon=True).start()
    server.run()


# --- authoring ---------------------------------------------------------------


@main.command()
@click.argument("dashboard", required=False)
def check(dashboard):
    """Validate a dashboard (or everything): query refs, tile fields, filter bindings, SQL."""
    _, reg = _project()
    issues = run_checks(reg, dashboard)
    errors = _print_issues(issues)
    click.echo(f"{errors} error(s), {len(issues) - errors} warning(s)")
    sys.exit(1 if errors else 0)


@main.command()
@click.argument("dashboard")
@click.option("--fixture", is_flag=True, help="use representative data; no warehouse query")
@click.option("--port", type=int, help="port to use (default: next free after server.port + 1)")
@click.option("--screenshot", type=click.Path(dir_okay=False), help="save a PNG and exit (needs playwright)")
@click.option("--width", default=1440, show_default=True)
@click.option("--start", help="start_date for the screenshot")
@click.option("--end", help="end_date (exclusive) for the screenshot")
def preview(dashboard, fixture, port, screenshot, width, start, end):
    """Render one dashboard, optionally to a PNG."""
    cfg, reg = _project()
    _print_issues(run_checks(reg, dashboard))
    if dashboard not in reg.dashboards:
        sys.exit(1)
    server = _server(cfg, fixture, port or _free_port(cfg.port + 1))
    qs = "&".join(f"{k}={v}" for k, v in (("start", start), ("end", end)) if v)
    url = f"http://127.0.0.1:{server.config.port}/d/{dashboard}" + (f"?{qs}" if qs else "")
    if not screenshot:
        click.echo(url)
        server.run()
        return
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise click.ClickException("screenshots need playwright: install bqdash with the [screenshot] extra, "
                                   "then run `playwright install chromium`") from None
    t = _start_in_thread(server)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": width, "height": 900})
            page.goto(url)
            page.wait_for_selector("[data-dashboard-ready='true']", timeout=120_000)
            page.screenshot(path=screenshot, full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        t.join(timeout=5)
    click.echo(f"wrote {screenshot}")


@main.command("dry-run")
@click.argument("query_id")
@click.option("--start", help="start_date (inclusive), default 7 days ago")
@click.option("--end", help="end_date (exclusive), default tomorrow")
@click.option("--param", "params", multiple=True, help="extra NAME=VALUE")
def dry_run(query_id, start, end, params):
    """Estimate bytes for a saved query with BigQuery's dry run (free)."""
    from .executor import bq_params
    from .sqlcheck import check_read_only_select

    cfg, reg = _project()
    q, values = _query_values(reg, query_id, start, end, params)
    problems = check_read_only_select(q.sql)
    if problems:
        raise click.ClickException("; ".join(problems))
    r = _warehouse(cfg).dry_run(q.sql, bq_params(q, values))
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
    click.echo("note: estimates on clustered tables can be conservative; compare with `bqdash bench`.")
    if r.statement_type and r.statement_type != "SELECT":
        raise click.ClickException(f"statement type {r.statement_type} is not allowed")
    if cfg.maximum_bytes_billed and r.bytes_processed > cfg.maximum_bytes_billed:
        raise click.ClickException("estimate exceeds maximum_bytes_billed; the job would fail")


@main.command()
@click.argument("query_id")
@click.option("--start", help="start_date (inclusive), default 7 days ago")
@click.option("--end", help="end_date (exclusive), default tomorrow")
@click.option("--param", "params", multiple=True, help="extra NAME=VALUE")
@click.option("--runs", default=1, show_default=True)
def bench(query_id, start, end, params, runs):
    """Run a query with both caches bypassed and record it, for before/after comparisons."""
    from .executor import Executor, git_revision
    from .store import open_store
    from .usage import query_runs

    cfg, reg = _project()
    q, values = _query_values(reg, query_id, start, end, params)
    store = open_store(cfg)
    exe = Executor(cfg, store, _warehouse(cfg), app_revision=git_revision(cfg.root))

    async def go():
        for i in range(runs):
            s = await exe.run(q, values, dashboard_id=None, bypass_cache=True)
            j = store.get_job(s.result.job_id)
            click.echo(f"run {i + 1}: job {j['job_id']}  exec {j['exec_ms']} ms  queue {j['queue_ms']} ms  "
                       f"billed {_fmt_bytes(j['bytes_billed'])}  slot {j['slot_ms']} ms  rows {len(s.result.rows)}")

    asyncio.run(go())
    out = query_runs(store, cfg, q.id, 0, time.time() + 1)
    pj = json.loads(normalize_params(values))
    click.echo("\nSQL versions for these params, BigQuery cache bypassed:")
    for v in out["versions"]:
        if v["bq_cache_bypassed"] and v["params"] == pj:
            mark = "  <- current" if v["sql_hash"] == q.sql_hash else ""
            click.echo(f"  {v['sql_hash'][:12]}  runs {v['runs']:>3}  median exec {v['median_exec_ms']} ms  "
                       f"median billed {_fmt_bytes(v['median_bytes_billed'] or 0)}  "
                       f"median slot {v['median_slot_ms']} ms{mark}")


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
