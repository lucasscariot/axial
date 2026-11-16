"""Project configuration: axial.yaml at the project root, overridable by AXIAL_* env vars."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

CONFIG_FILE = "axial.yaml"
SCHEMA_VERSION = 2
SECTIONS = ("bigquery", "duckdb", "billing", "execution", "display", "server", "storage")
WAREHOUSES = ("bigquery", "duckdb")
ENV_PREFIX = "AXIAL_"
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def engine_version() -> str:
    try:
        return version("axial")
    except PackageNotFoundError:  # running from a source tree without install
        return "0.0.0"


class ConfigError(Exception):
    """A project configuration problem, with a message that says how to fix it."""


@dataclass
class Config:
    root: Path
    engine: str | None = None
    warehouse: str = "bigquery"
    duckdb_path: Path | None = None
    duckdb_allow_external_access: bool = False
    duckdb_threads: int | None = None
    duckdb_memory_limit: str | None = None
    billing_project: str | None = None
    location: str | None = None
    datasets: list[str] = field(default_factory=list)
    maximum_bytes_billed: int | None = None
    wait_timeout_seconds: float = 90.0
    billing_mode: str = "on_demand"
    price_per_tib_usd: float = 6.25
    default_cache_ttl_seconds: int = 120
    max_concurrent_jobs: int = 4
    row_cap: int = 2000
    reconcile_interval_seconds: float = 30.0
    title: str | None = None
    currency: str = "USD"
    locale: str = "en-US"
    brand_color: str | None = None
    logo: Path | None = None
    slow_tile_ms: int = 3000
    port: int = 8765
    storage_backend: str = "sqlite"
    history_path: Path = Path(".axial/history.sqlite3")

    @property
    def queries_dir(self) -> Path:
        return self.root / "queries"

    @property
    def dashboards_dir(self) -> Path:
        return self.root / "dashboards"

    @property
    def fixtures_dir(self) -> Path:
        return self.root / "fixtures"

    @property
    def local(self) -> bool:
        """True when queries run on this machine, so nothing is billed."""
        return self.warehouse == "duckdb"

    def display(self) -> dict:
        return {
            "warehouse": self.warehouse,
            "database": self.duckdb_path.name if self.duckdb_path else None,
            "title": self.title,
            "currency": self.currency,
            "locale": self.locale,
            "brand_color": self.brand_color,
            "has_logo": bool(self.logo and self.logo.exists()),
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
        if (candidate / CONFIG_FILE).exists():
            return candidate
        if (candidate / "bqdash.yaml").exists():
            raise ConfigError("this is a bqdash project. Run `axial guide migration` to rename its "
                              "configuration and preserve its history before using Axial.")
    raise ConfigError(f"no {CONFIG_FILE} found in {here} or its parents. "
                      "Run this inside an Axial project, or create one with `axial init <dir>`.")


def apply_env(raw: dict, environ: dict[str, str]) -> dict:
    """AXIAL_<SECTION>_<KEY>=value overrides raw[section][key]; values are parsed as YAML scalars."""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in raw.items()}
    for name, value in environ.items():
        if not name.startswith(ENV_PREFIX):
            continue
        rest = name[len(ENV_PREFIX):].lower()
        for section in SECTIONS:
            if rest.startswith(section + "_"):
                key = rest[len(section) + 1:]
                parsed = yaml.safe_load(value) if value != "" else None
                out.setdefault(section, {})
                if not isinstance(out[section], dict):
                    out[section] = {}
                out[section][key] = parsed
                break
    return out


def check_engine(requirement: str | None, current: str | None = None) -> None:
    if not requirement:
        return
    current = current or engine_version()
    try:
        spec = SpecifierSet(requirement)
    except InvalidSpecifier:
        raise ConfigError(f"engine: {requirement!r} is not a valid version range (e.g. \">=0.1,<0.2\")") from None
    if Version(current) not in spec:
        raise ConfigError(
            f"this project needs axial {requirement}, but {current} is installed. "
            f"Install a matching version: uv tool install --force "
            f"'git+https://github.com/lucasscariot/axial.git@v<version>'"
        )


def _datasets(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [x.strip() for x in v.split(",") if x.strip()]
    if not isinstance(v, list) or not all(isinstance(x, str) and x.count(".") <= 1 for x in v):
        raise ConfigError(f"{CONFIG_FILE}: bigquery.datasets must be a list like [project.dataset, dataset]")
    return v


def _section(raw: dict, name: str) -> dict:
    v = raw.get(name) or {}
    if not isinstance(v, dict):
        raise ConfigError(f"{CONFIG_FILE}: {name} must be a mapping")
    return v


def load_config(start: Path | None = None, *, environ: dict[str, str] | None = None,
                check_version: bool = True) -> Config:
    root = find_root(start)
    path = root / CONFIG_FILE
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML: {e}") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a mapping")
    schema = raw.get("version", SCHEMA_VERSION)
    if schema != SCHEMA_VERSION:
        raise ConfigError(f"{path}: config version {schema} is not supported by axial {engine_version()} "
                          f"(supports version {SCHEMA_VERSION}). Run `axial guide migration` for upgrade notes.")
    raw = apply_env(raw, dict(os.environ) if environ is None else environ)
    if check_version:
        check_engine(raw.get("engine"))

    bq, billing, ex = _section(raw, "bigquery"), _section(raw, "billing"), _section(raw, "execution")
    duck = _section(raw, "duckdb")
    disp, server, storage = _section(raw, "display"), _section(raw, "server"), _section(raw, "storage")

    def num(section: dict, key: str, default: Any, cast):
        v = section.get(key, default)
        if v is None:
            return None
        try:
            return cast(v)
        except (TypeError, ValueError):
            raise ConfigError(f"{CONFIG_FILE}: {key} must be a number, got {v!r}") from None

    warehouse = raw.get("warehouse") or "bigquery"
    if warehouse not in WAREHOUSES:
        raise ConfigError(f"{CONFIG_FILE}: warehouse must be one of {', '.join(WAREHOUSES)}, got {warehouse!r}")
    duckdb_path = None
    if warehouse == "duckdb":
        if not duck.get("path"):
            raise ConfigError(f"{CONFIG_FILE}: warehouse is duckdb, so set duckdb.path to the database file "
                              "(relative to the project root), or AXIAL_DUCKDB_PATH")
        duckdb_path = Path(str(duck["path"])).expanduser()
        if not duckdb_path.is_absolute():
            duckdb_path = (root / duckdb_path).resolve()

    cfg = Config(
        root=root,
        engine=raw.get("engine"),
        warehouse=warehouse,
        duckdb_path=duckdb_path,
        duckdb_allow_external_access=bool(duck.get("allow_external_access", False)),
        duckdb_threads=num(duck, "threads", None, int),
        duckdb_memory_limit=duck.get("memory_limit"),
        billing_project=bq.get("billing_project"),
        location=bq.get("location"),
        datasets=_datasets(bq.get("datasets")),
        maximum_bytes_billed=num(bq, "maximum_bytes_billed", None, int),
        wait_timeout_seconds=num(bq, "wait_timeout_seconds", 90, float),
        billing_mode=billing.get("mode", "on_demand"),
        price_per_tib_usd=num(billing, "price_per_tib_usd", 6.25, float),
        default_cache_ttl_seconds=num(ex, "default_cache_ttl_seconds", 120, int),
        max_concurrent_jobs=num(ex, "max_concurrent_jobs", 4, int),
        row_cap=num(ex, "row_cap", 2000, int),
        reconcile_interval_seconds=num(ex, "reconcile_interval_seconds", 30, float),
        title=disp.get("title"),
        currency=disp.get("currency", "USD"),
        locale=disp.get("locale", "en-US"),
        brand_color=disp.get("brand_color"),
        logo=(root / disp["logo"]) if disp.get("logo") else None,
        slow_tile_ms=num(disp, "slow_tile_ms", 3000, int),
        port=num(server, "port", 8765, int),
        storage_backend=storage.get("backend", "sqlite"),
        history_path=root / storage.get("path", ".axial/history.sqlite3"),
    )
    if cfg.local:
        # A local database bills nothing: there are no bytes billed, slots or job costs to show.
        cfg.billing_mode, cfg.billing_project, cfg.location, cfg.maximum_bytes_billed = "local", None, None, None
        cfg.datasets = _datasets(duck.get("datasets"))
    elif cfg.billing_mode not in ("on_demand", "reservation"):
        raise ConfigError(f"billing.mode must be on_demand or reservation, got {cfg.billing_mode!r}")
    if cfg.brand_color is not None and not HEX_COLOR.match(str(cfg.brand_color)):
        raise ConfigError(f"display.brand_color must be a #rrggbb color, got {cfg.brand_color!r}")
    if cfg.logo is not None:
        if cfg.logo.suffix.lower() not in (".svg", ".png", ".jpg", ".jpeg", ".webp"):
            raise ConfigError("display.logo must be an .svg, .png, .jpg or .webp file")
        if not cfg.logo.resolve().is_relative_to(root.resolve()):
            raise ConfigError("display.logo must be inside the project")
    if cfg.storage_backend != "sqlite":
        raise ConfigError(f"storage.backend {cfg.storage_backend!r} is not supported (available: sqlite)")
    return cfg
