"""Path / role scoring on top of FTS ranks."""

from __future__ import annotations

import re
from pathlib import Path

from smarttoken.models import SearchHit

ROLE_BOOST = {
    "source": 1.3,
    "doc": 1.1,
    "test": 0.9,
    "config": 0.8,
    "generated": 0.15,
}

FIX_HINTS = ("fix", "bug", "error", "fail", "retr", "crash", "regress")
ENHANCE_HINTS = (
    "add", "enhanc", "implement", "featur", "support",
    "chang", "reject", "validat", "prevent", "must", "mak", "remov", "renam", "replac",
)
# Stems plus common endings: "added" and "validation" match, "address" and "making-of" style noise doesn't.
_HINT_SUFFIX = r"(?:e|es|ed|s|ing|ion|ions|ation|ure|ures|y|ies|ied|ying)?\b"
_FIX_RE = re.compile(r"\b(?:" + "|".join(FIX_HINTS) + ")" + _HINT_SUFFIX)
_ENHANCE_RE = re.compile(r"\b(?:" + "|".join(ENHANCE_HINTS) + ")" + _HINT_SUFFIX)
# Quoted text in a task ("Rating must be between 1 and 5") is usually a literal from the code.
_QUOTED_RE = re.compile(r"\"([^\"]{6,120})\"|(?<![\w'])'([^']{6,120})'(?![\w'])|`([^`]{6,120})`")

STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "to",
        "of",
        "in",
        "on",
        "for",
        "from",
        "with",
        "by",
        "at",
        "as",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "where",
        "what",
        "which",
        "who",
        "how",
        "why",
        "when",
        "does",
        "do",
        "did",
        "can",
        "could",
        "should",
        "would",
        "we",
        "you",
        "i",
        "me",
        "my",
        "our",
        "your",
        "and",
        "or",
        "not",
        "into",
        "using",
        "use",
        "used",
        "please",
        "show",
        "tell",
        "find",
        "locate",
        "implemented",
        "implementation",
        "explain",
        "about",
        "does",
    }
)

# Intent only — do not search or path-boost these.
TASK_VERBS = frozenset({"fix", "add", "enhance", "implement", "create", "update", "make"})


def task_kind(query: str) -> str:
    q = query.lower()
    if _FIX_RE.search(q):
        return "fix"
    if _ENHANCE_RE.search(q):
        return "enhance"
    return "understand"


_SENSITIVE_NAME_RE = re.compile(
    r"(?i)(keystore|\.env$|\.env\.(?!example|sample|template)|\.pem$|\.key$|\.p12$|\.pfx$|\.jks$|id_rsa|id_ed25519)"
)
# "secret"/"credential" in a code file name (SecretManager.cs) is fine; in a data file it isn't.
_SENSITIVE_WORD_RE = re.compile(r"(?i)(secret|credential|password)")
_DATA_SUFFIXES = {"", ".txt", ".json", ".yml", ".yaml", ".properties", ".config", ".xml", ".ini", ".toml", ".csv"}


def is_sensitive_path(rel_path: str) -> bool:
    """Files that likely hold credentials. Never packed, whatever they match."""
    name = Path(rel_path).name
    if _SENSITIVE_NAME_RE.search(name):
        return True
    return bool(_SENSITIVE_WORD_RE.search(name)) and Path(name).suffix.lower() in _DATA_SUFFIXES


def quoted_phrases(query: str) -> list[str]:
    """Multi-word quoted strings from the task, as plain words for an FTS phrase match."""
    phrases: list[str] = []
    for match in _QUOTED_RE.finditer(query):
        text = next(group for group in match.groups() if group)
        words = re.findall(r"[A-Za-z0-9_]+", text)
        if len(words) >= 2:
            phrases.append(" ".join(words).lower())
    return phrases


def content_terms(query: str) -> list[str]:
    """Words worth searching. Drops stopwords and task verbs like 'fix'."""
    raw = re.findall(r"[A-Za-z0-9_]+", query)
    terms: list[str] = []
    seen: set[str] = set()
    for part in raw:
        low = part.lower()
        if low in STOPWORDS or low in TASK_VERBS:
            continue
        if len(low) < 3:
            continue
        if low not in seen:
            seen.add(low)
            terms.append(low)
    if terms:
        return terms
    return [part.lower() for part in raw if len(part) > 1]


def stem(term: str) -> str:
    """Crude suffix strip so 'adding' meets AddItem and 'placed' meets PlaceOrder."""
    if len(term) < 5:
        return term
    for suffix in ("ing", "ed", "es", "s"):
        if term.endswith(suffix) and len(term) - len(suffix) >= 3:
            return term[: -len(suffix)]
    return term


def term_in(term: str, text: str) -> bool:
    return term in text or stem(term) in text


def match_terms(query: str) -> list[str]:
    """Content terms plus their stems, for substring matching in paths and lines."""
    out: list[str] = []
    for term in content_terms(query):
        for candidate in (term, stem(term)):
            if candidate not in out:
                out.append(candidate)
    return out


def identifier_terms(terms: list[str]) -> list[str]:
    """Long/code-like tokens only. Bare words like 'service' or 'hub' are not identifiers."""
    out: list[str] = []
    for term in terms:
        if len(term) >= 14:
            out.append(term)
        elif len(term) >= 10 and term.endswith(("async", "service", "controller")):
            out.append(term)
    return out


def query_terms(query: str) -> list[str]:
    return content_terms(query)


def path_penalty(rel_path: str, query: str) -> float:
    path = rel_path.replace("\\", "/").lower()
    terms = set(content_terms(query))
    if terms & {"deploy", "migration", "migrations", "script", "csproj"}:
        return 1.0
    if (
        "/deploy/" in path
        or "/migrations/" in path
        or path.endswith((".sh", ".sql", ".csproj"))
        or "/scripts/" in path
    ):
        return 0.2
    if "/entities/" in path and not terms & {"entity", "entities"}:
        return 0.35
    return 1.0


def score_hit(
    query: str,
    rel_path: str,
    role: str,
    bm25: float,
    kind: str | None = None,
) -> float:
    """Higher is better. SQLite bm25 is lower-is-better; invert it."""
    kind = kind or task_kind(query)
    terms = content_terms(query)
    name = Path(rel_path).name.lower()
    path = rel_path.replace("\\", "/").lower()
    score = max(0.0, 12.0 - bm25)

    name_hits = sum(1 for term in terms if term_in(term, name))
    path_hits = sum(1 for term in terms if term_in(term, path))
    score += name_hits * 12
    score += path_hits * 5
    if name_hits >= 2:
        score += 20
    for term in identifier_terms(terms):
        if term in path or term in name:
            score += 25
    if "mobile" in terms and "mobile" in path:
        score += 22
    if "mobile" in terms and "webapp" in path.replace(".", ""):
        score *= 0.45
    if any(term in terms for term in ("login", "jwt", "token", "auth")) and "auth" in name:
        score += 22
    if role == "source" and Path(name).stem.endswith(("service", "controller", "handler")):
        score += 12

    boost = dict(ROLE_BOOST)
    if kind == "fix":
        boost["test"] = 1.25
        boost["doc"] = 0.85
    elif kind == "enhance":
        boost["doc"] = 0.6
    elif kind == "understand":
        boost["doc"] = 1.25
    score *= boost.get(role, 1.0)
    score *= path_penalty(rel_path, query)
    return score


def sort_hits(hits: list[SearchHit]) -> list[SearchHit]:
    return sorted(hits, key=lambda hit: (-hit.score, hit.rel_path))
