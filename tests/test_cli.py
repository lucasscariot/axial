import os

import pytest
import yaml
from click.testing import CliRunner

from axial.cli import _engine_range, main
from axial.config import load_config


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
    for f in ("axial.yaml", ".gitignore", "AGENTS.md", "queries/orders_daily.sql", "dashboards/overview.yaml"):
        assert (target / f).exists(), f
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
