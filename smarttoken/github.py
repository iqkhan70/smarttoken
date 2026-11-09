"""GitHub REST/GraphQL helpers for the PR review loop. Token is never written to disk."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

API = "https://api.github.com"
COPILOT_REVIEWER = "copilot-pull-request-reviewer[bot]"
COPILOT_LOGINS = frozenset(
    {
        "copilot",
        "copilot-pull-request-reviewer[bot]",
        "copilot-pull-request-reviewer",
    }
)
_SLUG_RE = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?", re.I)
_SEVERITY_RE = re.compile(
    r"(?is)"
    r"(?:<!--\s*copilot-severity:\s*(high|medium|low)\s*-->)"
    r"|(?:\bseverity\s*[:\-]\s*(high|medium|low)\b)"
    r"|(?:\b(?:priority|importance)\s*[:\-]\s*(high|medium|low)\b)"
    r"|(?:^\s*\*{0,2}(high|medium|low)\*{0,2}\s*[:\-])"
    r"|(?:(?:🔴|🟠|🟡|:red_circle:|:orange_circle:|:yellow_circle:)\s*\*{0,2}(high|medium|low))"
)
_OVERVIEW_COMMENT_RE = re.compile(r"discussion_r(?P<id>\d+)")
_OVERVIEW_SEVERITY_RE = re.compile(
    r"(?i)(?:alt=[\"'](?P<alt>high|medium|low) severity[\"']|\b(?P<plain>high|medium|low) severity\b)"
)


class GitHubError(RuntimeError):
    pass


@dataclass
class Finding:
    comment_id: int
    path: str
    line: Optional[int]
    severity: str
    body: str
    html_url: str
    resolved: bool = False


def parse_slug(text: str) -> Optional[Tuple[str, str]]:
    """owner, repo from 'owner/repo' or a github.com URL."""
    raw = (text or "").strip().rstrip("/")
    match = _SLUG_RE.search(raw)
    if match:
        return match.group("owner"), match.group("repo")
    if raw.count("/") == 1 and " " not in raw and not raw.startswith("."):
        owner, repo = raw.split("/", 1)
        repo = repo[:-4] if repo.endswith(".git") else repo
        if owner and repo:
            return owner, repo
    return None


def parse_severity(body: str) -> str:
    """high / medium / low / unknown from a Copilot review comment body."""
    if not body:
        return "unknown"
    match = _SEVERITY_RE.search(body)
    if not match:
        return "unknown"
    value = next((group for group in match.groups() if group), "unknown")
    return value.lower()


def is_copilot_login(login: Optional[str]) -> bool:
    return bool(login) and login.lower() in COPILOT_LOGINS


def token_from_env() -> Optional[str]:
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GITHUB_PAT"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return None


def token_from_gh() -> Optional[str]:
    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        result = subprocess.run(
            [gh, "auth", "token"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    token = (result.stdout or "").strip()
    return token if result.returncode == 0 and token else None


class GitHubClient:
    def __init__(self, token: str) -> None:
        if not token:
            raise GitHubError("GitHub token is empty.")
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        graphql: bool = False,
    ) -> Any:
        url = "https://api.github.com/graphql" if graphql else f"{API}{path}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "smarttoken-prloop",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise GitHubError(f"GitHub {exc.code} on {method} {path}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise GitHubError(f"Could not reach GitHub: {exc.reason}") from exc

    def whoami(self) -> str:
        user = self.request("GET", "/user")
        login = user.get("login")
        if not login:
            raise GitHubError("Token is valid but has no user login. Check scopes.")
        return str(login)

    def get_repo(self, owner: str, repo: str) -> Dict[str, Any]:
        try:
            return self.request("GET", f"/repos/{owner}/{repo}")
        except GitHubError as exc:
            if " 404 " in str(exc):
                raise GitHubError(
                    f"Repository '{owner}/{repo}' was not found, or this token cannot see it."
                ) from exc
            raise

    def default_branch(self, owner: str, repo: str) -> str:
        return str(self.get_repo(owner, repo).get("default_branch") or "main")

    def open_pulls_for_head(self, owner: str, repo: str, head: str) -> List[Dict[str, Any]]:
        data = self.request("GET", f"/repos/{owner}/{repo}/pulls?head={owner}:{head}&state=open")
        return data if isinstance(data, list) else []

    def create_pull(
        self, owner: str, repo: str, title: str, head: str, base: str, body: str
    ) -> Dict[str, Any]:
        return self.request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            {"title": title, "head": head, "base": base, "body": body},
        )

    def request_copilot_review(self, owner: str, repo: str, number: int) -> None:
        self.request(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{number}/requested_reviewers",
            {"reviewers": [COPILOT_REVIEWER]},
        )

    def list_reviews(self, owner: str, repo: str, number: int) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        page = 1
        while True:
            data = self.request("GET", f"/repos/{owner}/{repo}/pulls/{number}/reviews?per_page=100&page={page}")
            if not isinstance(data, list):
                break
            rows.extend(data)
            if len(data) < 100:
                break
            page += 1
        return rows

    def list_comments(self, owner: str, repo: str, number: int) -> List[Dict[str, Any]]:
        data = self.request("GET", f"/repos/{owner}/{repo}/pulls/{number}/comments")
        return data if isinstance(data, list) else []

    def review_threads(self, owner: str, repo: str, number: int) -> List[Dict[str, Any]]:
        query = """
        query($owner: String!, $name: String!, $number: Int!) {
          repository(owner: $owner, name: $name) {
            pullRequest(number: $number) {
              reviewThreads(first: 100) {
                nodes {
                  isResolved
                  comments(first: 20) {
                    nodes { databaseId author { login } body path }
                  }
                }
              }
            }
          }
        }
        """
        data = self.request(
            "POST",
            "/graphql",
            {"query": query, "variables": {"owner": owner, "name": repo, "number": number}},
            graphql=True,
        )
        errors = data.get("errors") if isinstance(data, dict) else None
        if errors:
            raise GitHubError(f"GitHub GraphQL error: {errors[0].get('message', errors)}")
        nodes = (
            data.get("data", {})
            .get("repository", {})
            .get("pullRequest", {})
            .get("reviewThreads", {})
            .get("nodes")
            or []
        )
        return nodes


def copilot_findings(
    comments: List[Dict[str, Any]],
    threads: Optional[List[Dict[str, Any]]] = None,
    reviews: Optional[List[Dict[str, Any]]] = None,
) -> List[Finding]:
    overview_severities: Dict[int, str] = {}
    for review in reviews or []:
        if not is_copilot_login((review.get("user") or {}).get("login")):
            continue
        for line in str(review.get("body") or "").splitlines():
            comment_match = _OVERVIEW_COMMENT_RE.search(line)
            severity_match = _OVERVIEW_SEVERITY_RE.search(line)
            if comment_match and severity_match:
                severity = severity_match.group("alt") or severity_match.group("plain")
                overview_severities[int(comment_match.group("id"))] = severity.lower()

    resolved_ids = set()
    for thread in threads or []:
        if not thread.get("isResolved"):
            continue
        for node in (thread.get("comments") or {}).get("nodes") or []:
            cid = node.get("databaseId")
            if cid:
                resolved_ids.add(int(cid))
    findings: List[Finding] = []
    for row in comments:
        user = (row.get("user") or {}).get("login")
        if not is_copilot_login(user):
            continue
        if row.get("in_reply_to_id"):
            continue
        cid = int(row["id"])
        body = str(row.get("body") or "").strip()
        severity = parse_severity(body)
        if severity == "unknown":
            severity = overview_severities.get(cid, severity)
        findings.append(
            Finding(
                comment_id=cid,
                path=str(row.get("path") or ""),
                line=row.get("line") or row.get("original_line"),
                severity=severity,
                body=body,
                html_url=str(row.get("html_url") or ""),
                resolved=cid in resolved_ids,
            )
        )
    return findings


def actionable(findings: List[Finding]) -> List[Finding]:
    return [item for item in findings if not item.resolved and item.severity in {"high", "medium"}]


def build_fix_prompt(findings: List[Finding], pr_url: str) -> str:
    lines = [
        "GitHub Copilot left these pull-request review comments. They are medium or high severity.",
        f"PR: {pr_url}",
        "Fix each one with the smallest correct change. Do not commit, push, or change git state.",
        "Do not address low-severity comments unless they fall out of a high/medium fix.",
        "",
    ]
    for index, item in enumerate(findings, start=1):
        where = f"{item.path}" + (f":{item.line}" if item.line else "")
        lines.append(f"## {index}. {item.severity.upper()} — {where}")
        if item.html_url:
            lines.append(item.html_url)
        lines.append(item.body)
        lines.append("")
    return "\n".join(lines)
