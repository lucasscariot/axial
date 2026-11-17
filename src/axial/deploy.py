"""`axial deploy`: build this project's image and run it on Cloud Run behind IAP.

One fixed shape, with no knobs beyond `deploy:` in axial.yaml: a private service that only
Identity-Aware Proxy can invoke, a dedicated service account that can run BigQuery jobs and
read `bigquery.datasets`, an image tagged with the Git commit, and a single instance (the
app cache and job sharing are per process). Every step is safe to repeat.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import Callable

import click

from .config import CONFIG_FILE, Config

APIS = ("run.googleapis.com", "cloudbuild.googleapis.com", "artifactregistry.googleapis.com", "iap.googleapis.com")
# also the service account ID, which allows 6-30 lowercase letters, digits and hyphens
SERVICE_NAME = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
PLACEHOLDER_NUMBER = "PROJECT_NUMBER"


@dataclass
class Target:
    project: str
    region: str
    service: str
    viewers: list[str]
    datasets: list[str]  # project.dataset
    record_viewers: bool
    location: str | None

    @property
    def service_account(self) -> str:
        return f"{self.service}@{self.project}.iam.gserviceaccount.com"

    def image(self, revision: str) -> str:
        return f"{self.region}-docker.pkg.dev/{self.project}/{self.service}/{self.service}:{revision}"


def target(cfg: Config) -> Target:
    """The deployment `deploy:` describes, or one error that lists everything missing."""
    if cfg.local:
        raise click.ClickException("axial deploy publishes BigQuery projects; a DuckDB project's database "
                                   "file is not part of the image (see `axial guide deploy`)")
    project = cfg.deploy_project or cfg.billing_project
    missing = [name for name, value in (("deploy.service", cfg.deploy_service), ("deploy.region", cfg.deploy_region),
                                        ("deploy.viewers", cfg.deploy_viewers),
                                        ("bigquery.billing_project", cfg.billing_project),
                                        ("bigquery.datasets", cfg.datasets)) if not value]
    if missing:
        raise click.ClickException(
            f"set {', '.join(missing)} in {CONFIG_FILE} first. bigquery.datasets lists the datasets the "
            "service account may read; deploy.viewers lists who may open the dashboards.")
    if not SERVICE_NAME.match(str(cfg.deploy_service)):
        raise click.ClickException("deploy.service must be 6-30 lowercase letters, digits or hyphens, starting "
                                   "with a letter: it also names the service account")
    datasets = [d if "." in d else f"{cfg.billing_project}.{d}" for d in cfg.datasets]
    return Target(project=project, region=cfg.deploy_region, service=cfg.deploy_service,
                  viewers=list(cfg.deploy_viewers), datasets=datasets,
                  record_viewers=cfg.deploy_record_viewers, location=cfg.location)


def run_command(command: list[str], cwd, capture: bool) -> str:
    result = subprocess.run(command, cwd=cwd, check=True, text=True, stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ""


def grant_dataset_readers(t: Target, token: str) -> None:
    """Give the runtime service account READER on each dataset, leaving other entries alone."""
    from google.cloud import bigquery
    from google.oauth2.credentials import Credentials

    # The gcloud identity's token stays in memory: the developer's ADC file is not touched.
    client = bigquery.Client(project=t.project, credentials=Credentials(token), location=t.location)
    entry = bigquery.AccessEntry("READER", "userByEmail", t.service_account)
    for name in t.datasets:
        dataset = client.get_dataset(name)
        if entry not in dataset.access_entries:
            dataset.access_entries = [*dataset.access_entries, entry]
            client.update_dataset(dataset, ["access_entries"])


def deploy(cfg: Config, *, account: str | None = None, skip_build: bool = False, dry_run: bool = False,
           runner: Callable[[list[str], object, bool], str] = run_command,
           grant: Callable[[Target, str], None] = grant_dataset_readers) -> dict:
    """Run (or, with dry_run, print) every step. Returns the service URL, commit and image."""
    t = target(cfg)

    def sh(command: list[str], *, capture: bool = False, change: bool = True) -> str:
        """`change` commands alter cloud resources; a dry run prints them instead."""
        click.echo("+ " + shlex.join(command))
        if dry_run and change:
            return ""
        return runner(command, cfg.root, capture)

    def gcloud(*args: str, capture: bool = False, change: bool = True) -> str:
        scope = [f"--project={t.project}", *([f"--account={account}"] if account else []), "--quiet"]
        return sh(["gcloud", *args, *scope], capture=capture, change=change)

    def listed(*args: str) -> list[dict]:
        if dry_run:  # nothing is looked up, so every "create if missing" step is shown
            return []
        return json.loads(gcloud(*args, "--format=json", capture=True, change=False))

    try:
        if not dry_run and runner(["git", "status", "--porcelain"], cfg.root, True):
            raise click.ClickException("commit your changes first: the image is tagged with the Git commit, "
                                       "so the working tree must be clean")
        revision = runner(["git", "rev-parse", "HEAD"], cfg.root, True)
    except (OSError, subprocess.CalledProcessError):
        raise click.ClickException("axial deploy needs a Git repository with at least one commit") from None
    image = t.image(revision)

    try:
        if dry_run:
            number = PLACEHOLDER_NUMBER
        else:
            number = json.loads(gcloud("projects", "describe", t.project, "--format=json",
                                       capture=True, change=False))["projectNumber"]
        gcloud("services", "enable", *APIS)

        if not any(a["email"] == t.service_account for a in listed("iam", "service-accounts", "list")):
            gcloud("iam", "service-accounts", "create", t.service, f"--display-name={t.service} dashboards runtime")
        gcloud("projects", "add-iam-policy-binding", t.project, f"--member=serviceAccount:{t.service_account}",
               "--role=roles/bigquery.jobUser", "--condition=None", "--format=none")
        click.echo(f"+ grant {t.service_account} READER on {', '.join(t.datasets)}")
        if not dry_run:
            grant(t, gcloud("auth", "print-access-token", capture=True, change=False))

        repositories = listed("artifacts", "repositories", "list", f"--location={t.region}")
        if not any(r["name"].endswith(f"/repositories/{t.service}") for r in repositories):
            gcloud("artifacts", "repositories", "create", t.service, "--repository-format=docker",
                   f"--location={t.region}", "--immutable-tags")
        gcloud("beta", "services", "identity", "create", "--service=iap.googleapis.com")
        if not skip_build:
            # .dockerignore is the allowlist of runtime files, so it also bounds the upload
            gcloud("builds", "submit", ".", f"--tag={image}", "--ignore-file=.dockerignore",
                   f"--region={t.region}", "--default-buckets-behavior=regional-user-owned-bucket")

        env = [f"AXIAL_APP_REVISION={revision}", *(["AXIAL_SERVER_TRUST_IAP_USER=true"] if t.record_viewers else [])]
        gcloud("run", "deploy", t.service, f"--image={image}", f"--region={t.region}",
               f"--service-account={t.service_account}", "--iap", "--no-allow-unauthenticated", "--ingress=all",
               "--cpu=1", "--memory=512Mi", "--min=0", "--max=1", "--max-instances=1", "--concurrency=20",
               "--timeout=120", "--port=8080", "--cpu-throttling",
               "--startup-probe=httpGet.path=/api/health,httpGet.port=8080,periodSeconds=5,timeoutSeconds=2,"
               "failureThreshold=24",
               f"--set-env-vars={','.join(env)}")
        gcloud("run", "services", "add-iam-policy-binding", t.service, f"--region={t.region}",
               f"--member=serviceAccount:service-{number}@gcp-sa-iap.iam.gserviceaccount.com",
               "--role=roles/run.invoker", "--format=none")
        for viewer in t.viewers:
            gcloud("iap", "web", "add-iam-policy-binding", "--resource-type=cloud-run", f"--service={t.service}",
                   f"--region={t.region}", f"--member={viewer}", "--role=roles/iap.httpsResourceAccessor",
                   "--format=none")
        url = None
        if not dry_run:
            url = json.loads(gcloud("run", "services", "describe", t.service, f"--region={t.region}",
                                    "--format=json", capture=True, change=False))["status"]["url"]
    except FileNotFoundError:
        raise click.ClickException("gcloud is not installed or not on PATH "
                                   "(https://cloud.google.com/sdk/docs/install)") from None
    except subprocess.CalledProcessError as e:
        raise click.ClickException(f"`{shlex.join(e.cmd[:4])} …` failed; see its output above. "
                                   "Every step is safe to repeat once fixed.") from None
    return {"url": url, "commit": revision, "image": image, "viewers": t.viewers}
