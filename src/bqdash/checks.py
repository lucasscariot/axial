"""`bqdash check`: validate dashboards and their saved queries without touching BigQuery."""

from __future__ import annotations

from dataclasses import dataclass

from .registry import (COLUMN_TYPES, FORMATS, NUMERIC_TYPES, PARAM_TYPES, REDUCERS, TILE_TYPES, Dashboard,
                       Query, Registry)
from .sqlcheck import check_read_only_select, referenced_params


@dataclass
class Issue:
    level: str  # error | warning
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.level.upper():7} {self.where}: {self.message}"


def check_query(q: Query) -> list[Issue]:
    out: list[Issue] = []
    where = f"queries/{q.id}"

    def err(m: str) -> None:
        out.append(Issue("error", where, m))

    def warn(m: str) -> None:
        out.append(Issue("warning", where, m))

    if not q.sql_path.exists():
        err(f"missing SQL file {q.sql_path.name}")
        return out
    for p in check_read_only_select(q.sql):
        err(p)
    for p in q.params.values():
        if p.type not in PARAM_TYPES:
            err(f"param {p.name}: unsupported type {p.type} (use one of {', '.join(sorted(PARAM_TYPES))})")
    used = referenced_params(q.sql)
    for name in sorted(used - set(q.params)):
        err(f"SQL uses @{name} but it is not declared under params")
    for name in sorted(set(q.params) - used):
        warn(f"param {name} is declared but not used in the SQL")
    if not q.columns:
        err("columns must declare the output columns and their types")
    for c, t in q.columns.items():
        if t not in COLUMN_TYPES:
            err(f"column {c}: unsupported type {t}")
    for c, f in q.formats.items():
        if c not in q.columns:
            err(f"formats.{c}: not a declared column")
        if f not in FORMATS:
            err(f"formats.{c}: unknown format {f!r} (use one of {', '.join(sorted(FORMATS))})")
    if q.cache_ttl_seconds < 0:
        err("cache_ttl_seconds must be >= 0")
    return out


def check_dashboard(d: Dashboard, reg: Registry) -> list[Issue]:
    out: list[Issue] = []
    where = f"dashboards/{d.id}"

    def err(m: str, w: str = where) -> None:
        out.append(Issue("error", w, m))

    def warn(m: str, w: str = where) -> None:
        out.append(Issue("warning", w, m))

    if not d.tiles:
        err("dashboard has no tiles")
    dr = d.date_range
    if dr is not None:
        if dr.default_days < 1 or dr.max_days < 1:
            err("date_range.default_days and max_days must be >= 1")
        if dr.default_days > dr.max_days:
            err("date_range.default_days exceeds max_days")

    for q_id in d.query_ids:
        q = reg.queries.get(q_id)
        if q is None:
            continue
        bound = {dr.start_param, dr.end_param} if dr else set()
        for name, p in q.params.items():
            if name in bound:
                if p.type != "DATE":
                    err(f"query {q_id}: param {name} is bound to the date filter but has type {p.type}")
            elif p.required:
                err(f"query {q_id}: required param {name} is not bound to any dashboard filter (give it a default)")
        if dr and not (bound & set(q.params)):
            warn(f"query {q_id} has no {dr.start_param}/{dr.end_param} params; its tiles ignore the date filter")
        elif dr and len(bound & set(q.params)) == 1:
            err(f"query {q_id} binds only one of {dr.start_param}/{dr.end_param}")

    for i, t in enumerate(d.tiles):
        tw = f"{where} tile[{i}] ({t.type} {t.query})"
        if t.type not in TILE_TYPES:
            err(f"unknown tile type {t.type!r} (use one of {', '.join(sorted(TILE_TYPES))})", tw)
            continue
        if not 1 <= t.span <= 12:
            err("span must be between 1 and 12", tw)
        if t.type == "text":
            if not isinstance(t.raw.get("markdown"), str) or not t.raw["markdown"].strip():
                err("text tiles need a markdown string", tw)
            if t.query:
                err("text tiles do not take a query", tw)
            continue
        if not t.query:
            err("query is required", tw)
            continue
        q = reg.queries.get(t.query)
        if q is None:
            known = ", ".join(sorted(reg.queries)) or "none"
            err(f"query {t.query!r} does not exist (known: {known})", tw)
            continue
        cols = q.columns

        def need_col(field: str, numeric: bool = False) -> None:
            v = t.raw.get(field)
            names = v if isinstance(v, list) else [v]
            if v is None or v == []:
                err(f"{field} is required", tw)
                return
            for n in names:
                if n not in cols:
                    err(f"{field}: column {n!r} is not in query {q.id} columns ({', '.join(cols)})", tw)
                elif numeric and cols[n] not in NUMERIC_TYPES:
                    err(f"{field}: column {n!r} is {cols[n]}, expected a numeric type", tw)

        fmt = t.raw.get("format")
        if fmt is not None and fmt not in FORMATS:
            err(f"unknown format {fmt!r}", tw)
        if t.type == "kpi":
            need_col("value", numeric=True)
            reduce = t.raw.get("reduce", "single")
            if reduce not in REDUCERS:
                err(f"reduce {reduce!r} not supported (use one of {', '.join(sorted(REDUCERS))}); "
                    "compute rates and averages in SQL", tw)
        elif t.type in ("line", "bar"):
            need_col("x")
            need_col("y", numeric=True)
            if t.raw.get("series") is not None:
                need_col("series")
                if isinstance(t.raw.get("y"), list) and len(t.raw["y"]) > 1:
                    err("use either series or multiple y columns, not both", tw)
        elif t.type == "table":
            if t.raw.get("columns") is not None:
                need_col("columns")
            for c in (t.raw.get("formats") or {}):
                if c not in cols:
                    err(f"formats.{c}: not a column of {q.id}", tw)
    return out


def run_checks(reg: Registry, dashboard_id: str | None = None) -> list[Issue]:
    issues: list[Issue] = [Issue("error", str(e.path.relative_to(reg.cfg.root)), e.message) for e in reg.errors]
    if dashboard_id is not None:
        d = reg.dashboards.get(dashboard_id)
        if d is None:
            return issues + [Issue("error", f"dashboards/{dashboard_id}", "dashboard does not exist "
                                   f"(known: {', '.join(sorted(reg.dashboards)) or 'none'})")]
        dashboards = [d]
        query_ids = [q for q in d.query_ids if q in reg.queries]
    else:
        dashboards = list(reg.dashboards.values())
        query_ids = list(reg.queries)
    for q_id in query_ids:
        issues += check_query(reg.queries[q_id])
    for d in dashboards:
        issues += check_dashboard(d, reg)
    return issues
