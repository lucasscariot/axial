"""`dash` command line: the authoring loop for humans and coding agents."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time

import click

from .checks import run_checks
from .config import load_config
from .dbt import DbtIndex
from .registry import DateRange, ParamError, Registry, resolve_params

DEFAULT_PORT = 8765


def _ctx():
    cfg = load_config()
    return cfg, Registry(cfg), DbtIndex.load(cfg)


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "-"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return str(n)


def _query_values(reg: Registry, query_id: str, start: str | None, end: str | None, params: tuple[str, ...]):
    q = reg.queries.get(query_id)
    if q is None:
        raise click.ClickException(f"no query {query_id!r} (known: {', '.join(sorted(reg.queries))})")
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


@click.group()
def main():
    """Local, Git-backed BigQuery dashboards."""


# --- dbt ---------------------------------------------------------------------


@main.group()
def models():
    """Explore dbt models from the production manifest."""


@models.command("search")
@click.argument("text", nargs=-1, required=True)
@click.option("--limit", default=8)
@click.option("--json", "as_json", is_flag=True)
def models_search(text, limit, as_json):
    """Find dbt models by name, description, or column."""
    cfg, _, dbt = _ctx()
    if dbt.loaded_from is None:
        raise click.ClickException(f"dbt manifest not found at {cfg.dbt_manifest}")
    hits = dbt.search(" ".join(text), limit)
    if as_json:
        click.echo(json.dumps([m.to_dict() for _, m in hits], indent=2))
        return
    if not hits:
        click.echo("no matching models")
    for _, m in hits:
        click.secho(m.unique_id, bold=True)
        click.echo(f"  relation: {m.relation_name or '-'}")
        if m.description:
            click.echo(f"  {m.description.strip()}")
        for c in m.columns.values():
            desc = f"  {c.description.strip()}" if c.description else ""
            click.echo(f"    {c.name:<28} {(c.type or ''):<10}{desc}")
        if m.depends_on:
            click.echo(f"  upstream: {', '.join(m.depends_on)}")
        click.echo()


@models.command("show")
@click.argument("unique_id")
def models_show(unique_id):
    """Print one model as JSON."""
    _, _, dbt = _ctx()
    m = dbt.models.get(unique_id) or next((x for x in dbt.models.values() if x.name == unique_id), None)
    if m is None:
        raise click.ClickException(f"no model {unique_id!r}")
    click.echo(json.dumps(m.to_dict(), indent=2))


# --- authoring ---------------------------------------------------------------


@main.command()
@click.argument("dashboard", required=False)
def check(dashboard):
    """Validate a dashboard (or everything): model IDs, query refs, tile fields, filter bindings."""
    _, reg, dbt = _ctx()
    issues = run_checks(reg, dbt, dashboard)
    for i in issues:
        click.secho(str(i), fg="red" if i.level == "error" else "yellow")
    errors = sum(1 for i in issues if i.level == "error")
    warnings = len(issues) - errors
    click.echo(f"{errors} error(s), {warnings} warning(s)")
    sys.exit(1 if errors else 0)


@main.command("dry-run")
@click.argument("query_id")
@click.option("--start", help="start_date (inclusive), default 7 days ago")
@click.option("--end", help="end_date (exclusive), default tomorrow")
@click.option("--param", "params", multiple=True, help="extra NAME=VALUE")
def dry_run(query_id, start, end, params):
    """Estimate bytes for a saved query with BigQuery's dry run."""
    from .bq import BigQueryWarehouse
    from .executor import bq_params
    from .sqlcheck import check_read_only_select

    cfg, reg, _ = _ctx()
    q, values = _query_values(reg, query_id, start, end, params)
    problems = check_read_only_select(q.sql)
    if problems:
        raise click.ClickException("; ".join(problems))
    wh = BigQueryWarehouse(cfg.billing_project, cfg.location)
    r = wh.dry_run(q.sql, bq_params(q, values))
    click.echo(f"query:          {q.id}  sql {q.sql_hash[:12]}")
    click.echo(f"params:         {json.dumps({k: str(v) for k, v in values.items()})}")
    click.echo(f"statement type: {r.statement_type}")
    click.echo(f"bytes estimate: {_fmt_bytes(r.bytes_processed)}")
    if cfg.billing_mode == "on_demand":
        click.echo(f"est. cost:      ${r.bytes_processed / 2**40 * cfg.price_per_tib_usd:.4f} at ${cfg.price_per_tib_usd}/TiB")
    if cfg.maximum_bytes_billed:
        pct = r.bytes_processed / cfg.maximum_bytes_billed * 100
        click.echo(f"byte cap:       {_fmt_bytes(cfg.maximum_bytes_billed)} ({pct:.0f}% used)")
    click.echo(f"tables:         {', '.join(r.referenced_tables) or '-'}")
    click.echo("note: estimates on clustered tables can be conservative; compare with `dash bench`.")
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
    from .bq import BigQueryWarehouse
    from .executor import Executor, git_revision
    from .registry import normalize_params
    from .store import Store
    from .usage import query_runs

    cfg, reg, dbt = _ctx()
    q, values = _query_values(reg, query_id, start, end, params)
    store = Store(cfg.history_db)
    exe = Executor(cfg, store, BigQueryWarehouse(cfg.billing_project, cfg.location),
                   dbt_revision=dbt.revision, app_revision=git_revision(cfg.root))

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
                       f"median billed {_fmt_bytes(int(v['median_bytes_billed'] or 0))}  "
                       f"median slot {v['median_slot_ms']} ms{mark}")


@main.command()
def reconcile():
    """Fetch final stats for jobs the app stopped waiting for."""
    from .bq import BigQueryWarehouse
    from .executor import Executor
    from .store import Store

    cfg = load_config()
    store = Store(cfg.history_db)
    n = Executor(cfg, store, BigQueryWarehouse(cfg.billing_project, cfg.location)).reconcile_once()
    click.echo(f"updated {n} job(s)")


# --- serving -----------------------------------------------------------------


def _serve(fixture: bool, port: int):
    import uvicorn

    from .app import create_app

    cfg = load_config()
    app = create_app(cfg, mode="fixture" if fixture else "live")
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    return uvicorn.Server(config)


@main.command()
@click.option("--port", default=DEFAULT_PORT, show_default=True)
@click.option("--fixture", is_flag=True, help="serve representative data instead of querying BigQuery")
def serve(port, fixture):
    """Run the dashboard app on 127.0.0.1."""
    server = _serve(fixture, port)
    click.echo(f"http://127.0.0.1:{port}/  ({'fixture' if fixture else 'live'} mode)")
    server.run()


@main.command()
@click.argument("dashboard")
@click.option("--fixture", is_flag=True, help="use representative data; no warehouse query")
@click.option("--port", default=DEFAULT_PORT + 1, show_default=True)
@click.option("--screenshot", type=click.Path(dir_okay=False), help="save a PNG and exit (needs playwright)")
@click.option("--width", default=1440, show_default=True)
@click.option("--start", help="start_date for the screenshot")
@click.option("--end", help="end_date (exclusive) for the screenshot")
def preview(dashboard, fixture, port, screenshot, width, start, end):
    """Check and render a dashboard, optionally to a PNG."""
    _, reg, dbt = _ctx()
    issues = run_checks(reg, dbt, dashboard)
    for i in issues:
        click.secho(str(i), fg="red" if i.level == "error" else "yellow", err=True)
    if any(i.level == "error" for i in issues) and dashboard not in reg.dashboards:
        sys.exit(1)
    server = _serve(fixture, port)
    qs = "&".join(f"{k}={v}" for k, v in (("start", start), ("end", end)) if v)
    url = f"http://127.0.0.1:{port}/d/{dashboard}" + (f"?{qs}" if qs else "")
    if not screenshot:
        click.echo(url)
        server.run()
        return
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise click.ClickException("screenshots need playwright: uv sync --extra screenshot && "
                                   "uv run playwright install chromium") from None
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.05)
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


if __name__ == "__main__":
    main()
