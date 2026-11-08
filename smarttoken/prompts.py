"""Templates for map, pack, and brief outputs."""

from __future__ import annotations


def suggested_ask(task: str, kind: str, files: list[str]) -> str:
    file_list = ", ".join(f"`{path}`" for path in files[:6]) or "the ranked files above"
    if kind == "fix":
        return (
            f"Using only these files ({file_list}), fix: {task}. "
            "Do not expand scope. Cite the exact functions you change."
        )
    if kind == "enhance":
        return (
            f"Using only these files ({file_list}), implement: {task}. "
            "Match existing style. List files you would edit before writing code."
        )
    return (
        f"Using only these files ({file_list}), explain how this works for: {task}. "
        "Answer from the snippets. If something is missing, say so instead of guessing."
    )


def restated_task(task: str, kind: str) -> str:
    prefix = {
        "fix": "Fix request",
        "enhance": "Enhancement request",
        "understand": "Understand request",
    }[kind]
    return f"{prefix}: {task.strip()}"
