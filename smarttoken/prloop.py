"""Interactive loop: create a PR, request Copilot review, fix medium/high, commit, repeat."""

from __future__ import annotations

import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from smarttoken.config import AppPaths
from smarttoken.github import (
    Finding,
    GitHubClient,
    GitHubError,
    actionable,
    build_fix_prompt,
    copilot_findings,
    is_copilot_login,
    parse_slug,
    token_from_env,
    token_from_gh,
)
from smarttoken.index_store import IndexStore

COPILOT_DENY = [
    "shell(git commit)",
    "shell(git push)",
    "shell(git checkout)",
    "shell(git reset)",
    "shell(git restore)",
    "shell(git stash)",
    "shell(git clean)",
    "shell(git switch)",
]
MAX_ROUNDS = 8
POLL_SECONDS = 8
POLL_ATTEMPTS = 40  # ~5 minutes


class RepoNotFound(ValueError):
    pass


def _git(cwd: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def git_remote_slug(cwd: str) -> Optional[Tuple[str, str]]:
    result = _git(cwd, "remote", "get-url", "origin", check=False)
    if result.returncode != 0:
        return None
    return parse_slug(result.stdout)


def current_branch(cwd: str) -> str:
    return _git(cwd, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def last_commit_subject(cwd: str) -> str:
    result = _git(cwd, "log", "-1", "--format=%s", check=False)
    return (result.stdout or "Changes").strip() or "Changes"


def working_tree_dirty(cwd: str) -> bool:
    result = _git(cwd, "status", "--porcelain")
    return bool(result.stdout.strip())


def resolve_named_repo(name: str, store: Optional[IndexStore], cwd: Optional[str]) -> Tuple[str, str, str]:
    """Return (owner, repo, local_path). Raises RepoNotFound."""
    wanted = (name or "").strip()
    if not wanted:
        raise RepoNotFound("Repo name is empty.")
    slug = parse_slug(wanted)
    indexed = list(store.list_repos()) if store else []

    if slug:
        owner, repo = slug
        for item in indexed:
            remote = parse_slug(item.remote_url or "")
            if remote and tuple(part.lower() for part in remote) == (owner.lower(), repo.lower()):
                return remote[0], remote[1], item.path
        if cwd:
            here = git_remote_slug(cwd)
            if here and tuple(part.lower() for part in here) == (owner.lower(), repo.lower()):
                return here[0], here[1], cwd
        raise RepoNotFound(
            f"Cannot find a local clone for '{owner}/{repo}'. Index or enter the matching clone first."
        )

    for item in indexed:
        if item.name.lower() == wanted.lower():
            remote = parse_slug(item.remote_url or "")
            if remote:
                return remote[0], remote[1], item.path
            raise RepoNotFound(
                f"Indexed repo '{item.name}' has no github.com remote. "
                "Enter it as owner/name, for example iqkhan70/Eats."
            )
        remote = parse_slug(item.remote_url or "")
        if remote and remote[1].lower() == wanted.lower():
            return remote[0], remote[1], item.path

    if cwd:
        folder = Path(cwd).name
        here = git_remote_slug(cwd)
        if here and (folder.lower() == wanted.lower() or here[1].lower() == wanted.lower()):
            return here[0], here[1], cwd

    names = ", ".join(item.name for item in indexed) or "(none indexed)"
    raise RepoNotFound(
        f"Cannot find repo '{wanted}'. Use owner/name (for example iqkhan70/Eats). Indexed: {names}."
    )


def authenticate(prompt_token: Callable[[], str], have_gh_login: Optional[Callable[[], bool]] = None) -> GitHubClient:
    token = token_from_env() or token_from_gh()
    if token:
        return GitHubClient(token)
    if have_gh_login is not None:
        if have_gh_login():
            token = token_from_gh()
            if token:
                return GitHubClient(token)
    entered = (prompt_token() or "").strip()
    if not entered:
        raise GitHubError("Not authenticated. Set GH_TOKEN, run `gh auth login`, or paste a token with repo scope.")
    return GitHubClient(entered)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def wait_for_copilot_review(
    client: GitHubClient,
    owner: str,
    repo: str,
    number: int,
    since: str,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """True when Copilot has submitted a review at or after `since` (ISO timestamp)."""
    for _ in range(POLL_ATTEMPTS):
        reviews = client.list_reviews(owner, repo, number)
        for row in reviews:
            if not is_copilot_login((row.get("user") or {}).get("login")):
                continue
            submitted = row.get("submitted_at") or ""
            if submitted >= since and row.get("state") in {"COMMENTED", "CHANGES_REQUESTED", "APPROVED"}:
                return True
        comments = client.list_comments(owner, repo, number)
        for row in comments:
            if not is_copilot_login((row.get("user") or {}).get("login")):
                continue
            stamped = row.get("updated_at") or row.get("created_at") or ""
            if stamped >= since:
                return True
        sleep(POLL_SECONDS)
    return False


def collect_actionable(client: GitHubClient, owner: str, repo: str, number: int) -> List[Finding]:
    comments = client.list_comments(owner, repo, number)
    try:
        threads = client.review_threads(owner, repo, number)
    except GitHubError:
        threads = []
    return actionable(copilot_findings(comments, threads))


def run_copilot_fix(prompt: str, cwd: str) -> Tuple[bool, str]:
    exe = shutil.which("copilot")
    if exe is None:
        return False, "copilot CLI is not on PATH. Install GitHub Copilot CLI, or apply the findings by hand."
    command = [
        exe,
        "-p",
        prompt,
        "--allow-all-tools",
        "--disable-builtin-mcps",
        "--no-ask-user",
        "--deny-tool",
        *COPILOT_DENY,
    ]
    try:
        result = subprocess.run(command, cwd=cwd, check=False, text=True)
    except OSError as exc:
        return False, f"Could not start copilot: {exc}"
    if result.returncode != 0:
        return False, f"copilot exited with code {result.returncode}."
    return True, "Copilot finished applying fixes in the working tree."


def commit_fixes(cwd: str, message: str) -> Tuple[bool, str]:
    add = _git(cwd, "add", "-A", check=False)
    if add.returncode != 0:
        return False, add.stderr.strip() or "git add failed."
    commit = _git(cwd, "commit", "-m", message, check=False)
    if commit.returncode != 0:
        err = (commit.stderr or commit.stdout or "").strip()
        return False, err or "git commit failed."
    return True, "Committed."


def push_branch(cwd: str) -> Tuple[bool, str]:
    result = _git(cwd, "push", "-u", "origin", "HEAD", check=False)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "git push failed.").strip()
    return True, "Pushed."


def run_loop(
    *,
    name: str,
    paths: AppPaths,
    ask: Callable[[str], str],
    confirm: Callable[[str, bool], bool],
    secret: Callable[[str], str],
    cwd: Optional[str] = None,
    client: Optional[GitHubClient] = None,
    sleep: Callable[[float], None] = time.sleep,
    fixer: Callable[[str, str], Tuple[bool, str]] = run_copilot_fix,
    committer: Callable[[str, str], Tuple[bool, str]] = commit_fixes,
    pusher: Callable[[str], Tuple[bool, str]] = push_branch,
    now_iso: Callable[[], str] = utc_now,
    out: Callable[[str], None] = print,
) -> int:
    cwd = cwd or str(Path.cwd())
    store = None
    if paths.db_file.exists():
        store = IndexStore(paths.db_file)
    try:
        owner, repo, local = resolve_named_repo(name, store, cwd)
    except RepoNotFound as exc:
        out(str(exc))
        return 1
    if client is None:
        try:
            client = authenticate(lambda: secret("GitHub token (repo scope, not stored): "))
        except GitHubError as exc:
            out(str(exc))
            return 1
    try:
        info = client.get_repo(owner, repo)
        login = client.whoami()
    except GitHubError as exc:
        out(str(exc))
        return 1
    local = local or cwd
    if not local or not (Path(local) / ".git").exists():
        out(f"No local git clone for {owner}/{repo}. cd into the clone, then run this again.")
        return 1
    out(f"Using {owner}/{repo} as {login}. Local clone: {local}")
    default_base = info.get("default_branch") or "main"
    branch = current_branch(local)
    if branch in {"HEAD", default_base, "main", "master"}:
        out(f"You are on '{branch}'. Create or check out a feature branch before opening a PR.")
        return 1
    if working_tree_dirty(local):
        out("Working tree is not clean. Commit or stash local changes first.")
        return 1

    pulls = client.open_pulls_for_head(owner, repo, branch)
    pr = pulls[0] if pulls else None
    if pr is None:
        if not confirm(f"No open PR for branch '{branch}'. Push and create one against {default_base}?", True):
            out("Cancelled. Nothing created.")
            return 0
        ok, note = pusher(local)
        if not ok:
            out(note)
            return 1
        title = ask(f"PR title [{last_commit_subject(local)}]: ").strip() or last_commit_subject(local)
        body = ask("PR body (optional): ").strip()
        try:
            pr = client.create_pull(owner, repo, title, branch, default_base, body)
        except GitHubError as exc:
            out(str(exc))
            return 1
        out(f"Created {pr.get('html_url')}")
    else:
        out(f"Open PR: {pr.get('html_url')}")

    if not confirm("PR looks good. Request a Copilot review now?", True):
        out("Stopped before Copilot review.")
        return 0

    for round_no in range(1, MAX_ROUNDS + 1):
        started = now_iso()
        try:
            client.request_copilot_review(owner, repo, int(pr["number"]))
        except GitHubError as exc:
            out(str(exc))
            out("If Copilot is not a valid reviewer, enable Copilot code review on this org/repo.")
            return 1
        out(f"Round {round_no}: waiting for Copilot review…")
        if not wait_for_copilot_review(client, owner, repo, int(pr["number"]), started, sleep=sleep):
            out("Timed out waiting for Copilot. Open the PR in the browser and check that code review is enabled.")
            return 1
        findings = collect_actionable(client, owner, repo, int(pr["number"]))
        if not findings:
            out("No unresolved medium or high Copilot findings. Done.")
            return 0
        out(f"{len(findings)} medium/high finding(s):")
        for item in findings:
            loc = f"{item.path}:{item.line}" if item.line else item.path
            out(f"  - {item.severity.upper()} {loc}")
        if not confirm("Have Copilot apply those fixes in this clone (it will not commit)?", True):
            out("Stopped. Findings are still open on the PR.")
            return 0
        prompt = build_fix_prompt(findings, str(pr.get("html_url") or ""))
        ok, note = fixer(prompt, local)
        out(note)
        if not ok:
            return 1
        if not working_tree_dirty(local):
            out("Copilot made no file changes. Stopping so you can inspect the findings.")
            return 1
        if not confirm("Commit Copilot's fixes on this branch?", False):
            out("Not committed. Review the working tree, then run this command again.")
            return 0
        message = f"Address Copilot review round {round_no} (medium/high)"
        ok, note = committer(local, message)
        out(note)
        if not ok:
            return 1
        if not confirm("Push the commit and request another Copilot review?", True):
            out("Commit is local only. Push later, then run this command again.")
            return 0
        ok, note = pusher(local)
        out(note)
        if not ok:
            return 1
    out(f"Reached {MAX_ROUNDS} rounds with findings still open. Inspect the PR by hand.")
    return 1
