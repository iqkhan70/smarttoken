# smarttoken enhancement 1.3: packet fixes from the TraditionalEats A/B test

Apply on top of 1.2. Python 3.9 compatible: keep `from __future__ import annotations` at the top of each module,
and use `Optional`/`List` in any signature typer or pydantic evaluates.

## Why

A real A/B test (`docs/experiments/traditionaleats_smarttoken_ab_test.md`) found these problems in the packet:
1. The file that had to change (the controller) had no snippet.
2. Near-duplicate windows from one class were all kept.
3. A change request was classed as "understand".
4. A draft packet pulled in a committed `keystore.release.txt`.
5. The eval's `obj/` exclude rule matched the word "Object" in code.

All the fixes are generic. After them: fixture 4/4, golangpoc 8/8, Eats 7/7, and no wrong HIGH or LOW on confidence.

The same test showed that a full-snippet packet does not reliably save tokens when the AI is an agent with its own
search tools (net −4.7% across two rounds, with ±25% swings). This change therefore also adds a **pointer prompt**:
about 300 tokens of file paths, line ranges and symbol names, and no source code. Agent tools (`copilot`, `vscode`,
`cursor-agent`, `claude`) get a pointer by default. Paste, print and Cursor chat still get the full pack.
`--pack full` or `--pack pointer` overrides that.

A follow-up test with two vague tasks and two runs each side is in
`docs/experiments/traditionaleats_pointer_ab_test.md`. Every run made a correct change. The pointer used **35.6% fewer
reported tokens** and **35.7% fewer AI credits** than working with no Smart Token. That is the number to replicate
at work, not the earlier full-pack result.

## Change 1: `rank.py`, task type

Replace `FIX_HINTS` / `ENHANCE_HINTS` and `task_kind`. Hints are stems with allowed endings, so "added" and
"validation" match, but "address" doesn't match "add".

```python
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
```

```python
def task_kind(query: str) -> str:
    q = query.lower()
    if _FIX_RE.search(q):
        return "fix"
    if _ENHANCE_RE.search(q):
        return "enhance"
    return "understand"
```

## Change 2: `rank.py`, quoted phrases and credential files

Add below `task_kind`:

```python
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
```

```python
def quoted_phrases(query: str) -> list[str]:
    """Multi-word quoted strings from the task, as plain words for an FTS phrase match."""
    phrases: list[str] = []
    for match in _QUOTED_RE.finditer(query):
        text = next(group for group in match.groups() if group)
        words = re.findall(r"[A-Za-z0-9_]+", text)
        if len(words) >= 2:
            phrases.append(" ".join(words).lower())
    return phrases
```

## Change 3: `search.py`

Import `is_sensitive_path` and `quoted_phrases` from `rank`, and add the constant:

```python
# A file containing a phrase the task quotes verbatim is almost always the one to change.
PHRASE_BONUS = 25.0
```

In `search_index`, just before `hits.extend(_path_fallback(...))`:

```python
    for phrase in quoted_phrases(query):
        by_id = {hit.file_id: hit for hit in hits}
        for row in _fts_rows(store, f'"{phrase}"', repo_id, limit):
            hit = by_id.get(row["file_id"])
            if hit is None:
                hit = _hit_from_row(store, row, query, kind, bm25=float(row["bm25"]))
                hits.append(hit)
                by_id[hit.file_id] = hit
            hit.score += PHRASE_BONUS
```

Then filter credential files when sorting:

```python
    hits.extend(_path_fallback(store, query, repo_id, kind, existing=hits))
    hits = sort_hits([hit for hit in hits if not is_sensitive_path(hit.rel_path)])
```

## Change 4: `rank.py` `score_hit`, docs matter less for change requests

```python
    if kind == "fix":
        boost["test"] = 1.25
        boost["doc"] = 0.85
    elif kind == "enhance":
        boost["doc"] = 0.6
    elif kind == "understand":
        boost["doc"] = 1.25
```

## Change 5: `pack.py`, window choice and order

Constants:

```python
# Skip a window when more than this share of it is already in a window from the same file.
MAX_WINDOW_OVERLAP = 0.4
# Files scoring at least this share of the top file each get a window before any file gets a second.
STRONG_FILE_SHARE = 0.75
```

In `_hit_windows`, replace the `relevance` function and the `best = sorted(...)[:MAX_WINDOWS_PER_FILE]` selection.
Windows are now returned in relevance order, not line order:

```python
    def relevance(span: tuple[int, int]) -> tuple[int, int]:
        text = "\n".join(lines[span[0] : span[1]]).lower()
        covered = sum(1 for term in terms if term_in(term, text))
        named = candidates[span][1].lower()
        named_terms = sum(1 for term in terms if named and term_in(term, named))
        # A symbol named after two task words (UpdateReview) beats a long body that mentions many.
        name_bonus = named_terms * 4 + (6 if named_terms >= 2 else 0)
        return (covered * 2 + name_bonus, -span[0])

    best: list[tuple[int, int]] = []
    for span in sorted(kept, key=relevance, reverse=True):
        if len(best) == MAX_WINDOWS_PER_FILE:
            break
        if any(_overlap_share(span, other) > MAX_WINDOW_OVERLAP for other in best):
            continue
        best.append(span)
```

New helpers:

```python
def _overlap_share(a: tuple[int, int], b: tuple[int, int]) -> float:
    """Shared lines as a fraction of the shorter span."""
    shared = min(a[1], b[1]) - max(a[0], b[0])
    shorter = min(a[1] - a[0], b[1] - b[0])
    return shared / shorter if shared > 0 and shorter > 0 else 0.0
```

```python
def _breadth_first(snippets: list[Snippet]) -> list[Snippet]:
    """Among the strong files, best window of each first, then second windows, so a strong file
    isn't crowded out by the top file's extra windows. Weaker files follow in score order."""
    if not snippets:
        return snippets
    floor = snippets[0].score * STRONG_FILE_SHARE
    strong = [snippet for snippet in snippets if snippet.score >= floor]
    weak = [snippet for snippet in snippets if snippet.score < floor]
    rounds: list[list[Snippet]] = []
    seen: dict[tuple[str, str], int] = {}
    for snippet in strong:
        key = (snippet.repo_name, snippet.rel_path)
        index = seen.get(key, 0)
        seen[key] = index + 1
        while len(rounds) <= index:
            rounds.append([])
        rounds[index].append(snippet)
    return [snippet for batch in rounds for snippet in batch] + weak
```

End of `_snippets_for_hits`, which must import `Snippet` from `models`:

```python
    # Stable sort: files by score; inside a file, windows stay in relevance order.
    snippets.sort(key=lambda item: (-item.score, item.repo_name, item.rel_path))
    return _breadth_first(snippets)
```

## Change 6: `measure.py`, folder rules

```python
def _path_in(needle: str, haystack: str) -> bool:
    needle = needle.replace("\\", "/").lower()
    while needle.startswith("./"):
        needle = needle[2:]
    blob = haystack.replace("\\", "/").lower()
    if needle.endswith("/"):
        # A folder rule (obj/, dist/) matches a path segment, not the word "object" in code.
        return re.search(r"(?:^|[\s/`'\"(])" + re.escape(needle), blob) is not None
    if needle in blob:
        return True
    compact = re.sub(r"\[[^/\]]+\]/", "", needle)
    if compact != needle and compact in blob:
        return True
    name = Path(needle).name.lower()
    return bool(name) and name in blob
```

## Change 7: tests

Add to `tests/test_search.py`:
- `task_kind`: "Change it so … rejected" is enhance; "validate the email" is enhance; "where is the shipping
  address stored" is understand; "fix the failing webhook" is fix.
- `quoted_phrases('return "Rating must be between 1 and 5" …')` returns `["rating must be between 1 and 5"]`;
  `"don't change what isn't broken"` returns `[]`; a single quoted word returns `[]`.
- `is_sensitive_path`:
  - true for `keystore.release.txt`, `.env`, `.env.production`, `server.pem`, `credentials.json`, `secrets.yaml`;
  - false for `.env.example`, `SecretManager.cs`, `pkg/credentials/provider.go`, `README.md`.
- On the fixture, quoting `"payment admin routes"` raises `auth.py`'s score by exactly `PHRASE_BONUS`.

Add to `tests/test_pack.py`:
- No two windows from `_hit_windows` share more than 40% of the shorter one.
- `_breadth_first` puts the second strong file's window right after the top file's first window, and weak files last.

Add to `tests/test_measure.py`:
- `_path_in("obj/", "stripeEvent.Data.Object")` is False;
  `_path_in("obj/", "Eats/src/Api/obj/Debug/x.cs")` is True.

## Verify

```
python -m pytest -q
smarttoken eval --fixture
smarttoken eval --tasks evals/<your_work_repo>_tasks.json
```

## Change 8: pointer prompt for agent hand-off

`pack.py` needs `from pathlib import Path`. Add the constants next to the other pack limits, and the functions
below `_breadth_first`. This does not change search ranking or the full pack, so the eval sets stay as they are.

```python
# Agent hand-off lists locations, not code: a few matching files, two ranges each.
POINTER_MAX = 6
POINTER_FILES = 4
POINTER_PER_FILE = 2
# Instruction words, not feature names. They must not decide which file a pointer names.
_POINTER_SKIP = frozenset(
    {"saved", "still", "small", "being", "keep", "only", "actual", "someone", "something", "please", "change", "changing"}
)
```

```python
def _pointer_groups(snippets: list[Snippet], task: str) -> list[list[Snippet]]:
    """Files a pointer may name: the path contains a task word, services and controllers first."""
    terms = [term for term in content_terms(task) if len(term) >= 5 and term not in _POINTER_SKIP]
    groups: dict[tuple[str, str], list[Snippet]] = {}
    for snippet in snippets:
        path = snippet.rel_path.replace("\\", "/").lower()
        if terms and not any(term_in(term, path) for term in terms):
            continue
        groups.setdefault((snippet.repo_name, snippet.rel_path), []).append(snippet)
    if not groups:
        for snippet in snippets:
            groups.setdefault((snippet.repo_name, snippet.rel_path), []).append(snippet)

    def backend(path: str) -> bool:
        return Path(path).stem.lower().endswith(("service", "controller", "handler"))

    ordered = sorted(groups.values(), key=lambda group: (
        0 if backend(group[0].rel_path) else 1,
        -group[0].score,
        group[0].rel_path,
    ))
    floor = ordered[0][0].score * 0.6
    return [group for group in ordered if group[0].score >= floor][:POINTER_FILES]


def select_pointers(snippets: list[Snippet], task: str = "") -> list[Snippet]:
    """The locations an agent should open. No source code, no near-duplicate ranges."""
    if not snippets:
        return []
    groups = _pointer_groups(snippets, task)
    chosen: list[Snippet] = []
    for round_index in range(POINTER_PER_FILE):
        for group in groups:
            if round_index >= len(group):
                continue
            snippet = group[round_index]
            span = (snippet.start_line, snippet.end_line + 1)
            if any(
                _overlap_share(span, (other.start_line, other.end_line + 1)) > MAX_WINDOW_OVERLAP
                for other in chosen
                if (other.repo_name, other.rel_path) == (snippet.repo_name, snippet.rel_path)
            ):
                continue
            chosen.append(snippet)
            if len(chosen) >= POINTER_MAX:
                return chosen
    return chosen


def render_pointers(snippets: list[Snippet], task: str = "") -> str:
    """One line per location: file, line range, symbol. No source code."""
    chosen = select_pointers(snippets, task)
    if not chosen:
        return "- No ranked locations. Search the repo before editing."
    return "\n".join(
        f"- `{snippet.repo_name}/{snippet.rel_path}` L{snippet.start_line}-{snippet.end_line} — {snippet.why}"
        for snippet in chosen
    )


def pointer_block(
    store: IndexStore,
    task: str,
    settings: Settings,
    repo_name: str | None = None,
) -> str:
    hits = search_index(store, task, repo_name=repo_name, limit=max(settings.search_limit, 20))
    snippets = _snippets_for_hits(store, hits, task, settings.context_lines)
    return render_pointers(snippets, task)
```

`handoff.py`, next to `TARGETS`:

```python
AGENT_TARGETS = ("vscode", "cursor-agent", "copilot", "claude")
PACK_STYLES = ("auto", "full", "pointer")
# Cursor rejects prompt deeplinks longer than this (whole URL, after encoding).
def resolve_pack_style(requested: str, target: str) -> str:
    """auto: a pointer for agent CLIs, the full pack for paste/print/Cursor chat."""
    if requested not in PACK_STYLES:
        raise ValueError(f"Unknown pack style '{requested}'. Use one of: {', '.join(PACK_STYLES)}.")
    if requested == "auto":
        return "pointer" if target in AGENT_TARGETS else "full"
    return requested


def build_pointer_prompt(task: str, pointer_markdown: str, workspaces: List[str]) -> str:
    where = ", ".join(f"`{path}`" for path in workspaces) or "the registered repos"
    return (
        f"# Task\n{task}\n\n"
        f"Work in: {where}.\n"
        "Open the locations below first. Each line is a file and the line range where this task matches. "
        "Read those ranges, then make the smallest change that does the task. "
        "Open other files only if these are not enough, and say which ones and why.\n\n"
        f"{pointer_markdown}\n"
    )
```

`cli.py`: import `pointer_block`, add `--pack`, and choose the prompt.

```python
from smarttoken.pack import build_packet, pointer_block

# on ask_cmd, after --yes:
pack: str = typer.Option(
    "auto", "--pack",
    help="auto: pointer for agent tools, full snippets for paste/print. Or full, or pointer.",
)

pack_style = handoff.resolve_pack_style(pack.lower(), send_to)

workspaces = [repo_paths[n] for n in hit_repos if n in repo_paths]
if pack_style == "pointer":
    prompt = handoff.build_pointer_prompt(task, pointer_block(store, task, settings, repo), workspaces)
else:
    prompt = handoff.build_prompt(task, packet.markdown, workspaces)

if pack_style == "pointer":
    message = (
        f"Pointer prompt is ~{need} tokens (full pack would be {packet.used_tokens}). "
        "The agent opens the listed files itself."
    )
else:
    message = handoff.tier_message(need, tier[0], tier[1], len(tiers))
```

Tests to add:
- `resolve_pack_style("auto", "copilot")` is `pointer`; `resolve_pack_style("auto", "print")` is `full`;
  an explicit `full` or `pointer` wins.
- `smarttoken ask "…" --to print --pack pointer --yes` prints `Pointer prompt is ~` and `Open the locations below`,
  and the prompt has line ranges and no fenced code.
- `select_pointers` drops a file that scores under 60% of the best file, and `render_pointers` does not include
  the snippet text.

## Experiment: vague tasks, two runs each side

Recorded in `docs/experiments/traditionaleats_pointer_ab_test.md`. Short version, so work has the result without
opening that file:

Two tasks, neither naming a file, an endpoint, or an error string. Each was run twice with no Smart Token and twice
with the pointer. Order alternated (baseline first on task 1, pointer first on task 2). Same Copilot CLI flags,
model `auto` routing to `gpt-6-luna`, one premium request per run. Eats was restored after every run
(commit `59ff18f7`, clean tree).

| Task | No Smart Token (2 runs) | Pointer (2 runs) | Token reduction |
|---|---:|---:|---:|
| Vague star-rating edit | 228,938 and 212,526 | 154,526 and 147,926 | 31.5% |
| Vague blank review reply | 197,044 and 276,896 | 134,574 and 152,456 | 39.4% |
| **Both tasks** | **915,404 tokens, 2.425 credits** | **589,482 tokens, 1.558 credits** | **35.6% tokens, 35.7% credits** |

All 8 runs passed the acceptance checks. The pointer prompts were 309 and 313 tokens. Baseline runs searched more
(`rg` 4 to 10 times). Pointer runs opened the named files (`view` 12 to 15 times) and searched once or twice.
The pointer still named some irrelevant ranges, and it also named `UpdateReviewAsync` and `AddRestaurantResponseAsync`,
which was enough.

One measurement mistake, corrected before these numbers: the first check used `dotnet run --no-build`, so it tested
the old binary and reported every run as a failure. Re-applying each saved diff and rebuilding showed all 8 passing.
Do not use the first check output.
