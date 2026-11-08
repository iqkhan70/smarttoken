"""SQLite + FTS5 index stored outside the clones."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from smarttoken.config import Settings
from smarttoken.models import FileRecord, IndexStats, Repo, ScannedFile
from smarttoken.scan import git_metadata, is_git_repo, scan_repo


SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL UNIQUE,
    remote_url TEXT,
    default_branch TEXT,
    last_indexed_at TEXT
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    repo_id INTEGER NOT NULL,
    rel_path TEXT NOT NULL,
    lang TEXT NOT NULL,
    role TEXT NOT NULL,
    hash TEXT NOT NULL,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    UNIQUE(repo_id, rel_path),
    FOREIGN KEY(repo_id) REFERENCES repos(id) ON DELETE CASCADE
);

CREATE VIRTUAL TABLE IF NOT EXISTS file_text USING fts5(
    file_id UNINDEXED,
    repo_id UNINDEXED,
    rel_path,
    content,
    tokenize = 'porter'
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class IndexStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(repos)")}
        if "content_tokens" not in cols:
            self.conn.execute("ALTER TABLE repos ADD COLUMN content_tokens INTEGER")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> IndexStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def upsert_repo(self, name: str, path: str) -> Repo:
        root = Path(path)
        remote, branch = git_metadata(root) if is_git_repo(root) else (None, None)
        existing = self.get_repo_by_path(path)
        if existing:
            self.conn.execute(
                """
                UPDATE repos
                SET name = ?, remote_url = ?, default_branch = ?
                WHERE id = ?
                """,
                (existing.name, remote, branch, existing.id),
            )
            self.conn.commit()
            return self.get_repo(existing.name) or existing

        unique = self._unique_name(name)
        cur = self.conn.execute(
            """
            INSERT INTO repos (name, path, remote_url, default_branch)
            VALUES (?, ?, ?, ?)
            """,
            (unique, path, remote, branch),
        )
        self.conn.commit()
        return Repo(
            id=int(cur.lastrowid),
            name=unique,
            path=path,
            remote_url=remote,
            default_branch=branch,
        )

    def _unique_name(self, base: str) -> str:
        name = base
        suffix = 2
        while self.get_repo(name) is not None:
            name = f"{base}-{suffix}"
            suffix += 1
        return name

    def get_repo(self, name: str) -> Repo | None:
        row = self.conn.execute("SELECT * FROM repos WHERE name = ?", (name,)).fetchone()
        return _repo_from_row(row) if row else None

    def get_repo_by_path(self, path: str) -> Repo | None:
        row = self.conn.execute("SELECT * FROM repos WHERE path = ?", (path,)).fetchone()
        return _repo_from_row(row) if row else None

    def list_repos(self) -> list[Repo]:
        rows = self.conn.execute("SELECT * FROM repos ORDER BY name").fetchall()
        return [_repo_from_row(row) for row in rows]

    def require_repo(self, name: str) -> Repo:
        repo = self.get_repo(name)
        if repo is None:
            raise KeyError(f"Unknown repo '{name}'. Run `smarttoken repos`.")
        return repo

    def list_files(self, repo_id: int | None = None) -> list[FileRecord]:
        if repo_id is None:
            rows = self.conn.execute("SELECT * FROM files ORDER BY rel_path").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM files WHERE repo_id = ? ORDER BY rel_path",
                (repo_id,),
            ).fetchall()
        return [_file_from_row(row) for row in rows]

    def get_file_text(self, file_id: int) -> str:
        row = self.conn.execute(
            "SELECT content FROM file_text WHERE file_id = ?",
            (file_id,),
        ).fetchone()
        return row["content"] if row else ""

    def index_repo(self, repo: Repo, settings: Settings) -> IndexStats:
        root = Path(repo.path)
        remote, branch = git_metadata(root) if is_git_repo(root) else (None, None)
        scanned, skipped = scan_repo(root, settings)
        stats = IndexStats(repo_name=repo.name, scanned=len(scanned), skipped=skipped)

        existing_rows = self.conn.execute(
            "SELECT id, rel_path, hash FROM files WHERE repo_id = ?",
            (repo.id,),
        ).fetchall()
        by_path = {row["rel_path"]: row for row in existing_rows}
        seen: set[str] = set()

        for item in scanned:
            seen.add(item.rel_path)
            stats.languages[item.lang] = stats.languages.get(item.lang, 0) + 1
            prev = by_path.get(item.rel_path)
            if prev and prev["hash"] == item.hash:
                stats.unchanged += 1
                continue
            self._upsert_file(repo.id, item, prev["id"] if prev else None)
            stats.written += 1

        stale = [row["id"] for row in existing_rows if row["rel_path"] not in seen]
        for file_id in stale:
            self.conn.execute("DELETE FROM file_text WHERE file_id = ?", (file_id,))
            self.conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
            stats.deleted += 1

        content_tokens = self._sum_content_tokens(repo.id, settings)
        self.conn.execute(
            """
            UPDATE repos
            SET remote_url = ?, default_branch = ?, last_indexed_at = ?, content_tokens = ?
            WHERE id = ?
            """,
            (remote, branch, utc_now(), content_tokens, repo.id),
        )
        self.conn.commit()
        return stats

    def _sum_content_tokens(self, repo_id: int, settings: Settings) -> int:
        from smarttoken.tokens import count_tokens

        total = 0
        rows = self.conn.execute(
            """
            SELECT files.id, files.role
            FROM files
            WHERE files.repo_id = ?
            """,
            (repo_id,),
        ).fetchall()
        for row in rows:
            if row["role"] == "generated":
                continue
            total += count_tokens(self.get_file_text(row["id"]), settings.encoding)
        return total

    def _upsert_file(self, repo_id: int, item: ScannedFile, file_id: int | None) -> None:
        if file_id is None:
            cur = self.conn.execute(
                """
                INSERT INTO files (repo_id, rel_path, lang, role, hash, mtime, size)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (repo_id, item.rel_path, item.lang, item.role, item.hash, item.mtime, item.size),
            )
            file_id = int(cur.lastrowid)
        else:
            self.conn.execute(
                """
                UPDATE files
                SET lang = ?, role = ?, hash = ?, mtime = ?, size = ?
                WHERE id = ?
                """,
                (item.lang, item.role, item.hash, item.mtime, item.size, file_id),
            )
            self.conn.execute("DELETE FROM file_text WHERE file_id = ?", (file_id,))

        self.conn.execute(
            """
            INSERT INTO file_text (file_id, repo_id, rel_path, content)
            VALUES (?, ?, ?, ?)
            """,
            (file_id, repo_id, item.rel_path, item.content),
        )


def _repo_from_row(row: sqlite3.Row) -> Repo:
    return Repo(
        id=row["id"],
        name=row["name"],
        path=row["path"],
        remote_url=row["remote_url"],
        default_branch=row["default_branch"],
        last_indexed_at=row["last_indexed_at"],
        content_tokens=row["content_tokens"] if "content_tokens" in row.keys() else None,
    )


def _file_from_row(row: sqlite3.Row) -> FileRecord:
    return FileRecord(
        id=row["id"],
        repo_id=row["repo_id"],
        rel_path=row["rel_path"],
        lang=row["lang"],
        role=row["role"],
        hash=row["hash"],
        mtime=row["mtime"],
        size=row["size"],
    )
