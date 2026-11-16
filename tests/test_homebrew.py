"""The formula updater must publish a complete Axial package, never an old bqdash tag."""

import io
import tarfile

import pytest

from scripts import update_homebrew as brew


def archive(*, ref="v0.2.0", name="axial", version="0.2.0", lock_version="0.2.0", with_ui=True):
    root = f"axial-{ref.removeprefix('v')}"
    files = {
        f"{root}/pyproject.toml": f'[project]\nname = "{name}"\nversion = "{version}"\n',
        f"{root}/uv.lock": f'[[package]]\nname = "{name}"\nversion = "{lock_version}"\n',
    }
    if with_ui:
        files[f"{root}/src/axial/web_dist/index.html"] = "<!doctype html>"
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as target:
        for path, text in files.items():
            content = text.encode()
            member = tarfile.TarInfo(path)
            member.size = len(content)
            target.addfile(member, io.BytesIO(content))
    return data.getvalue()


@pytest.fixture
def formula(tmp_path):
    path = tmp_path / "axial.rb"
    path.write_text(brew.FORMULA.read_text())
    return path


def test_update_uses_archive_checksum_and_matching_release(formula, monkeypatch):
    data = archive()
    requests = []

    def download(url, **kwargs):
        requests.append(url)
        return io.BytesIO(data)

    monkeypatch.setattr(brew, "urlopen", download)
    brew.update_formula("v0.2.0", formula)
    assert requests == ["https://github.com/lucasscariot/axial/archive/refs/tags/v0.2.0.tar.gz"]
    text = formula.read_text()
    assert 'version "0.2.0"' in text
    assert f'sha256 "{brew.hashlib.sha256(data).hexdigest()}"' in text
    assert '--frozen' in text and '"--extra", "duckdb"' in text


def test_initial_commit_is_immutable_and_uses_package_version(formula, monkeypatch):
    ref = "a" * 40
    monkeypatch.setattr(brew, "urlopen", lambda *a, **kw: io.BytesIO(archive(ref=ref)))
    brew.update_formula(ref, formula)
    assert f'/archive/{ref}.tar.gz"' in formula.read_text()
    assert 'version "0.2.0"' in formula.read_text()


@pytest.mark.parametrize("metadata", [
    {"name": "bqdash"},
    {"version": "0.1.2"},
    {"lock_version": "0.1.2"},
    {"with_ui": False},
])
def test_invalid_release_leaves_formula_untouched(formula, monkeypatch, metadata):
    before = formula.read_bytes()
    monkeypatch.setattr(brew, "urlopen", lambda *a, **kw: io.BytesIO(archive(**metadata)))
    with pytest.raises((ValueError, KeyError)):
        brew.update_formula("v0.2.0", formula)
    assert formula.read_bytes() == before


@pytest.mark.parametrize("ref", ["main", "v0.2.0;echo bad", "abc123", "v0.2.0-rc1"])
def test_rejects_moving_or_invalid_refs_before_network(formula, monkeypatch, ref):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid refs must not make a network request")

    monkeypatch.setattr(brew, "urlopen", unexpected)
    with pytest.raises(ValueError):
        brew.update_formula(ref, formula)
