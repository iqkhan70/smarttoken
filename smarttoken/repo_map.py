"""Onboarding map from the local index. No model calls."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from smarttoken.index_store import IndexStore
from smarttoken.models import FileRecord, Repo
from smarttoken.scan import is_entrypoint


def render_map(store: IndexStore, repo: Repo) -> str:
    files = store.list_files(repo.id)
    if not files:
        return (
            f"# {repo.name}\n\n"
            f"**Path:** `{repo.path}`\n\n"
            "No files indexed. Run `smarttoken index`.\n"
        )

    langs = Counter(item.lang for item in files)
    roles = Counter(item.role for item in files)
    purpose = _purpose(store, files)
    docs = [item for item in files if item.role == "doc"]
    entries = [item for item in files if is_entrypoint(item.rel_path)]
    top_level = _top_level(files)

    lines = [
        f"# {repo.name}",
        "",
        f"**Path:** `{repo.path}`",
        f"**Remote:** {repo.remote_url or 'local (no origin)'}",
        f"**Branch:** {repo.default_branch or '?'}",
        f"**Last indexed:** {repo.last_indexed_at or 'never'}",
        f"**Files:** {len(files)}  ("
        + ", ".join(f"{lang} {count}" for lang, count in langs.most_common(8))
        + ")",
        f"**Roles:** " + ", ".join(f"{role} {count}" for role, count in roles.most_common()),
        "",
        "## Purpose",
        purpose,
        "",
        "## Start here",
    ]
    if docs:
        for doc in docs[:8]:
            lines.append(f"- `{doc.rel_path}`")
    else:
        lines.append("- No README/docs harvested.")

    lines.extend(["", "## Entrypoints"])
    if entries:
        for item in entries[:10]:
            lines.append(f"- `{item.rel_path}`")
    else:
        lines.append("- No conventional entrypoints (`main.py`, `app.py`, `src/`, `cmd/`).")

    lines.extend(["", "## Top-level"])
    for name, count in top_level:
        lines.append(f"- `{name}`  ({count} files)")
    return "\n".join(lines) + "\n"


def _purpose(store: IndexStore, files: list[FileRecord]) -> str:
    readmes = [
        item
        for item in files
        if Path(item.rel_path).name.lower() in {"readme.md", "readme.rst", "readme.txt"}
    ]
    if not readmes:
        return "No README found. Infer purpose from the top-level layout and `smarttoken search`."
    readme = sorted(readmes, key=lambda item: len(item.rel_path))[0]
    text = store.get_file_text(readme.id)
    return _first_paragraph(text)


def _first_paragraph(text: str) -> str:
    chunks: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if chunks:
                break
            continue
        if line.startswith("#") or line.startswith("![") or line.startswith("[!"):
            continue
        chunks.append(line)
        if len(" ".join(chunks)) > 280:
            break
    if not chunks:
        return "README has no usable prose after headings/badges."
    return " ".join(chunks)


def _top_level(files: list[FileRecord]) -> list[tuple[str, int]]:
    counts: Counter[str] = Counter()
    for item in files:
        first = item.rel_path.replace("\\", "/").split("/", 1)[0]
        counts[first] += 1
    return counts.most_common(16)
