"""`axial deploy`: the gcloud commands it runs, without running gcloud."""

import json
import subprocess

import pytest
from click.testing import CliRunner

from axial import deploy as dp
from axial.cli import main
from axial.config import ConfigError, load_config

DEPLOY = """
deploy:
  service: ops-dashboards
  region: europe-west2
  viewers: [user:jane@example.com, group:ops@example.com]
"""
SA = "ops-dashboards@test-project.iam.gserviceaccount.com"
IMAGE = "europe-west2-docker.pkg.dev/test-project/ops-dashboards/ops-dashboards:abc123"


@pytest.fixture
def project(repo, monkeypatch):
    p = repo / "axial.yaml"
    p.write_text(p.read_text().replace("  location: EU\n", "  location: EU\n  datasets: [analytics, other.shared]\n")
                 + DEPLOY)
    monkeypatch.chdir(repo)
    return repo


class Gcloud:
    """Answers the lookups `deploy` makes and records every command."""

    def __init__(self, dirty="", existing=False):
        self.commands: list[list[str]] = []
        self.dirty, self.existing = dirty, existing

    def __call__(self, command, cwd, capture):
        self.commands.append(command)
        text = " ".join(command)
        if text == "git status --porcelain":
            return self.dirty
        if text == "git rev-parse HEAD":
            return "abc123"
        if "projects describe" in text:
            return json.dumps({"projectNumber": "42"})
        if "service-accounts list" in text:
            return json.dumps([{"email": SA}] if self.existing else [])
        if "repositories list" in text:
            name = "projects/test-project/locations/europe-west2/repositories/ops-dashboards"
            return json.dumps([{"name": name}] if self.existing else [])
        if "print-access-token" in text:
            return "token"
        if "services describe" in text:
            return json.dumps({"status": {"url": "https://ops-dashboards.run.app"}})
        return ""

    def ran(self, *words):
        return [c for c in self.commands if all(w in c for w in words)]


def test_deploy_runs_every_step_as_a_private_iap_service(project):
    gcloud, granted = Gcloud(), []
    out = dp.deploy(load_config(project, environ={}), runner=gcloud, grant=lambda t, token: granted.append((t, token)))
    assert out == {"url": "https://ops-dashboards.run.app", "commit": "abc123", "image": IMAGE,
                   "viewers": ["user:jane@example.com", "group:ops@example.com"]}
    assert all("--project=test-project" in c and "--quiet" in c for c in gcloud.commands if c[0] == "gcloud")

    assert gcloud.ran("service-accounts", "create", "ops-dashboards")
    assert gcloud.ran("projects", "add-iam-policy-binding", f"--member=serviceAccount:{SA}",
                      "--role=roles/bigquery.jobUser")
    (t, token), = granted
    assert token == "token" and t.datasets == ["test-project.analytics", "other.shared"]
    assert gcloud.ran("repositories", "create", "--immutable-tags")
    assert gcloud.ran("builds", "submit", f"--tag={IMAGE}", "--ignore-file=.dockerignore")

    run, = gcloud.ran("run", "deploy")
    for flag in (f"--image={IMAGE}", f"--service-account={SA}", "--iap", "--no-allow-unauthenticated",
                 "--max-instances=1", "--set-env-vars=AXIAL_APP_REVISION=abc123"):
        assert flag in run, flag
    assert not any("allow-unauthenticated" in a and not a.startswith("--no-") for a in run)
    assert gcloud.ran("run", "services", "add-iam-policy-binding",
                      "--member=serviceAccount:service-42@gcp-sa-iap.iam.gserviceaccount.com")
    viewers = [next(a for a in c if a.startswith("--member=")) for c in gcloud.ran("iap", "web")]
    assert viewers == ["--member=user:jane@example.com", "--member=group:ops@example.com"]


def test_deploy_grants_job_access_on_the_billing_project(project):
    gcloud = Gcloud()
    cfg = load_config(project, environ={"AXIAL_DEPLOY_PROJECT": "hosting-project"})
    dp.deploy(cfg, runner=gcloud, grant=lambda t, token: None)
    sa = "ops-dashboards@hosting-project.iam.gserviceaccount.com"
    binding, = gcloud.ran("projects", "add-iam-policy-binding")
    assert binding[:4] == ["gcloud", "projects", "add-iam-policy-binding", "test-project"]
    assert f"--member=serviceAccount:{sa}" in binding and "--role=roles/bigquery.jobUser" in binding
    assert f"--service-account={sa}" in gcloud.ran("run", "deploy")[0]
    # only that grant is scoped to the billing project; everything else is in the hosting project
    assert "--project=test-project" in binding and "--project=hosting-project" not in binding
    others = [c for c in gcloud.commands if c[0] == "gcloud" and c is not binding]
    assert others and all("--project=hosting-project" in c and "--project=test-project" not in c for c in others)
    assert gcloud.ran("projects", "describe")[0][3] == "hosting-project"


def test_deploy_reuses_what_exists_and_can_skip_the_build(project):
    gcloud = Gcloud(existing=True)
    cfg = load_config(project, environ={"AXIAL_DEPLOY_RECORD_VIEWERS": "true"})
    dp.deploy(cfg, account="me@example.com", skip_build=True, runner=gcloud, grant=lambda t, token: None)
    assert not gcloud.ran("service-accounts", "create") and not gcloud.ran("repositories", "create")
    assert not gcloud.ran("builds", "submit")
    assert all("--account=me@example.com" in c for c in gcloud.commands if c[0] == "gcloud")
    run, = gcloud.ran("run", "deploy")
    assert "--set-env-vars=AXIAL_APP_REVISION=abc123,AXIAL_SERVER_TRUST_IAP_USER=true" in run


def test_deploy_refuses_a_dirty_tree_and_incomplete_config(project, repo):
    with pytest.raises(Exception, match="commit your changes first"):
        dp.deploy(load_config(project, environ={}), runner=Gcloud(dirty=" M queries/x.sql"), grant=None)
    cfg = load_config(project, environ={"AXIAL_DEPLOY_SERVICE": "", "AXIAL_BIGQUERY_DATASETS": ""})
    with pytest.raises(Exception, match=r"set deploy\.service, bigquery\.datasets in axial\.yaml first"):
        dp.deploy(cfg, runner=Gcloud(), grant=None)
    with pytest.raises(Exception, match="6-30 lowercase"):
        dp.deploy(load_config(project, environ={"AXIAL_DEPLOY_SERVICE": "Ops"}), runner=Gcloud(), grant=None)
    with pytest.raises(ConfigError, match="deploy.viewers must be a list of IAM members"):
        load_config(project, environ={"AXIAL_DEPLOY_VIEWERS": "jane@example.com"})
    with pytest.raises(ConfigError, match="record_viewers must be true or false"):
        load_config(project, environ={"AXIAL_DEPLOY_RECORD_VIEWERS": "sometimes"})


def test_dry_run_prints_the_plan_and_changes_nothing(project, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        assert command[0] == "git", f"dry run executed {command}"
        out = "" if command[1] == "status" else "abc123\n"
        return subprocess.CompletedProcess(command, 0, stdout=out)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(dp, "grant_dataset_readers", lambda *a: pytest.fail("granted access in a dry run"))
    r = CliRunner().invoke(main, ["deploy", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert calls == [["git", "rev-parse", "HEAD"]]
    for expected in ("+ gcloud services enable run.googleapis.com", "service-accounts create ops-dashboards",
                     f"+ grant {SA} READER on test-project.analytics, other.shared",
                     f"builds submit . --tag={IMAGE}", "--iap --no-allow-unauthenticated",
                     "service-PROJECT_NUMBER@gcp-sa-iap.iam.gserviceaccount.com",
                     "--member=group:ops@example.com", "dry run: nothing was changed"):
        assert expected in r.output, expected


def test_deploy_command_reports_missing_gcloud_and_failed_steps(project, monkeypatch):
    def no_gcloud(command, cwd, capture):
        if command[0] == "git":
            return "" if command[1] == "status" else "abc123"
        raise FileNotFoundError("gcloud")

    monkeypatch.setattr(dp, "run_command", no_gcloud)
    with pytest.raises(Exception, match="gcloud is not installed"):
        dp.deploy(load_config(project, environ={}), runner=no_gcloud, grant=None)

    def failing(command, cwd, capture):
        if command[0] == "git":
            return "" if command[1] == "status" else "abc123"
        raise subprocess.CalledProcessError(1, command)

    with pytest.raises(Exception, match="failed; see its output above"):
        dp.deploy(load_config(project, environ={}), runner=failing, grant=None)


def test_deploy_is_for_bigquery_projects(tmp_path, monkeypatch):
    target = tmp_path / "p"
    assert CliRunner().invoke(main, ["init", str(target), "--duckdb", "data.duckdb"]).exit_code == 0
    monkeypatch.chdir(target)
    r = CliRunner().invoke(main, ["deploy", "--dry-run"])
    assert r.exit_code == 1 and "publishes BigQuery projects" in r.output
