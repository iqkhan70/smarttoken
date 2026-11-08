from pathlib import Path

from typer.testing import CliRunner

from smarttoken import handoff
from smarttoken.cli import _with_default_command, app

runner = CliRunner()


def _home(tmp_path: Path, sample_root: Path, tiers: str = "[3500, 5000, 7000]") -> list:
    home = tmp_path / "home"
    base = ["--home", str(home)]
    runner.invoke(app, [*base, "init"])
    runner.invoke(app, [*base, "add", str(sample_root)])
    runner.invoke(app, [*base, "index"])
    config = home / "config.toml"
    text = config.read_text().replace("ask_tiers = [3500, 5000, 7000]", f"ask_tiers = {tiers}")
    config.write_text(text)
    return base


def test_default_command_routing() -> None:
    assert _with_default_command(["add a button", "all"]) == ["ask", "add a button", "all"]
    assert _with_default_command(["--home", "/h", "add a button"]) == ["--home", "/h", "ask", "add a button"]
    assert _with_default_command(["search", "x"]) == ["search", "x"]
    assert _with_default_command(["--help"]) == ["--help"]
    assert _with_default_command([]) == []


def test_choose_tier_and_messages() -> None:
    tiers = [3500, 5000, 7000]
    assert handoff.choose_tier(3100, tiers) == (0, 3500)
    assert handoff.choose_tier(3501, tiers) == (1, 5000)
    assert handoff.choose_tier(7000, tiers) == (2, 7000)
    assert handoff.choose_tier(7001, tiers) is None
    assert handoff.tier_message(3100, 0, 3500, 3) == "This will take ~3100 tokens."
    assert handoff.tier_message(4200, 1, 5000, 3).startswith("Base limit exceeded. Requires 5000 tokens")
    assert handoff.tier_message(6100, 2, 7000, 3).startswith("Large context detected. Requires 7000 tokens")


def test_pack_style_defaults() -> None:
    assert handoff.resolve_pack_style("auto", "copilot") == "pointer"
    assert handoff.resolve_pack_style("auto", "vscode") == "pointer"
    assert handoff.resolve_pack_style("auto", "print") == "full"
    assert handoff.resolve_pack_style("auto", "clipboard") == "full"
    assert handoff.resolve_pack_style("full", "copilot") == "full"
    assert handoff.resolve_pack_style("pointer", "print") == "pointer"


def test_ask_pointer_names_locations_without_code(tmp_path: Path, sample_root: Path) -> None:
    base = _home(tmp_path, sample_root)
    result = runner.invoke(
        app,
        [*base, "ask", "where is the webhook signature checked", "--to", "print", "--pack", "pointer", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "Pointer prompt is ~" in result.output
    assert "Open the locations below first" in result.output
    prompt = result.output.split("Open the locations below first", 1)[1]
    assert "```" not in prompt
    assert " L" in prompt


def test_ask_print_sends_prompt(tmp_path: Path, sample_root: Path) -> None:
    base = _home(tmp_path, sample_root)
    result = runner.invoke(app, [*base, "ask", "where is the webhook signature checked", "--to", "print", "--yes"])
    assert result.exit_code == 0, result.output
    assert "This will take ~" in result.output
    assert "# Task\nwhere is the webhook signature checked" in result.output
    assert list((tmp_path / "home" / "packs").glob("*.md"))


def test_ask_no_answer_sends_nothing(tmp_path: Path, sample_root: Path) -> None:
    base = _home(tmp_path, sample_root)
    result = runner.invoke(app, [*base, "ask", "where is the webhook signature checked", "all", "--to", "print"], input="n\n")
    assert result.exit_code == 0
    assert "Cancelled. Nothing sent." in result.output
    assert not (tmp_path / "home" / "packs").exists()


def test_ask_over_limit_exits(tmp_path: Path, sample_root: Path) -> None:
    base = _home(tmp_path, sample_root, tiers="[100, 200, 300]")
    result = runner.invoke(app, [*base, "ask", "where is the webhook signature checked", "--to", "print", "--yes"])
    assert result.exit_code == 4
    assert "(Max limit 300). Please narrow your prompt." in result.output


def test_ask_unknown_repo(tmp_path: Path, sample_root: Path) -> None:
    base = _home(tmp_path, sample_root)
    result = runner.invoke(app, [*base, "ask", "webhook signature", "nope", "--to", "print", "--yes"])
    assert result.exit_code == 1
    assert "Unknown repo 'nope'" in result.output


def test_inside_repo_folder_targets_that_repo(tmp_path: Path, sample_root: Path, monkeypatch) -> None:
    base = _home(tmp_path, sample_root)
    monkeypatch.chdir(sample_root)
    result = runner.invoke(app, [*base, "ask", "webhook signature", "--to", "print", "--yes"])
    assert result.exit_code == 0, result.output
    assert f"Target: {sample_root.name} (current folder" in result.output
    everything = runner.invoke(app, [*base, "ask", "webhook signature", "all", "--to", "print", "--yes"])
    assert "Target: all repos" in everything.output


def test_low_confidence_defaults_to_no(tmp_path: Path, sample_root: Path) -> None:
    base = _home(tmp_path, sample_root)
    result = runner.invoke(app, [*base, "ask", "where is the kafka consumer for graphql webhook", "--to", "print"], input="\n")
    assert "Default is No" in result.output
    assert "Cancelled. Nothing sent." in result.output


def test_cursor_link_falls_back_to_file_pointer(tmp_path: Path) -> None:
    long_prompt = "context line\n" * 2000
    plan = handoff.plan("cursor", long_prompt, tmp_path / "p.md", "add x", None)
    assert plan.url and len(plan.url) <= handoff.CURSOR_DEEPLINK_MAX
    assert "p.md" in plan.url
    short = handoff.plan("cursor", "# Task\nadd x", tmp_path / "p.md", "add x", None)
    assert "p.md" not in short.url


def test_workspace_for() -> None:
    assert handoff.workspace_for(["/a/b"]) == "/a/b"
    assert handoff.workspace_for(["/src/one", "/src/two"]) == "/src"
    assert handoff.workspace_for([]) is None
