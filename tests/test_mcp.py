from pathlib import Path

import pytest

pytest.importorskip("mcp")

from smarttoken.config import AppPaths, Settings
from smarttoken.index_store import IndexStore
from smarttoken.mcp_server import pack, search
from smarttoken.registry import add_paths


def test_mcp_search_returns_ranked_paths_and_redacts_previews(
    tmp_path: Path, sample_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = AppPaths(tmp_path / "home")
    paths.ensure_home()
    monkeypatch.setenv("SMARTTOKEN_HOME", str(paths.home))
    with IndexStore(paths.db_file) as store:
        repo = add_paths(store, [sample_root])[0]
        store.index_repo(repo, Settings())

    result = search("webhook signature", limit=5)

    assert "src/payments/webhook.py" in result
    assert "sk-test-not-real" not in result
    assert "AKIAIOSFODNN7EXAMPLE" not in result


def test_mcp_pack_is_bounded_and_rejects_bad_inputs(
    tmp_path: Path, sample_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = AppPaths(tmp_path / "home")
    paths.ensure_home()
    monkeypatch.setenv("SMARTTOKEN_HOME", str(paths.home))
    with IndexStore(paths.db_file) as store:
        repo = add_paths(store, [sample_root])[0]
        store.index_repo(repo, Settings())

    result = pack("fix retry on payment webhook", budget=800)

    assert "Smart Token pack" in result
    assert "Used " in result
    assert "sk-test-not-real" not in result
    with pytest.raises(ValueError, match="at least 200"):
        pack("webhook", budget=199)
    with pytest.raises(ValueError, match="must not be empty"):
        search("   ")
