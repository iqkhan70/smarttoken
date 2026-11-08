# smarttoken enhancement 1.2: default `ask` command (confirm the token tier, then send to the AI tool)

Apply on top of enhancement 1.1. Python 3.9 compatible (`Optional`/`List` in signatures, no `match`,
`from __future__ import annotations` at the top of each module).

## Why

`check` then `pack --copy` then paste is three steps, and it's easy to paste the wrong pack. Now one command
sizes the context, asks before spending, and hands the prompt to the AI tool directly.

## Behaviour

```
smarttoken "add a button to upload an aop" all                  # all repos (same as no target)
smarttoken "add a button to upload an aop" my-frontend-app      # one repo, positional
smarttoken "add a button to upload an aop" --repo my-frontend-app
smarttoken "add a button to upload an aop" --to vscode          # Copilot Chat in VS Code, agent mode
smarttoken "add a button to upload an aop" --to cursor-agent    # add --model NAME; `agent models` lists names
```

1. Resolve the target: `all` means every repo. With no target, it uses the registered repo that contains the
   current folder (so running inside a repo targets that repo), else every repo. A name means that repo; an unknown name lists the indexed repos and exits 1.
2. Search, check confidence, and run the focus check, the same as `check`. The question is recorded in history as `ask`.
3. Build the pack with budget 50,000. Its size is then set by the pack rules (≤10 snippets, ≤3 windows per file), not by a budget.
   Wrap it in the agent prompt (task, where to work, and "start from these files") and count tokens.
4. Pick the smallest tier in `ask_tiers` (default `[3500, 5000, 7000]`):
   - tier 1: `This will take ~3100 tokens.`
   - middle tiers: `Base limit exceeded. Requires 5000 tokens (~4051).`
   - last tier: `Large context detected. Requires 7000 tokens (~6100).`
   - over the last tier: `Context is 8123 tokens (Max limit 7000). Please narrow your prompt.`, then exit 4.
5. `Proceed? [Y/n]`. When confidence is LOW or the focus check warned, it prints "Default is No because of the
   warning above." and the prompt becomes `[y/N]`. `--yes` skips the question.
6. On yes:
   - save the prompt to `<home>/packs/<time>-<slug>.md`;
   - log a usage row (`source="ask"`);
   - send it with `--to`, or `send_to` from config when `--to` isn't given.

| `--to`         | What runs                                                                                                                                                     |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `clipboard`    | default; copies the prompt                                                                                                                                    |
| `print`        | prints the prompt to stdout                                                                                                                                   |
| `vscode`       | `code chat -m agent "<task>. Use the pack attached from stdin…" -`, with the prompt on stdin. Opens Copilot Chat in agent mode, run from the repo folder |
| `cursor`       | opens `cursor://anysphere.cursor-deeplink/prompt?text=<prompt>`, which Cursor asks you to confirm. URLs are capped at 10,000 chars, so a long prompt becomes "Read the pack at <saved file> first, then do the task" |
| `cursor-agent` | `cursor-agent --workspace <repo> [--model M] "<prompt>"` (falls back to `agent`), interactive in the terminal                                                 |
| `copilot`      | `copilot [--model M] -i "<prompt>"` (GitHub Copilot CLI), interactive, run from the repo folder                                                               |
| `claude`       | `claude [--model M] "<prompt>"` (Claude Code), interactive, run from the repo folder                                                                          |

If the CLI isn't on PATH, it prints the error, copies to the clipboard instead, and exits 1.
The workspace is the repo folder. When the top files span several repos, it's their common parent folder.

At work, set the default once in `~/.smarttoken/config.toml`:

```toml
send_to = "vscode"
```

`smarttoken "task"` works because the console script is now `smarttoken.cli:run`. That wrapper inserts `ask`
when the first word isn't a subcommand, so `smarttoken search …` and the other commands are unchanged.

Real tasks measured on Eats and golangpoc: 1.8k to 4.2k tokens. 11 of 15 fit tier 1, 4 need tier 2, none
were over the limit.

## Change 1: `pyproject.toml`

```toml
[project.scripts]
smarttoken = "smarttoken.cli:run"
```

Reinstall after the change: `pip install -e '.[dev]'`.

## Change 2: `config.py`

Add `from typing import List`, three settings, and `packs_dir`:

```python
class Settings(BaseModel):
    # ... existing fields ...
    dollars_per_million_input: float = Field(default=3.0, ge=0)
    # `smarttoken "task"`: confirm at each tier; above the last one, ask to narrow the prompt.
    ask_tiers: List[int] = Field(default_factory=lambda: [3500, 5000, 7000])
    # Where `smarttoken "task"` sends the prompt: clipboard, print, vscode, cursor, cursor-agent, copilot, claude.
    send_to: str = "clipboard"
    # Optional model for CLIs that take one (cursor-agent, claude, copilot).
    send_model: str = ""
```

In `AppPaths.__init__`, add:

```python
        self.packs_dir = home / "packs"
```

In `save_settings`, add these lines to the written TOML:

```python
            f"ask_tiers = [{', '.join(str(t) for t in settings.ask_tiers)}]\n"
            f'send_to = "{settings.send_to}"\n'
            f'send_model = "{settings.send_model}"\n'
```

## Change 3: new module `smarttoken/handoff.py`

```python
"""Send a finished prompt straight to an AI tool, so there's nothing to copy and paste."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple
from urllib.parse import quote

from smarttoken.clipboard import copy_text
from smarttoken.config import AppPaths

TARGETS = ("clipboard", "print", "vscode", "cursor", "cursor-agent", "copilot", "claude")
# Cursor rejects prompt deeplinks longer than this (whole URL, after encoding).
CURSOR_DEEPLINK_MAX = 10_000
CURSOR_DEEPLINK = "cursor://anysphere.cursor-deeplink/prompt?text="

# Binaries to try for each CLI target, in order.
_BINARIES = {
    "vscode": ("code",),
    "cursor-agent": ("cursor-agent", "agent"),
    "copilot": ("copilot",),
    "claude": ("claude",),
}


def choose_tier(tokens: int, tiers: Sequence[int]) -> Optional[Tuple[int, int]]:
    """(tier index, tier limit) for the smallest tier that fits; None when over the largest."""
    for index, limit in enumerate(sorted(tiers)):
        if tokens <= limit:
            return index, limit
    return None


def tier_message(tokens: int, index: int, limit: int, tier_count: int) -> str:
    if index == 0:
        return f"This will take ~{tokens} tokens."
    if index == tier_count - 1:
        return f"Large context detected. Requires {limit} tokens (~{tokens})."
    return f"Base limit exceeded. Requires {limit} tokens (~{tokens})."


def build_prompt(task: str, packet_markdown: str, workspaces: List[str]) -> str:
    where = ", ".join(f"`{path}`" for path in workspaces) or "the registered repos"
    return (
        f"# Task\n{task}\n\n"
        f"Work in: {where}.\n"
        "The Smart Token pack below lists the files, line ranges and snippets that matter. "
        "Start from those files. Open other files only if the pack is not enough, and say which ones and why. "
        "Keep changes small and inside the named files where possible.\n\n"
        f"{packet_markdown}\n"
    )


def save_prompt(paths: AppPaths, task: str, text: str) -> Path:
    paths.packs_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")[:40] or "task"
    path = paths.packs_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{slug}.md"
    path.write_text(text, encoding="utf-8")
    return path


def workspace_for(repo_paths: List[str]) -> Optional[str]:
    """The repo folder, or the common parent when the task spans several repos."""
    unique = sorted(set(repo_paths))
    if not unique:
        return None
    if len(unique) == 1:
        return unique[0]
    common = os.path.commonpath(unique)
    return common if common not in ("", os.sep) else unique[0]


@dataclass
class Handoff:
    target: str
    command: List[str] = field(default_factory=list)
    stdin: Optional[str] = None
    cwd: Optional[str] = None
    url: Optional[str] = None
    note: str = ""


def _binary(target: str) -> Optional[str]:
    for name in _BINARIES.get(target, ()):
        found = shutil.which(name)
        if found:
            return found
    return None


def plan(
    target: str,
    prompt: str,
    prompt_file: Path,
    task: str,
    workspace: Optional[str],
    model: str = "",
) -> Handoff:
    """What to run for a target. Raises ValueError for unknown targets or missing CLIs."""
    if target not in TARGETS:
        raise ValueError(f"Unknown target '{target}'. Use one of: {', '.join(TARGETS)}.")
    if target in ("clipboard", "print"):
        return Handoff(target)

    if target == "cursor":
        url = CURSOR_DEEPLINK + quote(prompt, safe="")
        note = "Review and send it in Cursor's chat."
        if len(url) > CURSOR_DEEPLINK_MAX:
            short = (
                f"{task}\n\nRead the Smart Token context pack at {prompt_file} first. "
                "It lists the files and line ranges to change. Then do the task."
            )
            url = CURSOR_DEEPLINK + quote(short, safe="")
            note = "The pack is too long for a Cursor link, so the prompt points Cursor at the saved pack file."
        return Handoff(target, url=url, note=note)

    exe = _binary(target)
    if exe is None:
        names = " or ".join(_BINARIES[target])
        raise ValueError(f"'{names}' not found on PATH. Install it, or use --to clipboard.")

    if target == "vscode":
        instruction = (
            f"{task}. Use the Smart Token context pack attached from stdin: it lists the files and line ranges to change."
        )
        return Handoff(target, [exe, "chat", "-m", "agent", instruction, "-"], stdin=prompt, cwd=workspace,
                       note="Opened in VS Code chat (Copilot agent mode).")
    if target == "cursor-agent":
        command = [exe]
        if workspace:
            command += ["--workspace", workspace]
        if model:
            command += ["--model", model]
        return Handoff(target, command + [prompt], cwd=workspace, note="Starting Cursor Agent in this terminal.")
    if target == "copilot":
        command = [exe] + (["--model", model] if model else []) + ["-i", prompt]
        return Handoff(target, command, cwd=workspace, note="Starting GitHub Copilot CLI in this terminal.")
    command = [exe] + (["--model", model] if model else []) + [prompt]
    return Handoff(target, command, cwd=workspace, note="Starting Claude Code in this terminal.")


def _open_url(url: str) -> bool:
    try:
        if sys.platform == "darwin":
            return subprocess.run(["open", url], check=False).returncode == 0
        if sys.platform.startswith("win"):
            os.startfile(url)  # type: ignore[attr-defined]
            return True
        opener = shutil.which("xdg-open")
        return bool(opener) and subprocess.run([opener, url], check=False).returncode == 0
    except OSError:
        return False


def execute(handoff: Handoff, prompt: str) -> Tuple[bool, str]:
    """Run the hand-off. Returns (ok, message)."""
    if handoff.target == "print":
        return True, ""
    if handoff.target == "clipboard":
        if copy_text(prompt):
            return True, "Copied to clipboard. Paste it into your AI chat."
        return False, "Clipboard copy failed (no pbcopy/wl-copy/xclip)."
    if handoff.url:
        if _open_url(handoff.url):
            return True, handoff.note
        return False, "Could not open the Cursor link."
    try:
        result = subprocess.run(handoff.command, input=handoff.stdin, text=True, cwd=handoff.cwd, check=False)
    except OSError as exc:
        return False, f"Could not start {handoff.command[0]}: {exc}"
    if result.returncode != 0:
        return False, f"{Path(handoff.command[0]).name} exited with code {result.returncode}."
    return True, handoff.note
```

## Change 4: `cli.py`

Imports and constant:

```python
import sys
from typing import List, Optional

from smarttoken import __version__, focus, handoff
from smarttoken.tokens import count_tokens

# `ask` sizes the natural pack with this ceiling, then picks the smallest tier that holds it.
ASK_NATURAL_BUDGET = 50_000
```

`_repo_for_cwd` and the `ask` command (put them before `focus`; it reuses `_focus_check`, `_print_drift` and `_drift_blocks_copy` from 1.1):

```python
def _repo_for_cwd(repos: list) -> Optional[str]:
    """The registered repo containing the current folder (deepest match), if any."""
    cwd = Path.cwd().resolve()
    best = None
    for item in repos:
        root = Path(item.path).resolve()
        if (cwd == root or root in cwd.parents) and (best is None or len(str(root)) > len(best[1])):
            best = (item.name, str(root))
    return best[0] if best else None


@app.command("ask")
def ask_cmd(
    ctx: typer.Context,
    task: str = typer.Argument(..., help="What you want the AI to do."),
    target: Optional[str] = typer.Argument(None, help="'all' or a repo name. Default: the repo you're in, else all."),
    repo: Optional[str] = typer.Option(None, "--repo", "-r", help="Limit to one repo."),
    to: Optional[str] = typer.Option(
        None, "--to", "-t", help=f"Where to send it: {', '.join(handoff.TARGETS)}. Default: send_to in config."
    ),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Model for cursor-agent, copilot or claude."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the Proceed? question."),
) -> None:
    """Default command: size the context, confirm the token tier, then hand it to your AI tool."""
    app_paths = AppPaths(ctx.obj["home"])
    settings = load_settings(app_paths)
    send_to = (to or settings.send_to).lower()
    if send_to not in handoff.TARGETS:
        err_console.print(f"[red]Unknown --to '{send_to}'. Use one of: {', '.join(handoff.TARGETS)}.[/red]")
        raise typer.Exit(code=1)
    if target and repo and target.lower() != "all" and target != repo:
        err_console.print(f"[red]Two targets given: '{target}' and --repo {repo}. Use one.[/red]")
        raise typer.Exit(code=1)
    repo = repo or (target if target and target.lower() != "all" else None)
    tiers = sorted(settings.ask_tiers) or [3500, 5000, 7000]

    with _store(app_paths) as store:
        repos = store.list_repos()
        if not repos:
            err_console.print("Nothing indexed. Run [bold]smarttoken add[/bold] then [bold]index[/bold].")
            raise typer.Exit(code=1)
        if repo and store.get_repo(repo) is None:
            names = ", ".join(r.name for r in repos)
            err_console.print(f"[red]Unknown repo '{repo}'.[/red] Indexed: {names}")
            raise typer.Exit(code=1)
        from_folder = None
        if repo is None and target is None:
            from_folder = _repo_for_cwd(repos)
            repo = from_folder
        hits = search_index(store, task, repo_name=repo, limit=max(settings.search_limit, 20))
        if not hits:
            console.print("[red]No matching files. Rephrase with words that appear in the code.[/red]")
            raise typer.Exit(code=1)
        confidence = assess(store, task, hits)
        drift = _focus_check(app_paths, store, "ask", task, hits, repo)
        packet = build_packet(store, task, settings, repo_name=repo, budget=ASK_NATURAL_BUDGET)
        repo_paths = {r.name: r.path for r in repos}
        hit_repos = [repo] if repo else list(dict.fromkeys(hit.repo_name for hit in hits[:3]))
        workspace = handoff.workspace_for([repo_paths[name] for name in hit_repos if name in repo_paths])
        prompt = handoff.build_prompt(task, packet.markdown, [repo_paths[n] for n in hit_repos if n in repo_paths])
        need = count_tokens(prompt)
        tier = handoff.choose_tier(need, tiers)
        result = measure_task(store, task, settings, repo_name=repo, budget=tier[1] if tier else tiers[-1])

    scope = f"{repo} (current folder; add 'all' for every repo)" if from_folder else repo or "all repos"
    color = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}[confidence.level]
    console.print(f"Target: [bold]{scope}[/bold] · Confidence: [bold {color}]{confidence.level}[/bold {color}]")
    for reason in confidence.reasons:
        console.print(f"  - {reason}")
    console.print("Top files:")
    for hit in hits[:3]:
        console.print(f"  - {hit.repo_name}/{hit.rel_path} ({hit.role})")
    pct = result.savings_pct(result.whole_hits)
    if pct is not None:
        console.print(f"Saved: [bold]{pct:.1f}%[/bold] vs attaching the {result.whole_hits.files} hit files")
    if drift:
        _print_drift(drift, task, console)

    if tier is None:
        console.print(
            f"[bold red]Context is {need} tokens (Max limit {tiers[-1]}). Please narrow your prompt.[/bold red]"
        )
        console.print("  Name the screen, file or feature, or add --repo.")
        raise typer.Exit(code=4)

    message = handoff.tier_message(need, tier[0], tier[1], len(tiers))
    risky = confidence.level == "LOW" or _drift_blocks_copy(drift, False)
    if risky:
        console.print("[yellow]Default is No because of the warning above.[/yellow]")
    console.print(f"{message} Send to [bold]{send_to}[/bold].")
    if not yes and not typer.confirm("Proceed?", default=not risky):
        console.print("Cancelled. Nothing sent.")
        raise typer.Exit(code=0)

    prompt_file = handoff.save_prompt(app_paths, task, prompt)
    append_measure(app_paths, result, source="ask")
    try:
        plan = handoff.plan(send_to, prompt, prompt_file, task, workspace, model or settings.send_model)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        if copy_text(prompt):
            console.print("[green]Copied to clipboard instead.[/green]")
        console.print(f"Prompt saved: {prompt_file}")
        raise typer.Exit(code=1)
    if send_to == "print":
        typer.echo(prompt)
        return
    ok, note = handoff.execute(plan, prompt)
    if note:
        (console if ok else err_console).print(note if ok else f"[red]{note}[/red]")
    console.print(f"[dim]Prompt saved: {prompt_file}[/dim]")
    if not ok:
        raise typer.Exit(code=1)
```

At the end of the file:

```python
def _with_default_command(argv: List[str]) -> List[str]:
    """Insert `ask` when the first word isn't a subcommand: `smarttoken "task" all`."""
    names = {cmd.name or cmd.callback.__name__ for cmd in app.registered_commands}
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--home":
            index += 2
            continue
        if arg.startswith("-"):
            return argv
        if arg in names:
            return argv
        return argv[:index] + ["ask"] + argv[index:]
    return argv


def run() -> None:
    app(args=_with_default_command(sys.argv[1:]), prog_name="smarttoken")
```

## Change 5: tests, `tests/test_ask.py`

These use the existing `sample_root` fixture from `conftest.py`.

```python
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
```

## Run it from any folder

`pip install -e .` puts `smarttoken` in the venv only. To run it from any folder without activating the venv,
link the venv's script into a folder on PATH. An editable install means code changes apply immediately:

```
mkdir -p ~/.local/bin
ln -sf "$PWD/.venv/bin/smarttoken" ~/.local/bin/smarttoken
```

If `~/.local/bin` isn't on PATH, add `export PATH="$HOME/.local/bin:$PATH"` to `~/.zshrc`. Use the default
data home `~/.smarttoken`, and don't export `SMARTTOKEN_HOME` in `~/.zshrc`, so every folder sees the same index.

## Verify

```
pip install -e '.[dev]'
python -m pytest -q
smarttoken "where is the webhook signature checked" --to print --yes
echo n | smarttoken "add a button to upload an aop" all
smarttoken "add a button to upload an aop" --repo my-frontend-app --to vscode
```

Expect:
- the tier line, then `Proceed? [Y/n]`;
- `n` prints "Cancelled. Nothing sent.";
- `--to vscode` opens Copilot Chat in agent mode with the pack attached;
- `ls ~/.smarttoken/packs` shows each prompt that was sent.
