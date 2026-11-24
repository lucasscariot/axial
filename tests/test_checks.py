"""Strictness of `axial check`: unknown keys, fixtures and files the engine would ignore."""

import json

import pytest

from axial.checks import run_checks
from axial.config import ConfigError, load_config
from axial.registry import Registry


def errors(repo, dashboard=None):
    issues = run_checks(Registry(load_config(repo, environ={})), dashboard)
    return [f"{i.where}: {i.message}" for i in issues if i.level == "error"]


def test_test_project_is_clean(repo):
    assert errors(repo) == []


def test_config_rejects_unknown_keys(repo):
    p = repo / "axial.yaml"
    base = p.read_text()
    p.write_text(base + "dispaly:\n  title: Ops\n")
    with pytest.raises(ConfigError, match="unknown key 'dispaly'"):
        load_config(repo, environ={})
    p.write_text(base.replace("  currency: GBP\n", "  currency: GBP\n  titel: Ops\n"))
    with pytest.raises(ConfigError, match=r"unknown key display\.titel .*Known display keys: brand_color"):
        load_config(repo, environ={})
    p.write_text(base)
    with pytest.raises(ConfigError, match=r"unknown key server\.prot .*AXIAL_SERVER_PROT"):
        load_config(repo, environ={"AXIAL_SERVER_PROT": "9000"})
    # not a section, so not a config key: left alone
    assert load_config(repo, environ={"AXIAL_APP_REVISION": "abc"}).port == 8765


def test_query_rejects_unknown_keys(repo):
    p = repo / "queries" / "bookings_daily.yaml"
    p.write_text(p.read_text() + "cache_ttl: 60\n")
    assert any("queries/bookings_daily: unknown key 'cache_ttl'" in e for e in errors(repo))
    p = repo / "queries" / "booking_exceptions.yaml"
    p.write_text(p.read_text().replace("default:", "defualt:"))
    assert any("unknown key 'defualt'" in e for e in errors(repo))


def test_dashboard_rejects_unknown_keys(repo):
    (repo / "dashboards" / "booking_ops.yaml").write_text(
        "title: Ops\n"
        "owner: ops-analytics\n"
        "date_range: {default_days: 7, max_days: 31, min_days: 1}\n"
        "tiles:\n"
        "  - {type: kpi, query: bookings_daily, value: bookings, reduce: sum, heigth: 80}\n"
        "  - {type: line, query: bookings_daily, x: day, y: bookings, reduce: sum}\n"
        "  - {type: text, markdown: note, span: 12}\n"
    )
    found = errors(repo, "booking_ops")
    assert any("unknown key 'owner'" in e for e in found)
    assert any("date_range: unknown key 'min_days'" in e for e in found)
    assert any("tile[0]" in e and "unknown key 'heigth' for a kpi tile" in e for e in found)
    assert any("tile[1]" in e and "unknown key 'reduce' for a line tile" in e for e in found)
    assert len(found) == 4


def fixture(repo, rows, query="bookings_daily"):
    path = repo / "fixtures" / f"{query}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(rows if isinstance(rows, str) else json.dumps(rows))


def test_fixture_must_match_declared_columns(repo):
    good = {"day": "2026-11-01", "bookings": 3, "gross_value": 10.5}
    fixture(repo, [good, {**good, "gross_value": None}, {**good, "gross_value": "10.50"}])
    assert errors(repo) == []
    fixture(repo, {"rows": [good]})
    assert errors(repo) == []

    fixture(repo, [{"day": "2026-11-01", "bookings": 3, "revenue": 1}])
    assert errors(repo) == ["fixtures/bookings_daily: row 0: missing gross_value; "
                            "not a column of bookings_daily: revenue"]
    fixture(repo, [{**good, "bookings": "3"}, {**good, "day": "yesterday"}, {**good, "bookings": True}, [1]])
    assert errors(repo) == [
        "fixtures/bookings_daily: row 0: bookings is '3', expected INT64 or null",
        "fixtures/bookings_daily: row 1: day is 'yesterday', expected DATE or null",
        "fixtures/bookings_daily: row 2: bookings is True, expected INT64 or null",
        "fixtures/bookings_daily: row 3 must be an object",
    ]
    fixture(repo, "{not json")
    assert errors(repo)[0].startswith("fixtures/bookings_daily: invalid JSON")
    fixture(repo, {"data": []})
    assert errors(repo) == ["fixtures/bookings_daily: expected a JSON list of rows"]


def test_fixture_problems_are_capped_and_scoped_to_the_dashboard(repo):
    fixture(repo, [{"day": "x", "bookings": 1, "gross_value": 1}] * 20)
    found = errors(repo, "booking_ops")
    assert len(found) == 6 and found[-1] == "fixtures/bookings_daily: 15 more problem(s) not shown"


def test_files_the_engine_would_ignore_are_errors(repo):
    (repo / "queries" / "orphan.sql").write_text("SELECT 1")
    (repo / "queries" / "other.yml").write_text("title: x\n")
    (repo / "queries" / "crm").mkdir()
    (repo / "queries" / "crm" / "summary.yaml").write_text("title: x\n")
    (repo / "queries" / "notes.md").write_text("kept\n")
    (repo / "dashboards" / "ops.yml").write_text("title: x\n")
    fixture(repo, [], query="renamed_query")
    (repo / "fixtures" / "README.md").write_text("provenance\n")
    (repo / "fixtures" / "old").mkdir()
    (repo / "fixtures" / "old" / "bookings_daily.json").write_text("[]")
    assert errors(repo) == [
        "queries/crm/summary.yaml: files in subfolders are not loaded; move it to queries/",
        "queries/orphan.sql: no orphan.yaml next to it, so this query is not loaded",
        "queries/other.yml: rename to .yaml; .yml files are not loaded",
        "dashboards/ops.yml: rename to .yaml; .yml files are not loaded",
        "fixtures/old/bookings_daily.json: files in subfolders are not loaded; move it to fixtures/",
        "fixtures/renamed_query.json: no query named renamed_query; a fixture is matched to its query by file name",
    ]
    # a single-dashboard check stays about that dashboard
    assert errors(repo, "booking_ops") == []


def test_dockerfile_engine_pin_must_satisfy_the_engine_range(repo):
    cfg_path, dockerfile = repo / "axial.yaml", repo / "Dockerfile"
    cfg_path.write_text('engine: ">=0.1,<99"\n' + cfg_path.read_text())

    def issues():
        return [(i.level, i.message) for i in run_checks(Registry(load_config(repo, environ={})))]

    dockerfile.write_text("FROM python:3.13-slim\nARG AXIAL_REF=v0.2.0\n")
    assert issues() == []
    dockerfile.write_text("ARG AXIAL_REF=v99.1.0\n")
    assert issues() == [("error", "AXIAL_REF=v99.1.0 is outside engine '>=0.1,<99' in axial.yaml; "
                         "change one so the image and local runs use compatible engines")]
    dockerfile.write_text("ARG AXIAL_REF=d7e231cb1bbacf4257c22549bd20757dd42e3e64\n")
    (level, message), = issues()
    assert level == "warning" and "is not a release tag" in message
    dockerfile.write_text("FROM scratch\n")
    assert issues() == []


def test_time_series_options_are_checked(repo):
    def found(tile: str) -> list[str]:
        (repo / "dashboards" / "service_health.yaml").write_text(
            "title: x\ndate_range: {default_days: 3, max_days: 31}\ntiles:\n  - " + tile + "\n")
        return [e.split(": ", 1)[1] for e in errors(repo, "service_health")]

    base = "{type: line, query: service_readings, x: read_at, y: [error_rate, replicas]"
    assert found(base + ", facet: service, facet_columns: 2, lanes: true, highlight: incident, step: replicas}") == []
    assert found(base + ", step: true, facet: service}") == []
    assert found(base + ", facet: region}") == [
        "facet: column 'region' is not in query service_readings columns (service, read_at, error_rate, latency, replicas, incident)"]
    assert found(base + ", facet: replicas}") == [
        "facet: column 'replicas' is already the x, y or series of this tile; facet by another column"]
    assert found(base + ", facet_columns: 2}") == ["facet_columns only applies with facet"]
    assert found(base + ", facet: service, facet_columns: 5}") == ["facet_columns must be a whole number from 1 to 4, got 5"]
    assert found(base + ", highlight: replicas}") == [
        "highlight: column 'replicas' is INT64, expected BOOL (true where the period is shaded)"]
    assert found(base + ", step: [replicas, incident]}") == [
        "step: 'incident' is not one of this tile's y columns (error_rate, replicas); use true for every line, or name y columns"]
    assert found(base + ", lanes: 'on'}") == ["lanes must be true or false, got 'on'"]
    assert found("{type: line, query: service_readings, x: read_at, y: error_rate, lanes: true}") == [
        "lanes needs a list of at least two y columns, one per lane"]
    assert found("{type: line, query: service_readings, x: read_at, y: error_rate, series: service, lanes: true}") == [
        "lanes needs a list of at least two y columns, one per lane",
        "lanes cannot be combined with series: lanes draw one y column each"]
    # the keys belong to line and bar tiles only, and step to line tiles only
    assert found("{type: bar, query: service_readings, x: read_at, y: replicas, step: true}")[0].startswith(
        "unknown key 'step' for a bar tile")
    assert found("{type: table, query: service_readings, facet: service}")[0].startswith(
        "unknown key 'facet' for a table tile")
