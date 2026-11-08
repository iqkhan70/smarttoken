"""Typer CLI: local index in, tight packets out."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from smarttoken import __version__, focus, handoff
from smarttoken.clipboard import copy_text
from smarttoken.confidence import assess
from smarttoken.config import AppPaths, Settings, load_settings, resolve_home, save_settings
from smarttoken.eval_tasks import load_tasks
from smarttoken.index_store import IndexStore
from smarttoken.measure import (
    bundled_fixture_path,
    bundled_tasks_path,
    measure_task,
    render_markdown,
)
from smarttoken.pack import build_packet, pointer_block
from smarttoken.registry import add_paths
from smarttoken.repo_map import render_map
from smarttoken.search import search_index
from smarttoken.tokens import count_tokens
from smarttoken.usage import append_measure, read_usage, summarize

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Scour local git clones and emit token-budgeted packets for paid AI.",
)
console = Console()
err_console = Console(stderr=True)
# `ask` sizes the natural pack with this ceiling, then picks the smallest tier that holds it.
ASK_NATURAL_BUDGET = 50_000


def _store(paths: AppPaths) -> IndexStore:
    paths.ensure_home()
    return IndexStore(paths.db_file)


def _focus_check(
    paths: AppPaths, store: IndexStore, command: str, query: str, hits: list, repo: Optional[str]
) -> Optional[focus.Drift]:
    """Compare against recent questions, then record this one."""
    drift = focus.check_drift(
        paths,
        store,
        query,
        hits,
        repo_name=repo,
        search_fn=lambda text: search_index(store, text, repo_name=repo, limit=20),
    )
    drift_to = drift.new_words[0] if drift and not drift.asked_for_new_area else None
    focus.record(paths, command, query, hits, drift_to=drift_to)
    return drift


def _print_drift(drift: focus.Drift, query: str, out: Console) -> None:
    was = ", ".join(drift.focus_words)
    now = drift.new_words[0]
    source = f"pinned focus '{drift.pinned}'" if drift.pinned and not drift.focus_files else "recent questions"
    out.print("[bold yellow]Focus check: this lands outside what you've been working on.[/bold yellow]")
    example = f" (e.g. {drift.focus_files[0]})" if drift.focus_files else ""
    out.print(f"  Your {source}: [bold]{was}[/bold]{example}")
    out.print(f"  This question lands on: [bold]{now}[/bold], {drift.new_file}")
    if drift.asked_for_new_area:
        out.print(f"  You mentioned '{now}', so this is probably intended. Carry on if so.")
        return
    if drift.suggestion:
        out.print(f"  Closest file in {was}: [bold]{drift.suggestion.repo_name}/{drift.suggestion.rel_path}[/bold]")
    out.print(f'  If you meant {was}, ask again naming it: smarttoken check "{query} {drift.focus_words[0]}"')
    out.print(f"  If you meant {now}, carry on. Focus follows you after a couple of questions.")


def _drift_blocks_copy(drift: Optional[focus.Drift], force: bool) -> bool:
    return drift is not None and not drift.asked_for_new_area and not force


@app.callback()
def main(
    ctx: typer.Context,
    home: Optional[Path] = typer.Option(
        None,
        "--home",
        envvar="SMARTTOKEN_HOME",
        help="Data directory (default ~/.smarttoken).",
    ),
) -> None:
    ctx.obj = {"home": resolve_home(home)}


@app.command()
def version() -> None:
    """Print the package version."""
    console.print(__version__)


@app.command("init")
def init_cmd(ctx: typer.Context) -> None:
    """Create ~/.smarttoken (or --home) and a default config."""
    paths = AppPaths(ctx.obj["home"])
    paths.ensure_home()
    if not paths.config_file.exists():
        save_settings(paths, Settings())
    IndexStore(paths.db_file).close()
    console.print(f"Initialized {paths.home}")
    console.print(f"Config: {paths.config_file}")
    console.print(f"Index:  {paths.db_file}")


@app.command("add")
def add_cmd(
    ctx: typer.Context,
    paths: list[Path] = typer.Argument(..., help="Local clone directories."),
) -> None:
    """Register one or more local repo roots."""
    app_paths = AppPaths(ctx.obj["home"])
    with _store(app_paths) as store:
        try:
            repos = add_paths(store, paths)
        except FileNotFoundError as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        table = Table(title="Registered")
        table.add_column("name")
        table.add_column("path")
        table.add_column("git")
        for repo in repos:
            table.add_row(repo.name, repo.path, repo.default_branch or "not a git repo / no HEAD")
        console.print(table)
    console.print("Next: [bold]smarttoken index[/bold]")


@app.command("repos")
def repos_cmd(ctx: typer.Context) -> None:
    """List registered roots."""
    with _store(AppPaths(ctx.obj["home"])) as store:
        repos = store.list_repos()
        if not repos:
            console.print("No repos. Run [bold]smarttoken add <path>[/bold].")
            return
        table = Table(title="Registered repos")
        table.add_column("name")
        table.add_column("path")
        table.add_column("branch")
        table.add_column("indexed")
        for repo in repos:
            table.add_row(
                repo.name,
                repo.path,
                repo.default_branch or "—",
                repo.last_indexed_at or "never",
            )
        console.print(table)


@app.command("index")
def index_cmd(
    ctx: typer.Context,
    repo: Optional[str] = typer.Option(None, "--repo", "-r", help="Only this registered name."),
) -> None:
    """Scan registered roots into the local SQLite index."""
    app_paths = AppPaths(ctx.obj["home"])
    settings = load_settings(app_paths)
    with _store(app_paths) as store:
        if repo:
            if store.get_repo(repo) is None:
                err_console.print(f"[red]Unknown repo '{repo}'.[/red]")
                raise typer.Exit(code=1)
            targets = [store.require_repo(repo)]
        else:
            targets = store.list_repos()
        if not targets:
            err_console.print("Nothing to index. Run [bold]smarttoken add[/bold] first.")
            raise typer.Exit(code=1)
        for target in targets:
            try:
                stats = store.index_repo(target, settings)
            except FileNotFoundError as exc:
                err_console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1) from exc
            langs = ", ".join(f"{name}={count}" for name, count in sorted(stats.languages.items()))
            console.print(
                f"[green]{stats.repo_name}[/green] scanned={stats.scanned} "
                f"written={stats.written} unchanged={stats.unchanged} "
                f"deleted={stats.deleted} skipped={stats.skipped} {langs}"
            )


@app.command("map")
def map_cmd(
    ctx: typer.Context,
    repo: Optional[str] = typer.Argument(None, help="Registered repo name. Omit to map all."),
) -> None:
    """Print an onboarding map. Local only; no model calls."""
    with _store(AppPaths(ctx.obj["home"])) as store:
        if repo:
            try:
                targets = [store.require_repo(repo)]
            except KeyError as exc:
                err_console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1) from exc
        else:
            targets = store.list_repos()
        if not targets:
            err_console.print("No repos. Run [bold]smarttoken add[/bold] then [bold]smarttoken index[/bold].")
            raise typer.Exit(code=1)
        blocks = [render_map(store, target) for target in targets]
        console.print("\n".join(blocks))


@app.command("search")
def search_cmd(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Words to find in indexed files."),
    repo: Optional[str] = typer.Option(None, "--repo", "-r"),
    limit: int = typer.Option(15, "--limit", "-n"),
) -> None:
    """Ranked local search. One-line hits, no paid AI."""
    app_paths = AppPaths(ctx.obj["home"])
    with _store(app_paths) as store:
        if repo and store.get_repo(repo) is None:
            err_console.print(f"[red]Unknown repo '{repo}'.[/red]")
            raise typer.Exit(code=1)
        hits = search_index(store, query, repo_name=repo, limit=limit)
        if not hits:
            console.print("No hits. Index first, or try fewer / different words.")
            console.print("Fail open: [bold]rg[/bold] in the clone, then `smarttoken index`.")
            return
        drift = _focus_check(app_paths, store, "search", query, hits, repo)
        table = Table(title=f"search: {query}")
        table.add_column("score", justify="right")
        table.add_column("repo")
        table.add_column("path")
        table.add_column("role")
        table.add_column("hit")
        for hit in hits:
            table.add_row(f"{hit.score:.1f}", hit.repo_name, hit.rel_path, hit.role, hit.preview)
        console.print(table)
        if drift:
            _print_drift(drift, query, console)


@app.command("explain")
def explain_cmd(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="Symbol or topic, e.g. payments.auth"),
    repo: Optional[str] = typer.Option(None, "--repo", "-r"),
) -> None:
    """Show local snippets for a symbol/topic; a named function or class is shown whole when it fits."""
    app_paths = AppPaths(ctx.obj["home"])
    settings = load_settings(app_paths)
    with _store(app_paths) as store:
        packet = build_packet(
            store,
            target,
            settings,
            repo_name=repo,
            budget=min(settings.default_budget, 2000),
        )
        hits = search_index(store, target, repo_name=repo, limit=max(settings.search_limit, 20))
        drift = _focus_check(app_paths, store, "explain", target, hits, repo)
    console.print(packet.markdown)
    if drift:
        _print_drift(drift, target, err_console)


@app.command("pack")
def pack_cmd(
    ctx: typer.Context,
    task: str = typer.Argument(..., help="The job you will give paid AI."),
    repo: Optional[str] = typer.Option(None, "--repo", "-r"),
    budget: Optional[int] = typer.Option(None, "--budget", "-b", help="Token ceiling (default 4000)."),
    copy: bool = typer.Option(False, "--copy", help="Copy the packet to the clipboard."),
    force: bool = typer.Option(False, "--force", help="With --copy, copy even when the focus check warns."),
) -> None:
    """Emit a token-budgeted markdown packet for Cursor or ChatGPT."""
    app_paths = AppPaths(ctx.obj["home"])
    settings = load_settings(app_paths)
    with _store(app_paths) as store:
        if repo and store.get_repo(repo) is None:
            err_console.print(f"[red]Unknown repo '{repo}'.[/red]")
            raise typer.Exit(code=1)
        packet = build_packet(store, task, settings, repo_name=repo, budget=budget)
        hits = search_index(store, task, repo_name=repo, limit=max(settings.search_limit, 20))
        drift = _focus_check(app_paths, store, "pack", task, hits, repo)
    console.print(packet.markdown)
    console.print(f"[dim]{packet.used_tokens}/{packet.budget} tokens · {len(packet.files)} files[/dim]")
    if drift:
        _print_drift(drift, task, err_console)
    if copy and _drift_blocks_copy(drift, force):
        err_console.print("[yellow]Not copied because of the focus check. Add --force to copy anyway.[/yellow]")
        raise typer.Exit(code=3)
    if copy:
        if copy_text(packet.markdown):
            console.print("[green]Copied to clipboard.[/green]")
        else:
            err_console.print("[yellow]Clipboard copy failed (no pbcopy/wl-copy/xclip).[/yellow]")
            raise typer.Exit(code=2)


@app.command("brief")
def brief_cmd(
    ctx: typer.Context,
    question: str = typer.Argument(..., help="Onboarding or change-plan question."),
    repo: Optional[str] = typer.Option(None, "--repo", "-r"),
    budget: Optional[int] = typer.Option(None, "--budget", "-b"),
    copy: bool = typer.Option(False, "--copy"),
) -> None:
    """Longer local brief: maps plus a bounded pack (still no paid API)."""
    app_paths = AppPaths(ctx.obj["home"])
    settings = load_settings(app_paths)
    ceiling = budget or settings.default_budget
    with _store(app_paths) as store:
        packet = build_packet(
            store,
            question,
            settings,
            repo_name=repo,
            budget=ceiling,
            include_maps=True,
        )
    console.print(packet.markdown)
    if copy and not copy_text(packet.markdown):
        err_console.print("[yellow]Clipboard copy failed.[/yellow]")
        raise typer.Exit(code=2)


def _print_measure(result, markdown: bool, as_json: bool) -> None:
    import json

    if as_json:
        console.print(json.dumps(result.to_dict(), indent=2))
        return
    if markdown:
        console.print(render_markdown(result))
        return
    table = Table(title=result.task)
    table.add_column("approach")
    table.add_column("tokens", justify="right")
    table.add_column("files", justify="right")
    table.add_column("est. $", justify="right")
    for baseline in result.baselines:
        table.add_row(
            baseline.label,
            str(baseline.tokens),
            str(baseline.files),
            f"${result.usd(baseline.tokens):.4f}",
        )
    table.add_row(
        "With: smarttoken pack",
        str(result.pack_tokens),
        str(len(result.pack_files)),
        f"${result.usd(result.pack_tokens):.4f}",
        style="bold",
    )
    console.print(table)
    if result.whole_repo and result.savings_pct(result.whole_repo) is not None:
        saved = result.whole_repo.tokens - result.pack_tokens
        if saved < 0:
            console.print(
                "[yellow]Pack overhead exceeds this tiny clone. "
                "That is expected — measure a real org repo.[/yellow]"
            )
        else:
            console.print(
                f"Saved vs dump: [bold]{result.savings_pct(result.whole_repo):.1f}%[/bold] "
                f"({saved} tokens)"
            )
    if result.whole_hits and result.savings_pct(result.whole_hits) is not None:
        hit_saved = result.whole_hits.tokens - result.pack_tokens
        if hit_saved < 0:
            console.print(
                f"[yellow]Pack is {abs(hit_saved)} tokens heavier than pasting "
                f"{result.whole_hits.files} hit files whole "
                f"(wrapper overhead — this wins on large files, not 80-line fixtures).[/yellow]"
            )
        else:
            console.print(
                f"Saved vs whole search hits: [bold]{result.savings_pct(result.whole_hits):.1f}%[/bold] "
                f"({hit_saved} tokens)"
            )
    console.print(
        f"[dim]est. $ uses ${result.dollars_per_million:.2f}/1M input tokens. "
        "Cheap is not useful if gold files are missing.[/dim]"
    )
    if result.gold:
        for item in result.gold:
            mark = "[green]HIT[/green]" if item.found else "[red]MISS[/red]"
            kind = "must" if item.required else "nice"
            console.print(f"{mark} ({kind}) {item.path}")
    if result.excluded_leaks:
        for path in result.excluded_leaks:
            console.print(f"[red]LEAK[/red] must-exclude {path}")
    console.print(f"Verdict: [bold]{result.verdict}[/bold]")
    if result.verdict == "NOT CHECKED":
        console.print("[dim]Add --expect <file> to check recall, or run `smarttoken check` for a confidence read.[/dim]")


@app.command("measure")
def measure_cmd(
    ctx: typer.Context,
    task: str = typer.Argument(..., help="The same task you would pack."),
    repo: Optional[str] = typer.Option(None, "--repo", "-r"),
    budget: Optional[int] = typer.Option(None, "--budget", "-b"),
    expect: list[str] = typer.Option(None, "--expect", help="Gold file that must appear in the pack. Repeatable."),
    exclude: list[str] = typer.Option(None, "--exclude", help="Path that must not appear. Repeatable."),
    markdown: bool = typer.Option(False, "--markdown", help="Print a pasteable markdown report."),
    as_json: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
) -> None:
    """Compare dump-the-repo vs pack, and check gold files. This is the sell number."""
    app_paths = AppPaths(ctx.obj["home"])
    settings = load_settings(app_paths)
    with _store(app_paths) as store:
        if not store.list_repos():
            err_console.print("Nothing indexed. Run [bold]smarttoken add[/bold] then [bold]index[/bold].")
            raise typer.Exit(code=1)
        if repo and store.get_repo(repo) is None:
            err_console.print(f"[red]Unknown repo '{repo}'.[/red]")
            raise typer.Exit(code=1)
        result = measure_task(
            store,
            task,
            settings,
            repo_name=repo,
            budget=budget,
            must_include=expect or [],
            must_exclude=exclude or [],
        )
    append_measure(app_paths, result, source="measure")
    _print_measure(result, markdown=markdown, as_json=as_json)
    if expect and not result.passed:
        raise typer.Exit(code=2)


@app.command("check")
def check_cmd(
    ctx: typer.Context,
    task: str = typer.Argument(..., help="The question you are about to ask paid AI."),
    repo: Optional[str] = typer.Option(None, "--repo", "-r"),
    budget: Optional[int] = typer.Option(None, "--budget", "-b"),
    copy: bool = typer.Option(
        False, "--copy", help="Copy the pack unless confidence is LOW or the focus check warns."
    ),
    force: bool = typer.Option(False, "--force", help="With --copy, copy anyway."),
) -> None:
    """Before spending paid tokens: confidence, focus check, and what the pack saves."""
    app_paths = AppPaths(ctx.obj["home"])
    settings = load_settings(app_paths)
    with _store(app_paths) as store:
        if not store.list_repos():
            err_console.print("Nothing indexed. Run [bold]smarttoken add[/bold] then [bold]index[/bold].")
            raise typer.Exit(code=1)
        if repo and store.get_repo(repo) is None:
            err_console.print(f"[red]Unknown repo '{repo}'.[/red]")
            raise typer.Exit(code=1)
        hits = search_index(store, task, repo_name=repo, limit=max(settings.search_limit, 20))
        confidence = assess(store, task, hits)
        drift = _focus_check(app_paths, store, "check", task, hits, repo)
        result = measure_task(store, task, settings, repo_name=repo, budget=budget)
        blocked = None
        if copy and not force:
            if confidence.level == "LOW":
                blocked = "confidence is LOW"
            elif _drift_blocks_copy(drift, force):
                blocked = "the focus check warned"
        packet = None
        if copy and blocked is None:
            packet = build_packet(store, task, settings, repo_name=repo, budget=budget)

    color = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}[confidence.level]
    console.print(f"Confidence: [bold {color}]{confidence.level}[/bold {color}]")
    for reason in confidence.reasons:
        console.print(f"  - {reason}")
    if hits:
        console.print("Top files:")
        for hit in hits[:3]:
            console.print(f"  - {hit.repo_name}/{hit.rel_path} ({hit.role})")

    pack_cost = result.usd(result.pack_tokens)
    if result.whole_hits:
        console.print(
            f"Pack: {result.pack_tokens} tokens (${pack_cost:.4f}) vs attaching the "
            f"{result.whole_hits.files} hit files: {result.whole_hits.tokens} tokens "
            f"(${result.usd(result.whole_hits.tokens):.4f})"
        )
    else:
        console.print(f"Pack: {result.pack_tokens} tokens (${pack_cost:.4f})")
    saved = []
    for label, baseline in (("vs hit files", result.whole_hits), ("vs whole repo", result.whole_repo)):
        pct = result.savings_pct(baseline)
        if pct is not None:
            saved.append(f"{pct:.1f}% {label}")
    if saved:
        console.print(f"Saved: [bold]{' · '.join(saved)}[/bold]")
    console.print(f"Advice: {confidence.advice}")
    if drift:
        _print_drift(drift, task, console)
    if copy:
        if packet is None:
            console.print(f"[yellow]Not copied: {blocked}. Add --force to copy anyway.[/yellow]")
        elif copy_text(packet.markdown):
            console.print(f"[green]Pack copied to clipboard ({packet.used_tokens} tokens).[/green]")
        else:
            err_console.print("[red]Could not copy. Run `smarttoken pack` and copy the output.[/red]")


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
    pack: str = typer.Option(
        "auto",
        "--pack",
        help="auto: pointer for agent tools, full snippets for paste/print. Or full, or pointer.",
    ),
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
    try:
        pack_style = handoff.resolve_pack_style(pack.lower(), send_to)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

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
        workspaces = [repo_paths[n] for n in hit_repos if n in repo_paths]
        if pack_style == "pointer":
            prompt = handoff.build_pointer_prompt(task, pointer_block(store, task, settings, repo), workspaces)
        else:
            prompt = handoff.build_prompt(task, packet.markdown, workspaces)
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

    if pack_style == "pointer":
        message = (
            f"Pointer prompt is ~{need} tokens (full pack would be {packet.used_tokens}). "
            "The agent opens the listed files itself."
        )
    else:
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


@app.command("focus")
def focus_cmd(
    ctx: typer.Context,
    set_: Optional[str] = typer.Option(None, "--set", help="Pin an area, e.g. --set aop."),
    unpin: bool = typer.Option(False, "--unpin", help="Remove the pinned area, keep history."),
    clear: bool = typer.Option(False, "--clear", help="Forget the pin and recent questions."),
    repo: Optional[str] = typer.Option(None, "--repo", "-r"),
) -> None:
    """Show what area you've been working on; pin or clear it."""
    app_paths = AppPaths(ctx.obj["home"])
    if clear:
        focus.clear(app_paths)
        console.print("Focus cleared.")
        return
    if unpin:
        focus.pin(app_paths, None)
        console.print("Pin removed.")
    if set_:
        focus.pin(app_paths, set_)
        console.print(f"Pinned focus: [bold]{set_}[/bold]. Questions landing elsewhere will be flagged.")
    with _store(app_paths) as store:
        if repo and store.get_repo(repo) is None:
            err_console.print(f"[red]Unknown repo '{repo}'.[/red]")
            raise typer.Exit(code=1)
        current = focus.current_focus(app_paths, focus.PathVocabulary(store, repo), repo)
    if current.pinned:
        console.print(f"Pinned: [bold]{current.pinned}[/bold]")
    if current.words:
        console.print(f"Focus words: [bold]{', '.join(current.words[:5])}[/bold]")
    else:
        console.print("No focus yet. It builds up after a couple of questions in the same area.")
    if current.recent_queries:
        console.print("Recent questions (newest first):")
        for query, path in zip(current.recent_queries, current.recent_tops):
            console.print(f"  - {query}  [dim]→ {path}[/dim]")


@app.command("eval")
def eval_cmd(
    ctx: typer.Context,
    fixture: bool = typer.Option(False, "--fixture", help="Index the bundled sample repo and run gold tasks."),
    tasks: Optional[Path] = typer.Option(None, "--tasks", help="JSON file of gold tasks for your org clones."),
    budget: Optional[int] = typer.Option(None, "--budget", "-b"),
    markdown: bool = typer.Option(False, "--markdown"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Run gold-file tasks. Token savings without recall is not a useful claim."""
    from tempfile import TemporaryDirectory

    settings_budget = budget
    if fixture:
        sample = bundled_fixture_path()
        if not sample.is_dir():
            err_console.print(f"[red]Bundled fixture not found at {sample}. Run from a source checkout.[/red]")
            raise typer.Exit(code=1)
        task_path = tasks or bundled_tasks_path()
        with TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            app_paths = AppPaths(home)
            app_paths.ensure_home()
            save_settings(app_paths, Settings())
            with _store(app_paths) as store:
                added = add_paths(store, [sample])
                store.index_repo(added[0], Settings())
                _run_eval(store, load_settings(app_paths), task_path, settings_budget, markdown, as_json, force_repo=added[0].name)
        return

    app_paths = AppPaths(ctx.obj["home"])
    if tasks is None:
        err_console.print("Pass [bold]--fixture[/bold] or [bold]--tasks path.json[/bold].")
        raise typer.Exit(code=1)
    with _store(app_paths) as store:
        if not store.list_repos():
            err_console.print("Nothing indexed. Run [bold]smarttoken add[/bold] then [bold]index[/bold].")
            raise typer.Exit(code=1)
        _run_eval(store, load_settings(app_paths), tasks, settings_budget, markdown, as_json, force_repo=None)


def _run_eval(
    store: IndexStore,
    settings: Settings,
    task_path: Path,
    budget: int | None,
    markdown: bool,
    as_json: bool,
    force_repo: str | None,
) -> None:
    import json

    cases = load_tasks(task_path)
    results = []
    failed = 0
    for case in cases:
        repo = force_repo or case.repo
        result = measure_task(
            store,
            case.task,
            settings,
            repo_name=repo,
            budget=budget,
            must_include=case.must_include,
            nice_include=case.nice_include,
            must_exclude=case.must_exclude,
        )
        results.append(result)
        if not result.passed:
            failed += 1
        if not as_json and not markdown:
            console.print(f"\n[bold]{case.id}[/bold]")
            _print_measure(result, markdown=False, as_json=False)

    if as_json:
        console.print(json.dumps([item.to_dict() for item in results], indent=2))
    elif markdown:
        console.print("\n".join(render_markdown(item) for item in results))
    else:
        table = Table(title="eval summary")
        table.add_column("id")
        table.add_column("pack")
        table.add_column("vs dump")
        table.add_column("recall")
        table.add_column("verdict")
        for case, result in zip(cases, results, strict=True):
            pct = result.savings_pct(result.whole_repo)
            recall = "n/a" if result.required_recall is None else f"{result.required_recall:.0%}"
            table.add_row(
                case.id,
                str(result.pack_tokens),
                f"{pct:.1f}%" if pct is not None else "—",
                recall,
                "PASS" if result.passed else "FAIL",
            )
        console.print(table)
        console.print(
            "A useful tool is cheaper [bold]and[/bold] still finds the files a human would open."
        )
    if failed:
        raise typer.Exit(code=2)


@app.command("prloop")
def prloop_cmd(
    ctx: typer.Context,
    repo: Optional[str] = typer.Option(None, "--repo", "-r", help="owner/name or an indexed repo name."),
) -> None:
    """Create a PR, wait for you to confirm it, then loop Copilot review until medium/high findings are gone."""
    from getpass import getpass

    from smarttoken.prloop import run_loop

    app_paths = AppPaths(ctx.obj["home"])
    name = repo or typer.prompt("Repo name (owner/name, or an indexed name)")
    raise typer.Exit(
        code=run_loop(
            name=name,
            paths=app_paths,
            ask=lambda message: typer.prompt(message, default=""),
            confirm=lambda message, default: typer.confirm(message, default=default),
            secret=lambda message: getpass(message),
            out=lambda message: console.print(message),
        )
    )


@app.command("report")
def report_cmd(ctx: typer.Context) -> None:
    """Sum logged measure/eval-style events. Run measure as you work to fill this."""
    app_paths = AppPaths(ctx.obj["home"])
    settings = load_settings(app_paths)
    rows = read_usage(app_paths.usage_file)
    if not rows:
        console.print("No usage yet. Run [bold]smarttoken measure \"your task\"[/bold] on real clones.")
        return
    stats = summarize(rows, settings.dollars_per_million_input)
    table = Table(title=f"{stats['events']} logged tasks")
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_row("pack tokens (with tool)", str(stats["pack_tokens"]))
    table.add_row("dump tokens (without)", str(stats["whole_repo_tokens"]))
    table.add_row("whole-hit tokens (without)", str(stats["whole_hits_tokens"]))
    if stats["saved_vs_repo"] is not None:
        table.add_row("saved vs dump", str(stats["saved_vs_repo"]))
        table.add_row("est. $ pack", f"${stats['est_usd_pack']:.4f}")
        if stats["est_usd_whole_repo"] is not None:
            table.add_row("est. $ dump", f"${stats['est_usd_whole_repo']:.4f}")
    console.print(table)
    console.print(f"[dim]Log: {app_paths.usage_file}[/dim]")


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
