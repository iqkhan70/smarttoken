import subprocess
from types import SimpleNamespace

import pytest

from smarttoken.prloop import RepoNotFound, commit_fixes, resolve_named_repo


def test_resolve_named_repo_requires_exact_remote_match(tmp_path, monkeypatch) -> None:
    indexed_repo = SimpleNamespace(
        name="target",
        remote_url="https://github.com/different-owner/target.git",
        path=str(tmp_path / "target"),
    )
    store = SimpleNamespace(list_repos=lambda: [indexed_repo])
    monkeypatch.setattr("smarttoken.prloop.git_remote_slug", lambda cwd: ("different-owner", "target"))

    with pytest.raises(RepoNotFound, match="local clone"):
        resolve_named_repo("requested-owner/target", store, str(tmp_path))


def test_resolve_named_repo_returns_matching_clone(tmp_path, monkeypatch) -> None:
    clone_path = str(tmp_path / "target")
    indexed_repo = SimpleNamespace(
        name="target",
        remote_url="https://github.com/owner/target.git",
        path=clone_path,
    )
    store = SimpleNamespace(list_repos=lambda: [indexed_repo])
    monkeypatch.setattr("smarttoken.prloop.git_remote_slug", lambda cwd: None)

    assert resolve_named_repo("owner/target", store, str(tmp_path)) == ("owner", "target", clone_path)


def test_commit_fixes_includes_new_files(tmp_path) -> None:
    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True
        )
        return result.stdout.strip()

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (tmp_path / "base.txt").write_text("base")
    git("add", "base.txt")
    git("commit", "-m", "initial")
    (tmp_path / "copilot-new.txt").write_text("new fix")

    ok, note = commit_fixes(str(tmp_path), "address review")

    assert ok, note
    assert "copilot-new.txt" in git("show", "--pretty=", "--name-only", "HEAD").splitlines()