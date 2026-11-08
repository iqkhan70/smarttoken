"""Register local clone roots in the index database."""

from __future__ import annotations

from pathlib import Path

from smarttoken.index_store import IndexStore
from smarttoken.models import Repo


def add_paths(store: IndexStore, paths: list[Path]) -> list[Repo]:
    added: list[Repo] = []
    for raw in paths:
        root = raw.expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise FileNotFoundError(f"Not a directory: {raw}")
        added.append(store.upsert_repo(root.name, str(root)))
    return added
