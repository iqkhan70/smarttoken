# Smart Token vs. TraditionalEats — Explained Simply

## The goal

You recently joined a new organization, so you often need to explore unfamiliar code repositories before you can safely fix bugs or build features.

**Smart Token is meant to help you find the small set of code files that matter for a task.** Instead of giving an AI tool the entire repository, it creates a smaller, focused packet of relevant code and context.

TraditionalEats (Eats) is the test project because it is a real codebase whose important file locations are already known. That lets us check whether Smart Token finds the right files instead of merely assuming it works.

## The basic idea: don't give the AI the whole library

Imagine the repository is a library with 781 books.

- **Full repository dump:** give the AI all 781 books.
- **Manual search:** search the library, then read every potentially relevant book in full.
- **Smart Token pack:** search for the likely relevant material and create a short packet to give the AI.

The packet is useful only if it contains the right code. A tiny packet that misses the important file is not a success.

## What the first measurements tell us

The test indexed **781 tracked files** on the `dev` branch.

| Approach | Approximate size | What it means |
|---|---:|---|
| Dump the whole repository | 1.2 million tokens | Lots of context; expensive and unwieldy |
| Smart Token packet | 3,000–4,000 tokens | Much smaller context for one task |
| Estimated reduction | About 99.7% | The packet is dramatically smaller |

At the stated example rate of **$3 per million tokens**, a 1.2-million-token dump is about **$3.60**, while a 4,000-token packet is about **$0.012** (roughly one cent). These are estimates for the input-token comparison, not a guarantee of the total cost of an AI session.

**Important:** saving tokens is only half the job. Smart Token must also find the correct files.

### The first seven-task result

| Task | Correct file found? |
|---|---|
| Stripe payment intent | No — `PaymentService.cs` was missed |
| JWT login | Yes — `AuthService.cs` |
| Order-ready notification | Yes — `OrderStatusEventHandler.cs` |
| Vendor chat hub | Yes — `VendorChatHub.cs` |
| Mobile cart | Yes — `cart.ts` |
| Mobile BFF | Yes — `MobileBffController.cs` |
| Request ride | No — `request-ride.tsx` was missed |

Result: **5 out of 7 tasks passed.** That is a useful first result, but not good enough to claim that Smart Token always finds the right file.

## Why a task can fail even when the file exists

Consider this question:

> Where is `CreatePaymentIntentAsync` implemented for Stripe?

The correct file is:

`src/services/TraditionalEats.PaymentService/Services/PaymentService.cs`

The search may treat every word in the question as required. The file might mention `CreatePaymentIntentAsync` and Stripe but not the word `implemented`. If the search requires all the words to match, it can miss the correct file and return unrelated files.

This is a **search/query problem**, not proof that the repository index is broken.

The first fix to investigate is query shaping and stopwords. In plain English:
- Recognize unimportant words such as “where,” “is,” and “implemented.”
- Search using the distinctive code symbol, `CreatePaymentIntentAsync`.
- Keep useful terms such as `Stripe` when they help narrow the results.
- Check whether the correct file ranks near the top.

Do not jump straight to embeddings, MCP, or a TUI. First fix and measure the simple retrieval problem.

## What each Smart Token command does

### 1. `repos` — what repositories are known?

Lists repositories that Smart Token knows about.

### 2. `map Eats` — give me the lay of the land

Shows a high-level map of the repository. Think of it as an onboarding guide: which parts exist and how the project is organized.

### 3. `search` — find likely files

Searches the index for a word, phrase, or code symbol.

For example, searching for `CreatePaymentIntentAsync` should find the payment service file. Start with the exact symbol when you know it; then try a natural-language question.

### 4. `measure` — compare the approaches

Builds a packet for one task and reports measurements, including whether the expected file was found.

For this test, compare:
- **Full dump:** the whole repository as context.
- **Search-hit files in full:** a careful manual approach that reads all candidate files completely.
- **Smart Token pack:** the smaller selected packet.

The key result is **HIT or MISS**. A low token count with a MISS is not a good outcome.

### 5. `eval` — run the known test questions

Runs the seven prepared tasks and checks whether each task's expected “gold” file is found. A **gold file** simply means the known correct file for that task.

This gives you a repeatable score. After changing search behavior, run the same suite again to see whether the result improved without making the packet too large.

### 6. `pack --copy` — prepare context for Cursor

Creates a compact code packet and copies it so you can paste it into Cursor.

Try using that packet for a real task. The practical question is: can Cursor help you make the change safely without you manually attaching a large number of files?

### 7. `brief` — create a longer onboarding note

Creates a more explanatory packet for a broad question, such as how checkout flows from the mobile cart to `PaymentService`.

Use `pack` when you need focused context for a specific change. Use `brief` when you are trying to understand a feature or flow across the codebase.

## Run the test step by step

These commands assume the Smart Token project and its virtual environment are already set up on your Mac.

### Step 1: Open the project and select the existing Eats index

```bash
cd /Users/mohammedkhan/iq/agents/smarttokenapp
source .venv/bin/activate

export ST_HOME="$PWD/.smarttoken/eats"
```

`ST_HOME` tells Smart Token where to find the Eats index and its local data. Using this location keeps the Eats test separate from the default Smart Token data under `~/.smarttoken`.

The index is expected to represent **Eats, branch `dev`, about 781 files**. Since the index already exists, do not rebuild it unless you intentionally want a clean test.

### Step 2: Check the repository map and search

```bash
smarttoken --home "$ST_HOME" repos
smarttoken --home "$ST_HOME" map Eats

smarttoken --home "$ST_HOME" search "CreatePaymentIntentAsync"
smarttoken --home "$ST_HOME" search "JwtSecurityToken"
smarttoken --home "$ST_HOME" search "VendorChatHub"
smarttoken --home "$ST_HOME" search "order ready notification"
```

Start by checking whether the exact payment method name finds the correct file. Then compare that with the longer English query. If the exact symbol works but the English question fails, that points toward query handling.

### Step 3: Measure one task

```bash
smarttoken --home "$ST_HOME" measure \
  "where is CreatePaymentIntentAsync implemented for Stripe" \
  --repo Eats \
  --expect src/services/TraditionalEats.PaymentService/Services/PaymentService.cs \
  --budget 4000
```

Read both the token counts and the final HIT/MISS result. The budget is the target maximum packet size; it does not guarantee retrieval accuracy.

### Step 4: Run all seven tests

```bash
smarttoken --home "$ST_HOME" eval --tasks evals/eats_tasks.json --budget 4000
```

For a report you can save or share:

```bash
smarttoken --home "$ST_HOME" eval --tasks evals/eats_tasks.json --markdown
```

Check which tasks pass, which fail, and which files are missing. Aim for at least **5/7**, understand both misses, and ideally improve the score before adding more features.

### Step 5: Try it in a real coding session

For a focused task:

```bash
smarttoken --home "$ST_HOME" pack \
  "fix retry on Stripe payment capture" \
  --repo Eats --budget 4000 --copy
```

For broader onboarding:

```bash
smarttoken --home "$ST_HOME" brief \
  "how does checkout go from mobile cart to PaymentService" \
  --repo Eats --budget 4000
```

Use the copied packet in Cursor. Verify the files and logic yourself; a packet is context to help you reason, not proof that the proposed code change is correct.

## How to decide whether Smart Token is useful

Use this checklist before working on ideas in `ProposedStuff.txt`.

- [ ] **Repository map:** Would `map` have helped you understand Eats in your first week?
- [ ] **Retrieval quality:** Does the seven-task evaluation reach at least 5/7, and do you understand the two misses?
- [ ] **Token budget:** Does the packet stay within about 4,000 tokens and remain far smaller than the full repository?
- [ ] **Real work:** Can you complete one actual Cursor task using the packet without attaching half of `src/` manually?

The goal is not to win a token-count contest. It is to **reduce the time and context needed to understand unfamiliar code while still finding the right implementation**.

## The next build step

Only after completing the test above:

1. Fix stopwords and query shaping so natural-language questions do not accidentally exclude the correct files.
2. Re-run the same seven-task evaluation.
3. Compare the new HIT/MISS results and token counts with the original baseline.
4. Then decide whether the later ideas in `ProposedStuff.txt`—such as symbol indexing, MCP, or a TUI—are worth building.

Keeping the same gold tasks before and after a change is important: it lets you tell whether the improvement actually helped.

## One-line summary

**Smart Token should act like a knowledgeable guide to a large codebase: find the right files, package only the useful context, and prove it works on known tasks before adding complexity.**
