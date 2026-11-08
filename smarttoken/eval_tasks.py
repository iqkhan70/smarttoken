"""Load gold-file eval tasks from JSON."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class EvalTask:
    id: str
    task: str
    repo: str | None = None
    must_include: list[str] | None = None
    nice_include: list[str] | None = None
    must_exclude: list[str] | None = None


def load_tasks(path: Path) -> list[EvalTask]:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data["tasks"] if isinstance(data, dict) else data
    tasks: list[EvalTask] = []
    for item in raw:
        tasks.append(
            EvalTask(
                id=item.get("id") or item["task"][:40],
                task=item["task"],
                repo=item.get("repo"),
                must_include=item.get("must_include") or [],
                nice_include=item.get("nice_include") or [],
                must_exclude=item.get("must_exclude") or [],
            )
        )
    return tasks
