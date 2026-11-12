"""App configuration loaded from dash.yaml at the repository root."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Config:
    root: Path
    billing_project: str | None = None
    location: str | None = None
    maximum_bytes_billed: int | None = None
    wait_timeout_seconds: float = 90.0
    billing_mode: str = "on_demand"
    price_per_tib_usd: float = 6.25
    default_cache_ttl_seconds: int = 120
    max_concurrent_jobs: int = 4
    row_cap: int = 2000
    reconcile_interval_seconds: float = 30.0
    dbt_manifest: Path | None = None
    dbt_catalog: Path | None = None
    dbt_docs_url: str | None = None
    dbt_revision: str | None = None
    currency: str = "GBP"
    locale: str = "en-GB"
    slow_tile_ms: int = 3000
    history_db: Path = field(default_factory=lambda: Path(".dash/history.sqlite3"))

    @property
    def queries_dir(self) -> Path:
        return self.root / "queries"

    @property
    def dashboards_dir(self) -> Path:
        return self.root / "dashboards"

    @property
    def fixtures_dir(self) -> Path:
        return self.root / "fixtures"

    def display(self) -> dict:
        return {
            "currency": self.currency,
            "locale": self.locale,
            "slow_tile_ms": self.slow_tile_ms,
            "billing_mode": self.billing_mode,
            "price_per_tib_usd": self.price_per_tib_usd,
            "billing_project": self.billing_project,
            "location": self.location,
            "row_cap": self.row_cap,
        }


def find_root(start: Path | None = None) -> Path:
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "dash.yaml").exists():
            return candidate
    return here


def load_config(root: Path | None = None) -> Config:
    root = find_root(root)
    path = root / "dash.yaml"
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    raw = raw or {}
    bq = raw.get("bigquery", {}) or {}
    billing = raw.get("billing", {}) or {}
    ex = raw.get("execution", {}) or {}
    dbt = raw.get("dbt", {}) or {}
    disp = raw.get("display", {}) or {}
    storage = raw.get("storage", {}) or {}

    def rel(p: str | None) -> Path | None:
        return (root / p) if p else None

    cfg = Config(
        root=root,
        billing_project=os.environ.get("DASH_BILLING_PROJECT") or bq.get("billing_project"),
        location=bq.get("location"),
        maximum_bytes_billed=bq.get("maximum_bytes_billed"),
        wait_timeout_seconds=float(bq.get("wait_timeout_seconds", 90)),
        billing_mode=billing.get("mode", "on_demand"),
        price_per_tib_usd=float(billing.get("price_per_tib_usd", 6.25)),
        default_cache_ttl_seconds=int(ex.get("default_cache_ttl_seconds", 120)),
        max_concurrent_jobs=int(ex.get("max_concurrent_jobs", 4)),
        row_cap=int(ex.get("row_cap", 2000)),
        reconcile_interval_seconds=float(ex.get("reconcile_interval_seconds", 30)),
        dbt_manifest=rel(dbt.get("manifest")),
        dbt_catalog=rel(dbt.get("catalog")),
        dbt_docs_url=dbt.get("docs_url"),
        dbt_revision=dbt.get("revision") or os.environ.get("DBT_REVISION"),
        currency=disp.get("currency", "GBP"),
        locale=disp.get("locale", "en-GB"),
        slow_tile_ms=int(disp.get("slow_tile_ms", 3000)),
        history_db=root / storage.get("history_db", ".dash/history.sqlite3"),
    )
    if cfg.billing_mode not in ("on_demand", "reservation"):
        raise ValueError(f"billing.mode must be on_demand or reservation, got {cfg.billing_mode!r}")
    return cfg
