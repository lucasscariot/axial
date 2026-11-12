"""Index of dbt models from the production manifest.json (+ catalog.json types)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config


@dataclass
class Column:
    name: str
    description: str = ""
    type: str | None = None


@dataclass
class Model:
    unique_id: str
    name: str
    description: str
    relation_name: str | None
    columns: dict[str, Column]
    depends_on: list[str]
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "unique_id": self.unique_id,
            "name": self.name,
            "description": self.description,
            "relation_name": self.relation_name,
            "columns": [{"name": c.name, "description": c.description, "type": c.type} for c in self.columns.values()],
            "depends_on": self.depends_on,
            "tags": self.tags,
        }


class DbtIndex:
    def __init__(self, models: dict[str, Model], revision: str | None, loaded_from: Path | None):
        self.models = models
        self.revision = revision
        self.loaded_from = loaded_from

    @classmethod
    def empty(cls) -> "DbtIndex":
        return cls({}, None, None)

    @classmethod
    def load(cls, cfg: Config) -> "DbtIndex":
        path = cfg.dbt_manifest
        if path is None or not path.exists():
            return cls({}, cfg.dbt_revision, None)
        manifest = json.loads(path.read_text())
        catalog = {}
        if cfg.dbt_catalog and cfg.dbt_catalog.exists():
            catalog = json.loads(cfg.dbt_catalog.read_text()).get("nodes", {})
        models: dict[str, Model] = {}
        for uid, node in (manifest.get("nodes") or {}).items():
            if node.get("resource_type") != "model":
                continue
            cat_cols = {k.lower(): v for k, v in (catalog.get(uid, {}).get("columns") or {}).items()}
            cols: dict[str, Column] = {}
            for name, c in (node.get("columns") or {}).items():
                cols[name.lower()] = Column(name, c.get("description", ""), (cat_cols.get(name.lower()) or {}).get("type") or c.get("data_type"))
            for name, c in cat_cols.items():  # undocumented warehouse columns
                cols.setdefault(name, Column(c.get("name", name), "", c.get("type")))
            models[uid] = Model(
                unique_id=uid,
                name=node.get("name", uid.split(".")[-1]),
                description=node.get("description", ""),
                relation_name=node.get("relation_name"),
                columns=cols,
                depends_on=list((node.get("depends_on") or {}).get("nodes") or []),
                tags=list(node.get("tags") or []),
            )
        meta = manifest.get("metadata") or {}
        revision = cfg.dbt_revision or meta.get("invocation_id")
        if revision and meta.get("generated_at") and not cfg.dbt_revision:
            revision = f"{revision}@{meta['generated_at']}"
        return cls(models, revision, path)

    def search(self, text: str, limit: int = 10) -> list[tuple[float, Model]]:
        terms = [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]
        scored: list[tuple[float, Model]] = []
        for m in self.models.values():
            name = m.name.lower()
            desc = m.description.lower()
            colnames = " ".join(m.columns)
            coldesc = " ".join(c.description.lower() for c in m.columns.values())
            score = 0.0
            for t in terms:
                if t == name:
                    score += 10
                elif t in name:
                    score += 5
                if t in (m.relation_name or "").lower():
                    score += 1
                if t in desc:
                    score += 2
                if t in colnames:
                    score += 1.5
                if t in coldesc:
                    score += 0.5
            if score:
                # Dashboards should read marts, not staging models
                if name.startswith(("fct_", "dim_", "mart_")):
                    score += 1
                elif name.startswith(("stg_", "base_", "int_")):
                    score -= 1
                scored.append((score, m))
        scored.sort(key=lambda x: (-x[0], x[1].name))
        return scored[:limit]

    def docs_link(self, docs_url: str | None, unique_id: str) -> str | None:
        return f"{docs_url.rstrip('/')}/#!/model/{unique_id}" if docs_url else None
