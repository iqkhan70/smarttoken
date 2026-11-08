"""Local usage ledger so a week of real packs can be totaled."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from smarttoken.config import AppPaths
from smarttoken.measure import MeasureResult


def append_measure(paths: AppPaths, result: MeasureResult, source: str) -> None:
    paths.ensure_home()
    row = {
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": source,
        "task": result.task,
        "repo": result.repo_name,
        "pack_tokens": result.pack_tokens,
        "whole_repo_tokens": result.whole_repo.tokens if result.whole_repo else None,
        "whole_hits_tokens": result.whole_hits.tokens if result.whole_hits else None,
        "pack_files": len(result.pack_files),
        "passed": result.passed,
    }
    with paths.usage_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def read_usage(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def summarize(rows: list[dict], dollars_per_million: float) -> dict:
    pack = sum(int(row.get("pack_tokens") or 0) for row in rows)
    repo = sum(int(row.get("whole_repo_tokens") or 0) for row in rows)
    hits = sum(int(row.get("whole_hits_tokens") or 0) for row in rows)
    return {
        "events": len(rows),
        "pack_tokens": pack,
        "whole_repo_tokens": repo,
        "whole_hits_tokens": hits,
        "saved_vs_repo": repo - pack if repo else None,
        "saved_vs_hits": hits - pack if hits else None,
        "est_usd_pack": pack / 1_000_000 * dollars_per_million,
        "est_usd_whole_repo": repo / 1_000_000 * dollars_per_million if repo else None,
    }
