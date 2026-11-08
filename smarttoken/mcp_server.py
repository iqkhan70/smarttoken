"""Read-only MCP tools for SmartToken's local index."""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from smarttoken.config import AppPaths, load_settings, resolve_home
from smarttoken.index_store import IndexStore
from smarttoken.pack import build_packet
from smarttoken.redact import redact
from smarttoken.search import search_index

mcp = MCPServer("smarttoken")
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    openWorldHint=False,
)


@mcp.tool(annotations=READ_ONLY)
def search(query: str, repo: str | None = None, limit: int = 10) -> str:
    """Search SmartToken's local index and return ranked paths with redacted previews.

    Args:
        query: Words or a question to find in indexed repositories.
        repo: Optional registered repository name to search.
        limit: Maximum number of results, from 1 to 100.
    """
    if not query.strip():
        raise ValueError("query must not be empty")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")

    paths = AppPaths(resolve_home())
    with IndexStore(paths.db_file) as store:
        if repo and store.get_repo(repo) is None:
            raise ValueError(f"Unknown repo '{repo}'. Run `smarttoken repos`.")
        hits = search_index(store, query, repo_name=repo, limit=limit)

    if not hits:
        return "No matching indexed files."
    return "\n".join(
        f"- `{hit.repo_name}/{hit.rel_path}` ({hit.role}, {hit.lang}; score {hit.score:.1f})"
        + (f"\n  {redact(hit.preview)}" if hit.preview else "")
        for hit in hits
    )


@mcp.tool(annotations=READ_ONLY)
def pack(task: str, repo: str | None = None, budget: int | None = None) -> str:
    """Build a bounded, redacted Markdown context pack from the local index.

    Args:
        task: The coding task or question for which to retrieve context.
        repo: Optional registered repository name to limit the pack to.
        budget: Optional token ceiling; must be at least 200.
    """
    if not task.strip():
        raise ValueError("task must not be empty")
    if budget is not None and budget < 200:
        raise ValueError("budget must be at least 200 tokens")

    paths = AppPaths(resolve_home())
    settings = load_settings(paths)
    with IndexStore(paths.db_file) as store:
        if repo and store.get_repo(repo) is None:
            raise ValueError(f"Unknown repo '{repo}'. Run `smarttoken repos`.")
        packet = build_packet(store, task, settings, repo_name=repo, budget=budget)
    return packet.markdown


def run() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run()
