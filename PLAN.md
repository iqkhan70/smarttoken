# Smart Token App — Living Build Plan

This is the in-repo spec. [`start.txt`](start.txt) is the original problem. [`ProposedStuff.txt`](ProposedStuff.txt) is a scratch pad, not the spec.

## Problem

You joined a new org. Productivity means opening many git repos, learning how things actually work, then making real fixes and enhancements. Dumping whole trees into paid AI burns tokens and still gives vague answers. Smart prompts alone do not fix that. The expensive model should receive a **small, definitive packet**, not a scavenger hunt.

## Product thesis

**Do the cheap work locally. Spend paid tokens only on a ranked, bounded packet.**

The app never needs a model key for its core loop. It walks your clones, indexes them, answers “where / what / who calls” on disk, then writes a packet you paste into Cursor or ChatGPT.

```mermaid
flowchart LR
  repos[LocalGitRepos] --> index[LocalIndex]
  index --> query[CLIQuery]
  query --> retrieve[SearchAndSymbols]
  retrieve --> pack[TokenBudgetPacker]
  pack --> packet[MarkdownPacket]
  packet --> paidAI[CursorOrChatGPT]
```

## What we will not build first

- No TUI or web UI (CLI first).
- No required cloud embeddings or paid API in the index/search path.
- No automatic commits, code mutation, or “agent that edits the org.”
- No replacing Cursor. This tool **feeds** Cursor.

## User loop we optimize for

1. Clone / pull org repos into a workspace folder.
2. `smarttoken add` those roots and `smarttoken index`.
3. Ask a work question (`search`, `map`, `explain`).
4. When you are ready to use paid AI: `smarttoken pack "fix retry on payment webhook" --budget 4000`.
5. Paste the packet. Paid AI gets files, line ranges, symbols, and a task brief — not the monorepo.

## Stack

Python 3.11+, because scouring and packing is I/O and text work.

- CLI: `typer` + `rich`
- Config: `pydantic` + `~/.smarttoken/config.toml` (override with `--home` or `SMARTTOKEN_HOME`)
- Store: one SQLite file with FTS5 at `~/.smarttoken/index.db` (outside clones; do not pollute org repos)
- Ignore rules: `.gitignore` via `pathspec`, or `git ls-files` when the root is a git repo
- Token estimates: `tiktoken` (cl100k) so `--budget` is real
- Search: SQLite FTS5, with `ripgrep` as an optional extra hit source
- Symbols (phase 4): `tree-sitter` for the languages you actually hit
- Optional later: local embeddings, MCP server, TUI

## CLI surface (north star)

```text
smarttoken init
smarttoken add ~/code/org-payments ~/code/org-platform
smarttoken index [--repo name]
smarttoken repos
smarttoken map [repo]                 # onboarding map: purpose, entrypoints, dirs
smarttoken search "invoice webhook"
smarttoken explain payments.auth
smarttoken pack "add retry to payment webhook" --budget 4000 --copy
smarttoken brief "how do we deploy checkout"
```

`pack` is the money command. Everything else exists to make `pack` accurate and small.

## Packet contract

Every pack is markdown with a hard token ceiling and this shape:

- **Task** — one-paragraph restatement of what you asked
- **Repos involved** — which clones, default branch, last index time
- **Read this first** — 3–8 ranked files with why
- **Snippets** — only the relevant ranges, not whole files
- **Symbols / callers** — names and paths, not essays
- **Do not open** — generated, vendored, lockfiles, secrets
- **Suggested Cursor ask** — a short, specific prompt
- **Budget** — used / remaining tokens

If content does not fit, drop lowest-ranked snippets and say what was omitted. Fail open: recommend `search` / `rg` rather than stuffing more files.

## Architecture

```text
smarttoken/
  cli.py                 # typer commands
  config.py
  registry.py            # registered roots, names, git remotes
  scan.py                # walk + gitignore + size/binary caps
  index_store.py         # sqlite schema + incremental hash refresh
  search.py              # FTS + path scoring + rg
  symbols.py             # tree-sitter ranges, regex fallback
  rank.py                # file/snippet scoring
  pack.py                # budget + packet markdown
  prompts.py             # pack / brief / map templates
  tokens.py              # tiktoken counters
  redact.py              # secret masking in snippets
```

SQLite tables (v1): `repos`, `files` (path, lang, role, hash, mtime, size), `file_text` (FTS5). Incremental index: skip unchanged hashes.

File **roles** matter for ranking: `source`, `test`, `doc`, `config`, `generated`. Boost source and docs for “how does X work”; boost tests when the task is a fix.

## Build phases

Work in this order. Each phase should be usable before the next starts.

### Phase 0 — Living reference + skeleton

- [x] Write this document to `PLAN.md`.
- [x] Keep `ProposedStuff.txt` as a scratch pad for later ideas; do not treat it as spec.
- [x] Create the Python package, `pyproject.toml`, `smarttoken init`, empty config, and a version command.
- [x] Add a tiny fixture repo under `tests/fixtures/` so later phases have something deterministic to index.

### Phase 1 — Registry, scan, repo map (no LLM)

- [x] Register one or many local git roots.
- [x] Walk files, honor `.gitignore`, skip `node_modules`, `.git`, binaries, huge files.
- [x] Record language-by-extension, top-level layout, README/docs harvest, remote URL, default branch.
- [x] `smarttoken map` prints an onboarding map per repo: what it seems to be, entrypoints (`main`, `app.py`, `src/`, `cmd/`), and “start here” docs.
- [x] Success: you can point it at 3 real org clones and get a readable map in seconds without calling any model.

### Phase 2 — Local search

- [x] Index file text into SQLite FTS5.
- [x] `smarttoken search` returns ranked paths + one-line hits.
- [x] Scoring: exact path/name > FTS BM25 > extension/role priors.
- [x] Success: “where is the webhook signature checked?” finds the right file without opening Cursor.

### Phase 3 — Packer (the product)

- [x] `smarttoken pack "<task>" --budget N --copy`
- [x] Retrieve candidates, take snippets around hits, count tokens, emit the packet contract.
- [x] Prompt templates for: understand, fix, enhance.
- [x] Redact obvious secrets (API key assignments, PEM blocks) in snippets.
- [x] Success: a 4k-token pack that a paid model can act on, instead of `@`-ing half the repo.

### Phase 4 — Symbols

- [x] Tree-sitter extract functions/classes/methods with line ranges for the languages you use most (Python, TS/TSX/JS, Go, C#; regex fallback for the rest).
- [x] `explain` and `pack` prefer symbol ranges over random 40-line windows.
- [ ] Optional: same-file and import-based references. Do not boil the ocean on a full call graph.

### Phase 5 — Briefs for paid AI

- [ ] `smarttoken brief` writes a longer onboarding or change-plan note (still local, still bounded).
- [ ] Cross-repo pack: one task, several registered clones (common when you are new).
- [ ] Index freshness stamp in every packet so you do not paste a stale map.

### Phase 6 — Only if the core loop is daily-use

- [ ] Optional local embeddings (sentence-transformers) as a second retriever, never required.
- [x] Read-only MCP server exposing local `search` / `pack` over stdio for Copilot CLI.
- [ ] TUI. Incremental watch.
- [x] Eval / measure harness (pulled forward so usefulness is a number, not a demo).

## Token-saving rules (non-negotiable)

- Default pack budget: 4000 tokens. Always print used/limit.
- Never include lockfiles, `dist/`, `vendor/`, minified assets, or files over a size cap.
- Prefer line ranges over whole files.
- One pack = one task. Do not “just in case” add siblings.
- Local commands are free. Paid AI is a last step, not the search engine.

## How we use this document while building

- `PLAN.md` is the spec. When we finish a phase, check it off here.
- New ideas go in `ProposedStuff.txt` first; promote into PLAN only if we commit to build them.
- If a phase wants embeddings, a UI, or an API key, it is Phase 6 unless we explicitly pull it forward.

## Measuring usefulness

A cheaper pack that misses the file you needed is not useful. Every claim has two numbers:

1. **Tokens (cost)** — dump the clone vs paste every search-hit file whole vs `smarttoken pack`.
2. **Recall (usefulness)** — did the pack still contain the gold files a human would open?

```bash
# One-command proof on the bundled fixture (no setup)
smarttoken eval --fixture

# One real task on an org clone you already indexed
smarttoken measure "where is the webhook signature checked?" \
  --expect src/payments/webhook.py --markdown

# Your own gold set after `add` + `index`
cp evals/org_tasks.example.json evals/my_org.json
# edit must_include to files you already know are right
smarttoken eval --tasks evals/my_org.json --markdown

# After a week of `measure` on real work
smarttoken report
```

`eval --fixture` is the demo. `eval --tasks` on real clones is the sell. If recall is not 100% on questions you can already answer, do not claim the token savings.

## How to run (current)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
smarttoken --help
smarttoken init
smarttoken add /path/to/org-clone
smarttoken index
smarttoken map
smarttoken search "webhook signature"
smarttoken pack "fix retry on payment webhook" --budget 4000 --copy
smarttoken eval --fixture
smarttoken measure "where is the webhook signature checked?" --expect src/payments/webhook.py
```

Data lives in `~/.smarttoken` unless you pass `--home` or set `SMARTTOKEN_HOME`. Index is never written into the clones.

Phases 0–4 are implemented (Phase 4 references/callers left optional). Snippets are whole functions or classes up to 28 lines; longer ones get a line window labelled with the enclosing symbol. Checked against Eats 7/7, golangpoc 8/8, fixture 4/4.
