import sqlite3
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
    secrets_file = sample_root / "src/payments/secrets_demo.py"
    long_secret = "LONGSECRETDO_NOT_LEAK_" * 12
    pem_body = "pemmaterial zebravalve"
    pem_block = [
        "# padding before the PEM block"
    ] * 20 + ["-----BEGIN RSA PRIVATE KEY-----", pem_body] + [
        "privatekeypaddingmaterial"
    ] * 40 + ["-----END RSA PRIVATE KEY-----"]
    secrets_file.write_text(
        secrets_file.read_text()
        + "\n"
        + "\n".join(pem_block)
        + f'\nLONG_API_KEY = "{long_secret}"\n',
        encoding="utf-8",
    )
    paths = AppPaths(tmp_path / "home")
    paths.ensure_home()
    monkeypatch.setenv("SMARTTOKEN_HOME", str(paths.home))
    with IndexStore(paths.db_file) as store:
        repo = add_paths(store, [sample_root])[0]
        store.index_repo(repo, Settings())
    with IndexStore(paths.db_file, read_only=True) as store:
        with pytest.raises(sqlite3.OperationalError):
            store.conn.execute("CREATE TABLE should_not_be_written (id INTEGER)")

    result = search("API_KEY", limit=5)

    assert "src/payments/secrets_demo.py" in result
    assert "[REDACTED]" in result
    assert "sk-test-not-real" not in result
    assert "AKIAIOSFODNN7EXAMPLE" not in result

    long_secret_result = search("LONG_API_KEY", limit=5)
    assert "src/payments/secrets_demo.py" in long_secret_result
    assert "[REDACTED]" in long_secret_result
    assert long_secret not in result
    assert long_secret not in long_secret_result

    pem_result = search(pem_body, limit=5)
    assert "src/payments/secrets_demo.py" in pem_result
    assert "[REDACTED]" in pem_result
    assert pem_body not in pem_result

    pem_pack = pack(pem_body, repo=repo.name, budget=1200)
    assert "src/payments/secrets_demo.py" in pem_pack
    assert "[REDACTED]" in pem_pack
    snippets = pem_pack.split("## Snippets", 1)[1].split("## Symbols / callers", 1)[0]
    assert pem_body not in snippets
    assert "privatekeypaddingmaterial" not in snippets


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
    with pytest.raises(ValueError, match="exceeding the budget"):
        pack("long task " * 300, budget=200)
    with pytest.raises(ValueError, match="must not be empty"):
        search("   ")
    with pytest.raises(ValueError, match="Unknown repo"):
        search("webhook", repo="")
    with pytest.raises(ValueError, match="Unknown repo"):
        pack("webhook", repo="")


def test_mcp_requires_existing_index_without_creating_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "missing-home"
    monkeypatch.setenv("SMARTTOKEN_HOME", str(home))

    with pytest.raises(ValueError, match="Run `smarttoken index` first"):
        search("anything")

    assert not (home / "index.db").exists()
