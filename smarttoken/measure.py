"""With vs without: token cost of a dump, vs a pack that still hits gold files."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from smarttoken.config import Settings
from smarttoken.index_store import IndexStore
from smarttoken.models import FileRecord
from smarttoken.pack import build_packet
from smarttoken.search import search_index
from smarttoken.tokens import count_tokens

SKIP_ROLES = {"generated"}


@dataclass
class Baseline:
    name: str
    label: str
    tokens: int
    files: int


@dataclass
class GoldCheck:
    path: str
    found: bool
    required: bool


@dataclass
class MeasureResult:
    task: str
    repo_name: str | None
    budget: int
    pack_tokens: int
    pack_files: list[str]
    baselines: list[Baseline]
    gold: list[GoldCheck] = field(default_factory=list)
    excluded_leaks: list[str] = field(default_factory=list)
    dollars_per_million: float = 3.0

    @property
    def whole_repo(self) -> Baseline | None:
        return next((item for item in self.baselines if item.name == "whole_repo"), None)

    @property
    def whole_hits(self) -> Baseline | None:
        return next((item for item in self.baselines if item.name == "whole_hits"), None)

    @property
    def required_recall(self) -> float | None:
        required = [item for item in self.gold if item.required]
        if not required:
            return None
        return sum(1 for item in required if item.found) / len(required)

    @property
    def passed(self) -> bool:
        return all(item.found for item in self.gold if item.required) and not self.excluded_leaks

    @property
    def verdict(self) -> str:
        """PASS/FAIL only when there was something to check; otherwise NOT CHECKED."""
        if not self.gold and not self.excluded_leaks:
            return "NOT CHECKED"
        return "PASS" if self.passed else "FAIL"

    def usd(self, tokens: int) -> float:
        return tokens / 1_000_000 * self.dollars_per_million

    def savings_pct(self, baseline: Baseline | None) -> float | None:
        if baseline is None or baseline.tokens <= 0:
            return None
        return (baseline.tokens - self.pack_tokens) / baseline.tokens * 100

    def to_dict(self) -> dict:
        return {
            "task": self.task,
            "repo": self.repo_name,
            "budget": self.budget,
            "pack_tokens": self.pack_tokens,
            "pack_files": self.pack_files,
            "baselines": [asdict(item) for item in self.baselines],
            "gold": [asdict(item) for item in self.gold],
            "excluded_leaks": self.excluded_leaks,
            "required_recall": self.required_recall,
            "passed": self.passed,
            "verdict": self.verdict,
            "savings_vs_whole_repo_pct": self.savings_pct(self.whole_repo),
            "savings_vs_whole_hits_pct": self.savings_pct(self.whole_hits),
            "est_usd_pack": round(self.usd(self.pack_tokens), 6),
            "est_usd_whole_repo": round(self.usd(self.whole_repo.tokens), 6) if self.whole_repo else None,
        }


def measure_task(
    store: IndexStore,
    task: str,
    settings: Settings,
    repo_name: str | None = None,
    budget: int | None = None,
    must_include: list[str] | None = None,
    nice_include: list[str] | None = None,
    must_exclude: list[str] | None = None,
) -> MeasureResult:
    packet = build_packet(store, task, settings, repo_name=repo_name, budget=budget)
    whole_repo = _whole_repo_baseline(store, settings, repo_name)
    whole_hits = _whole_hits_baseline(store, settings, task, repo_name)
    haystack = _haystack(packet.files, packet.markdown)
    gold: list[GoldCheck] = []
    for path in must_include or []:
        gold.append(GoldCheck(path=path, found=_path_in(path, haystack), required=True))
    for path in nice_include or []:
        gold.append(GoldCheck(path=path, found=_path_in(path, haystack), required=False))
    leaks = [path for path in (must_exclude or []) if _path_in(path, haystack)]
    return MeasureResult(
        task=task,
        repo_name=repo_name,
        budget=packet.budget,
        pack_tokens=packet.used_tokens,
        pack_files=packet.files,
        baselines=[whole_repo, whole_hits],
        gold=gold,
        excluded_leaks=leaks,
        dollars_per_million=settings.dollars_per_million_input,
    )


def _whole_repo_baseline(store: IndexStore, settings: Settings, repo_name: str | None) -> Baseline:
    records = _index_files(store, repo_name)
    tokens = 0
    files = 0
    for record in records:
        tokens += count_tokens(store.get_file_text(record.id), settings.encoding)
        files += 1
    return Baseline("whole_repo", "Without: dump the indexed clone", tokens, files)


def _whole_hits_baseline(
    store: IndexStore, settings: Settings, task: str, repo_name: str | None
) -> Baseline:
    hits = search_index(store, task, repo_name=repo_name, limit=settings.search_limit)
    seen: set[int] = set()
    tokens = 0
    files = 0
    for hit in hits:
        if hit.file_id in seen or hit.role in SKIP_ROLES:
            continue
        seen.add(hit.file_id)
        tokens += count_tokens(store.get_file_text(hit.file_id), settings.encoding)
        files += 1
    return Baseline("whole_hits", "Without: paste every search-hit file whole", tokens, files)


def _index_files(store: IndexStore, repo_name: str | None) -> list[FileRecord]:
    repo_id = store.require_repo(repo_name).id if repo_name else None
    return [record for record in store.list_files(repo_id) if record.role not in SKIP_ROLES]


def _haystack(files: list[str], markdown: str) -> str:
    return "\n".join(files) + "\n" + markdown


def _path_in(needle: str, haystack: str) -> bool:
    needle = needle.replace("\\", "/").lower()
    while needle.startswith("./"):
        needle = needle[2:]
    blob = haystack.replace("\\", "/").lower()
    if needle.endswith("/"):
        # A folder rule (obj/, dist/) matches a path segment, not the word "object" in code.
        return re.search(r"(?:^|[\s/`'\"(])" + re.escape(needle), blob) is not None
    if needle in blob:
        return True
    compact = re.sub(r"\[[^/\]]+\]/", "", needle)
    if compact != needle and compact in blob:
        return True
    name = Path(needle).name.lower()
    return bool(name) and name in blob


def bundled_tasks_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "fixture_tasks.json"


def bundled_fixture_path() -> Path:
    installed = Path(__file__).resolve().parent / "data" / "sample_repo"
    if installed.is_dir():
        return installed
    return Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "sample_repo"


def render_markdown(result: MeasureResult) -> str:
    lines = [
        f"# Measure: {result.task}",
        "",
        "| approach | tokens | files | est. input $ |",
        "|---|---:|---:|---:|",
    ]
    for baseline in result.baselines:
        lines.append(
            f"| {baseline.label} | {baseline.tokens} | {baseline.files} | ${result.usd(baseline.tokens):.4f} |"
        )
    lines.append(
        f"| **With: smarttoken pack** | **{result.pack_tokens}** | **{len(result.pack_files)}** | **${result.usd(result.pack_tokens):.4f}** |"
    )
    lines.append("")
    if result.whole_repo:
        pct = result.savings_pct(result.whole_repo)
        lines.append(
            f"Saved vs dump: **{pct:.1f}%** ({result.whole_repo.tokens - result.pack_tokens} tokens)."
        )
    if result.whole_hits:
        pct = result.savings_pct(result.whole_hits)
        if pct is not None:
            lines.append(
                f"Saved vs whole search hits: **{pct:.1f}%** ({result.whole_hits.tokens - result.pack_tokens} tokens)."
            )
    lines.append("")
    lines.append(f"Dollar figures assume ${result.dollars_per_million:.2f} / 1M input tokens (configurable).")
    if result.whole_repo and result.pack_tokens >= result.whole_repo.tokens:
        lines.append(
            "Pack overhead exceeds this tiny clone. That is expected — measure a real org repo."
        )
    if result.gold:
        lines.extend(["", "## Gold files", ""])
        for item in result.gold:
            mark = "HIT" if item.found else "MISS"
            kind = "must" if item.required else "nice"
            lines.append(f"- {mark} ({kind}) `{item.path}`")
    if result.excluded_leaks:
        lines.extend(["", "## Must-exclude leaked", ""])
        lines.extend(f"- `{path}`" for path in result.excluded_leaks)
    lines.extend(["", f"Verdict: {result.verdict} — cheap is not useful if gold files are missing."])
    return "\n".join(lines) + "\n"
