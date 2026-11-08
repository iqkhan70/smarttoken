# Test Smart Token against TraditionalEats

Do this **before** adding ideas from `ProposedStuff.txt`. Eats is a real clone we already know, so it is the first honest with-vs-without check.

Clone path: `/Users/mohammedkhan/iq/Eats`  
Gold tasks: [`eats_tasks.json`](eats_tasks.json)

## What we measured

Indexed **781 files** on branch `dev` (mostly C# and TypeScript). Dumping the clone is about **1.2M tokens (~$3.62** at $3/1M). A pack is about **3–4k tokens (~$0.01)**.

After retrieval fixes (stopwords, no `fix-*.sh` bonus, smaller snippets, Expo `[param]` paths), the gold suite is **7/7 PASS**:

| Task | Pack tokens | vs dump | Gold file | Verdict |
|---|---:|---:|---|---|
| Stripe payment intent | 3935 | 99.7% | `PaymentService.cs` | PASS |
| JWT login | 2881 | 99.8% | `AuthService.cs` | PASS |
| Order-ready notify | 3323 | 99.7% | `OrderStatusEventHandler.cs` | PASS |
| Vendor chat hub | 2631 | 99.8% | `VendorChatHub.cs` | PASS |
| Mobile cart | 2877 | 99.8% | `cart.ts` | PASS |
| Mobile BFF | 2995 | 99.8% | `MobileBffController.cs` | PASS |
| Request ride | 3338 | 99.7% | `request-ride.tsx` | PASS |

Re-run anytime:

```bash
smarttoken --home "$ST_HOME" eval --tasks evals/eats_tasks.json --budget 4000
```

## 1. One-time setup

```bash
cd /Users/mohammedkhan/iq/agents/smarttokenapp
source .venv/bin/activate

# Reuse the index already built (skip add/index if this folder exists)
export ST_HOME="$PWD/.smarttoken/eats"

# Only if you want a clean rebuild:
# smarttoken --home "$ST_HOME" init
# smarttoken --home "$ST_HOME" add /Users/mohammedkhan/iq/Eats
# smarttoken --home "$ST_HOME" index
```

Expected index line: **Eats**, branch `dev`, about **781 files**. `node_modules` / `bin` / `obj` stay out because indexing uses `git ls-files`.

Without `--home`, data goes to `~/.smarttoken`. The Eats run above is isolated under `.smarttoken/eats` in this repo (gitignored).

## 2. Local-only (free) — does it understand Eats?

```bash
smarttoken --home "$ST_HOME" repos
smarttoken --home "$ST_HOME" map Eats
smarttoken --home "$ST_HOME" search "CreatePaymentIntentAsync"
smarttoken --home "$ST_HOME" search "JwtSecurityToken"
smarttoken --home "$ST_HOME" search "VendorChatHub"
smarttoken --home "$ST_HOME" search "order ready notification"
```

`search "CreatePaymentIntentAsync"` should rank

`src/services/TraditionalEats.PaymentService/Services/PaymentService.cs`.

If a long English question misses that file, the packer is the problem, not the index.

## 3. With vs without — one real task

```bash
smarttoken --home "$ST_HOME" measure \
  "where is CreatePaymentIntentAsync implemented for Stripe" \
  --repo Eats \
  --expect src/services/TraditionalEats.PaymentService/Services/PaymentService.cs \
  --budget 4000
```

Read three rows:

- **Dump the clone** — ~1.2M tokens. This is “@ the repo”.
- **Paste every search-hit file whole** — tens of thousands of tokens. This is a careful human.
- **smarttoken pack** — ~4k tokens.

Then the **HIT/MISS** line. Cheap plus MISS is not useful.

## 4. The gold suite (7 questions you already know)

```bash
smarttoken --home "$ST_HOME" eval --tasks evals/eats_tasks.json --budget 4000
```

Pasteable report:

```bash
smarttoken --home "$ST_HOME" eval --tasks evals/eats_tasks.json --markdown
```

Gold paths are real Eats files: Stripe, JWT, order-ready, vendor chat, mobile cart, Mobile BFF, request-ride.

## 5. Use it the way you would at a new org

```bash
# Tight packet to paste into Cursor
smarttoken --home "$ST_HOME" pack \
  "fix retry on Stripe payment capture" \
  --repo Eats --budget 4000 --copy

# Longer onboarding note
smarttoken --home "$ST_HOME" brief \
  "how does checkout go from mobile cart to PaymentService" \
  --repo Eats --budget 4000
```

Paste into Cursor and see if you can work without `@`-ing half of `src/`.

## 6. How to decide “this is useful”

Stay off `ProposedStuff.txt` until you can say yes or no to these:

1. **`map`** — would this have helped on week 1 of Eats?
2. **`eval`** — gold-file recall. Current bar after ranking fixes: **7/7**.
3. **`measure`** — pack stays under 4000 and is far cheaper than dump (already true: **~99.7%**).
4. **`pack --copy`** — one real Cursor session succeeds from the packet.

## 7. Second repo check (golangpoc)

To prove the 7/7 wasn't tuned to Eats, `evals/golangpoc_tasks.json` has 8 questions about a different repo
(Go + HTMX, not a git repo), written before running the tool.

```bash
smarttoken --home "$ST_HOME" add /Users/mohammedkhan/iq/golangpoc
smarttoken --home "$ST_HOME" index --repo golangpoc
smarttoken --home "$ST_HOME" eval --tasks evals/golangpoc_tasks.json --budget 4000
```

- First run, no changes: **5/8**. The misses came from search rules that only suited Eats: requiring every
  search word, an over-eager path filter, and no handling for word endings like `adding` vs `AddItem`.
- After generic fixes: golangpoc **8/8** (~95% cheaper than the dump), Eats still **7/7**, fixture 4/4.

For any new repo: write 5–10 questions plus the files you know answer them, then run `eval`. A repo's first
score, before any tuning, is the honest number to quote.

## 8. Test Phase 4 (whole-function snippets)

**Step 1 — reinstall** (Phase 4 added the `tree-sitter-language-pack` dependency):

```bash
cd /Users/mohammedkhan/iq/agents/smarttokenapp
source .venv/bin/activate
pip install -e '.[dev]'
export ST_HOME="$PWD/.smarttoken/eats"
```

**Step 2 — automated checks.** Expect 23 passed, then Eats 7/7, golangpoc 8/8, fixture 4/4:

```bash
pytest -q
smarttoken --home "$ST_HOME" eval --tasks evals/eats_tasks.json --budget 4000
smarttoken --home "$ST_HOME" eval --tasks evals/golangpoc_tasks.json --budget 4000
smarttoken eval --fixture
```

**Step 3 — look at one snippet by eye:**

```bash
smarttoken --home "$ST_HOME" explain AddItem --repo golangpoc
```

Under `## Snippets` you should see three blocks: `handler.go` `Routes` (the URL wiring), `handler.go` `addItem`,
and `service.go` `AddItem` L28-80. The last one is the whole method, from `func` to its closing `}`.

**Step 4 — check the snippet labels in a C# pack:**

```bash
smarttoken --home "$ST_HOME" pack "where is CreatePaymentIntentAsync implemented for Stripe" --repo Eats --budget 4000
```

Each snippet has a label line under its header. Good signs:

- `_method `CreatePaymentIntentAsync`; source_` means the whole method was sent.
- `_inside method `StripeWebhook` L33-126; source_` means the method was too long to send whole, and the label says where the window came from.
- Under `## Symbols / callers`, each method appears once with a range like `L238-254`, not twice.

**Step 5 — the real test (costs a few cents of paid tokens).** Pick a small change you actually want, in Eats or golangpoc:

```bash
smarttoken --home "$ST_HOME" pack "add a max quantity of 20 per item when adding to the cart" --repo golangpoc --budget 4000 --copy
```

Open a **new** Cursor chat, paste the pack, and send it. Don't `@` any files or folders. Then score it:

| Question | Pass if |
|---|---|
| Did it edit the right file and function on the first try? | yes, without you naming the file |
| Did it ask to open other files? | none, or one or two it reasoned about from the pack |
| Did the change build/run? | `go build ./...` (golangpoc) or the Eats build passes |
| Tokens | Cursor's usage shows roughly the pack size plus the answer, not tens of thousands |

Run it 2–3 times on different tasks. If most pass, the core loop works and MCP (Cursor calling `pack` itself
instead of you pasting) is worth building next. If the model keeps asking for a file, note which one: that
task's question and file belong in the gold set, and the ranking needs another look.

## 9. Know before you ask: `smarttoken check`

```bash
smarttoken --home "$ST_HOME" check "how does the vendor chat hub send messages" --repo Eats
```

Prints a confidence level (HIGH/MEDIUM/LOW) with reasons, the top 3 files, the pack cost against attaching the hit
files, and advice. No paid tokens are used.

Calibrated on the 15 gold questions plus 12 questions about topics that aren't in the repos
(`confidence_negatives.json`: kafka, graphql, saml, ...):

| | HIGH | MEDIUM | LOW |
|---|---:|---:|---:|
| Good questions (15) | 13 | 2 | 0 |
| Not-in-repo questions (12) | 0 | 3 | 9 |

A MEDIUM lists the words it couldn't find anywhere; if that word is the subject (e.g. "kafka"), the repo doesn't
have it. These levels were tuned on this same set, so recheck them on a work repo's gold set before trusting them.

`measure` without `--expect` now says **NOT CHECKED** instead of a misleading PASS.

## Next

Retrieval for the Eats and golangpoc gold sets is green, and Phase 4 (tree-sitter symbols) is in: snippets now
follow function and class boundaries. Recall is unchanged; Go packs are about 18% smaller, C# packs about the same.
MCP / TUI in `ProposedStuff.txt` wait until one real Cursor session from a pack works.
