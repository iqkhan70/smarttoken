from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from smarttoken.config import AppPaths, Settings
from smarttoken.index_store import IndexStore
from smarttoken.registry import add_paths

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


@pytest.fixture
def sample_root(tmp_path: Path) -> Path:
    dest = tmp_path / "sample_repo"
    shutil.copytree(FIXTURE, dest)
    return dest


@pytest.fixture
def home(tmp_path: Path) -> AppPaths:
    paths = AppPaths(tmp_path / "st-home")
    paths.ensure_home()
    return paths


@pytest.fixture
def indexed(home: AppPaths, sample_root: Path) -> tuple[IndexStore, str]:
    store = IndexStore(home.db_file)
    repos = add_paths(store, [sample_root])
    store.index_repo(repos[0], Settings())
    return store, repos[0].name
