import pytest

from bqdash.config import ConfigError, apply_env, check_engine, load_config


def test_env_overrides_any_value(repo):
    cfg = load_config(repo, environ={
        "BQDASH_BIGQUERY_BILLING_PROJECT": "from-env",
        "BQDASH_BIGQUERY_MAXIMUM_BYTES_BILLED": "1024",
        "BQDASH_SERVER_PORT": "9000",
        "BQDASH_DISPLAY_BRAND_COLOR": "'#123456'",
        "UNRELATED": "x",
    })
    assert cfg.billing_project == "from-env"
    assert cfg.maximum_bytes_billed == 1024
    assert cfg.port == 9000
    assert cfg.brand_color == "#123456"


def test_apply_env_ignores_unknown_sections():
    assert apply_env({"bigquery": {"location": "EU"}}, {"BQDASH_NOPE_X": "1"}) == {"bigquery": {"location": "EU"}}


def test_engine_range():
    check_engine(">=0.1,<0.2", "0.1.5")
    check_engine(None, "9.9.9")
    with pytest.raises(ConfigError, match="needs bqdash >=0.2,<0.3, but 0.1.0 is installed"):
        check_engine(">=0.2,<0.3", "0.1.0")
    with pytest.raises(ConfigError, match="not a valid version range"):
        check_engine("latest", "0.1.0")


def test_engine_range_enforced_on_load(repo):
    p = repo / "bqdash.yaml"
    p.write_text('engine: ">=99"\n' + p.read_text())
    with pytest.raises(ConfigError, match="needs bqdash >=99"):
        load_config(repo, environ={})
    assert load_config(repo, environ={}, check_version=False).engine == ">=99"


def test_rejects_bad_values(repo):
    p = repo / "bqdash.yaml"
    base = p.read_text()
    p.write_text(base.replace("version: 1", "version: 2"))
    with pytest.raises(ConfigError, match="config version 2 is not supported"):
        load_config(repo, environ={})
    p.write_text(base)
    with pytest.raises(ConfigError, match="brand_color"):
        load_config(repo, environ={"BQDASH_DISPLAY_BRAND_COLOR": "blue"})
    with pytest.raises(ConfigError, match="logo must be inside the project"):
        load_config(repo, environ={"BQDASH_DISPLAY_LOGO": "../../etc/logo.png"})


def test_not_a_project(tmp_path):
    with pytest.raises(ConfigError, match="bqdash init"):
        load_config(tmp_path, environ={})
