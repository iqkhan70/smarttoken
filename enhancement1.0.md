# smarttoken enhancement 1.0: `check` (know before you ask) and an honest `measure` verdict

Paste this into Copilot **after** the base project from `COPILOT_REBUILD.md` is built and its tests pass.
It only adds to the project. Adapt names (`search_index`, `get_file_text`, `symbols_matching`, `measure_task`, …) to
whatever the existing code calls them. Keep it Python 3.9 compatible.

## Why

Before spending paid tokens on a question, I want to know two things:
1. **Cost:** the pack versus attaching the files myself.
2. **Confidence:** how likely the pack is to contain the right files.

`measure` already gives the cost, but with no `--expect` it printed "PASS", which is misleading because nothing
was checked.

## Change 1: `measure` verdict says NOT CHECKED when there's nothing to check

In `measure.py`, add to `MeasureResult`:

```python
    @property
    def verdict(self) -> str:
        """PASS/FAIL only when there was something to check; otherwise NOT CHECKED."""
        if not self.gold and not self.excluded_leaks:
            return "NOT CHECKED"
        return "PASS" if self.passed else "FAIL"
```

- Add `"verdict": self.verdict` to `to_dict()`.
- In the markdown report and the CLI output of `measure`, print `Verdict: {result.verdict}` instead of PASS/FAIL.
- When the verdict is NOT CHECKED, also print the hint:
  `Add --expect <file> to check recall, or run smarttoken check for a confidence read.`
- `eval` is unchanged (gold tasks always have files to check). The exit code stays 2 only when `--expect` was given and failed.

## Change 2: new module `smarttoken/confidence.py`

Signals, computed from the same search the pack uses:
- **coverage:** the best fraction of task words found (word or stem, substring) in the path + content of any of the top 3 files.
- **missing words:** task words found nowhere in the index for that repo. Check an FTS match on the word **or its stem**
  (the FTS porter stemmer turns "adding" into "ad", so also try our own stem "add"), then a path substring.
- **named symbols:** functions/classes in the top 3 files whose name contains a task word (span > 1 line), up to 5.

Levels:
- **LOW:** no hits, coverage < 0.5, or at least half the words missing.
- **HIGH:** no words missing and coverage ≥ 0.75.
- **MEDIUM:** everything else. Always name the missing words.

```python
"""Before you spend paid tokens: how likely is the pack to contain the right files?"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from smarttoken.index_store import IndexStore
from smarttoken.models import SearchHit
from smarttoken.rank import content_terms, stem, term_in
from smarttoken.symbols import symbols_matching


@dataclass
class Signals:
    terms: List[str]
    hits: int
    top_path: Optional[str]
    top_role: Optional[str]
    # Fraction of task words found in the best of the top 3 files (path or content).
    coverage: float
    # Task words found in no indexed file at all.
    missing_terms: List[str]
    named_symbols: List[str] = field(default_factory=list)


@dataclass
class Confidence:
    level: str
    reasons: List[str]
    advice: str
    signals: Signals


HIGH_COVERAGE = 0.75
LOW_COVERAGE = 0.5


def assess(store: IndexStore, query: str, hits: List[SearchHit]) -> Confidence:
    signals = gather_signals(store, query, hits)
    reasons: List[str] = []
    if not hits or not signals.terms:
        reasons = ["no search hits" if signals.terms else "no searchable words in the question"]
        if signals.missing_terms:
            reasons.append("not found anywhere in the index: " + ", ".join(signals.missing_terms))
        return Confidence(
            "LOW",
            reasons,
            "Rephrase with the names you'd grep for, or ask the AI directly.",
            signals,
        )

    reasons.append(f"{signals.coverage:.0%} of task words are in the top files")
    if signals.missing_terms:
        reasons.append("not found anywhere in the index: " + ", ".join(signals.missing_terms))
    if signals.named_symbols:
        reasons.append("functions/classes named after the task: " + ", ".join(signals.named_symbols))

    missing_share = len(signals.missing_terms) / len(signals.terms)
    if signals.coverage < LOW_COVERAGE or missing_share >= 0.5:
        level = "LOW"
        advice = (
            "The repo probably doesn't contain this. Check the missing words, rephrase with names from "
            "the code, or ask the AI directly instead of pasting the pack."
        )
    elif not signals.missing_terms and signals.coverage >= HIGH_COVERAGE:
        level = "HIGH"
        advice = "Paste the pack: `smarttoken pack \"...\" --copy`."
    else:
        level = "MEDIUM"
        advice = (
            "Pack is likely close. If a missing word is the main subject, the repo may not have it; "
            "otherwise paste the pack and expect a follow-up."
        )
    return Confidence(level, reasons, advice, signals)


def gather_signals(store: IndexStore, query: str, hits: List[SearchHit]) -> Signals:
    terms = content_terms(query)
    top = hits[0] if hits else None
    coverage = 0.0
    named: List[str] = []
    for hit in hits[:3]:
        content = store.get_file_text(hit.file_id)
        text = f"{hit.rel_path}\n{content}".lower()
        if terms:
            coverage = max(coverage, sum(1 for term in terms if term_in(term, text)) / len(terms))
        for symbol in symbols_matching(content, hit.lang, query, hit.rel_path):
            if symbol.span > 1 and symbol.name not in named:
                named.append(symbol.name)
    repo_id = hits[0].repo_id if hits and len({h.repo_id for h in hits}) == 1 else None
    missing = [
        term
        for term in terms
        if not _term_indexed(store, term, repo_id) and not _term_indexed(store, stem(term), repo_id)
    ]
    return Signals(
        terms=terms,
        hits=len(hits),
        top_path=f"{top.repo_name}/{top.rel_path}" if top else None,
        top_role=top.role if top else None,
        coverage=coverage,
        missing_terms=missing,
        named_symbols=named[:5],
    )


def _term_indexed(store: IndexStore, term: str, repo_id: Optional[int]) -> bool:
    sql = "SELECT 1 FROM file_text WHERE file_text MATCH ?"
    params: List[object] = [f'"{term}"']
    if repo_id is not None:
        sql += " AND repo_id = ?"
        params.append(repo_id)
    if store.conn.execute(sql + " LIMIT 1", params).fetchone():
        return True
    path_sql = "SELECT 1 FROM files WHERE lower(rel_path) LIKE ?"
    path_params: List[object] = [f"%{term}%"]
    if repo_id is not None:
        path_sql += " AND repo_id = ?"
        path_params.append(repo_id)
    return store.conn.execute(path_sql + " LIMIT 1", path_params).fetchone() is not None
```

Dependencies on the base project: `stem(term)` (strip one of ing/ed/es/s if len ≥ 5, keep ≥ 3 characters),
`term_in(term, text)` (term or stem is a substring), `content_terms(query)` (stopwords and task verbs removed),
and `SymbolHit.span` (end_line − line + 1).

## Change 3: new CLI command `check`

In `cli.py` (import `assess` from `smarttoken.confidence`):

```python
@app.command("check")
def check_cmd(
    ctx: typer.Context,
    task: str = typer.Argument(..., help="The question you are about to ask paid AI."),
    repo: Optional[str] = typer.Option(None, "--repo", "-r"),
    budget: Optional[int] = typer.Option(None, "--budget", "-b"),
    copy: bool = typer.Option(False, "--copy", help="Copy the pack to the clipboard unless confidence is LOW."),
    force: bool = typer.Option(False, "--force", help="With --copy, copy even when confidence is LOW."),
) -> None:
    """Before spending paid tokens: confidence the pack has the right files, and what it saves."""
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
        result = measure_task(store, task, settings, repo_name=repo, budget=budget)
        packet = None
        if copy and (confidence.level != "LOW" or force):
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
    if copy:
        if packet is None:
            console.print("[yellow]Not copied: confidence is LOW. Add --force to copy anyway.[/yellow]")
        elif copy_text(packet.markdown):
            console.print(f"[green]Pack copied to clipboard ({packet.used_tokens} tokens).[/green]")
        else:
            err_console.print("[red]Could not copy. Run `smarttoken pack` and copy the output.[/red]")
```

`check --copy` copies the pack in one step when confidence is HIGH or MEDIUM. On LOW it holds back unless `--force` is given.

Example output:

```text
Confidence: HIGH
  - 100% of task words are in the top files
  - functions/classes named after the task: VendorChatHub, JoinVendorConversation
Top files:
  - Eats/src/services/.../Hubs/VendorChatHub.cs (source)
Pack: 3245 tokens ($0.0097) vs attaching the 15 hit files: 80614 tokens ($0.2418)
Saved: 96.0% vs hit files · 99.7% vs whole repo
Advice: Paste the pack: `smarttoken pack "..." --copy`.
```

## Change 4: tests, in `tests/test_confidence.py`

Uses the existing `indexed` and `sample_root` fixtures from `conftest.py`.

```python
from pathlib import Path

from typer.testing import CliRunner

from smarttoken.cli import app
from smarttoken.confidence import assess
from smarttoken.index_store import IndexStore
from smarttoken.search import search_index

runner = CliRunner()


def _assess(store: IndexStore, query: str):
    return assess(store, query, search_index(store, query, limit=20))


def test_answerable_question_is_high(indexed) -> None:
    store, _ = indexed
    result = _assess(store, "where is the webhook signature checked?")
    assert result.level == "HIGH"
    assert result.signals.missing_terms == []


def test_absent_topic_is_low_and_names_missing_words(indexed) -> None:
    store, _ = indexed
    result = _assess(store, "where is the kafka consumer for graphql events")
    assert result.level == "LOW"
    assert "kafka" in result.signals.missing_terms
    assert any("kafka" in reason for reason in result.reasons)


def test_stemmed_word_is_not_missing(indexed) -> None:
    store, _ = indexed
    result = _assess(store, "verifying webhook signatures")
    assert "signatures" not in result.signals.missing_terms


def test_check_and_measure_not_checked(tmp_path: Path, sample_root: Path) -> None:
    base = ["--home", str(tmp_path / "home")]
    runner.invoke(app, [*base, "add", str(sample_root)])
    runner.invoke(app, [*base, "index"])

    checked = runner.invoke(app, [*base, "check", "where is the webhook signature checked?"])
    assert checked.exit_code == 0, checked.stdout
    assert "Confidence: HIGH" in checked.stdout
    assert "Pack:" in checked.stdout
    assert "Saved:" in checked.stdout

    low = runner.invoke(app, [*base, "check", "where is the kafka consumer for graphql events", "--copy"])
    assert low.exit_code == 0
    assert "Not copied: confidence is LOW" in low.stdout

    measured = runner.invoke(app, [*base, "measure", "where is the webhook signature checked?"])
    assert measured.exit_code == 0
    assert "NOT CHECKED" in measured.stdout
```

The sample repo has no word starting with "retr", so don't use "retrying" as the stemmed-word example.

## Change 5: calibrate on a work repo (do this, don't skip it)

The thresholds (0.75 / 0.5 / half missing) were tuned on my home repos. On my 15 gold questions and 12 not-in-repo
questions the result was:

| | HIGH | MEDIUM | LOW |
|---|---:|---:|---:|
| Good questions (15) | 13 | 2 | 0 |
| Not-in-repo questions (12) | 0 | 3 | 9 |

At work, recheck this on your own repos:
1. Use the existing work gold set as the "good" questions.
2. Write about 10 questions on topics you're sure the repo doesn't have, after confirming with `rg -il <word> <repo>`
   that the word appears nowhere. Save them as `evals/confidence_negatives.json`:

```json
{"name": "questions whose answer is not in the repo", "tasks": [
  {"id": "kafka", "task": "where is the kafka consumer for order events", "repo": "my-repo"}
]}
```

3. For each question, run `search_index` then `assess`, and tally the levels.
   Target: **no good question rated LOW and no not-in-repo question rated HIGH.**
4. Adjust the thresholds only if that target fails, and keep the change generic.

## Done when

- All existing tests plus the 4 new ones pass, and `eval --fixture` is still 4/4.
- The work gold set's eval result is unchanged (this enhancement doesn't touch search or pack).
- `smarttoken check "<question>" --repo <repo>` prints the confidence level, reasons, top files, cost and advice.
- `smarttoken measure "<question>"` without `--expect` prints `Verdict: NOT CHECKED`.
