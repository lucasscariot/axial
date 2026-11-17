"""Saved queries (.sql + .yaml) and dashboards (.yaml) loaded from the repo."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .config import Config

PARAM_TYPES = {"DATE", "INT64", "FLOAT64", "NUMERIC", "STRING", "BOOL"}
COLUMN_TYPES = PARAM_TYPES | {"TIMESTAMP", "DATETIME", "BIGNUMERIC"}
NUMERIC_TYPES = {"INT64", "FLOAT64", "NUMERIC", "BIGNUMERIC"}
FORMATS = {"integer", "number", "money", "percent", "duration", "date", "text"}
TILE_TYPES = {"kpi", "line", "bar", "funnel", "table", "text"}
REDUCERS = {"sum", "min", "max", "first", "last", "count", "single"}
# Every key a query or dashboard file may hold; `axial check` rejects anything else.
QUERY_KEYS = {"title", "description", "params", "columns", "formats", "labels", "cache_ttl_seconds"}
PARAM_KEYS = {"type", "default"}
DASHBOARD_KEYS = {"title", "description", "date_range", "tiles"}
DATE_RANGE_KEYS = {"default_days", "max_days", "start_param", "end_param"}
COMMON_TILE_KEYS = {"type", "query", "span", "title", "height"}
TILE_KEYS = {
    "kpi": {"value", "reduce", "format"},
    "line": {"x", "y", "series", "format"},
    "bar": {"x", "y", "series", "format"},
    "funnel": {"x", "y", "format"},
    "table": {"columns", "formats"},
    "text": {"markdown"},
}


class ParamError(ValueError):
    """Raised when filter values from the browser fail validation."""


@dataclass
class Param:
    name: str
    type: str
    default: Any = None
    required: bool = True


@dataclass
class Query:
    id: str
    title: str
    sql: str
    sql_path: Path
    yaml_path: Path
    params: dict[str, Param]
    columns: dict[str, str]
    formats: dict[str, str]
    labels: dict[str, str]
    cache_ttl_seconds: int
    description: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def sql_hash(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()

    def meta(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "columns": self.columns,
            "formats": self.formats,
            "labels": self.labels,
            "params": {p.name: p.type for p in self.params.values()},
            "sql_hash": self.sql_hash,
        }


@dataclass
class DateRange:
    default_days: int = 7
    max_days: int = 31
    start_param: str = "start_date"
    end_param: str = "end_date"

    def default(self, today: dt.date | None = None) -> tuple[dt.date, dt.date]:
        """Last `default_days` days including today, as [start, end)."""
        end = (today or dt.date.today()) + dt.timedelta(days=1)
        return end - dt.timedelta(days=self.default_days), end


@dataclass
class Tile:
    type: str
    query: str
    span: int = 12
    title: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class Dashboard:
    id: str
    title: str
    path: Path
    date_range: DateRange | None
    tiles: list[Tile]
    description: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def query_ids(self) -> list[str]:
        seen: list[str] = []
        for t in self.tiles:
            if t.query and t.query not in seen:
                seen.append(t.query)
        return seen


class LoadError(Exception):
    def __init__(self, path: Path, message: str):
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


def _parse_params(raw: Any, path: Path) -> dict[str, Param]:
    params: dict[str, Param] = {}
    for name, spec in (raw or {}).items():
        if isinstance(spec, str):
            params[name] = Param(name, spec.upper())
        elif isinstance(spec, dict) and "type" in spec:
            params[name] = Param(
                name,
                str(spec["type"]).upper(),
                default=spec.get("default"),
                required="default" not in spec,
            )
        else:
            raise LoadError(path, f"param {name!r} must be a type name or {{type, default}}")
    return params


def load_query(yaml_path: Path, cfg: Config) -> Query:
    raw = yaml.safe_load(yaml_path.read_text()) or {}
    if not isinstance(raw, dict):
        raise LoadError(yaml_path, "expected a mapping")
    sql_path = yaml_path.with_suffix(".sql")
    sql = sql_path.read_text() if sql_path.exists() else ""
    return Query(
        id=yaml_path.stem,
        title=raw.get("title", yaml_path.stem),
        description=raw.get("description", ""),
        sql=sql,
        sql_path=sql_path,
        yaml_path=yaml_path,
        params=_parse_params(raw.get("params"), yaml_path),
        columns={k: str(v).upper() for k, v in (raw.get("columns") or {}).items()},
        formats=dict(raw.get("formats") or {}),
        labels={str(k): str(v) for k, v in (raw.get("labels") or {}).items()},
        cache_ttl_seconds=int(raw.get("cache_ttl_seconds", cfg.default_cache_ttl_seconds)),
        raw=raw,
    )


def load_dashboard(path: Path) -> Dashboard:
    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        raise LoadError(path, "expected a mapping")
    dr = raw.get("date_range")
    date_range = None
    if dr is not None:
        if not isinstance(dr, dict):
            raise LoadError(path, "date_range must be a mapping")
        date_range = DateRange(
            default_days=int(dr.get("default_days", 7)),
            max_days=int(dr.get("max_days", 31)),
            start_param=dr.get("start_param", "start_date"),
            end_param=dr.get("end_param", "end_date"),
        )
    tiles = []
    for t in raw.get("tiles") or []:
        if not isinstance(t, dict):
            raise LoadError(path, "each tile must be a mapping")
        tiles.append(
            Tile(
                type=str(t.get("type", "")),
                query=str(t.get("query") or ""),
                span=int(t.get("span", 12)),
                title=t.get("title"),
                raw=t,
            )
        )
    return Dashboard(
        id=path.stem,
        title=raw.get("title", path.stem),
        description=raw.get("description", ""),
        path=path,
        date_range=date_range,
        tiles=tiles,
        raw=raw,
    )


class Registry:
    """Loads queries and dashboards, reloading when files change on disk."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.queries: dict[str, Query] = {}
        self.dashboards: dict[str, Dashboard] = {}
        self.errors: list[LoadError] = []
        self._stamp: tuple = ()
        self.reload()

    def _files(self) -> list[Path]:
        files: list[Path] = []
        for d, pats in ((self.cfg.queries_dir, ("*.yaml", "*.sql")), (self.cfg.dashboards_dir, ("*.yaml",))):
            if d.exists():
                for pat in pats:
                    files.extend(sorted(d.glob(pat)))
        return files

    def _current_stamp(self) -> tuple:
        out = []
        for f in self._files():
            try:
                out.append((str(f), f.stat().st_mtime_ns))
            except FileNotFoundError:
                pass
        return tuple(out)

    def reload_if_changed(self) -> None:
        if self._current_stamp() != self._stamp:
            self.reload()

    def reload(self) -> None:
        queries: dict[str, Query] = {}
        dashboards: dict[str, Dashboard] = {}
        errors: list[LoadError] = []
        if self.cfg.queries_dir.exists():
            for p in sorted(self.cfg.queries_dir.glob("*.yaml")):
                try:
                    queries[p.stem] = load_query(p, self.cfg)
                except (LoadError, yaml.YAMLError, ValueError, TypeError) as e:
                    errors.append(e if isinstance(e, LoadError) else LoadError(p, str(e)))
        if self.cfg.dashboards_dir.exists():
            for p in sorted(self.cfg.dashboards_dir.glob("*.yaml")):
                try:
                    dashboards[p.stem] = load_dashboard(p)
                except (LoadError, yaml.YAMLError, ValueError, TypeError) as e:
                    errors.append(e if isinstance(e, LoadError) else LoadError(p, str(e)))
        self.queries, self.dashboards, self.errors = queries, dashboards, errors
        self._stamp = self._current_stamp()


# --- parameter validation -------------------------------------------------


def _coerce(param: Param, value: Any) -> Any:
    t = param.type
    try:
        if t == "DATE":
            if isinstance(value, dt.date):
                return value
            return dt.date.fromisoformat(str(value))
        if t == "INT64":
            if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
                raise ValueError
            return int(value)
        if t in ("FLOAT64", "NUMERIC"):
            if isinstance(value, bool):
                raise ValueError
            return float(value)
        if t == "BOOL":
            if isinstance(value, bool):
                return value
            if str(value).lower() in ("true", "1"):
                return True
            if str(value).lower() in ("false", "0"):
                return False
            raise ValueError
        if t == "STRING":
            if not isinstance(value, (str, int, float)):
                raise ValueError
            s = str(value)
            if len(s) > 1024:
                raise ParamError(f"{param.name}: string longer than 1024 characters")
            return s
    except ParamError:
        raise
    except (ValueError, TypeError):
        raise ParamError(f"{param.name}: expected {t}, got {value!r}") from None
    raise ParamError(f"{param.name}: unsupported type {t}")


def resolve_params(
    query: Query,
    raw: dict[str, Any],
    date_range: DateRange | None,
) -> dict[str, Any]:
    """Validate browser-supplied values against the query's declared params."""
    unknown = set(raw) - set(query.params)
    if unknown:
        raise ParamError(f"unknown params: {', '.join(sorted(unknown))}")
    values: dict[str, Any] = {}
    for name, p in query.params.items():
        if name in raw and raw[name] is not None:
            values[name] = _coerce(p, raw[name])
        elif not p.required:
            values[name] = None if p.default is None else _coerce(p, p.default)
        else:
            raise ParamError(f"missing required param {name}")
    if date_range is not None:
        s, e = values.get(date_range.start_param), values.get(date_range.end_param)
        if isinstance(s, dt.date) and isinstance(e, dt.date):
            if e <= s:
                raise ParamError(f"{date_range.end_param} (exclusive) must be after {date_range.start_param}")
            if (e - s).days > date_range.max_days:
                raise ParamError(f"date range is {(e - s).days} days; this dashboard allows at most {date_range.max_days}")
    return values


def normalize_params(values: dict[str, Any]) -> str:
    def enc(v: Any) -> Any:
        return v.isoformat() if isinstance(v, dt.date) else v

    return json.dumps({k: enc(v) for k, v in sorted(values.items())}, separators=(",", ":"))
