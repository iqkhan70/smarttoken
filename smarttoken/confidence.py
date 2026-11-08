"""Before you spend paid tokens: how likely is the pack to contain the right files?"""

from __future__ import annotations

from dataclasses import dataclass, field

from smarttoken.index_store import IndexStore
from smarttoken.models import SearchHit
from smarttoken.rank import content_terms, stem, term_in
from smarttoken.symbols import symbols_matching


@dataclass
class Signals:
    terms: list[str]
    hits: int
    top_path: str | None
    top_role: str | None
    # Fraction of task words found in the best of the top 3 files (path or content).
    coverage: float
    # Task words found in no indexed file at all.
    missing_terms: list[str]
    named_symbols: list[str] = field(default_factory=list)


@dataclass
class Confidence:
    level: str
    reasons: list[str]
    advice: str
    signals: Signals


HIGH_COVERAGE = 0.75
LOW_COVERAGE = 0.5


def assess(store: IndexStore, query: str, hits: list[SearchHit]) -> Confidence:
    signals = gather_signals(store, query, hits)
    reasons: list[str] = []
    if not hits or not signals.terms:
        reasons = ["no search hits" if signals.terms else "no searchable words in the question"]
        if signals.missing_terms:
            reasons.append("not found anywhere in the index: " + ", ".join(signals.missing_terms))
        return Confidence(
            "LOW",
            reasons,
            "Rephrase with the names you'd grep for, or ask Cursor directly.",
            signals,
        )

    reasons.append(f"{signals.coverage:.0%} of task words are in the top files")
    if signals.missing_terms:
        reasons.append("not found anywhere in the index: " + ", ".join(signals.missing_terms))
    if signals.named_symbols:
        reasons.append("functions/classes named after the task: " + ", ".join(signals.named_symbols))

    missing_share = len(signals.missing_terms) / len(signals.terms)
    if signals.coverage < LOW_COVERAGE or missing_share >= 0.5:
        level = "LOW"
        advice = (
            "The repo probably doesn't contain this. Check the missing words, rephrase with names from "
            "the code, or ask Cursor directly instead of pasting the pack."
        )
    elif not signals.missing_terms and signals.coverage >= HIGH_COVERAGE:
        level = "HIGH"
        advice = "Paste the pack: `smarttoken pack \"...\" --copy`."
    else:
        level = "MEDIUM"
        advice = (
            "Pack is likely close. If a missing word is the main subject, the repo may not have it; "
            "otherwise paste the pack and expect a follow-up."
        )
    return Confidence(level, reasons, advice, signals)


def gather_signals(store: IndexStore, query: str, hits: list[SearchHit]) -> Signals:
    terms = content_terms(query)
    top = hits[0] if hits else None
    coverage = 0.0
    named: list[str] = []
    for hit in hits[:3]:
        content = store.get_file_text(hit.file_id)
        text = f"{hit.rel_path}\n{content}".lower()
        if terms:
            coverage = max(coverage, sum(1 for term in terms if term_in(term, text)) / len(terms))
        for symbol in symbols_matching(content, hit.lang, query, hit.rel_path):
            if symbol.span > 1 and symbol.name not in named:
                named.append(symbol.name)
    repo_id = hits[0].repo_id if hits and len({h.repo_id for h in hits}) == 1 else None
    missing = [
        term
        for term in terms
        if not _term_indexed(store, term, repo_id) and not _term_indexed(store, stem(term), repo_id)
    ]
    return Signals(
        terms=terms,
        hits=len(hits),
        top_path=f"{top.repo_name}/{top.rel_path}" if top else None,
        top_role=top.role if top else None,
        coverage=coverage,
        missing_terms=missing,
        named_symbols=named[:5],
    )


def _term_indexed(store: IndexStore, term: str, repo_id: int | None) -> bool:
    sql = "SELECT 1 FROM file_text WHERE file_text MATCH ?"
    params: list[object] = [f'"{term}"']
    if repo_id is not None:
        sql += " AND repo_id = ?"
        params.append(repo_id)
    if store.conn.execute(sql + " LIMIT 1", params).fetchone():
        return True
    path_sql = "SELECT 1 FROM files WHERE lower(rel_path) LIKE ?"
    path_params: list[object] = [f"%{term}%"]
    if repo_id is not None:
        path_sql += " AND repo_id = ?"
        path_params.append(repo_id)
    return store.conn.execute(path_sql + " LIMIT 1", path_params).fetchone() is not None
