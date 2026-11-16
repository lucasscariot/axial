"""Pick the warehouse adapter a project is configured for."""

from __future__ import annotations

from .config import Config, ConfigError


def make_warehouse(cfg: Config):
    if cfg.warehouse == "duckdb":
        try:
            from .duck import DuckDBWarehouse
        except ImportError:  # pragma: no cover - duckdb is a declared dependency
            raise ConfigError("warehouse: duckdb needs the duckdb package: uv tool install --force "
                              "'axial[duckdb] @ git+https://github.com/lucasscariot/axial.git@main'") from None
        try:
            return DuckDBWarehouse(cfg.duckdb_path, allow_external_access=cfg.duckdb_allow_external_access,
                                   threads=cfg.duckdb_threads, memory_limit=cfg.duckdb_memory_limit)
        except FileNotFoundError as e:
            raise ConfigError(f"{e}. Set duckdb.path in axial.yaml (relative to the project root).") from None
    from .bq import BigQueryWarehouse

    return BigQueryWarehouse(cfg.billing_project, cfg.location)
