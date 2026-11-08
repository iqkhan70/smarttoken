# Build "smarttoken": a local CLI that packs small, exact code context for paid AI

Paste this whole file into Copilot. Build it in the phase order below, with tests at each phase. Don't skip ahead.

## Goal

I'm new to an org with many git repos. I need a **local** Python CLI that indexes my clones and, for a
task like "where is the JWT issued", outputs a **small markdown packet** (about 4k tokens) with the right files,
line ranges and snippets. I paste that into a paid AI instead of attaching whole folders. No model calls,
no network at runtime, nothing written into the repos.

## Environment constraints

- **Python 3.9 compatible.** Use `from __future__ import annotations` in every module. Use `Optional[X]` / `List[X]`
  in anything evaluated at runtime (pydantic models, typer options). No `match`, no `zip(strict=)`.
  Read TOML with `tomllib` if available, else `tomli`.
- Check SQLite FTS5 first: `python3 -c "import sqlite3; sqlite3.connect(':memory:').execute('create virtual table t using fts5(x)')"`.
- Dependencies: `typer`, `rich`, `pydantic>=2`, `pathspec`, `tiktoken`, `tree-sitter`, `tree-sitter-language-pack`
  (on Python 3.9 that resolves to tree-sitter 0.23 / language pack 0.9; verify `get_parser("go")` works). Dev: `pytest`.
- Package layout: `smarttoken/` package, `pyproject.toml` (hatchling), console script `smarttoken = "smarttoken.cli:run"`
  (`run()` inserts the default `ask` command; see `enhancement1.2.md`).
  Use a venv, and `pip install -e '.[dev]'`.

## Modules

`config.py`, `models.py`, `scan.py`, `index_store.py`, `rank.py`, `search.py`, `symbols.py`, `pack.py`,
`redact.py`, `tokens.py`, `confidence.py`, `focus.py`, `handoff.py`, `prompts.py`, `repo_map.py`, `measure.py`, `eval_tasks.py`, `usage.py`, `cli.py`,
`data/fixture_tasks.json`, `tests/` with a small fixture repo in `tests/fixtures/sample_repo/`.

## Data home

- Default `~/.smarttoken`; override with `--home` (global option) or `SMARTTOKEN_HOME`.
- Holds `config.toml`, `index.db` (SQLite) and `usage.jsonl`. **Never write into the indexed repos.**
- Settings: `default_budget=4000`, `max_file_bytes=512000`, `context_lines=12`, `encoding="cl100k_base"`,
  `search_limit=15`, `dollars_per_million_input=3.0`, `ask_tiers=[3500, 5000, 7000]`, `send_to="clipboard"`,
  `send_model=""`. Sent prompts are saved in `<home>/packs/`.

## CLI commands

| Command                                                           | Does                                                                                               |
| ----------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `version`                                                         | print version                                                                                      |
| `init`                                                            | create the home and default config                                                                 |
| `add PATH...`                                                     | register repo roots (name = folder name, `--name` to override); store git remote and branch if any |
| `repos`                                                           | list repos with branch and last indexed time                                                       |
| `index [--repo]`                                                  | scan and index, incremental by file hash; delete rows for removed files                            |
| `map [--repo]`                                                    | onboarding map: languages, top-level layout, entrypoints, start-here docs                          |
| `search "q" [--repo]`                                             | ranked table: score, repo, path, role, one-line hit                                                |
| `explain "name" [--repo]`                                         | pack with budget ≤ 2000, for a symbol or topic                                                     |
| `pack "task" [--repo] [--budget N] [--copy]`                      | the packet (clipboard via pbcopy / wl-copy / xclip / clip)                                         |
| `brief "task"`                                                    | longer onboarding note, still bounded                                                              |
| `measure "task" --expect PATH --exclude PATH [--json/--markdown]` | with vs without numbers; exit 2 if an expected file is missing                                     |
| `check "task" [--repo] [--budget] [--copy] [--force]` | before spending paid tokens: HIGH/MEDIUM/LOW confidence with reasons, top 3 files, pack cost vs attaching the hit files, % saved vs hit files and vs whole repo, advice. `--copy` copies the pack unless confidence is LOW (`--force` overrides) |
| `eval --tasks FILE [--budget] / --fixture`                        | run a gold set and print a table: id, pack tokens, % vs dump, recall, PASS/FAIL                    |
| `focus [--set AREA] [--unpin] [--clear] [--repo]` | show the area you've been working on (learned from recent questions), pin one, or reset |
| `report`                                                          | sum the `usage.jsonl` entries logged by `measure`                                                  |
| `"task" [all\|REPO] [--repo] [--to T] [--model M] [--yes]` (= `ask`) | default command: size the prompt, confirm the token tier (Y/n), then send it to VS Code Copilot, Cursor, Cursor Agent, Copilot CLI, Claude Code, clipboard or print. Over the last tier: "narrow your prompt", exit 4 |

With no `--repo`, search and pack span **all** registered repos.

## Scan (`scan.py`)

- Git repo: `git ls-files -z --cached --others --exclude-standard`. Otherwise `os.walk` plus `.gitignore` via
  `pathspec` ("gitignore" patterns).
- Always skip dirs: `.git node_modules __pycache__ .venv venv .tox .mypy_cache .pytest_cache .ruff_cache dist build target vendor .idea .smarttoken`.
- Skip binary extensions (images, archives, fonts, `.dll .so .exe .class .pyc .wasm .jar`), files over `max_file_bytes`,
  and files with a NUL byte in the first 8 KB.
- Language by extension (py, ts/tsx, js/jsx/mjs/cjs, go, rs, java, kt, rb, php, cs, c/cpp, md, toml, yaml, json, sql, sh, tf, swift, scala).
- **Role** (order matters):
  1. `generated`: lockfiles, `*.min.js`/`*.min.css`, any path part in dist/vendor/generated/node_modules/build.
  2. `test`: path part test/tests/spec/**tests**, `test_*`, `*_test.*`, `.spec.`, `.test.`.
  3. `doc`: README/CONTRIBUTING/CHANGELOG/ARCHITECTURE, `docs/`, any `.md`.
  4. `config`: path part deploy/scripts, `.sh`, `.sql`, `.csproj`, manifests (package.json, pyproject, go.mod, Dockerfile, Makefile, tsconfig), `.yml .yaml .toml .ini .cfg`.
  5. Otherwise `source`.

## Index (`index_store.py`)

Tables: `repos(id, name UNIQUE, path UNIQUE, remote_url, default_branch, last_indexed_at, content_tokens)`,
`files(id, repo_id, rel_path, lang, role, hash, mtime, size, UNIQUE(repo_id, rel_path))`, and
`file_text` = FTS5 virtual table `(file_id UNINDEXED, repo_id UNINDEXED, rel_path, content, tokenize='porter')`.
Rank with `bm25()` (lower is better).

## Query terms (`rank.py`): this is where retrieval quality comes from

- **Stopwords dropped:** articles and pronouns, question words (where/what/how/why/when/which/who),
  does/do/can/should, and/or/not, using/use/please/show/tell/find/locate/explain/about, **implemented/implementation**.
- **Task verbs dropped from search** but used for intent: fix, add, enhance, implement, create, update, make.
- Terms shorter than 3 characters dropped.
- `task_kind`: "fix" if the text has fix/bug/error/fail/retry/crash/regress; "enhance" if add/enhance/implement/feature/support; else "understand".
- `stem(term)`: only if len ≥ 5, strip one of `ing, ed, es, s`, keeping ≥ 3 characters ("adding"→"add", "placed"→"plac").
  `term_in(term, text)` = term or its stem is a substring. Use this for path, name and line matching.
- **Identifier terms:** len ≥ 14, or len ≥ 10 ending in async/service/controller. Plain words like "service" are NOT identifiers.

## Search (`search.py`)

1. `fts_query`: if there are identifier terms, AND them; else if ≤ 3 terms, AND them; else OR all terms.
2. Run that query, **and also** run the OR-of-all-terms query; union them. Files that matched the strict
   query get +12.
3. Path fallback: also add files whose filename contains a term, or whose path contains ≥ 2 terms.
4. Score each hit:
   - start with `max(0, 12 - bm25)`
   - +12 per term in the filename, +5 per term in the path, +20 if ≥ 2 terms are in the filename, +25 per identifier term in the path
   - if the query says "mobile": +22 for paths containing mobile, ×0.45 for webapp paths
   - +22 if the query has login/jwt/token/auth and the filename contains "auth"
   - +12 for `source` files whose stem ends in service/controller/handler (any language)
   - multiply by role: source 1.3, doc 1.1, test 0.9, config 0.8, generated 0.15. For fix tasks test is 1.25 and doc 0.85; for understand tasks doc is 1.25.
   - **Penalty ×0.2** for `/deploy/`, `/migrations/`, `/scripts/`, `.sh`, `.sql`, `.csproj`, unless the query
     mentions deploy/migration/script/csproj. ×0.35 for `/entities/` unless the query says entity.
5. Sort, then **drop hits scoring below 40% of the top hit**. Return up to `limit`.
6. Preview = first line containing a term.

## Symbols (`symbols.py`)

- Tree-sitter via `tree_sitter_language_pack.get_parser(lang)`. Use the `tsx` grammar for `.tsx`/`.jsx`.
  Return `name, kind, start_line, end_line`.
- Node types:
  - python: `function_definition`, `class_definition`
  - go: `function_declaration`, `method_declaration`, `type_spec`
  - csharp: class/interface/struct/record/enum `_declaration`, `method_declaration`, `constructor_declaration`
  - ts/js: `function_declaration`, `class_declaration`, `interface_declaration`, `method_definition`, and
    `variable_declarator` whose value is an arrow function or function expression
    Name comes from the `name` field.
- If tree-sitter can't load, fall back to regex for `def`/`class`/`function` (start line only, no range).
- `symbols_matching(query)`: symbols whose name contains a term. Drop a one-line declaration (e.g. a C# interface
  method) when a symbol with the same name and a body exists in the same file.

## Pack (`pack.py`): the product

Packet sections, in order: `# Smart Token pack`, `## Task` (restated by kind), `## Repos involved` (name,
path, branch, remote, **indexed timestamp**), `## Read this first` (≤ 8 non-generated hits with previews),
`## Snippets`, `## Symbols / callers` (≤ 12, with line ranges), `## Do not open` (generated files, lockfiles, dist),
`## Omitted (over budget)`, `## Suggested Cursor ask` (per kind, names the files), `## Budget` ("Used N / B tokens").

Snippet windows per hit file (≤ 3 per file, ≤ 10 total):

- Find lines containing a term. For each, look at the symbols containing that line with a span of at least 3 lines,
  smallest first. If one spans ≤ 28 lines, or its name equals a query term and it spans ≤ 80, use the **whole symbol**,
  labelled e.g. "method `AddItem`".
- Otherwise use a ±12-line window capped at 28 lines, clipped to the enclosing symbol and labelled
  "inside method `X` L10-120". With no symbol, label it "query term match".
- Drop windows contained in another; merge overlapping windows with the same label.
- Keep the 3 windows with the most distinct terms (+3 if the symbol name contains a term). No line match means
  the first 24 lines, labelled "file head".
- Order snippets by hit score. Add them greedily while they fit the remaining budget. If one doesn't fit, try
  its first 16 lines; else list it under Omitted.
- **Hard budget:** count tokens on the final redacted markdown. While it's over budget, drop the last snippet.
  Use a placeholder for the used-token number (not `str.format`, because code contains braces).

## Redaction (`redact.py`)

Run on every snippet and preview, and on the final packet: PEM private key blocks; assignments where the name
contains api_key/secret/password/passwd/token/access_key and the quoted value is 8+ characters; `sk-...` keys;
`AKIA[0-9A-Z]{16}`; `Bearer <token>`.

## Tokens (`tokens.py`)

tiktoken `cl100k_base`, cached. **If the encoding can't load (offline or blocked network), fall back to
`ceil(len(text)/4)`.** Never crash.

## Measure and eval: the proof that it's useful

- Baselines per task: **whole_repo** (tokens of every non-generated file in scope) and **whole_hits** (every hit
  file pasted whole). Report the pack's % saving against both, and dollars at $3 per 1M tokens.
- Gold check: each `must_include` path must appear in the pack. Match by suffix, by basename, and after
  stripping bracket segments like `[id]/`. Any `must_exclude` path appearing is a leak and fails the task.
  PASS = all must_include found and no leaks. With no `--expect`/`--exclude`, the verdict is **NOT CHECKED**, not PASS.

## Check (`confidence.py`): should I use the pack for this question?

Run the same search the pack uses, then compute:
- **coverage:** the best fraction of task words (term or stem, substring) found in the path+content of any of the top 3 files.
- **missing words:** task words found nowhere in the index for that repo (FTS match on the word or its stem, or a path substring).
- **named symbols:** functions/classes in the top 3 files whose name contains a task word.

Levels:
- **LOW** if there are no hits, coverage < 0.5, or at least half the words are missing. Advice: the repo probably doesn't have it; rephrase or ask the AI directly.
- **HIGH** if no words are missing and coverage ≥ 0.75. Advice: paste the pack.
- **MEDIUM** otherwise. Always list the missing words: if the subject word (e.g. "kafka") is missing, the repo likely doesn't have it.

Calibrate the levels on your gold set plus about 10 questions about topics you know aren't in the repo. Target:
no good question rated LOW, and no not-in-repo question rated HIGH. Keep those questions in a negatives file.
- Gold set JSON:

```json
{
  "name": "my repo",
  "tasks": [
    {
      "id": "jwt-issue",
      "task": "where is the JWT access token issued",
      "repo": "my-repo",
      "must_include": ["pkg/auth/jwt.go"],
      "nice_include": [],
      "must_exclude": []
    }
  ]
}
```

- `eval --fixture` indexes `tests/fixtures/sample_repo` into a temp home and runs `data/fixture_tasks.json`.
  Bundle the fixture inside the wheel too, and look for it in the package first.
- The fixture repo: a small Python payments app (`src/payments/webhook.py` with a signature check and retry,
  `auth.py` with `authenticate_request`, `secrets_demo.py` with a fake key for the redaction test, `src/app.py`,
  `docs/deploy.md`, `tests/test_webhook.py`, `dist/bundle.js`, README, `pyproject.toml`, `.gitignore`). Pad it with
  a large unrelated `src/unrelated/inventory.py` so a whole-repo dump is bigger than a pack. Fixture tasks
  (`data/fixture_tasks.json`):
  - "where is the webhook signature checked?" → must include `src/payments/webhook.py`
  - "fix retry on payment webhook" → must include `webhook.py`, nice to include `tests/test_webhook.py`
  - "how do we deploy checkout" → must include `docs/deploy.md`
  - "explain authenticate_request payments auth" → must include `src/payments/auth.py`
  - the first two must exclude `dist/bundle.js` and `src/unrelated/inventory.py`

## Focus check (`focus.py`): warn when a question lands in the wrong area

Similar screens (e.g. an AOP list and a case list) share most of their words. A vague question can land on the
wrong one, and pasting that pack wastes a whole AI session. The full spec and code are in `enhancement1.1.md`.
In short:
- `search`, `check`, `pack` and `explain` record the question and top 3 files in `<home>/history.jsonl`.
- Area words come from paths: folders plus camelCase parts, lowercased, trailing `s` removed, ignoring words in
  ≥ 50% of paths. Focus words are those in ≥ 60% of the last 6 questions (8 h window, older ones ×0.7).
- Drift: the new top file lacks a focus word and brings an area word you haven't used. Not drift if your question
  names the missing focus word, or the file is shared code.
- Report the new area (the word in the same path position as the focus word), and the closest file in your area.
- A drifted question doesn't move focus until a different question lands in the same new area.
- On drift, `check --copy` and `pack --copy` don't copy (pack exits 3) unless `--force`, or unless you named the
  new area yourself. `pack` and `explain` print the warning to stderr.

## Ask and hand-off (`handoff.py`): no copy and paste

`smarttoken "task" [all|REPO]` (no target: the registered repo containing the current folder, else all) builds the pack with no practical budget (50k), wraps it in a short agent
prompt, counts tokens, and picks the smallest tier in `ask_tiers` that fits: tier 1 "This will take ~N tokens.",
middle tiers "Base limit exceeded. Requires T tokens.", last tier "Large context detected. Requires T tokens.",
then `Proceed? [Y/n]`. Above the last tier it prints "Context is N tokens (Max limit T). Please narrow your
prompt." and exits 4. The default answer is N when confidence is LOW or the focus check warned. On yes it saves
the prompt to `<home>/packs/` and sends it with `--to` (default `send_to`): `vscode` = `code chat -m agent
"<task>" -` with the prompt on stdin; `cursor` = `cursor://anysphere.cursor-deeplink/prompt?text=` (10,000-char
URL limit, so long prompts become a pointer to the saved file); `cursor-agent`, `copilot -i`, `claude` run in
the repo folder with the prompt as an argument. A missing CLI falls back to the clipboard. `--pack auto` (the default) sends a pointer (paths and line ranges, no
code) to `vscode`, `cursor-agent`, `copilot` and `claude`, and the full pack to paste, print and Cursor chat.
`--pack full` or `--pack pointer` overrides it. Full code is in `enhancement1.2.md` and `enhancement1.3.md`.

## Tests (pytest)

scan respects gitignore and skip dirs; roles; search ranks the right file for the fixture questions and does
not boost `fix-*.sh` for "fix" queries; stopwords removed; pack stays ≤ budget and redacts the fake secret;
measure bracket-path matching; symbols (Go method range, TSX arrow component, regex fallback, whole-function
window, one-line C# interface method borrows its parent); confidence (fixture webhook question is HIGH, a
kafka/graphql question is LOW and names "kafka" as missing, a stemmed word like "signatures" isn't missing);
`measure` with no `--expect` prints NOT CHECKED; CLI `eval --fixture` exits 0.
Set `norecursedirs = ["fixtures"]` so the fixture's own tests aren't collected.

## Phases

1. Skeleton: package, `init`, `version`, config, fixture repo.
2. `add`, `repos`, `index`, `map`.
3. `search` with the ranking rules above.
4. `pack`, redaction, `--copy`, hard budget.
5. `measure`, `eval`, `report`, `check`, then `focus` (from `enhancement1.1.md`), then `ask` (from `enhancement1.2.md`),
   then the packet fixes from `enhancement1.3.md`:
   - quoted-phrase boost;
   - strong files get a window first;
   - drop overlapping windows;
   - change-word task type;
   - never return credential files;
   - folder-rule fix in measure.
6. Tree-sitter symbols in packs and `explain`.

Done when `eval --fixture` is 4/4, and a 5–10 question gold set on a real work repo passes. **Record that
repo's first score before tuning anything.** Any ranking fix must be generic (no repo-specific paths), and must
not break the fixture.
