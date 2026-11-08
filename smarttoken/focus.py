"""Track what you've been asking about, and warn when a new question lands in a different area.

An "area" is learned from file paths, not configured. Your focus words are the path words shared by most
of your recent top files (e.g. `aop`). A new question drifts when its top file, the one the AI would
edit, lacks those words and brings its own (e.g. `case`).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Set

from smarttoken.config import AppPaths
from smarttoken.index_store import IndexStore
from smarttoken.models import SearchHit

HISTORY_WINDOW = timedelta(hours=8)
HISTORY_ENTRIES = 6
HISTORY_KEEP = 200
TOP_FILES = 3
# Each older question counts this much less than the one after it.
DECAY = 0.7
# A word is part of your focus when it's in at least this share of your recent questions.
FOCUS_SHARE = 0.6
# Need about two recent questions (or a pin) before warning.
MIN_FOCUS_STRENGTH = 1.5
# Words in at least this share of all paths (src, app, ...) say nothing about the area.
UBIQUITOUS_SHARE = 0.5

_EXTENSIONS = {
    "ts", "tsx", "js", "jsx", "mjs", "cjs", "py", "go", "cs", "java", "kt", "rb", "php", "rs",
    "html", "htm", "css", "scss", "less", "json", "md", "yml", "yaml", "toml", "xml", "sql",
    "sh", "razor", "cshtml", "vue", "svelte", "swift",
}


def normalize(word: str) -> str:
    word = word.lower()
    if len(word) >= 4 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    return word


def path_words(rel_path: str) -> List[str]:
    """`src/aops/AopListComponent.tsx` -> ['src', 'aop', 'list', 'component']."""
    words: List[str] = []
    for chunk in re.split(r"[^A-Za-z0-9]+", rel_path):
        for part in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", chunk):
            low = part.lower()
            if len(low) < 3 or low in _EXTENSIONS or low.isdigit():
                continue
            norm = normalize(low)
            if norm not in words:
                words.append(norm)
    return words


class PathVocabulary:
    """Which words name things in the indexed paths, and which are everywhere."""

    def __init__(self, store: IndexStore, repo_name: Optional[str] = None) -> None:
        repo_id = store.require_repo(repo_name).id if repo_name else None
        df: Counter = Counter()
        count = 0
        for record in store.list_files(repo_id):
            count += 1
            df.update(path_words(record.rel_path))
        self.count = count
        self.df = df

    def is_area_word(self, word: str) -> bool:
        df = self.df.get(word, 0)
        return df > 0 and (self.count == 0 or df / self.count < UBIQUITOUS_SHARE)

    def file_words(self, rel_path: str) -> Set[str]:
        return {word for word in path_words(rel_path) if self.is_area_word(word)}

    def text_words(self, text: str) -> Set[str]:
        """Words in a question or pin that also name things in paths (e.g. 'aop')."""
        words = {normalize(w) for w in re.findall(r"[A-Za-z0-9]+", text) if len(w) >= 3}
        return {word for word in words if self.is_area_word(word)}


# --- history and pin ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record(
    paths: AppPaths, command: str, query: str, hits: List[SearchHit], drift_to: Optional[str] = None
) -> None:
    """drift_to: the new area word when this question drifted and you didn't name that area yourself."""
    paths.ensure_home()
    row = {
        "ts": _now().replace(microsecond=0).isoformat(),
        "cmd": command,
        "query": query,
        "files": [{"repo": hit.repo_name, "path": hit.rel_path} for hit in hits[:TOP_FILES]],
    }
    if drift_to:
        row["drift_to"] = drift_to
    lines: List[str] = []
    if paths.history_file.exists():
        lines = paths.history_file.read_text(encoding="utf-8").splitlines()[-(HISTORY_KEEP - 1) :]
    lines.append(json.dumps(row))
    paths.history_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def recent(paths: AppPaths, repo_name: Optional[str] = None) -> List[dict]:
    """Newest first, within the time window, limited to the repo when one is given."""
    if not paths.history_file.exists():
        return []
    cutoff = _now() - HISTORY_WINDOW
    rows: List[dict] = []
    for line in reversed(paths.history_file.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            ts = datetime.fromisoformat(row["ts"])
        except (ValueError, KeyError):
            continue
        if ts < cutoff:
            break
        files = row.get("files") or []
        if not files:
            continue
        if repo_name and all(item.get("repo") != repo_name for item in files):
            continue
        rows.append(row)
        if len(rows) >= HISTORY_ENTRIES:
            break
    return rows


def pinned(paths: AppPaths) -> Optional[str]:
    if not paths.focus_file.exists():
        return None
    try:
        return json.loads(paths.focus_file.read_text(encoding="utf-8")).get("pinned") or None
    except ValueError:
        return None


def pin(paths: AppPaths, text: Optional[str]) -> None:
    paths.ensure_home()
    paths.focus_file.write_text(json.dumps({"pinned": text}), encoding="utf-8")


def clear(paths: AppPaths) -> None:
    for path in (paths.focus_file, paths.history_file):
        if path.exists():
            path.unlink()


# --- focus and drift ---------------------------------------------------------------------------


@dataclass
class Focus:
    words: List[str]
    strength: float
    pinned: Optional[str]
    shares: Dict[str, float] = field(default_factory=dict)
    recent_queries: List[str] = field(default_factory=list)
    recent_files: List[str] = field(default_factory=list)
    # Top file of each recent query, aligned with recent_queries.
    recent_tops: List[str] = field(default_factory=list)


def current_focus(paths: AppPaths, vocab: PathVocabulary, repo_name: Optional[str] = None) -> Focus:
    weights: Dict[str, float] = {}
    strength = 0.0
    queries: List[str] = []
    files: List[str] = []
    tops: List[str] = []
    rows = recent(paths, repo_name)
    # A drifted question only moves your focus once a different question lands in the same new area.
    drift_queries: Dict[str, Set[str]] = {}
    for row in rows:
        if row.get("drift_to"):
            drift_queries.setdefault(row["drift_to"], set()).add(row.get("query", ""))
    rows = [row for row in rows if not row.get("drift_to") or len(drift_queries[row["drift_to"]]) >= 2]
    for age, row in enumerate(rows):
        weight = DECAY**age
        strength += weight
        top = row["files"][0]["path"]
        for word in vocab.file_words(top) | vocab.text_words(row.get("query", "")):
            weights[word] = weights.get(word, 0.0) + weight
        queries.append(row.get("query", ""))
        tops.append(top)
        if top not in files:
            files.append(top)
    shares = {word: value / strength for word, value in weights.items()} if strength else {}
    pin_text = pinned(paths)
    if pin_text:
        for word in vocab.text_words(pin_text):
            shares[word] = 1.0
        strength = max(strength, MIN_FOCUS_STRENGTH)
    words = sorted((w for w, share in shares.items() if share >= FOCUS_SHARE), key=lambda w: -shares[w])
    return Focus(words, strength, pin_text, shares, queries, files, tops)


@dataclass
class Drift:
    # 0..1: share of your focus words found in the new top file.
    similarity: float
    # Focus words the new top file lacks (the area you seem to be leaving).
    focus_words: List[str]
    new_words: List[str]
    new_file: str
    focus_files: List[str]
    pinned: Optional[str]
    asked_for_new_area: bool
    suggestion: Optional[SearchHit]


def check_drift(
    paths: AppPaths,
    store: IndexStore,
    query: str,
    hits: List[SearchHit],
    repo_name: Optional[str] = None,
    search_fn: Optional[Callable[[str], List[SearchHit]]] = None,
) -> Optional[Drift]:
    """A Drift when the new question lands outside your recent focus; None when it fits or there's no focus yet."""
    if not hits:
        return None
    vocab = PathVocabulary(store, repo_name)
    focus = current_focus(paths, vocab, repo_name)
    if focus.strength < MIN_FOCUS_STRENGTH or not focus.words:
        return None

    asked = vocab.text_words(query)
    top_words = vocab.file_words(hits[0].rel_path)
    focus_set = set(focus.words)
    missing = [word for word in focus.words if word not in top_words]
    # You named the focus area yourself ("aop list ..."), so landing in shared code is intended.
    if asked & set(missing):
        return None
    unseen = [word for word in top_words if focus.shares.get(word, 0.0) == 0.0]
    if not missing or not unseen:
        return None
    counterparts = _counterparts(missing, focus.recent_files, hits[0].rel_path, focus_set)
    folder_words = set(path_words(hits[0].rel_path.rsplit("/", 1)[0])) if "/" in hits[0].rel_path else set()
    rest = sorted((w for w in unseen if w not in counterparts), key=lambda w: (w not in folder_words, vocab.df.get(w, 0)))
    new_words = counterparts + rest

    similarity = 1 - len(missing) / len(focus_set)
    suggestion = _suggest(vocab, focus_set, hits[1:])
    if suggestion is None and search_fn is not None:
        suggestion = _suggest(vocab, focus_set, search_fn(f"{query} {' '.join(focus.words[:2])}"))
    return Drift(
        similarity=similarity,
        focus_words=missing[:3],
        new_words=new_words[:3],
        new_file=f"{hits[0].repo_name}/{hits[0].rel_path}",
        focus_files=focus.recent_files[:2],
        pinned=focus.pinned,
        asked_for_new_area=new_words[0] in asked,
        suggestion=suggestion,
    )


def _counterparts(missing: List[str], focus_files: List[str], new_path: str, focus_words: Set[str]) -> List[str]:
    """The word sitting where your focus word sat: `aops/AopList` vs `cases/CaseList` gives 'case'."""
    new_seq = path_words(new_path)
    out: List[str] = []
    for word in missing:
        for focus_path in focus_files:
            seq = path_words(focus_path)
            if word not in seq:
                continue
            idx = seq.index(word)
            if idx < len(new_seq) and new_seq[idx] not in focus_words and new_seq[idx] not in out:
                out.append(new_seq[idx])
            break
    return out


def _suggest(vocab: PathVocabulary, focus_words: Set[str], candidates: List[SearchHit]) -> Optional[SearchHit]:
    """Best-ranked candidate whose path carries all your focus words."""
    for hit in candidates:
        if focus_words <= vocab.file_words(hit.rel_path):
            return hit
    return None
