"""FTS search plus optional ripgrep hit previews."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from smarttoken.index_store import IndexStore
from smarttoken.models import SearchHit
from smarttoken.rank import (
    content_terms,
    identifier_terms,
    is_sensitive_path,
    quoted_phrases,
    score_hit,
    sort_hits,
    task_kind,
    term_in,
)
from smarttoken.redact import redact_preserving_lines

ALL_TERMS_BONUS = 12.0
# A file containing a phrase the task quotes verbatim is almost always the one to change.
PHRASE_BONUS = 25.0
# Hits scoring below this fraction of the best hit are noise, not candidates.
RELATIVE_SCORE_FLOOR = 0.4


def fts_query(raw: str) -> str:
    terms = content_terms(raw)
    if not terms:
        return '""'
    identifiers = identifier_terms(terms)
    core = identifiers or (terms if len(terms) <= 3 else None)
    if core:
        return " AND ".join(f'"{term}"' for term in core)
    return " OR ".join(f'"{term}"' for term in terms)


def search_index(
    store: IndexStore,
    query: str,
    repo_name: str | None = None,
    limit: int = 15,
) -> list[SearchHit]:
    kind = task_kind(query)
    repo = store.require_repo(repo_name) if repo_name else None
    repo_id = repo.id if repo else None
    terms = content_terms(query)
    primary = fts_query(query)
    any_term = " OR ".join(f'"{term}"' for term in terms)

    all_rows = _fts_rows(store, primary, repo_id, limit)
    all_ids = {row["file_id"] for row in all_rows}
    rows = list(all_rows)
    if terms and any_term != primary:
        rows.extend(row for row in _fts_rows(store, any_term, repo_id, limit) if row["file_id"] not in all_ids)

    hits: list[SearchHit] = []
    for row in rows:
        hit = _hit_from_row(store, row, query, kind, bm25=float(row["bm25"]))
        if row["file_id"] in all_ids:
            hit.score += ALL_TERMS_BONUS
        hits.append(hit)

    for phrase in quoted_phrases(query):
        by_id = {hit.file_id: hit for hit in hits}
        for row in _fts_rows(store, f'"{phrase}"', repo_id, limit):
            hit = by_id.get(row["file_id"])
            if hit is None:
                hit = _hit_from_row(store, row, query, kind, bm25=float(row["bm25"]))
                hits.append(hit)
                by_id[hit.file_id] = hit
            hit.score += PHRASE_BONUS

    hits.extend(_path_fallback(store, query, repo_id, kind, existing=hits))
    hits = sort_hits([hit for hit in hits if not is_sensitive_path(hit.rel_path)])
    if hits:
        floor = hits[0].score * RELATIVE_SCORE_FLOOR
        hits = [hit for hit in hits if hit.score >= floor]
    return hits[:limit]


def _fts_rows(store: IndexStore, match: str, repo_id: int | None, limit: int) -> list:
    params: list[object] = [match]
    sql = """
        SELECT
            files.id AS file_id,
            files.repo_id AS repo_id,
            files.rel_path AS rel_path,
            files.lang AS lang,
            files.role AS role,
            repos.name AS repo_name,
            repos.path AS repo_path,
            repos.last_indexed_at AS last_indexed_at,
            repos.remote_url AS remote_url,
            repos.default_branch AS default_branch,
            bm25(file_text) AS bm25
        FROM file_text
        JOIN files ON files.id = file_text.file_id
        JOIN repos ON repos.id = files.repo_id
        WHERE file_text MATCH ?
    """
    if repo_id is not None:
        sql += " AND files.repo_id = ?"
        params.append(repo_id)
    sql += " ORDER BY bm25(file_text) LIMIT ?"
    params.append(max(limit * 4, 40))
    try:
        return list(store.conn.execute(sql, params).fetchall())
    except Exception:
        return []


def _hit_from_row(store: IndexStore, row, query: str, kind: str, bm25: float) -> SearchHit:
    file_id = row["file_id"] if "file_id" in row.keys() else row["id"]
    return SearchHit(
        repo_name=row["repo_name"],
        repo_path=row["repo_path"],
        rel_path=row["rel_path"],
        lang=row["lang"],
        role=row["role"],
        score=score_hit(query, row["rel_path"], row["role"], bm25, kind),
        preview=_preview_line(store.get_file_text(file_id), query),
        file_id=file_id,
        repo_id=row["repo_id"],
        last_indexed_at=row["last_indexed_at"],
        remote_url=row["remote_url"],
        default_branch=row["default_branch"],
    )


def _path_fallback(
    store: IndexStore,
    query: str,
    repo_id: int | None,
    kind: str,
    existing: list[SearchHit],
) -> list[SearchHit]:
    terms = content_terms(query)
    if not terms:
        return []
    seen = {(hit.repo_id, hit.rel_path) for hit in existing}
    extra: list[SearchHit] = []
    sql = """
        SELECT files.*, repos.name AS repo_name, repos.path AS repo_path,
               repos.last_indexed_at AS last_indexed_at,
               repos.remote_url AS remote_url,
               repos.default_branch AS default_branch
        FROM files
        JOIN repos ON repos.id = files.repo_id
    """
    params: list[object] = []
    if repo_id is not None:
        sql += " WHERE files.repo_id = ?"
        params.append(repo_id)
    for row in store.conn.execute(sql, params):
        key = (row["repo_id"], row["rel_path"])
        if key in seen:
            continue
        path = row["rel_path"].replace("\\", "/").lower()
        name = Path(path).name
        name_hits = sum(1 for term in terms if term_in(term, name))
        path_hits = sum(1 for term in terms if term_in(term, path))
        if name_hits == 0 and path_hits < 2:
            continue
        extra.append(
            SearchHit(
                repo_name=row["repo_name"],
                repo_path=row["repo_path"],
                rel_path=row["rel_path"],
                lang=row["lang"],
                role=row["role"],
                score=score_hit(query, row["rel_path"], row["role"], 8.0, kind),
                preview=_preview_line(store.get_file_text(row["id"]), query),
                file_id=row["id"],
                repo_id=row["repo_id"],
                last_indexed_at=row["last_indexed_at"],
                remote_url=row["remote_url"],
                default_branch=row["default_branch"],
            )
        )
    return extra


def _preview_line(content: str, query: str) -> str:
    terms = content_terms(query)
    original_lines = content.splitlines()
    safe_lines = redact_preserving_lines(content).splitlines()
    for index, line in enumerate(original_lines):
        lower = line.lower()
        if any(term in lower for term in terms):
            return safe_lines[index].strip()[:160]
    for line in safe_lines:
        if line.strip():
            return line.strip()[:160]
    return ""


def ripgrep_preview(root: Path, query: str, rel_path: str) -> str | None:
    rg = shutil.which("rg")
    if not rg:
        return None
    try:
        result = subprocess.run(
            [rg, "-n", "-m", "1", "-i", "--", query, str(root / rel_path)],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip().splitlines()[0][:160]
