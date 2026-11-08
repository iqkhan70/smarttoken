"""Build a token-budgeted markdown packet from local search hits."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from smarttoken.config import Settings
from smarttoken.index_store import IndexStore
from smarttoken.models import SearchHit, Snippet
from smarttoken.prompts import restated_task, suggested_ask
from smarttoken.rank import content_terms, task_kind, term_in
from smarttoken.redact import redact
from smarttoken.search import search_index
from smarttoken.symbols import SymbolHit, extract_symbols, symbols_matching
from smarttoken.tokens import count_tokens

DO_NOT_OPEN_ROLES = {"generated"}
MAX_READ_FIRST = 8
MAX_SNIPPETS = 10
MAX_WINDOW_LINES = 28
MAX_WINDOWS_PER_FILE = 3
# Skip a window when more than this share of it is already in a window from the same file.
MAX_WINDOW_OVERLAP = 0.4
# Files scoring at least this share of the top file each get a window before any file gets a second.
STRONG_FILE_SHARE = 0.75
# Agent hand-off lists locations, not code: a few matching files, two ranges each.
POINTER_MAX = 6
POINTER_FILES = 4
POINTER_PER_FILE = 2
# Instruction words, not feature names. They must not decide which file a pointer names.
_POINTER_SKIP = frozenset(
    {"saved", "still", "small", "being", "keep", "only", "actual", "someone", "something", "please", "change", "changing"}
)
# A whole function/class is sent when it fits; one-line declarations borrow their parent.
MIN_SYMBOL_LINES = 3
MAX_SYMBOL_LINES = 28
MAX_NAMED_SYMBOL_LINES = 80


@dataclass
class Packet:
    markdown: str
    used_tokens: int
    budget: int
    omitted: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


def build_packet(
    store: IndexStore,
    task: str,
    settings: Settings,
    repo_name: str | None = None,
    budget: int | None = None,
    include_maps: bool = False,
) -> Packet:
    from smarttoken.repo_map import render_map

    kind = task_kind(task)
    limit = settings.search_limit
    hits = search_index(store, task, repo_name=repo_name, limit=max(limit, 20))
    budget = budget or settings.default_budget

    read_first = [hit for hit in hits if hit.role != "generated"][:MAX_READ_FIRST]
    snippets = _snippets_for_hits(store, hits, task, settings.context_lines)
    symbol_lines = _symbol_lines(store, hits, task)
    do_not_open = _do_not_open(store, repo_name)

    repos = _repo_block(hits if hits else store.list_repos(), store, repo_name)
    header = [
        "# Smart Token pack",
        "",
        "## Task",
        restated_task(task, kind),
        "",
        "## Repos involved",
        repos,
        "",
        "## Read this first",
    ]
    if read_first:
        for hit in read_first:
            preview = redact(hit.preview) if hit.preview else "path match"
            header.append(f"- `{hit.repo_name}/{hit.rel_path}` ({hit.role}, {hit.lang}) — {preview}")
    else:
        header.append("- No indexed hits. Run `smarttoken search` / `rg` and re-index if the repos changed.")

    maps_block = ""
    if include_maps:
        names = {hit.repo_name for hit in hits} or (
            {repo_name} if repo_name else {repo.name for repo in store.list_repos()}
        )
        chunks = [render_map(store, store.require_repo(name)) for name in sorted(names) if store.get_repo(name)]
        if chunks:
            maps_block = "\n\n## Repo maps\n\n" + "\n\n".join(chunks)

    ask_files = [f"{hit.repo_name}/{hit.rel_path}" for hit in read_first]
    footer_ask = suggested_ask(task, kind, ask_files)

    omitted: list[str] = []
    kept_snippets: list[str] = []
    used_files: list[str] = [f"{hit.repo_name}/{hit.rel_path}" for hit in read_first]

    def assemble(extra_omitted: list[str]) -> str:
        parts = list(header)
        if maps_block:
            parts.append(maps_block)
        parts.extend(["", "## Snippets"])
        if kept_snippets:
            parts.extend(kept_snippets)
        else:
            parts.append("No snippets fit this budget. Try `smarttoken search` or raise `--budget`.")
        parts.extend(["", "## Symbols / callers"])
        if symbol_lines:
            parts.extend(symbol_lines[:12])
        else:
            parts.append("- No functions or classes named after the task words in the top hits.")
        parts.extend(["", "## Do not open"])
        if do_not_open:
            parts.extend(f"- `{path}`" for path in do_not_open[:12])
        else:
            parts.append("- lockfiles, `dist/`, `vendor/`, generated assets, files over the size cap")
        if extra_omitted:
            parts.extend(["", "## Omitted (over budget)"])
            parts.extend(f"- {item}" for item in extra_omitted)
        parts.extend(
            [
                "",
                "## Suggested Cursor ask",
                footer_ask,
                "",
                "## Budget",
                f"Used __USED_TOKENS__ / {budget} tokens ({kind} pack).",
            ]
        )
        return "\n".join(parts)

    # Greedy: add highest-ranked snippets until the budget is exhausted.
    reserved = count_tokens(assemble([]).replace("__USED_TOKENS__", "0"), settings.encoding) + 16
    remaining = max(0, budget - reserved)

    for snippet in snippets[:MAX_SNIPPETS]:
        chosen = snippet
        block = _render_snippet(chosen)
        cost = count_tokens(block, settings.encoding)
        if cost > remaining:
            chosen = _shrink_snippet(snippet, max_lines=16)
            block = _render_snippet(chosen)
            cost = count_tokens(block, settings.encoding)
        if cost <= remaining:
            kept_snippets.append(block)
            remaining -= cost
            label = f"{chosen.repo_name}/{chosen.rel_path}"
            if label not in used_files:
                used_files.append(label)
        else:
            omitted.append(
                f"`{snippet.repo_name}/{snippet.rel_path}` L{snippet.start_line}-{snippet.end_line} ({cost} tokens)"
            )

    draft = redact(assemble(omitted))
    used = count_tokens(draft.replace("__USED_TOKENS__", "00000"), settings.encoding)
    while used > budget and kept_snippets:
        dropped = kept_snippets.pop()
        omitted.append(f"trimmed to fit budget ({count_tokens(dropped, settings.encoding)} tokens)")
        draft = redact(assemble(omitted))
        used = count_tokens(draft.replace("__USED_TOKENS__", "00000"), settings.encoding)
    markdown = draft.replace("__USED_TOKENS__", str(used))
    used = count_tokens(markdown, settings.encoding)
    return Packet(
        markdown=markdown,
        used_tokens=used,
        budget=budget,
        omitted=omitted,
        files=used_files,
    )


def _repo_block(hits_or_repos: list, store: IndexStore, repo_name: str | None) -> str:
    if repo_name:
        repos = [store.require_repo(repo_name)]
    else:
        seen: dict[str, object] = {}
        for item in hits_or_repos:
            name = getattr(item, "repo_name", getattr(item, "name", None))
            if name and name not in seen:
                repo = store.get_repo(name)
                if repo:
                    seen[name] = repo
        repos = list(seen.values()) or store.list_repos()
    if not repos:
        return "- No repos registered. Run `smarttoken add`."
    lines = []
    for repo in repos:
        stamp = repo.last_indexed_at or "never"
        branch = repo.default_branch or "?"
        remote = repo.remote_url or "local"
        lines.append(f"- **{repo.name}** `{repo.path}` · {branch} · {remote} · indexed {stamp}")
    return "\n".join(lines)


def _snippets_for_hits(
    store: IndexStore,
    hits: list[SearchHit],
    query: str,
    context_lines: int,
) -> list[Snippet]:
    snippets: list[Snippet] = []
    for hit in hits:
        if hit.role == "generated":
            continue
        content = store.get_file_text(hit.file_id)
        symbols = extract_symbols(content, hit.lang, hit.rel_path)
        windows = _hit_windows(content, query, context_lines, symbols)
        for start, end, text, why in windows:
            snippets.append(
                Snippet(
                    repo_name=hit.repo_name,
                    rel_path=hit.rel_path,
                    start_line=start,
                    end_line=end,
                    text=redact(text),
                    why=why,
                    score=hit.score,
                    role=hit.role,
                    lang=hit.lang,
                )
            )
    # Stable sort: files by score; inside a file, windows stay in relevance order.
    snippets.sort(key=lambda item: (-item.score, item.repo_name, item.rel_path))
    return _breadth_first(snippets)


def _hit_windows(
    content: str,
    query: str,
    context_lines: int,
    symbols: list[SymbolHit] | None = None,
) -> list[tuple[int, int, str, str]]:
    """Up to MAX_WINDOWS_PER_FILE (start, end, text, why); start/end are 1-based inclusive."""
    lines = content.splitlines()
    terms = content_terms(query)
    match_idxs = [
        i
        for i, line in enumerate(lines)
        if terms and any(term_in(term, line.lower()) for term in terms)
    ]
    if not match_idxs:
        preview = "\n".join(lines[: min(len(lines), context_lines * 2)])
        if not preview.strip():
            return []
        return [(1, min(len(lines), context_lines * 2), preview, "file head (no line match)")]

    symbols = symbols or []
    max_span = min(MAX_WINDOW_LINES, context_lines * 2 + 1)
    # (start0, end0_exclusive) -> (why, symbol name or "")
    candidates: dict[tuple[int, int], tuple[str, str]] = {}
    for idx in match_idxs:
        containing = sorted(
            (s for s in symbols if s.line <= idx + 1 <= s.end_line and s.span >= MIN_SYMBOL_LINES),
            key=lambda s: s.span,
        )
        whole = next(
            (
                s
                for s in containing
                if s.span <= MAX_SYMBOL_LINES
                or (s.name.lower() in terms and s.span <= MAX_NAMED_SYMBOL_LINES)
            ),
            None,
        )
        if whole:
            candidates.setdefault((whole.line - 1, whole.end_line), (f"{whole.kind} `{whole.name}`", whole.name))
            continue
        start = max(0, idx - context_lines)
        end = min(len(lines), idx + context_lines + 1)
        if end - start > max_span:
            start = max(0, idx - max_span // 2)
            end = min(len(lines), start + max_span)
        if containing:
            outer = containing[0]
            start = max(start, outer.line - 1)
            end = min(end, outer.end_line)
            why = f"inside {outer.kind} `{outer.name}` L{outer.line}-{outer.end_line}"
            candidates.setdefault((start, end), (why, outer.name))
        else:
            candidates.setdefault((start, end), ("query term match", ""))

    spans = sorted(candidates, key=lambda span: (span[0], -span[1]))
    kept: list[tuple[int, int]] = []
    for span in spans:
        if any(o[0] <= span[0] and span[1] <= o[1] for o in kept):
            continue
        if kept and span[0] <= kept[-1][1] and candidates[span] == candidates[kept[-1]]:
            merged = (kept[-1][0], max(kept[-1][1], span[1]))
            if merged[1] - merged[0] <= max_span:
                candidates[merged] = candidates[kept[-1]]
                kept[-1] = merged
                continue
        kept.append(span)

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
    out: list[tuple[int, int, str, str]] = []
    for start, end in best:
        text = "\n".join(lines[start:end])
        out.append((start + 1, end, text, candidates[(start, end)][0]))
    return out


def _overlap_share(a: tuple[int, int], b: tuple[int, int]) -> float:
    """Shared lines as a fraction of the shorter span."""
    shared = min(a[1], b[1]) - max(a[0], b[0])
    shorter = min(a[1] - a[0], b[1] - b[0])
    return shared / shorter if shared > 0 and shorter > 0 else 0.0


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


def _shrink_snippet(snippet: Snippet, max_lines: int) -> Snippet:
    lines = snippet.text.splitlines()
    if len(lines) <= max_lines:
        return snippet
    text = "\n".join(lines[:max_lines])
    return Snippet(
        repo_name=snippet.repo_name,
        rel_path=snippet.rel_path,
        start_line=snippet.start_line,
        end_line=snippet.start_line + max_lines - 1,
        text=text,
        why=f"{snippet.why} (trimmed)",
        score=snippet.score,
        role=snippet.role,
        lang=snippet.lang,
    )


def _render_snippet(snippet: Snippet) -> str:
    return (
        f"### `{snippet.repo_name}/{snippet.rel_path}` L{snippet.start_line}-{snippet.end_line}\n"
        f"_{snippet.why}; {snippet.role}_\n\n"
        f"```{snippet.lang}\n{snippet.text}\n```\n"
    )


def _symbol_lines(store: IndexStore, hits: list[SearchHit], query: str) -> list[str]:
    lines: list[str] = []
    for hit in hits[:12]:
        content = store.get_file_text(hit.file_id)
        for symbol in symbols_matching(content, hit.lang, query, hit.rel_path):
            where = f"L{symbol.line}-{symbol.end_line}" if symbol.end_line > symbol.line else f"L{symbol.line}"
            lines.append(f"- `{symbol.kind}` `{symbol.name}` in `{hit.repo_name}/{hit.rel_path}` {where}")
    return lines


def _do_not_open(store: IndexStore, repo_name: str | None) -> list[str]:
    repo_id = store.require_repo(repo_name).id if repo_name else None
    files = store.list_files(repo_id)
    skip = []
    for record in files:
        name = record.rel_path.lower()
        if record.role in DO_NOT_OPEN_ROLES or name.endswith(".lock") or "/dist/" in f"/{name}":
            repo = next((item.name for item in store.list_repos() if item.id == record.repo_id), "?")
            skip.append(f"{repo}/{record.rel_path}")
    return skip[:20]
