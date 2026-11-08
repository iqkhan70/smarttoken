from smarttoken.config import Settings
from smarttoken.scan import infer_role, iter_rel_paths, scan_repo


def test_gitignore_and_always_skip_exclude_dist(sample_root) -> None:
    rels = iter_rel_paths(sample_root)
    assert "dist/bundle.js" not in rels
    assert "src/payments/webhook.py" in rels


def test_roles() -> None:
    assert infer_role("src/payments/webhook.py") == "source"
    assert infer_role("tests/test_webhook.py") == "test"
    assert infer_role("docs/deploy.md") == "doc"
    assert infer_role("README.md") == "doc"
    assert infer_role("pyproject.toml") == "config"
    assert infer_role("dist/bundle.js") == "generated"
    assert infer_role("deploy/digitalocean/scripts/fix-catalog-migrations.sh") == "config"


def test_scan_skips_dist(sample_root) -> None:
    files, _skipped = scan_repo(sample_root, Settings())
    paths = {item.rel_path for item in files}
    assert "src/payments/webhook.py" in paths
    assert "dist/bundle.js" not in paths
