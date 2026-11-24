"""Representative data for `axial preview --fixture`: no warehouse query is made."""

from __future__ import annotations

import datetime as dt
import json
import math
import random
from dataclasses import dataclass, field
from typing import Any

from .config import Config
from .registry import NUMERIC_TYPES, TIME_TYPES, Query, Registry


def returns_one_row(reg: Registry, query_id: str) -> bool:
    """A query some KPI tile reads with `reduce: single` (the default) returns one row by contract."""
    return any(t.type == "kpi" and t.query == query_id and t.raw.get("reduce", "single") == "single"
               for d in reg.dashboards.values() for t in d.tiles)


@dataclass
class TimeSeries:
    """How a line or bar tile plots a query on a time axis, so fixtures can look like readings."""

    x: str
    groups: dict[str, int] = field(default_factory=dict)  # facet and series columns, and how many values each
    step: list[str] = field(default_factory=list)  # y columns drawn as steps: values set and held


def time_series(reg: Registry, query_id: str) -> TimeSeries | None:
    """The first line or bar tile that plots this query against a TIMESTAMP or DATETIME x."""
    q = reg.queries.get(query_id)
    for d in reg.dashboards.values():
        for t in d.tiles:
            x = t.raw.get("x")
            if t.type not in ("line", "bar") or t.query != query_id or not q or q.columns.get(x) not in TIME_TYPES:
                continue
            groups = {c: n for c, n in ((t.raw.get("facet"), 8), (t.raw.get("series"), 3))
                      if isinstance(c, str) and q.columns.get(c) == "STRING"}
            ys = t.raw.get("y") if isinstance(t.raw.get("y"), list) else [t.raw.get("y")]
            step = t.raw.get("step")
            steps = ys if step is True else step if isinstance(step, list) else [step] if isinstance(step, str) else []
            return TimeSeries(x, groups, [c for c in steps if c in q.columns])
    return None


def fixture_rows(cfg: Config, q: Query, values: dict[str, Any], start_param: str = "start_date",
                 end_param: str = "end_date", one_row: bool = False, series: TimeSeries | None = None) -> list[dict]:
    path = cfg.fixtures_dir / f"{q.id}.json"
    if path.exists():
        data = json.loads(path.read_text())
        return data["rows"] if isinstance(data, dict) else data
    return synthesize(q, values, start_param, end_param, one_row, series)


def _dim(c: str, i: int) -> str:
    return f"{c.replace('_', ' ').capitalize()} {'ABCDEFGHIJKL'[i]}"


def _readings(q: Query, ts: TimeSeries, rng: random.Random, t_start: dt.datetime, t_end: dt.datetime) -> list[dict]:
    """Irregular readings per group value: smooth numbers with a daily cycle, BOOL columns true in
    a few contiguous runs (when the numbers rise), step columns held between occasional changes."""
    combos: list[tuple[str, ...]] = [()]
    for g, n in ts.groups.items():
        combos = [c + (_dim(g, i),) for c in combos for i in range(n)]
    span = (t_end - t_start).total_seconds()
    # about 90 readings per series over the range, fewer when many series share the row cap
    every = max(span / max(20, min(90, 1500 // len(combos))), 60.0)
    nums = [c for c, t in q.columns.items() if t in NUMERIC_TYPES]
    bools = [c for c, t in q.columns.items() if t == "BOOL"]
    rows: list[dict] = []
    for combo in combos:
        level = {c: rng.uniform(0.01, 0.05) if q.formats.get(c) == "percent" else rng.uniform(30, 600)
                 if q.formats.get(c) == "duration" else rng.uniform(2, 6) if c in ts.step else rng.uniform(50, 500)
                 for c in nums}
        held = dict(level)
        walk, phase, run_left, runs = 0.0, rng.uniform(0, 6.3), 0, 0
        t = rng.uniform(0, every)
        group_rows: list[dict] = []
        while t < span:
            if run_left == 0 and rng.random() < 0.035:
                run_left, runs = rng.choice((1, 3, 5, 8, 12)), runs + 1
            in_run = run_left > 0
            run_left = max(0, run_left - 1)
            walk = 0.85 * walk + rng.gauss(0, 0.06)
            when = t_start + dt.timedelta(seconds=int(t))
            cycle = 0.25 * math.sin(phase + 2 * math.pi * t / 86400)
            row: dict[str, Any] = dict(zip(ts.groups, combo))
            for c in nums:
                if c in ts.step:
                    if rng.random() < 0.06 or (in_run and held[c] <= level[c]):
                        held[c] = max(1.0, level[c] + rng.choice((-1, 0, 1, 2)) + (2 if in_run else 0))
                    v = held[c]
                else:
                    v = level[c] * max(0.05, 1 + cycle + walk) * (1.7 if in_run else 1)
                if q.formats.get(c) == "percent":
                    v = min(v, 0.99)
                row[c] = round(v) if q.columns[c] == "INT64" else round(v, 4 if q.formats.get(c) == "percent" else 2)
            for c in bools:
                row[c] = in_run
            for c, ty in q.columns.items():
                if c in row:
                    continue
                if c == ts.x or ty in TIME_TYPES:
                    row[c] = when.isoformat()
                elif ty == "DATE":
                    row[c] = when.date().isoformat()
                elif c.endswith("id"):
                    row[c] = f"{c[:3].upper()}-{rng.randint(100000, 999999)}"
                else:
                    row[c] = _dim(c, rng.randint(0, 5))
            group_rows.append(row)
            gap = rng.uniform(0.4, 1.6) * every
            t += gap * 6 if rng.random() < 0.02 else gap  # now and then a stretch with no readings
        if bools and not runs and group_rows:  # every series shows at least one shaded period
            i = rng.randrange(len(group_rows))
            for r in group_rows[i:i + 3]:
                for c in bools:
                    r[c] = True
        rows += group_rows
    return rows


def synthesize(q: Query, values: dict[str, Any], start_param: str = "start_date",
               end_param: str = "end_date", one_row: bool = False, series: TimeSeries | None = None) -> list[dict]:
    rng = random.Random(q.id)
    start, end = values.get(start_param), values.get(end_param)
    if series is not None and not one_row:
        if isinstance(start, dt.date) and isinstance(end, dt.date):
            t_start, t_end = dt.datetime.combine(start, dt.time()), dt.datetime.combine(end, dt.time())
        else:
            t_end = dt.datetime.now().replace(microsecond=0)
            t_start = t_end - dt.timedelta(days=7)
        return _readings(q, series, rng, t_start, t_end)
    date_cols = [c for c, t in q.columns.items() if t == "DATE"]
    if date_cols and isinstance(start, dt.date) and isinstance(end, dt.date):
        days = [start + dt.timedelta(days=i) for i in range((end - start).days)]
    else:
        days = None
    time_cols = [c for c, t in q.columns.items() if t in ("DATE", "TIMESTAMP", "DATETIME")]
    dims = [c for c, t in q.columns.items() if t in ("STRING", "BOOL")]
    if one_row:
        n = 1  # a summary a KPI tile reads as a single value, whatever labels or dates it also returns
        days = days[-1:] if days else None
    elif days:
        n = len(days)
    elif not time_cols and not dims:
        n = 1  # a period-wide aggregate, e.g. a rate computed from numerator and denominator
    else:
        n = 25
    span_s = ((end - start).days * 86400) if isinstance(start, dt.date) and isinstance(end, dt.date) else 86400
    t_end = dt.datetime.combine(end, dt.time()) if isinstance(end, dt.date) else dt.datetime.now().replace(microsecond=0)
    base = {c: rng.uniform(50, 5000) for c in q.columns}
    rows = []
    for i in range(n):
        row: dict[str, Any] = {}
        for c, t in q.columns.items():
            fmt = q.formats.get(c)
            if t == "DATE":
                d = days[i] if days else dt.date.today() - dt.timedelta(days=n - i)
                row[c] = d.isoformat()
            elif t in ("TIMESTAMP", "DATETIME"):
                row[c] = (t_end - dt.timedelta(seconds=int(span_s * (i + rng.random()) / n))).isoformat()
            elif fmt == "percent":
                row[c] = round(rng.uniform(0.01, 0.2), 4)
            elif fmt == "duration":
                row[c] = round(rng.uniform(30, 7200))
            elif t == "INT64":
                row[c] = int(base[c] * rng.uniform(0.8, 1.2))
            elif t in ("FLOAT64", "NUMERIC", "BIGNUMERIC"):
                row[c] = round(base[c] * rng.uniform(0.8, 1.2), 2)
            elif t == "BOOL":
                row[c] = rng.random() < 0.2
            elif c.endswith("id"):
                row[c] = f"{c[:3].upper()}-{rng.randint(100000, 999999)}"
            else:  # low-cardinality dimension, like a region or type
                row[c] = f"{c.replace('_', ' ').capitalize()} {'ABCDEF'[rng.randint(0, 5)]}"
        rows.append(row)
    return rows
