from pathlib import Path

from typer.testing import CliRunner

from smarttoken.cli import app

runner = CliRunner()


def test_help_and_init(tmp_path: Path) -> None:
    help_result = runner.invoke(app, ["--help"])
    assert help_result.exit_code == 0
    assert "pack" in help_result.stdout

    result = runner.invoke(app, ["--home", str(tmp_path / "home"), "init"])
    assert result.exit_code == 0
    assert (tmp_path / "home" / "config.toml").exists()
    assert (tmp_path / "home" / "index.db").exists()


def test_add_index_map_search_pack(tmp_path: Path, sample_root: Path) -> None:
    home = tmp_path / "home"
    base = ["--home", str(home)]

    assert runner.invoke(app, [*base, "init"]).exit_code == 0
    added = runner.invoke(app, [*base, "add", str(sample_root)])
    assert added.exit_code == 0
    assert "sample_repo" in added.stdout

    indexed = runner.invoke(app, [*base, "index"])
    assert indexed.exit_code == 0
    assert "written=" in indexed.stdout

    mapped = runner.invoke(app, [*base, "map"])
    assert mapped.exit_code == 0
    assert "Accepts payment webhooks" in mapped.stdout
    assert "src/app.py" in mapped.stdout

    searched = runner.invoke(app, [*base, "search", "webhook signature"])
    assert searched.exit_code == 0
    assert "webhook.py" in searched.stdout

    packed = runner.invoke(app, [*base, "pack", "fix retry on payment webhook", "--budget", "1200"])
    assert packed.exit_code == 0
    assert "Smart Token pack" in packed.stdout
    assert "Used " in packed.stdout
    assert "sk-test-not-real" not in packed.stdout

    measured = runner.invoke(
        app,
        [
            *base,
            "measure",
            "where is the webhook signature checked?",
            "--expect",
            "src/payments/webhook.py",
            "--budget",
            "1200",
        ],
    )
    assert measured.exit_code == 0
    assert "PASS" in measured.stdout
    assert "Saved vs dump" in measured.stdout


def test_eval_fixture() -> None:
    result = runner.invoke(app, ["eval", "--fixture"])
    assert result.exit_code == 0, result.stdout
    assert "PASS" in result.stdout
    assert "webhook-signature" in result.stdout
