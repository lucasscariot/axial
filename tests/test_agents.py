import json
import re

import pytest
import yaml
from click.testing import CliRunner

from axial import agents as ag
from axial.cli import GUIDE_DIR, main
from axial.registry import COMMON_TILE_KEYS, FORMATS, REDUCERS, TILE_KEYS, TILE_TYPES

from .conftest import FakeWarehouse

SKILLS = ["build-dashboard", "optimize-query", "rebuild-dashboard"]


@pytest.fixture
def project(tmp_path, monkeypatch):
    target = tmp_path / "p"
    r = CliRunner().invoke(main, ["init", str(target), "--billing-project", "test-project"])
    assert r.exit_code == 0, r.output
    monkeypatch.chdir(target)
    return target


def run(*args):
    return CliRunner().invoke(main, list(args))


def test_init_writes_agent_files_for_codex_and_claude(project):
    assert (project / "CLAUDE.md").read_text().strip().endswith("@AGENTS.md")
    assert "Read `CONTEXT.md` first" in (project / "AGENTS.md").read_text()
    assert not ag.is_managed(project / "CONTEXT.md")  # the user's file
    for d in (".agents/skills", ".claude/skills"):
        for name in SKILLS:
            text = (project / d / name / "SKILL.md").read_text()
            assert text.startswith("---\n"), "frontmatter must come first"
            front = yaml.safe_load(text.split("---\n")[1])
            assert front["name"] == name and len(front["description"]) > 40
            assert ag.is_managed(project / d / name / "SKILL.md")
    assert run("agents", "sync", "--check").exit_code == 0


def test_sync_restores_edits_removes_stale_and_respects_unmanaged(project):
    skill = project / ".claude/skills/build-dashboard/SKILL.md"
    skill.write_text(skill.read_text() + "\nlocal edit\n")
    stale = project / ".agents/skills/old-skill"
    stale.mkdir()
    (stale / "SKILL.md").write_text(ag.add_marker("---\nname: old-skill\ndescription: x\n---\nold\n", "0.0.1"))
    mine = project / ".claude/skills/my-skill"
    mine.mkdir()
    (mine / "SKILL.md").write_text("---\nname: my-skill\ndescription: mine\n---\n")
    (project / "AGENTS.md").write_text("# hand written\n")

    r = run("agents", "sync", "--check")
    assert r.exit_code == 1
    assert "update   .claude/skills/build-dashboard/SKILL.md" in r.output
    assert "remove   .agents/skills/old-skill" in r.output
    assert "skip     AGENTS.md" in r.output

    assert run("agents", "sync").exit_code == 0
    assert "local edit" not in skill.read_text()
    assert not stale.exists() and mine.exists()
    assert (project / "AGENTS.md").read_text() == "# hand written\n"
    r = run("agents", "sync", "--check")  # everything else is current, but agents read a hand-written guide
    assert r.exit_code == 1 and "1 file(s) not managed by axial" in r.output
    assert run("agents", "sync", "--force").exit_code == 0
    assert ag.is_managed(project / "AGENTS.md")
    assert run("agents", "sync", "--check").exit_code == 0


def test_marker_roundtrip():
    body = "---\nname: x\ndescription: y\n---\n# Body\n"
    marked = ag.add_marker(body, "1.2.3")
    assert marked.startswith("---\nname: x") and "axial:managed version=1.2.3" in marked
    assert ag.strip_marker(marked) == body
    assert ag.strip_marker(ag.add_marker("# A\n", "1")) == "# A\n"


def test_sync_migrates_legacy_managed_files_without_force(project):
    for rel in ag.desired_files():
        path = project / rel
        path.write_text(path.read_text().replace("axial:managed", "bqdash:managed"))
    notes = (project / "CONTEXT.md").read_text()
    assert run("agents", "sync", "--check").exit_code == 1
    result = run("agents", "sync")
    assert result.exit_code == 0, result.output
    for rel in ag.desired_files():
        text = (project / rel).read_text()
        assert "axial:managed" in text and "bqdash:managed" not in text
    assert (project / "CONTEXT.md").read_text() == notes
    assert run("agents", "sync", "--check").exit_code == 0


def test_guide_documents_every_tile_type_format_and_reducer():
    assert run("guide").exit_code == 0
    dashboards = (GUIDE_DIR / "dashboards.md").read_text()
    queries = (GUIDE_DIR / "queries.md").read_text()
    for t in TILE_TYPES:
        assert f"`{t}`" in dashboards, t
    for r in REDUCERS:
        assert f"`{r}`" in dashboards, r
    for f in FORMATS:
        assert f"`{f}`" in queries, f
    for k in COMMON_TILE_KEYS.union(*TILE_KEYS.values()):
        assert f"`{k}`" in dashboards, f"dashboards guide misses tile key {k}"
    assert run("guide", "nope").exit_code == 1


def test_skills_only_reference_real_commands():
    names = set(main.commands)
    for name in SKILLS + ["AGENTS"]:
        path = ag.SOURCE / ("AGENTS.md" if name == "AGENTS" else f"skills/{name}/SKILL.md")
        for cmd in re.findall(r"`axial ([a-z-]+)", path.read_text()):
            assert cmd in names, f"{path.name} mentions unknown command axial {cmd}"


def test_check_json(repo, monkeypatch):
    monkeypatch.chdir(repo)
    r = run("check", "--json")
    assert r.exit_code == 0
    assert json.loads(r.output) == {"errors": 0, "warnings": 0, "issues": []}


@pytest.fixture
def fake_wh(monkeypatch):
    wh = FakeWarehouse()
    monkeypatch.setattr("axial.cli._warehouse", lambda cfg: wh)
    return wh


def test_discovery_commands(repo, monkeypatch, fake_wh):
    monkeypatch.chdir(repo)
    r = run("tables", "--json")
    assert json.loads(r.output)["datasets"] == [{"id": "test-project.analytics"}]
    r = run("tables", "test-project.analytics", "--json")
    t = json.loads(r.output)["tables"]["test-project.analytics"][0]
    assert t["partitioning"]["field"] == "created_at"
    r = run("schema", "analytics.fct_bookings", "--json")
    s = json.loads(r.output)
    assert s["id"] == "test-project.analytics.fct_bookings" and s["clustering"] == ["region"]
    assert "partitioned by created_at" in run("schema", "analytics.fct_bookings").output


def test_dry_run_run_and_usage_json(repo, monkeypatch, fake_wh):
    monkeypatch.chdir(repo)
    r = run("dry-run", "bookings_daily", "--start", "2026-11-01", "--end", "2026-11-08", "--json")
    d = json.loads(r.output)
    assert r.exit_code == 0 and d["within_cap"] and d["statement_type"] == "SELECT"
    assert d["params"] == {"start_date": "2026-11-01", "end_date": "2026-11-08"}

    r = run("run", "bookings_daily", "--start", "2026-11-01", "--end", "2026-11-08", "--json")
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["rows"] == fake_wh.rows and out["job_id"].startswith("axial_bookings_daily_")

    r = run("usage", "--json")
    q = next(x for x in json.loads(r.output)["queries"] if x["query_id"] == "bookings_daily")
    assert q["jobs"] == 1 and q["bytes_billed"] == fake_wh.bytes_billed
    r = run("usage", "bookings_daily", "--json")
    assert json.loads(r.output)["versions"][0]["runs"] == 1

    fake_wh.bytes_billed = 10**12
    r = run("dry-run", "bookings_daily", "--json")
    assert r.exit_code == 1 and json.loads(r.output)["within_cap"] is False


def test_bench_json(repo, monkeypatch, fake_wh):
    monkeypatch.chdir(repo)
    r = run("bench", "bookings_daily", "--start", "2026-11-01", "--end", "2026-11-08", "--runs", "2", "--json")
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert len(out["runs"]) == 2 and out["versions"][0]["current"] and out["versions"][0]["runs"] == 2
    assert all(s["use_query_cache"] is False for s in fake_wh.submitted)
