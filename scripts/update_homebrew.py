"""Pin the Homebrew formula to an Axial release tag (or an initial immutable commit)."""

from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path
import re
import tarfile
import tomllib
from urllib.request import urlopen

FORMULA = Path(__file__).resolve().parents[1] / "Formula" / "axial.rb"


def update_formula(ref: str, path: Path = FORMULA) -> None:
    tag = re.fullmatch(r"v\d+\.\d+\.\d+", ref)
    if not tag and not re.fullmatch(r"[a-f0-9]{40}", ref):
        raise ValueError("source must be a vX.Y.Z release tag or a full commit SHA")
    archive_ref = f"refs/tags/{ref}" if tag else ref
    url = f"https://github.com/lucasscariot/axial/archive/{archive_ref}.tar.gz"
    with urlopen(url, timeout=60) as response:
        archive = response.read()
    # Inspect metadata without extracting or executing code from the archive.
    root = f"axial-{ref[1:] if tag else ref}"
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source:
        metadata = source.extractfile(f"{root}/pyproject.toml")
        if metadata is None:
            raise ValueError("archive has no package metadata")
        project = tomllib.load(metadata)["project"]
        version = project["version"]
        if project["name"] != "axial":
            raise ValueError("source predates the Axial rename; choose an Axial release")
        if tag and version != ref[1:]:
            raise ValueError("release tag does not match the package version")
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError("expected a stable package version")
        lockfile = source.extractfile(f"{root}/uv.lock")
        if lockfile is None or not any(
            p["name"] == "axial" and p["version"] == version
            for p in tomllib.load(lockfile)["package"]
        ):
            raise ValueError("lockfile does not match the Axial package version")
        source.getmember(f"{root}/src/axial/web_dist/index.html")

    formula = path.read_text()
    # Homebrew reads the version from a tag URL and rejects a `version` line that repeats it
    # (`brew audit`: "redundant with version scanned from URL"). A commit URL carries none.
    formula, count = re.subn(r'^  version "[^"]*"\n', "", formula, flags=re.M)
    if count > 1:
        raise ValueError("expected at most one version in the formula")
    replacements = {
        "url": url if tag else f'{url}"\n  version "{version}',
        "sha256": hashlib.sha256(archive).hexdigest(),
    }
    for field, value in replacements.items():
        formula, count = re.subn(rf'^  {field} "[^"]*"$', f'  {field} "{value}"', formula, flags=re.M)
        if count != 1:
            raise ValueError(f"expected one {field} in the formula")
    path.write_text(formula)
    print(f"Homebrew formula pinned to {ref} (Axial {version})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ref", help="published release tag, e.g. v0.2.0")
    args = parser.parse_args()
    try:
        update_formula(args.ref)
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
