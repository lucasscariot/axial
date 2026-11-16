"""Representative data for `axial preview --fixture`: no warehouse query is made."""

from __future__ import annotations

import datetime as dt
import json
import random
from typing import Any

from .config import Config
from .registry import Query


def fixture_rows(cfg: Config, q: Query, values: dict[str, Any], start_param: str = "start_date",
                 end_param: str = "end_date") -> list[dict]:
    path = cfg.fixtures_dir / f"{q.id}.json"
    if path.exists():
        data = json.loads(path.read_text())
        return data["rows"] if isinstance(data, dict) else data
    return synthesize(q, values, start_param, end_param)


def synthesize(q: Query, values: dict[str, Any], start_param: str = "start_date",
               end_param: str = "end_date") -> list[dict]:
    rng = random.Random(q.id)
    start, end = values.get(start_param), values.get(end_param)
    date_cols = [c for c, t in q.columns.items() if t == "DATE"]
    if date_cols and isinstance(start, dt.date) and isinstance(end, dt.date):
        days = [start + dt.timedelta(days=i) for i in range((end - start).days)]
    else:
        days = None
    time_cols = [c for c, t in q.columns.items() if t in ("DATE", "TIMESTAMP", "DATETIME")]
    dims = [c for c, t in q.columns.items() if t in ("STRING", "BOOL")]
    if days:
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
