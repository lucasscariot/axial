"""`axial check`: validate dashboards, their saved queries and fixtures without touching BigQuery."""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from .config import Config, engine_version
from .registry import (COLUMN_TYPES, COMMON_TILE_KEYS, DASHBOARD_KEYS, DATE_RANGE_KEYS, FORMATS, NUMERIC_TYPES,
                       PARAM_KEYS, PARAM_TYPES, QUERY_KEYS, REDUCERS, SNIPPET_NAME, TILE_KEYS, TILE_TYPES,
                       Dashboard, Query, Registry, includes)
from .sqlcheck import check_read_only_select, referenced_params


@dataclass
class Issue:
    level: str  # error | warning
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.level.upper():7} {self.where}: {self.message}"


def _unknown(keys, known: set[str]) -> list[str]:
    return sorted(str(k) for k in keys if k not in known)


def _use(known: set[str]) -> str:
    return f"use one of {', '.join(sorted(known))}"


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
    for k in _unknown(q.raw, QUERY_KEYS):
        err(f"unknown key {k!r} ({_use(QUERY_KEYS)})")
    for name, spec in (q.raw.get("params") or {}).items():
        if isinstance(spec, dict):
            for k in _unknown(spec, PARAM_KEYS):
                err(f"param {name}: unknown key {k!r} ({_use(PARAM_KEYS)})")
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
    for c in q.labels:
        if c not in q.columns:
            err(f"labels.{c}: not a declared column")
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
    for k in _unknown(d.raw, DASHBOARD_KEYS):
        err(f"unknown key {k!r} ({_use(DASHBOARD_KEYS)})")
    for k in _unknown(d.raw.get("date_range") or {}, DATE_RANGE_KEYS):
        err(f"date_range: unknown key {k!r} ({_use(DATE_RANGE_KEYS)})")
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
        allowed = COMMON_TILE_KEYS | TILE_KEYS[t.type]
        for k in _unknown(t.raw, allowed):
            err(f"unknown key {k!r} for a {t.type} tile ({_use(allowed)})", tw)
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
            if any(e.path.parent == reg.cfg.queries_dir and e.path.stem == t.query for e in reg.errors):
                continue  # the query exists but failed to load; that error is already reported
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
        elif t.type == "funnel":
            need_col("x")
            need_col("y", numeric=True)
            if isinstance(t.raw.get("y"), list) and len(t.raw["y"]) > 1:
                err("funnel tiles take a single y column", tw)
            if t.raw.get("series") is not None:
                err("funnel tiles do not take series", tw)
        elif t.type == "table":
            if t.raw.get("columns") is not None:
                need_col("columns")
            for c in (t.raw.get("formats") or {}):
                if c not in cols:
                    err(f"formats.{c}: not a column of {q.id}", tw)
    return out


def _fits(value: Any, kind: str) -> bool:
    """Whether a fixture value is what the warehouse returns for a column of this type."""
    if value is None:
        return True
    if kind == "INT64":
        return type(value) is int
    if kind == "BOOL":
        return type(value) is bool
    if kind == "STRING":
        return isinstance(value, str)
    if kind in NUMERIC_TYPES:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return False
        if isinstance(value, float):
            return math.isfinite(value)
        try:
            return Decimal(str(value)).is_finite()
        except InvalidOperation:
            return False
    if kind in ("DATE", "DATETIME", "TIMESTAMP") and isinstance(value, str):
        try:
            (dt.date if kind == "DATE" else dt.datetime).fromisoformat(value)
            return True
        except ValueError:
            return False
    return False


MAX_FIXTURE_ISSUES = 5  # per fixture: one broken column would otherwise report every row


def check_fixture(q: Query, cfg: Config) -> list[Issue]:
    """A saved fixture must hold rows with exactly the query's declared columns and types."""
    path = cfg.fixtures_dir / f"{q.id}.json"
    if not path.exists() or not q.columns:
        return []
    where = f"fixtures/{q.id}"
    try:
        data = json.loads(path.read_text())
    except (ValueError, UnicodeDecodeError) as e:
        return [Issue("error", where, f"invalid JSON: {e}")]
    rows = data.get("rows") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return [Issue("error", where, "expected a JSON list of rows")]
    problems: list[str] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            problems.append(f"row {i} must be an object")
            continue
        missing, extra = sorted(set(q.columns) - set(row)), sorted(set(row) - set(q.columns))
        if missing or extra:
            parts = ([f"missing {', '.join(missing)}"] if missing else []) + \
                    ([f"not a column of {q.id}: {', '.join(extra)}"] if extra else [])
            problems.append(f"row {i}: {'; '.join(parts)}")
            continue
        for c, t in q.columns.items():
            if t in COLUMN_TYPES and not _fits(row[c], t):
                problems.append(f"row {i}: {c} is {row[c]!r}, expected {t} or null")
    out = [Issue("error", where, m) for m in problems[:MAX_FIXTURE_ISSUES]]
    if len(problems) > MAX_FIXTURE_ISSUES:
        out.append(Issue("error", where, f"{len(problems) - MAX_FIXTURE_ISSUES} more problem(s) not shown"))
    return out


def check_layout(cfg: Config) -> list[Issue]:
    """Files the engine would silently ignore: wrong extension, wrong folder or no partner."""
    out: list[Issue] = []

    def err(path: Path, m: str) -> None:
        out.append(Issue("error", str(path.relative_to(cfg.root)), m))

    for folder, loaded in ((cfg.queries_dir, {".yaml", ".sql"}), (cfg.dashboards_dir, {".yaml"}),
                           (cfg.fixtures_dir, {".json"})):
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if path.parent != folder:
                if suffix in loaded | {".yml"}:
                    err(path, f"files in subfolders are not loaded; move it to {folder.name}/")
            elif suffix == ".yml" and ".yaml" in loaded:
                err(path, "rename to .yaml; .yml files are not loaded")
            elif suffix == ".sql" and not path.with_suffix(".yaml").exists():
                err(path, f"no {path.stem}.yaml next to it, so this query is not loaded")
            elif suffix == ".json" and not (cfg.queries_dir / f"{path.stem}.yaml").exists():
                err(path, f"no query named {path.stem}; a fixture is matched to its query by file name")
    return out


def check_snippets(cfg: Config) -> list[Issue]:
    """snippets/ holds flat <name>.sql files, each included by at least one query."""
    folder = cfg.snippets_dir
    if not folder.is_dir():
        return []
    out: list[Issue] = []
    used: set[str] = set()
    if cfg.queries_dir.is_dir():
        for sql in cfg.queries_dir.glob("*.sql"):
            used.update(includes(sql.read_text()))
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        where = str(path.relative_to(cfg.root))
        if path.parent != folder:
            out.append(Issue("error", where, "files in subfolders are not loaded; move it to snippets/"))
        elif path.suffix != ".sql":
            out.append(Issue("error", where, "snippets/ holds only .sql files; this file is not loaded"))
        elif not SNIPPET_NAME.fullmatch(path.stem):
            out.append(Issue("error", where, "a snippet name may hold only letters, digits and underscores, "
                             "and cannot start with a digit; rename it so a query can include it"))
        elif not path.read_text().strip():
            out.append(Issue("error", where, "snippet is empty"))
        elif path.stem not in used:
            out.append(Issue("warning", where, f"no query includes it; add a {{{{ {path.stem} }}}} line "
                             "to a query's SQL or delete the file"))
    return out


ENGINE_PIN = re.compile(r"^ARG AXIAL_REF=(\S+)", re.M)


def check_engine_pin(cfg: Config) -> list[Issue]:
    """The Dockerfile's AXIAL_REF is the project's engine pin; it must satisfy `engine:`."""
    path = cfg.root / "Dockerfile"
    if not cfg.engine or not path.exists():
        return []
    found = ENGINE_PIN.search(path.read_text())
    if not found:
        return []
    ref = found.group(1)
    try:
        release = Version(ref.removeprefix("v"))
    except InvalidVersion:
        return [Issue("warning", "Dockerfile", f"AXIAL_REF={ref} is not a release tag, so it cannot be checked "
                      f"against engine {cfg.engine!r}; pin a release such as v{engine_version()}")]
    try:
        if release in SpecifierSet(cfg.engine):
            return []
    except InvalidSpecifier:
        return []  # load_config reports it
    return [Issue("error", "Dockerfile", f"AXIAL_REF={ref} is outside engine {cfg.engine!r} in axial.yaml; "
                  "change one so the image and local runs use compatible engines")]


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
        issues += check_layout(reg.cfg)
        issues += check_snippets(reg.cfg)
        issues += check_engine_pin(reg.cfg)
    for q_id in query_ids:
        issues += check_query(reg.queries[q_id])
        issues += check_fixture(reg.queries[q_id], reg.cfg)
    for d in dashboards:
        issues += check_dashboard(d, reg)
    return issues
