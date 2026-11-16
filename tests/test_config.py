import pytest

from axial.config import ConfigError, apply_env, check_engine, load_config


def test_env_overrides_any_value(repo):
    cfg = load_config(repo, environ={
        "AXIAL_BIGQUERY_BILLING_PROJECT": "from-env",
        "AXIAL_BIGQUERY_MAXIMUM_BYTES_BILLED": "1024",
        "AXIAL_SERVER_PORT": "9000",
        "AXIAL_DISPLAY_BRAND_COLOR": "'#123456'",
        "UNRELATED": "x",
    })
    assert cfg.billing_project == "from-env"
    assert cfg.maximum_bytes_billed == 1024
    assert cfg.port == 9000
    assert cfg.brand_color == "#123456"


def test_apply_env_ignores_unknown_sections():
    assert apply_env({"bigquery": {"location": "EU"}}, {"AXIAL_NOPE_X": "1"}) == {"bigquery": {"location": "EU"}}


def test_engine_range():
    check_engine(">=0.1,<0.2", "0.1.5")
    check_engine(None, "9.9.9")
    with pytest.raises(ConfigError, match="needs axial >=0.2,<0.3, but 0.1.0 is installed"):
        check_engine(">=0.2,<0.3", "0.1.0")
    with pytest.raises(ConfigError, match="not a valid version range"):
        check_engine("latest", "0.1.0")


def test_engine_range_enforced_on_load(repo):
    p = repo / "axial.yaml"
    p.write_text('engine: ">=99"\n' + p.read_text())
    with pytest.raises(ConfigError, match="needs axial >=99"):
        load_config(repo, environ={})
    assert load_config(repo, environ={}, check_version=False).engine == ">=99"


def test_rejects_bad_values(repo):
    p = repo / "axial.yaml"
    base = p.read_text()
    p.write_text(base.replace("version: 2", "version: 99"))
    with pytest.raises(ConfigError, match="config version 99 is not supported"):
        load_config(repo, environ={})
    p.write_text(base)
    with pytest.raises(ConfigError, match="brand_color"):
        load_config(repo, environ={"AXIAL_DISPLAY_BRAND_COLOR": "blue"})
    with pytest.raises(ConfigError, match="logo must be inside the project"):
        load_config(repo, environ={"AXIAL_DISPLAY_LOGO": "../../etc/logo.png"})


def test_not_a_project(tmp_path):
    with pytest.raises(ConfigError, match="axial init"):
        load_config(tmp_path, environ={})


def test_legacy_project_points_to_migration_without_changing_history(tmp_path):
    config = tmp_path / "bqdash.yaml"
    config.write_text("version: 1\n")
    history = tmp_path / ".bqdash" / "history.sqlite3"
    history.parent.mkdir()
    history.write_bytes(b"existing history")
    with pytest.raises(ConfigError, match="axial guide migration"):
        load_config(tmp_path, environ={})
    assert history.read_bytes() == b"existing history"
    assert config.read_text() == "version: 1\n"
    assert not (tmp_path / "axial.yaml").exists()


def test_axial_config_uses_new_defaults_and_rejects_old_schema(repo):
    config = repo / "axial.yaml"
    config.write_text("version: 2\n")
    assert load_config(repo, environ={}).history_path == repo / ".axial/history.sqlite3"
    config.write_text("version: 1\n")
    with pytest.raises(ConfigError, match="axial guide migration"):
        load_config(repo, environ={})
