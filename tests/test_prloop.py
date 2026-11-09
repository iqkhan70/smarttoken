import subprocess
from types import SimpleNamespace

import pytest

from smarttoken.prloop import RepoNotFound, commit_fixes, resolve_named_repo
from smarttoken.github import actionable, copilot_findings


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


def test_copilot_review_overview_severities_apply_to_inline_comments() -> None:
    reviews = [
        {
            "user": {"login": "copilot-pull-request-reviewer[bot]"},
            "body": '\n- <img alt="High severity"> [Redact safely](https://github.com/o/r/pull/1#discussion_r123) · New\n'
            '- <img alt="Medium severity"> [Bound packet](https://github.com/o/r/pull/1#discussion_r456) · New\n',
        }
    ]
    comments = [
        {
            "id": 123,
            "user": {"login": "Copilot"},
            "path": "smarttoken/search.py",
            "body": "This can leak a secret.",
            "html_url": "https://github.com/o/r/pull/1#discussion_r123",
        },
        {
            "id": 456,
            "user": {"login": "Copilot"},
            "path": "smarttoken/mcp_server.py",
            "body": "This can exceed the budget.",
            "html_url": "https://github.com/o/r/pull/1#discussion_r456",
        },
    ]

    findings = copilot_findings(comments, reviews=reviews)

    assert [item.severity for item in findings] == ["high", "medium"]
    assert len(actionable(findings)) == 2