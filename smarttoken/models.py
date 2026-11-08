"""Shared dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Repo:
    id: int
    name: str
    path: str
    remote_url: str | None = None
    default_branch: str | None = None
    last_indexed_at: str | None = None
    content_tokens: int | None = None


@dataclass
class FileRecord:
    id: int
    repo_id: int
    rel_path: str
    lang: str
    role: str
    hash: str
    mtime: float
    size: int


@dataclass
class ScannedFile:
    rel_path: str
    abs_path: str
    lang: str
    role: str
    hash: str
    mtime: float
    size: int
    content: str


@dataclass
class SearchHit:
    repo_name: str
    repo_path: str
    rel_path: str
    lang: str
    role: str
    score: float
    preview: str
    file_id: int
    repo_id: int
    last_indexed_at: str | None = None
    remote_url: str | None = None
    default_branch: str | None = None


@dataclass
class Snippet:
    repo_name: str
    rel_path: str
    start_line: int
    end_line: int
    text: str
    why: str
    score: float
    role: str
    lang: str


@dataclass
class IndexStats:
    repo_name: str
    scanned: int = 0
    written: int = 0
    unchanged: int = 0
    deleted: int = 0
    skipped: int = 0
    languages: dict[str, int] = field(default_factory=dict)
