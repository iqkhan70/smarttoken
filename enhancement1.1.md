# smarttoken enhancement 1.1: focus check (warn when a question lands in the wrong area)

Paste this into Copilot **after** `enhancement1.0.md` is in and its tests pass. It only adds to the project.
Adapt names to the existing code. Keep it Python 3.9 compatible.

## Why

I was working on the **AOP list**, and a question accidentally landed on the **case list** screen, because both
screens have similar code (list, status, filter, sort). I pasted the pack, the AI worked on the wrong screen, and
it cost about 100K tokens. The tool should remember what I've been working on and warn me **before** I paste.

## Behaviour

- `search`, `check`, `pack` and `explain` record each question and its top 3 files in
  `<home>/history.jsonl` (local only, last 200 rows).
- **An area is learned from file paths, with no configuration.** Each path is split into words: folders, plus
  camelCase / kebab / snake parts, lowercased, with a trailing `s` removed (so `aops` becomes `aop`).
  Extensions and words under 3 characters are dropped. Words found in ≥ 50% of all paths (`src`, `app`) are ignored.
- **Focus words** are the area words in ≥ 60% of your recent questions. Recent means the last 6 questions within
  8 hours, each older one weighted ×0.7. The words come from each question's top file plus any area words typed
  in the question. You need roughly 2 questions, or a pinned area, before any warning.
- **Drift:** the new question's top file (the one the AI would edit) lacks a focus word, and has an area word
  you haven't been using. Example: focus `aop`, top file `cases/CaseListComponent.tsx`, new word `case`.
- **Not drift:**
  - your question names the missing focus word ("aop list should show status badge" landing in `shared/StatusBadge.tsx`);
  - the top file has no new area word, i.e. it's shared code used by both areas.
- **Naming the new area:** the word in the same path position as your focus word (`aops/Aop…` vs `cases/Case…`
  gives `case`). Otherwise, prefer words from folder names.
- **Asked for the new area:** if you typed that word ("update the case list"), the warning says "probably
  intended" and doesn't block anything.
- **Suggestion:** the best-ranked hit whose path has all the focus words. Otherwise, re-search with the
  question plus the top 2 focus words.
- **One accident doesn't move focus.** A drifted question is recorded with `drift_to: <new word>` and ignored
  for focus until a **different** question also lands in the same new area. Running `check` then `pack` on the
  same accidental question warns both times.
- **Copy is held back.** On drift (unless you named the new area), `check --copy` doesn't copy, and
  `pack --copy` doesn't copy and exits 3. `--force` overrides both.
- `pack` and `explain` print the warning to **stderr**, so the pack markdown stays clean.
- New command `focus`: shows the pin, the focus words and recent questions with their top files.
  `--set aop` pins an area, `--unpin` removes the pin, `--clear` forgets the pin and history.

Example:

```text
Focus check: this lands outside what you've been working on.
  Your recent questions: aop (e.g. src/app/aops/AopListComponent.tsx)
  This question lands on: case, portal/src/app/cases/CaseListComponent.tsx
  Closest file in aop: portal/src/app/aops/AopListComponent.tsx
  If you meant aop, ask again naming it: smarttoken check "add a priority column to the list aop"
  If you meant case, carry on. Focus follows you after a couple of questions.
Not copied: the focus check warned. Add --force to copy anyway.
```

## Change 1: `config.py`

In `AppPaths.__init__`, add:

```python
        self.history_file = home / "history.jsonl"
        self.focus_file = home / "focus.json"
```

## Change 2: new module `smarttoken/focus.py`

```python
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
```

## Change 3: CLI wiring (`cli.py`)

Import: `from smarttoken import __version__, focus`. Add these helpers below `_store`:

```python
def _focus_check(
    paths: AppPaths, store: IndexStore, command: str, query: str, hits: list, repo: Optional[str]
) -> Optional[focus.Drift]:
    """Compare against recent questions, then record this one."""
    drift = focus.check_drift(
        paths,
        store,
        query,
        hits,
        repo_name=repo,
        search_fn=lambda text: search_index(store, text, repo_name=repo, limit=20),
    )
    drift_to = drift.new_words[0] if drift and not drift.asked_for_new_area else None
    focus.record(paths, command, query, hits, drift_to=drift_to)
    return drift


def _print_drift(drift: focus.Drift, query: str, out: Console) -> None:
    was = ", ".join(drift.focus_words)
    now = drift.new_words[0]
    source = f"pinned focus '{drift.pinned}'" if drift.pinned and not drift.focus_files else "recent questions"
    out.print("[bold yellow]Focus check: this lands outside what you've been working on.[/bold yellow]")
    example = f" (e.g. {drift.focus_files[0]})" if drift.focus_files else ""
    out.print(f"  Your {source}: [bold]{was}[/bold]{example}")
    out.print(f"  This question lands on: [bold]{now}[/bold], {drift.new_file}")
    if drift.asked_for_new_area:
        out.print(f"  You mentioned '{now}', so this is probably intended. Carry on if so.")
        return
    if drift.suggestion:
        out.print(f"  Closest file in {was}: [bold]{drift.suggestion.repo_name}/{drift.suggestion.rel_path}[/bold]")
    out.print(f'  If you meant {was}, ask again naming it: smarttoken check "{query} {drift.focus_words[0]}"')
    out.print(f"  If you meant {now}, carry on. Focus follows you after a couple of questions.")


def _drift_blocks_copy(drift: Optional[focus.Drift], force: bool) -> bool:
    return drift is not None and not drift.asked_for_new_area and not force
```

Wire it into each command:

- **search:** after the no-hits early return, `drift = _focus_check(app_paths, store, "search", query, hits, repo)`.
  After printing the table, `if drift: _print_drift(drift, query, console)`.
- **explain:** after `build_packet`, run `hits = search_index(store, target, repo_name=repo,
  limit=max(settings.search_limit, 20))` and `drift = _focus_check(..., "explain", target, hits, repo)`.
  After printing the markdown, `if drift: _print_drift(drift, target, err_console)`.
- **pack:** add `force: bool = typer.Option(False, "--force", help="With --copy, copy even when the focus check
  warns.")`. Compute hits and drift the same way (command "pack"). After printing the markdown and token line:

```python
    if drift:
        _print_drift(drift, task, err_console)
    if copy and _drift_blocks_copy(drift, force):
        err_console.print("[yellow]Not copied because of the focus check. Add --force to copy anyway.[/yellow]")
        raise typer.Exit(code=3)
```

- **check:** change the help for `--copy` to "Copy the pack unless confidence is LOW or the focus check warns."
  and for `--force` to "With --copy, copy anyway.". Replace the copy gating with:

```python
        confidence = assess(store, task, hits)
        drift = _focus_check(app_paths, store, "check", task, hits, repo)
        result = measure_task(store, task, settings, repo_name=repo, budget=budget)
        blocked = None
        if copy and not force:
            if confidence.level == "LOW":
                blocked = "confidence is LOW"
            elif _drift_blocks_copy(drift, force):
                blocked = "the focus check warned"
        packet = None
        if copy and blocked is None:
            packet = build_packet(store, task, settings, repo_name=repo, budget=budget)
```

  After printing `Advice`, add `if drift: _print_drift(drift, task, console)`. Change the not-copied message to
  `f"[yellow]Not copied: {blocked}. Add --force to copy anyway.[/yellow]"`.

- **new `focus` command:**

```python
@app.command("focus")
def focus_cmd(
    ctx: typer.Context,
    set_: Optional[str] = typer.Option(None, "--set", help="Pin an area, e.g. --set aop."),
    unpin: bool = typer.Option(False, "--unpin", help="Remove the pinned area, keep history."),
    clear: bool = typer.Option(False, "--clear", help="Forget the pin and recent questions."),
    repo: Optional[str] = typer.Option(None, "--repo", "-r"),
) -> None:
    """Show what area you've been working on; pin or clear it."""
    app_paths = AppPaths(ctx.obj["home"])
    if clear:
        focus.clear(app_paths)
        console.print("Focus cleared.")
        return
    if unpin:
        focus.pin(app_paths, None)
        console.print("Pin removed.")
    if set_:
        focus.pin(app_paths, set_)
        console.print(f"Pinned focus: [bold]{set_}[/bold]. Questions landing elsewhere will be flagged.")
    with _store(app_paths) as store:
        if repo and store.get_repo(repo) is None:
            err_console.print(f"[red]Unknown repo '{repo}'.[/red]")
            raise typer.Exit(code=1)
        current = focus.current_focus(app_paths, focus.PathVocabulary(store, repo), repo)
    if current.pinned:
        console.print(f"Pinned: [bold]{current.pinned}[/bold]")
    if current.words:
        console.print(f"Focus words: [bold]{', '.join(current.words[:5])}[/bold]")
    else:
        console.print("No focus yet. It builds up after a couple of questions in the same area.")
    if current.recent_queries:
        console.print("Recent questions (newest first):")
        for query, path in zip(current.recent_queries, current.recent_tops):
            console.print(f"  - {query}  [dim]→ {path}[/dim]")
```

`measure` and `eval` do **not** record history (evals would pollute your focus).

## Change 4: tests, in `tests/test_focus.py`

The test builds a tiny "portal" repo with `aops/` and `cases/` areas written with similar wording.

```python
from pathlib import Path

import pytest
from typer.testing import CliRunner

from smarttoken import focus
from smarttoken.cli import app
from smarttoken.config import AppPaths, Settings
from smarttoken.index_store import IndexStore
from smarttoken.registry import add_paths
from smarttoken.search import search_index

runner = CliRunner()

PORTAL = {
    "src/app/aops/AopListComponent.tsx": "export function AopListComponent() {\n  // list of AOPs with status column, sort by due date, filter by owner\n}\n",
    "src/app/aops/aopService.ts": "export async function fetchAops(filter) {\n  // load AOP list with status and owner filter\n}\n",
    "src/app/aops/AopDetailPage.tsx": "export function AopDetailPage() {\n  // AOP detail with status history and owner\n}\n",
    "src/app/aops/AopFilters.tsx": "export function AopFilters() {\n  // status filter, owner filter, date range filter for the list\n}\n",
    "src/app/cases/CaseListComponent.tsx": "export function CaseListComponent() {\n  // list of cases with status column, priority badge, sort by created date\n}\n",
    "src/app/cases/caseService.ts": "export async function fetchCases(filter) {\n  // load case list with status filter and priority\n}\n",
    "src/app/cases/CaseDetailPage.tsx": "export function CaseDetailPage() {\n  // case detail with status history and assignee\n}\n",
    "src/app/cases/CaseFilters.tsx": "export function CaseFilters() {\n  // status filter, priority filter, assignee filter for the case list\n}\n",
    "src/app/shared/Table.tsx": "export function Table() {\n  // generic list table: sort, filter, paging\n}\n",
    "src/app/shared/StatusBadge.tsx": "export function StatusBadge() { /* colored badge for status */ }\n",
}


@pytest.fixture
def portal(tmp_path: Path):
    root = tmp_path / "portal"
    for rel, text in PORTAL.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    paths = AppPaths(tmp_path / "home")
    paths.ensure_home()
    store = IndexStore(paths.db_file)
    repos = add_paths(store, [root])
    store.index_repo(repos[0], Settings())
    return paths, store, root


def _ask(paths: AppPaths, store: IndexStore, query: str):
    hits = search_index(store, query, limit=20)
    drift = focus.check_drift(paths, store, query, hits, search_fn=lambda text: search_index(store, text, limit=20))
    drift_to = drift.new_words[0] if drift and not drift.asked_for_new_area else None
    focus.record(paths, "check", query, hits, drift_to=drift_to)
    return drift


def test_path_words_split_folders_and_camel_case() -> None:
    assert focus.path_words("src/app/aops/AopListComponent.tsx") == ["src", "app", "aop", "list", "component"]
    assert "status" in focus.path_words("shared/StatusBadge.tsx")


def test_question_landing_on_cases_after_aop_work_is_flagged(portal) -> None:
    paths, store, _ = portal
    assert _ask(paths, store, "add sort by due date to the aop list") is None
    assert _ask(paths, store, "show owner avatar in the aop list") is None
    drift = _ask(paths, store, "add a priority column to the list")
    assert drift is not None
    assert drift.focus_words[0] == "aop"
    assert drift.new_words[0] == "case"
    assert drift.suggestion is not None and drift.suggestion.rel_path.endswith("AopListComponent.tsx")
    assert not drift.asked_for_new_area


def test_staying_in_area_or_naming_it_is_not_flagged(portal) -> None:
    paths, store, _ = portal
    for query in ["aop status update api", "aop owner filter", "aop detail page", "fetch aops with filters"]:
        assert _ask(paths, store, query) is None
    assert _ask(paths, store, "aop list should show status badge") is None


def test_naming_the_new_area_is_marked_intended(portal) -> None:
    paths, store, _ = portal
    _ask(paths, store, "add sort by due date to the aop list")
    _ask(paths, store, "aop list should show status badge")
    drift = _ask(paths, store, "update the case list columns")
    assert drift is not None and drift.asked_for_new_area


def test_one_accident_keeps_focus_but_a_real_switch_moves_it(portal) -> None:
    paths, store, _ = portal
    _ask(paths, store, "add sort by due date to the aop list")
    _ask(paths, store, "show owner avatar in the aop list")
    assert _ask(paths, store, "add a priority column to the list") is not None
    assert _ask(paths, store, "add a priority column to the list") is not None
    assert _ask(paths, store, "show the assignee on the case detail page") is not None
    assert _ask(paths, store, "add a priority filter for cases") is None


def test_pinned_focus_flags_without_history(portal) -> None:
    paths, store, _ = portal
    focus.pin(paths, "aop")
    drift = _ask(paths, store, "add a priority column to the list")
    assert drift is not None and drift.new_words[0] == "case"
    assert _ask(paths, store, "add a priority column to the aop list") is None


def test_cli_check_copy_is_held_back_on_drift(portal) -> None:
    paths, _, _ = portal
    base = ["--home", str(paths.home)]
    runner.invoke(app, [*base, "check", "add sort by due date to the aop list"])
    runner.invoke(app, [*base, "check", "show owner avatar in the aop list"])

    shown = runner.invoke(app, [*base, "focus"])
    assert "aop" in shown.stdout

    result = runner.invoke(app, [*base, "check", "add a priority column to the list", "--copy"])
    assert result.exit_code == 0, result.stdout
    assert "Focus check" in result.stdout
    assert "Not copied: the focus check warned" in result.stdout

    packed = runner.invoke(app, [*base, "pack", "add a priority column to the list", "--copy"])
    assert packed.exit_code == 3

    assert runner.invoke(app, [*base, "focus", "--clear"]).exit_code == 0
    assert not paths.history_file.exists()
```

## Verify on a work repo

1. `smarttoken focus --clear`
2. Ask 2 questions about area A (e.g. AOP) with `check`. `smarttoken focus` should list A's word.
3. Ask a vague question that you know lands in area B (e.g. the case screen). Expect "Focus check …", the area B word,
   the closest area A file, and "Not copied" with `--copy`.
4. Ask the same question naming A: no warning. Ask it naming B: a warning that says "probably intended".
5. Pin and clear: `smarttoken focus --set aop`, then `smarttoken focus --clear`.

On my home repos, three questions that landed on `OrderChatHub.cs` or the general web `ChatService.cs` after
vendor-chat questions were flagged, and pointed to `VendorChatHub.cs`. Three questions that stayed in the area or
landed on shared chat code were not flagged.

## Done when

All existing tests plus the 7 new ones pass, and the gold-set eval results are unchanged (focus doesn't change
search or pack content).
