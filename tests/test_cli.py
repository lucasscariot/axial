import json

from click.testing import CliRunner

from bqdash.cli import main


def test_check_and_models_search(repo, monkeypatch):
    monkeypatch.chdir(repo)
    r = CliRunner().invoke(main, ["check", "booking_ops"])
    assert r.exit_code == 0, r.output
    assert "0 error(s)" in r.output

    r = CliRunner().invoke(main, ["models", "search", "exceptions", "--json"])
    assert r.exit_code == 0, r.output
    hits = json.loads(r.output)
    assert hits[0]["unique_id"] == "model.analytics.fct_booking_exceptions"
    assert {c["name"] for c in hits[0]["columns"]} >= {"wait_seconds", "exception_type"}


def test_check_fails_on_errors(repo, monkeypatch):
    monkeypatch.chdir(repo)
    (repo / "dashboards" / "booking_ops.yaml").write_text(
        "title: x\ntiles:\n  - {type: kpi, query: nope, value: v}\n")
    r = CliRunner().invoke(main, ["check", "booking_ops"])
    assert r.exit_code == 1
    assert "query 'nope' does not exist" in r.output
