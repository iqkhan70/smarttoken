# Copilot instructions

## Project commands

- Python 3.11+; install the CLI and development dependencies with `pip install -e ".[dev,mcp]"`.
- Run the full test suite with `python -m pytest`.
- Run one test with `python -m pytest tests/test_pack.py::test_pack_stays_under_budget_and_redacts` (replace the node with the test you are changing).
- Run the bundled indexing/retrieval evaluation with `smarttoken eval --fixture`.
- Inspect CLI commands and options with `smarttoken --help`.
- Start the read-only MCP server with `smarttoken-mcp` (stdio transport).
- There are no separate project-defined build or lint commands.

## Architecture

SmartToken is a local-first CLI; its core search and packing path does not call a model or require an API key. The main flow is:

1. `cli.py` exposes Typer commands. `registry.py` registers local roots; `scan.py` walks files, applies ignore/size/binary rules, and infers language and role.
2. `index_store.py` incrementally stores file metadata and text in SQLite/FTS5. The database, settings, and usage history live under `~/.smarttoken` by default, overridable with `--home` or `SMARTTOKEN_HOME`; never put index state in a clone.
3. `search.py` combines FTS matches with path matches, while `rank.py` scores candidates by task, path, and file role. `repo_map.py` builds onboarding maps from indexed metadata and docs.
4. `pack.py` selects ranked snippets and symbol ranges (`symbols.py`), enforces the token budget, and redacts snippet text. `prompts.py` supplies task-specific wording.
5. `ask`/`handoff.py` can print, copy, or send the prompt to supported local AI tools. The optional `mcp_server.py` exposes read-only `search` and `pack` tools over stdio. Both use the same local index and settings as the CLI. `measure.py` and `eval_tasks.py` check token cost and whether expected files made the pack.

## Repository-specific conventions

- Keep persistent app data outside registered repositories. Tests use `tmp_path` homes and copy `tests/fixtures/sample_repo` so indexing changes stay isolated and deterministic.
- For Git roots, the scanner uses tracked and untracked non-ignored files; for non-Git directories it applies `.gitignore` rules through `pathspec`. Always-skipped directories and file-size/binary limits are also enforced there.
- File roles inferred by `scan.py` feed ranking and packing. When changing role detection or retrieval, check both search ordering and pack/evaluation behavior.
- Sensitive paths are excluded from search results, and snippet contents are redacted before output. Preserve both protections when changing candidate retrieval or packet rendering.
- Snippet and symbol line ranges are 1-based and inclusive. Keep these semantics consistent across symbol extraction, packet rendering, and handoff pointers.
- Use `Settings` for validated configuration, shared dataclasses in `models.py` for cross-module records, and `IndexStore` as a context manager for database access.
- The repository-specific PR review workflow is in `.github/agents/pr-review-loop.agent.md`. Keep its approval gates: validate the exact named GitHub repository, preserve worktree changes, and do not commit or force-push as part of the review loop.
