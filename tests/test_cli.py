import os

import pytest
import yaml
from click.testing import CliRunner

from axial.cli import _engine_range, main
from axial.config import engine_version, load_config


def test_check(repo, monkeypatch):
    monkeypatch.chdir(repo)
    r = CliRunner().invoke(main, ["check", "booking_ops"])
    assert r.exit_code == 0, r.output
    assert "0 error(s)" in r.output


def test_check_fails_on_errors(repo, monkeypatch):
    monkeypatch.chdir(repo)
    (repo / "dashboards" / "booking_ops.yaml").write_text("title: x\ntiles:\n  - {type: kpi, query: nope, value: v}\n")
    r = CliRunner().invoke(main, ["check", "booking_ops"])
    assert r.exit_code == 1
    assert "query 'nope' does not exist" in r.output


def test_outside_a_project_is_a_clean_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(main, ["check"])
    assert r.exit_code == 1
    assert "axial init" in r.output and "Traceback" not in r.output


def test_init_creates_a_project_that_checks_clean(tmp_path, monkeypatch):
    target = tmp_path / "my-ops"
    r = CliRunner().invoke(main, ["init", str(target), "--billing-project", "acme-bi", "--location", "EU"])
    assert r.exit_code == 0, r.output
    for f in ("axial.yaml", ".gitignore", "AGENTS.md", "queries/orders_daily.sql", "dashboards/overview.yaml",
              "Dockerfile", ".dockerignore"):
        assert (target / f).exists(), f
    dockerfile = (target / "Dockerfile").read_text()
    assert f"ARG AXIAL_REF=v{engine_version()}\n" in dockerfile and "__" not in dockerfile
    assert 'CMD ["axial", "serve"]' in dockerfile and "USER axial" in dockerfile
    ignored = (target / ".dockerignore").read_text().split()
    assert {".axial/", ".git/", "*.duckdb", ".env"} <= set(ignored)
    raw = yaml.safe_load((target / "axial.yaml").read_text())
    assert raw["bigquery"] == {**raw["bigquery"], "billing_project": "acme-bi", "location": "EU"}
    cfg = load_config(target, environ={})
    assert cfg.engine and cfg.billing_project == "acme-bi"
    monkeypatch.chdir(target)
    r = CliRunner().invoke(main, ["check"])
    assert r.exit_code == 0, r.output
    # refuses to overwrite
    r = CliRunner().invoke(main, ["init", str(target)])
    assert r.exit_code == 1 and "refusing to overwrite" in r.output


def test_init_without_billing_project_leaves_it_unset(tmp_path):
    target = tmp_path / "p"
    assert CliRunner().invoke(main, ["init", str(target)]).exit_code == 0
    assert load_config(target, environ={}).billing_project is None


def test_up_live_requires_billing_project(tmp_path, monkeypatch):
    target = tmp_path / "p"
    CliRunner().invoke(main, ["init", str(target)])
    monkeypatch.chdir(target)
    for k in [k for k in os.environ if k.startswith("AXIAL_")]:
        monkeypatch.delenv(k)
    r = CliRunner().invoke(main, ["up", "--no-open"])
    assert r.exit_code == 1 and "billing_project is not set" in r.output


def test_up_refuses_projects_with_errors(repo, monkeypatch):
    monkeypatch.chdir(repo)
    (repo / "dashboards" / "broken.yaml").write_text("title: x\ntiles:\n  - {type: kpi, query: nope, value: v}\n")
    r = CliRunner().invoke(main, ["up", "--fixture", "--no-open"])
    assert r.exit_code == 1 and "error(s) in dashboards or queries" in r.output


def test_engine_range_helper():
    assert _engine_range("0.1.0") == ">=0.1,<0.2"
    assert _engine_range("1.4.2") == ">=1.4,<2"


def test_preview_takes_one_output(repo, monkeypatch):
    monkeypatch.chdir(repo)
    r = CliRunner().invoke(main, ["preview", "booking_ops", "--fixture", "--pdf", "a.pdf", "--screenshot", "a.png"])
    assert r.exit_code == 2 and "not both" in r.output


def test_preview_exports_a_pdf(repo, monkeypatch):
    """End to end: serve fixture data, render in Chromium, print to PDF. Needs the [screenshot] extra."""
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            p.chromium.launch().close()
        except Exception:
            pytest.skip("chromium is not installed (`playwright install chromium`)")
    monkeypatch.chdir(repo)
    out = repo / "booking_ops.pdf"
    r = CliRunner().invoke(main, ["preview", "booking_ops", "--fixture", "--pdf", str(out)])
    assert r.exit_code == 0, r.output
    data = out.read_bytes()
    assert data.startswith(b"%PDF") and len(data) > 10_000


@pytest.fixture
def served(monkeypatch):
    """Capture the uvicorn config instead of serving; fail if anything opens a browser."""
    import uvicorn

    configs = []
    monkeypatch.setattr(uvicorn.Server, "run", lambda self: configs.append(self.config))
    monkeypatch.setattr("webbrowser.open", lambda url: pytest.fail(f"opened a browser: {url}"))
    monkeypatch.delenv("PORT", raising=False)
    return configs


def test_serve_binds_all_interfaces_on_port_env_or_config(repo, monkeypatch, served):
    monkeypatch.chdir(repo)
    r = CliRunner().invoke(main, ["serve", "--fixture"])
    assert r.exit_code == 0, r.output
    assert (served[-1].host, served[-1].port, served[-1].log_level) == ("0.0.0.0", 8765, "info")
    assert "serving on 0.0.0.0:8765" in r.output
    monkeypatch.setenv("PORT", "8080")
    assert CliRunner().invoke(main, ["serve", "--fixture"]).exit_code == 0
    assert served[-1].port == 8080
    r = CliRunner().invoke(main, ["serve", "--fixture", "--port", "9000", "--host", "127.0.0.1"])
    assert r.exit_code == 0, r.output
    assert (served[-1].host, served[-1].port) == ("127.0.0.1", 9000)
    monkeypatch.setenv("PORT", "http")
    r = CliRunner().invoke(main, ["serve", "--fixture"])
    assert r.exit_code == 1 and "PORT='http' is not a number" in r.output


def test_serve_does_not_probe_for_a_free_port(repo, monkeypatch, served):
    monkeypatch.chdir(repo)
    monkeypatch.setattr("axial.cli._free_port", lambda p: pytest.fail("probed for a free port"))
    assert CliRunner().invoke(main, ["serve", "--fixture"]).exit_code == 0


def test_serve_refuses_projects_with_errors(repo, monkeypatch, served):
    monkeypatch.chdir(repo)
    (repo / "dashboards" / "broken.yaml").write_text("title: x\ntiles:\n  - {type: kpi, query: nope, value: v}\n")
    r = CliRunner().invoke(main, ["serve", "--fixture"])
    assert r.exit_code == 1 and "error(s) in dashboards or queries" in r.output and not served


def test_serve_live_requires_billing_project(tmp_path, monkeypatch, served):
    target = tmp_path / "p"
    CliRunner().invoke(main, ["init", str(target)])
    monkeypatch.chdir(target)
    for k in [k for k in os.environ if k.startswith("AXIAL_")]:
        monkeypatch.delenv(k)
    r = CliRunner().invoke(main, ["serve"])
    assert r.exit_code == 1 and "billing_project is not set" in r.output and not served


def test_up_still_binds_localhost(repo, monkeypatch, served):
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PORT", "8080")
    r = CliRunner().invoke(main, ["up", "--fixture", "--no-open"])
    assert r.exit_code == 0, r.output
    assert served[-1].host == "127.0.0.1" and served[-1].port != 8080
