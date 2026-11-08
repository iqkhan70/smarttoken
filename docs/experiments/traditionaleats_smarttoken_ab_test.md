# Smart Token A/B test on TraditionalEats: one real change, without vs with Smart Token

Date: 2026-10-21. Runner: GitHub Copilot CLI 1.0.98, headless (`copilot -p`), one session per run.

**Round 2 (after packet fixes, order reversed) went the other way: +24.8% tokens with Smart Token; see section E.**

**Round 1 result:** both runs made the same correct five-line fix and passed all 12 acceptance checks.
With Smart Token the run used **103,545 reported tokens vs 145,839 (−29.0%)** and **0.336 vs 0.401 AI credits (−16.2%)**.
That's from one run each (n = 1), so treat it as a single data point, not a definitive saving.

## A. Test definition

### Change

**Reject out-of-range ratings when a review is updated.**
`PUT /api/review/{reviewId}` (`ReviewController.UpdateReview`, then `ReviewService.UpdateReviewAsync`) saves any
`Rating` value, while review *creation* already rejects ratings outside 1–5. Before the experiment, a harness run
against the unchanged code confirmed the bug: ratings 0, 6 and −1 returned 200 and were stored.

Why it was chosen (before either run):
- It's a real feature gap (creation validates, update doesn't).
- Finding it means locating the right files among 14 services and 2 BFFs that all have `UpdateReview` endpoints.
- It's small, has no migrations or external services, and the result can be checked objectively.

### Acceptance criteria

Checked by a temporary harness outside Eats (`/tmp/abtest/harness`). It references the real `ReviewService` project
and drives the real `ReviewController` + `ReviewService` against an EF Core in-memory database, with a stub HTTP client
for the Elo call.

| ID | Criterion |
|---|---|
| AC1 | Update with rating 0, 6 or −1 returns HTTP 400 |
| AC2 | …with the message "Rating must be between 1 and 5" |
| AC3 | …and the stored rating is unchanged (stays 3) |
| AC4 | Update with no rating (comment only) returns 200 and saves the comment |
| AC5 | Update with rating 1 or 5 returns 200 and saves it |
| Build | `dotnet build` of `TraditionalEats.ReviewService.csproj`: 0 errors |

That's 12 harness checks in total. Against the unchanged code, 9 fail (AC1–AC3 × 3 ratings) and 3 pass (AC4, AC5).

### Task wording, identical in both runs

```
In this TraditionalEats repository, updating an existing review accepts any rating value.
Change it so that when a user updates a review, a rating outside 1 to 5 is rejected: the review update API endpoint
must return HTTP 400 with the message "Rating must be between 1 and 5", and the stored review must stay unchanged.
An update with no rating (for example comment only) must still work, and valid ratings 1 to 5 must still be saved.
Make the smallest correct change, then run `dotnet build` on the affected project to check it compiles.
Do not commit, push, or change git state.
```

### Starting state of Eats (`/Users/mohammedkhan/iq/Eats`)

```
branch: dev
head: 59ff18f72f06b2972c1d2372a50fdcb3e819bc43
status_lines: 0          (no modified, staged or untracked files)
stash: 0
reviewservice_sha: 43843b88adfdbe9f      (sha256 prefix, Services/ReviewService.cs)
reviewcontroller_sha: c6db6f918cacf614   (sha256 prefix, Controllers/ReviewController.cs)
```

The last Eats commit (2026-08-06) is older than the Smart Token index (2026-10-19) and the tree was clean, so the
existing index at `.smarttoken/eats` was current. It wasn't rebuilt.

### Model and measurement

- **Tool:** Copilot CLI, the same command for both runs (`/tmp/abtest/run_copilot.sh`):
  `copilot -p "<prompt>" --model auto --auto-tier balance --allow-all-tools --disable-builtin-mcps --no-ask-user
  --deny-tool 'shell(git commit)' 'shell(git push)' 'shell(git checkout)' 'shell(git reset)' 'shell(git restore)'
  'shell(git stash)' 'shell(git clean)' 'shell(git switch)' 'shell(smarttoken)' --output-format json
  --usage-output-file usage.json`, run from the Eats root.
- **Model:**
  - I tried to pin a model. `gpt-6-luna`, `mai-code-1.1-flash` and `claude-sonnet-4.6` were all rejected by this
    account ("not available"), so both runs used `auto` with the same tier.
  - The router picked **`gpt-6-luna`** for the main agent in both runs.
  - Run A also started Copilot's built-in search subagent, which used model `copilot-search-a`.
  - Reasoning effort can't be set with `auto`. Cursor Agent CLI was tried first, but headless mode failed with
    "Authentication required", so it wasn't used.
- **Token capture:** the CLI's own final usage file (`--usage-output-file`), which matches the last
  `session.usage_checkpoint` event. Nothing was estimated from character counts. The only local count is the Smart
  Token packet size, labelled as such.
- **How the CLI reports tokens:**
  - Per model, `inputTokens` includes cached tokens: `inputTokens = fresh input + cacheRead + cacheWrite`.
    For example, A main: 18 + 85,899 + 20,925 = 106,842.
  - The session-level `tokenDetails` splits the four buckets.
  - **Total reported tokens = fresh input + cache read + cache write + output** (the same as Σ per-model
    `inputTokens` + `outputTokens`).
  - `reasoningTokens` is listed separately. It's treated as part of output and not added again.
- **Billing:** Copilot reports `totalNanoAiu` (AI credits × 10⁹) and premium requests. No published $/token rate is
  available for `gpt-6-luna` through Copilot, so dollar cost is marked Unavailable.

## B. Results

| Metric | Without Smart Token (A) | With Smart Token (B) |
|---|---:|---:|
| Input tokens (fresh, not cached) | 16,927 (main 18 + search subagent 16,909) | 15 |
| Output tokens | 2,300 (main 1,073 + subagent 1,227) | 1,149 |
| ↳ of which reasoning | 268 | 495 |
| Cache-read tokens | 105,687 (main 85,899 + subagent 19,788) | 87,060 |
| Cache-creation tokens | 20,925 | 15,321 |
| **Total reported tokens** | **145,839** | **103,545** |
| Main-agent tokens only (`gpt-6-luna`) | 107,915 (6 calls) | 103,545 (5 calls) |
| Search-subagent tokens (`copilot-search-a`, 0 credits) | 37,924 (4 calls) | 0 (not used) |
| AI credits (`totalNanoAiu` / 10⁹) | 0.4013 | 0.3362 |
| Premium requests | 1 | 1 |
| Model API time / wall time | 24.7 s / 33.9 s | 15.9 s / 22.2 s |
| Smart Token packet tokens | Not applicable | 3,931 (full prompt incl. task + wrapper, cl100k, local count) |
| Additional context tokens (fallback beyond the packet) | N/A | Not separately reported. B read the whole `ReviewController.cs` (1,612 tokens by local count), which the packet listed but didn't include; its cost is inside the totals above |
| Smart Token local cost | N/A | 0 model tokens; `smarttoken ask` took ~5 s locally; indexing was done earlier (0 model tokens) |
| Files inspected (main agent) | 5 files + 20 search/grep/glob calls + 1 search subagent | 3 files + 1 grep + 1 glob |
| Files modified | 1 (`ReviewController.cs`, +5 lines) | 1 (`ReviewController.cs`, +5 lines) |
| Tests passed/failed (acceptance harness) | 12 / 0 | 12 / 0 |
| Build | 0 errors (8 existing warnings) | 0 errors (8 existing warnings) |
| Acceptance criteria satisfied | Yes, all | Yes, all |
| Estimated model cost ($) | Unavailable (credits shown above) | Unavailable (credits shown above) |

Files inspected:
- **A:**
  - `MobileBffController.cs` (L5870–6660)
  - `WebBffController.cs` (L4880–5720)
  - `ReviewController.cs` (L120–320, L130–175)
  - `ReviewService.cs` (L1–520, L215–270)
  - `TraditionalEats.ReviewService.csproj`
  - plus whatever the search subagent read internally (not listed by the CLI)
- **B:**
  - `ReviewService.cs` (L1–30, L210–260)
  - `ReviewController.cs` (whole file)
  - `TraditionalEats.ReviewService.csproj`

## C. Analysis

**Total reduction:**
- Absolute: 145,839 − 103,545 = **42,294 tokens**.
- Percentage: (145,839 − 103,545) / 145,839 × 100 = **29.0%**.
- Billed: (0.4013 − 0.3362) / 0.4013 = **16.2% fewer AI credits**.
- Premium requests: 1 vs 1, no difference.

**Where the saving comes from:**
- **Main agent alone:** it barely changed, 107,915 vs 103,545 (−4,370, **−4.0%**). Every model call resends a large
  fixed prefix: the system prompt plus about 40 tool schemas (~14.7k tokens), mostly as cache reads. So the per-call
  overhead dominates both runs. B made one fewer call.
- **Search subagent:** most of the token difference is the search subagent A started to locate the endpoint
  (37,924 tokens, billed at 0 credits). With the packet naming `ReviewService.cs` and `ReviewController.cs`, B went
  straight to the files.
- **Billed credits:** these fell less than the token totals (16% vs 29%) because the subagent's tokens were free.
  The billed saving comes from fewer main-agent calls, about 5.6k fewer cache-write tokens and half the output.
- **Packet size vs consumption:** the 3,931-token packet isn't what B consumed. B consumed 103,545 tokens, of which
  the packet is ~4%.

**Were the changes equivalent and correct?** Yes. Both added the same guard,
`if (dto.Rating.HasValue && (dto.Rating.Value < 1 || dto.Rating.Value > 5)) return BadRequest(new { message = "Rating must be between 1 and 5" });`,
to `ReviewController.UpdateReview`. A placed it before the `try`, B inside it; the behaviour is identical. Both
passed 12/12 checks and built cleanly. Neither added the check in `ReviewService.UpdateReviewAsync`, so other
callers of the service still bypass it. The task asked for endpoint behaviour, so this meets the criteria; it's
the same in both runs.

**Did Smart Token miss relevant code? Partly.** The packet got the service method right (`UpdateReviewAsync`,
whole). But:
1. **Missing:** `ReviewController.cs`, the file that was actually changed, was listed under "Read this first" but
   had **no snippet**. B had to read the whole file on its own (1,612 tokens by local count). That was the only
   fallback.
2. **Off-target:** about **2,643 of the 3,931 packet tokens (67%)** didn't bear on the change: three overlapping
   `EloRatingService.cs` windows (L36–60, L38–62, L41–65; 933 tokens) and the star-rating UI components
   `ReviewRating.tsx` / `ReviewRating.razor` (1,710 tokens). The two `CreateReviewAsync` windows also overlap
   (L68–92, L71–95).
3. **Wrong task type:** the task was classed as "understand" rather than "change", so the packet's
   "Suggested Cursor ask" says "explain how this works". Copilot ignored it, but it's wrong guidance.
4. **Confidence:** MEDIUM (51% of task words in the top files). That was a fair warning, given the missing controller.

**Is it a fair comparison? Reasonably, with limits:**
- **n = 1 per arm.** Agent runs are nondeterministic. A could take fewer search steps on a rerun, or B more. Several
  paired runs are needed before quoting a stable percentage.
- **Model not pinned.** The account only allows `auto`. Both main agents were `gpt-6-luna`, but A's router also
  started a search subagent and B's didn't. That's arguably the effect being measured (less searching), but it's
  routing behaviour, not a controlled variable.
- **Cache order:** B started 3 minutes after A (20:57:19 → 21:00:20 UTC), within the 30-minute cache TTL. B could have reused the shared
  system and tool prefix cached by A, which favours B somewhat. B still wrote 15,321 cache tokens. Alternating
  the order across repeated runs would control for this.
- **Prompt differs by design:** B's prompt carries the packet plus a short instruction to start from the listed
  files. That's the Smart Token workflow, not a confound, but it means B's first call was larger.
- **Task wording helps both runs:** it names "the review update API endpoint", which makes A's search easier than a
  vaguer request would. The saving could be larger on vaguer tasks; that wasn't tested.
- **Harness:** I wrote it after choosing the change and before either run, and validated it against the unchanged
  code (9 fail / 3 pass). The same harness was applied to both diffs.

**What this says about Smart Token:**
- **What it saved:** the cost of *finding* code (the search subagent and extra calls). Per-call agent overhead
  stays the same.
- **Biggest packet issues** this run exposed:
  - The changed file got no snippet.
  - Overlapping windows from the same method were kept separately.
  - Off-topic UI files got about 1.7k tokens.
  - A change request was classed as "understand".
- These are generic fixes to make next. They aren't applied here, so this run measures the tool as it was.

## E. Round 2: after the packet fixes (same day, 21:25–21:27 UTC)

### What changed in Smart Token

These are generic rules, nothing specific to Eats. The fixture is 4/4, golangpoc 8/8 and Eats 7/7. On confidence,
none of the 12 unanswerable questions rates HIGH and none of the 15 answerable ones rates LOW. There are 51 unit tests.

1. **Quoted phrases:** text the task quotes (`"Rating must be between 1 and 5"`) is matched as an FTS phrase, and
   files containing it get +25.
2. **Strong files first:** files scoring ≥ 75% of the top file each get their best window before any file gets a
   second one.
3. **Overlap:** a window more than 40% covered by an already-chosen window from the same file is dropped.
4. **Symbol names:** windows whose symbol name matches task words rank higher, at +4 per word and +6 for two or more
   words (`UpdateReview`).
5. **Change requests:** "change", "reject", "validate", "prevent", "must", "make", "remove", "rename" and "replace"
   now mark a change request. Matching uses word stems, so "address" no longer counts as "add".
6. **Docs:** docs are down-weighted (×0.6) for change requests.
7. **Credential files:** files like keystore, `.env`, `.pem`, `.key`, `.p12`, `.jks` and `id_rsa`, and data files
   named secret, credential or password, are never returned. This was found because `keystore.release.txt`
   (committed in Eats) entered a draft packet.
8. **Measure bug:** a folder rule like `obj/` no longer matches the word "Object" in code. That false positive had
   surfaced as a "leak" in the Stripe eval.

### Packet before and after (the same task, the same index)

| | Round 1 packet | Round 2 packet |
|---|---|---|
| Size | 3,931 tokens | 3,925 tokens |
| `ReviewController.UpdateReview` (the endpoint) | missing | included (L140–159) |
| `ReviewService.UpdateReviewAsync` | included | included |
| Existing validation pattern (`CreateReview` / `CreateReviewAsync`) | included (service, two overlapping windows) | included (controller) |
| Overlapping windows (> 40% shared) | 3 Elo + 2 CreateReviewAsync | none |
| Task type / suggested ask | "understand" / "explain how this works" | "enhance" / "implement" |
| Off-target snippet tokens | ~3,300 | ~2,300 (Elo, UpdateRestaurantRating, GetRestaurantRating, ReviewRating UI) |

### Results (B2 ran first this time, to reverse the cache order)

| Metric | Without Smart Token (A2) | With Smart Token (B2) |
|---|---:|---:|
| Input tokens (fresh) | 18 | 21 |
| Output tokens (reasoning) | 1,151 (421) | 1,417 (655) |
| Cache-read tokens | 103,055 | 133,503 |
| Cache-creation tokens | 15,680 | 14,724 |
| **Total reported tokens** | **119,904** | **149,665** |
| Model calls (`gpt-6-luna`; no search subagent in either) | 6 | 7 |
| AI credits | 0.3568 | 0.3886 |
| Premium requests | 1 | 1 |
| Model API time | 18.6 s | 22.0 s |
| Smart Token packet tokens | N/A | 3,925 |
| Tool calls | 9 (1 `rg`, 1 glob, 3 views, patch, build, git) | 9 (2 views, 1 `rg`, 1 glob, patch, build, git) |
| Files read | controller L1–175, service L1–270, csproj | controller L1–175, service L1–260 |
| Files modified | 2 (service throws `ArgumentException`; controller maps it to 400) | 1 (controller guard) |
| Acceptance harness | 12 / 12 | 12 / 12 |
| Build | 0 errors | 0 errors |

**Round 2 result:** Smart Token used **29,761 more tokens (+24.8%)** and **+8.9% AI credits**.

**Why the improved packet didn't save tokens here:**
- **No search to save.** A2 needed one `rg` for the quoted message (`Rating must be between|Update.*Review|…`)
  to land on both files. The task's quoted literal makes plain search as good as Smart Token's.
- **The agent re-reads anyway.** Both runs opened the same large ranges of the controller and service before
  editing. Copilot's agent verifies before patching, so packet snippets don't replace file reads.
- **The packet is paid on every call.** The prompt (including the packet) is resent on each model call. That's
  ~3.9k tokens × 7 calls ≈ 27k cache-read tokens, which is close to the whole gap.
- **Order now favours A.** Running A2 second, within the cache window, may help A2 the way B was helped in round 1.

**Equivalence:** both are correct. A2's change is arguably better: validating in the service protects every caller,
not only this endpoint.

### Both rounds together (n = 2 per arm)

| | Without | With | Difference |
|---|---:|---:|---:|
| Total reported tokens | 265,743 | 253,210 | −12,533 (−4.7%) |
| AI credits | 0.7581 | 0.7248 | −0.0333 (−4.4%) |

Round 1 favoured Smart Token by 29% and round 2 favoured the baseline by 25%. **The run-to-run variance is larger
than the effect**, so these runs don't show a reliable saving for an agent with its own search tools, on a task that
quotes a literal. The clear case for Smart Token was round 1, where the baseline started a search subagent
(~38k tokens).

### What this implies for the tool

- **For agent hand-off** (`--to copilot`, `cursor-agent`, `vscode` agent mode), the full-snippet packet is the wrong
  shape. A **slim pointer prompt** would cost ~300 tokens per call instead of ~3,900: files, line ranges and symbol
  names (`ReviewController.UpdateReview L140–159`, `ReviewService.UpdateReviewAsync L227–249`), with no code.
  The agent would still go straight to the right lines, and re-reading them is what it does anyway.
- **Full snippets** still make sense for chat use without tools, or a single answer: one call, where the
  alternative is pasting whole files.
- **Tasks for a fairer test of Smart Token:** vaguer requests with no quoted literal, larger repos, and more than
  one run per arm, alternating order.

### Round 2 raw data

Round 2 files are in `/tmp/abtest/B2` and `/tmp/abtest/A2`: `usage.json`, `events.jsonl`, `diff.patch` and
`acceptance.txt`. The B2 prompt is saved at `.smarttoken/eats/packs/20261021-162536-in-this-traditionaleats-repository-updat.md`.

A2 diff:

```diff
diff --git a/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs b/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs
index 009b5da5..106da644 100644
--- a/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs
+++ b/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs
@@ -151,6 +151,11 @@ public class ReviewController : ControllerBase
             }
             return Ok(new { message = "Review updated successfully" });
         }
+        catch (ArgumentException ex)
+        {
+            _logger.LogWarning(ex, "Invalid review update: {Message}", ex.Message);
+            return BadRequest(new { message = ex.Message });
+        }
         catch (Exception ex)
         {
             _logger.LogError(ex, "Failed to update review");
diff --git a/src/services/TraditionalEats.ReviewService/Services/ReviewService.cs b/src/services/TraditionalEats.ReviewService/Services/ReviewService.cs
index dbc52e8e..fa19d863 100644
--- a/src/services/TraditionalEats.ReviewService/Services/ReviewService.cs
+++ b/src/services/TraditionalEats.ReviewService/Services/ReviewService.cs
@@ -226,6 +226,11 @@ public class ReviewService : IReviewService
 
     public async Task<bool> UpdateReviewAsync(Guid reviewId, Guid userId, UpdateReviewDto dto)
     {
+        if (dto.Rating.HasValue && (dto.Rating.Value < 1 || dto.Rating.Value > 5))
+        {
+            throw new ArgumentException("Rating must be between 1 and 5");
+        }
+
         var review = await _context.Reviews
             .FirstOrDefaultAsync(r => r.ReviewId == reviewId && r.UserId == userId);
```

B2 diff:

```diff
diff --git a/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs b/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs
index 009b5da5..af8a48a3 100644
--- a/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs
+++ b/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs
@@ -143,6 +143,11 @@ public class ReviewController : ControllerBase
     {
         try
         {
+            if (dto.Rating.HasValue && (dto.Rating.Value < 1 || dto.Rating.Value > 5))
+            {
+                return BadRequest(new { message = "Rating must be between 1 and 5" });
+            }
+
             var userId = Guid.Parse(User.FindFirstValue(ClaimTypes.NameIdentifier)!);
             var success = await _reviewService.UpdateReviewAsync(reviewId, userId, dto);
             if (!success)
```

A2 tool calls:

```
- rg {"pattern": "Rating must be between|Update.*Review|review.*update|Rating", "paths": ["."], "glob": "*.cs", "output_mode": "content", "-n": true, "head_limit": 100}
- glob {"pattern": "**/*.csproj"}
- bash {"command": "git status --short", "description": "Check current worktree changes"}
- view {"path": "/Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs", "view_range": [1, 175]}
- view {"path": "/Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService/Services/ReviewService.cs", "view_range": [1, 270]}
- view {"path": "/Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService/TraditionalEats.ReviewService.csproj"}
- apply_patch "*** Begin Patch\n*** Update File: src/services/TraditionalEats.ReviewService/Services/ReviewService.cs\n@@\n     public async Task<bool> UpdateReviewAsync(Guid reviewId, Guid userId, UpdateReviewDto dto)\n     {\n+     
- bash {"command": "dotnet build src/services/TraditionalEats.ReviewService/TraditionalEats.ReviewService.csproj", "description": "Build the review service project", "initial_wait": 120, "mode": "sync"}
- bash {"command": "git --no-pager diff -- src/services/TraditionalEats.ReviewService/Services/ReviewService.cs src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs && git status --short", "description": "
```

B2 tool calls:

```
- view {"path": "/Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs", "view_range": [1, 175]}
- view {"path": "/Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService/Services/ReviewService.cs", "view_range": [1, 260]}
- rg {"pattern": "class UpdateReviewDto|record UpdateReviewDto|UpdateReviewDto", "paths": ["/Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService"], "output_mode": "content", "glob": "*.cs", "-n": true, "head
- bash {"command": "git status --short", "description": "Check existing worktree changes"}
- apply_patch "*** Begin Patch\n*** Update File: /Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs\n@@\n     [Authorize]\n     public async Task<IActionResult> UpdateReview(Guid rev
- glob {"pattern": "**/*.csproj", "paths": "/Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService"}
- bash {"command": "git --no-pager diff -- src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs", "description": "Review the controller change"}
- bash {"command": "dotnet build src/services/TraditionalEats.ReviewService/TraditionalEats.ReviewService.csproj --no-restore", "description": "Build the ReviewService project", "initial_wait": 120, "mode": "sync"}
- bash {"command": "git status --short && git --no-pager diff --check", "description": "Verify only intended file changed"}
```

Eats after round 2:
- `branch dev · head 59ff18f72f06b2972c1d2372a50fdcb3e819bc43 · status_lines 0 · stash 0`
- service `43843b88adfdbe9f`, controller `c6db6f918cacf614`: identical to the pre-experiment state.
- The harness on the restored code fails 9 checks again.

## D. Raw records

### Pre- and post-experiment state

```
pre:  branch dev · head 59ff18f72f06b2972c1d2372a50fdcb3e819bc43 · status_lines 0 · stash 0 · service 43843b88adfdbe9f · controller c6db6f918cacf614
post: branch dev · head 59ff18f72f06b2972c1d2372a50fdcb3e819bc43 · status_lines 0 · stash 0 · service 43843b88adfdbe9f · controller c6db6f918cacf614
`diff pre post` → identical. Harness on restored code: 9 checks fail again (bug back, no fix left behind).
```

### Smart Token command and output (Experiment B)

```
cd /Users/mohammedkhan/iq/Eats
smarttoken --home /Users/mohammedkhan/iq/agents/smarttokenapp/.smarttoken/eats ask "$(cat /tmp/abtest/task.txt)" Eats --to print --yes
```

```
Target: Eats · Confidence: MEDIUM
  - 51% of task words are in the top files
  - not found anywhere in the index: smallest
  - functions/classes named after the task: EloRatingService, IReviewService, 
ReviewService, CreateReviewAsync, GetReviewAsync
Top files:
  - Eats/src/services/TraditionalEats.ReviewService/Services/EloRatingService.cs
(source)
  - Eats/src/services/TraditionalEats.ReviewService/Services/ReviewService.cs 
(source)
  - Eats/src/apps/TraditionalEats.MobileApp/components/ReviewRating.tsx (source)
Saved: 94.2% vs attaching the 15 hit files
Base limit exceeded. Requires 5000 tokens (~3931). Send to print.
```

The full prompt sent to Copilot in B is saved at
`.smarttoken/eats/packs/20261021-155859-in-this-traditionaleats-repository-updat.md` (15,967 bytes).

### Experiment A: diff

```diff
diff --git a/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs b/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs
index 009b5da5..59ba1d83 100644
--- a/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs
+++ b/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs
@@ -141,6 +141,11 @@ public class ReviewController : ControllerBase
     [Authorize]
     public async Task<IActionResult> UpdateReview(Guid reviewId, [FromBody] UpdateReviewDto dto)
     {
+        if (dto.Rating.HasValue && (dto.Rating.Value < 1 || dto.Rating.Value > 5))
+        {
+            return BadRequest(new { message = "Rating must be between 1 and 5" });
+        }
+
         try
         {
             var userId = Guid.Parse(User.FindFirstValue(ClaimTypes.NameIdentifier)!);
```

### Experiment B: diff

```diff
diff --git a/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs b/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs
index 009b5da5..af8a48a3 100644
--- a/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs
+++ b/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs
@@ -143,6 +143,11 @@ public class ReviewController : ControllerBase
     {
         try
         {
+            if (dto.Rating.HasValue && (dto.Rating.Value < 1 || dto.Rating.Value > 5))
+            {
+                return BadRequest(new { message = "Rating must be between 1 and 5" });
+            }
+
             var userId = Guid.Parse(User.FindFirstValue(ClaimTypes.NameIdentifier)!);
             var success = await _reviewService.UpdateReviewAsync(reviewId, userId, dto);
             if (!success)
```

### Acceptance harness output (the same for A and B)

```
PASS  AC1 rating=0 returns 400  status=400 body={"message":"Rating must be between 1 and 5"}
PASS  AC2 rating=0 message  {"message":"Rating must be between 1 and 5"}
PASS  AC3 rating=0 not saved  stored=3
PASS  AC1 rating=6 returns 400  status=400 body={"message":"Rating must be between 1 and 5"}
PASS  AC2 rating=6 message  {"message":"Rating must be between 1 and 5"}
PASS  AC3 rating=6 not saved  stored=3
PASS  AC1 rating=-1 returns 400  status=400 body={"message":"Rating must be between 1 and 5"}
PASS  AC2 rating=-1 message  {"message":"Rating must be between 1 and 5"}
PASS  AC3 rating=-1 not saved  stored=3
PASS  AC4 null rating, comment-only update  status=200 comment=comment only rating=3
PASS  AC5 rating=1 saved  status=200 stored=1
PASS  AC5 rating=5 saved  status=200 stored=5
ALL ACCEPTANCE CHECKS PASSED
```

### Experiment A: tool calls (main agent)

```
- bash {"command": "git status --short", "description": "Check existing worktree changes"}
- search_code_subagent {"query": "Review update API endpoint handling rating updates, validation, and persistence", "details": "Locate the update-review API endpoint and related request/model validation. Identify the affected project and relev
- file_search {"query": "**/*review*.{api,endpoint,route,controller,handler}*"}
- grep_search {"query": "update-review|updateReview|Review.*update", "isRegexp": true}
- file_search {"query": "**/*rating*.{api,endpoint,route,controller,handler,validation}*"}
- grep_search {"query": "rating.*update|validate.*rating|persist.*rating", "isRegexp": true}
- file_search {"query": "**/*review*.{model,entity,document,db}*"}
- file_search {"query": "**/*api*.{spec,definition,contract}*"}
- grep_search {"query": "PUT.*review|PATCH.*review|POST.*review.*update", "isRegexp": true}
- file_search {"query": "**/*validation*.{spec,rule,handler,middleware}*"}
- read_file {"filePath": "/Users/mohammedkhan/iq/Eats/src/bff/TraditionalEats.Mobile.Bff/Controllers/MobileBffController.cs", "startLine": 5870, "endLine": 6660}
- read_file {"filePath": "/Users/mohammedkhan/iq/Eats/src/bff/TraditionalEats.Web.Bff/Controllers/WebBffController.cs", "startLine": 4880, "endLine": 5720}
- read_file {"filePath": "/Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs", "startLine": 120, "endLine": 220}
- read_file {"filePath": "/Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService/Services/ReviewService.cs", "startLine": 1, "endLine": 280}
- file_search {"query": "**/ReviewController.cs"}
- file_search {"query": "**/ReviewService.cs"}
- file_search {"query": "**/UpdateReviewDto.cs"}
- grep_search {"query": "UpdateReviewDto", "isRegexp": false}
- read_file {"filePath": "/Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService/Services/ReviewService.cs", "startLine": 280, "endLine": 520}
- read_file {"filePath": "/Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs", "startLine": 220, "endLine": 320}
- file_search {"query": "**/UpdateReviewDto.{cs,record,class}"}
- grep_search {"query": "UpdateReviewDto", "isRegexp": false, "includePattern": "**/*.cs"}
- file_search {"query": "**/ReviewService.{cs,interface}"}
- file_search {"query": "**/ReviewController.{cs,interface}"}
- file_search {"query": "**/Review.{entity,db,context}"}
- grep_search {"query": "rating update|update rating|Rating \\?", "isRegexp": true, "includePattern": "**/*.cs"}
- rg {"pattern": "UpdateReviewAsync|class UpdateReviewDto|record UpdateReviewDto", "paths": ["src/services/TraditionalEats.ReviewService"], "output_mode": "content", "-n": true, "-C": 8}
- view {"path": "/Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs", "view_range": [130, 175]}
- bash {"command": "find src/services/TraditionalEats.ReviewService -maxdepth 2 -name '*.csproj' -print", "description": "Locate review service project"}
- view {"path": "/Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService/Services/ReviewService.cs", "view_range": [215, 270]}
- view {"path": "/Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService/TraditionalEats.ReviewService.csproj"}
- rg {"pattern": "Rating must be between 1 and 5|UpdateReview", "paths": ["src/services/TraditionalEats.ReviewService"], "glob": "**/*Tests*.cs", "output_mode": "content", "-n": true, "-C": 3}
- apply_patch "*** Begin Patch\n*** Update File: src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs\n@@\n     [HttpPut(\"{reviewId}\")]\n     [Authorize]\n     public async Task<IActionResult> UpdateReview(Guid
- bash {"command": "dotnet build src/services/TraditionalEats.ReviewService/TraditionalEats.ReviewService.csproj --no-restore", "description": "Build review service project", "initial_wait": 180}
- bash {"command": "git diff -- src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs && git status --short", "description": "Review the validation change"}
```

### Experiment B: tool calls (main agent)

```
- view {"path": "/Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService/Services/ReviewService.cs", "view_range": [1, 30]}
- view {"path": "/Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService/Services/ReviewService.cs", "view_range": [210, 260]}
- view {"path": "/Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs"}
- glob {"pattern": "src/services/TraditionalEats.ReviewService/*.csproj", "paths": "/Users/mohammedkhan/iq/Eats"}
- rg {"pattern": "class UpdateReviewDto|record UpdateReviewDto|UpdateReviewDto", "paths": "/Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService", "output_mode": "content", "glob": "*.cs", "-n": true, "-C": 3
- view {"path": "/Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService/TraditionalEats.ReviewService.csproj"}
- apply_patch "*** Begin Patch\n*** Update File: /Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs\n@@\n     public async Task<IActionResult> UpdateReview(Guid reviewId, [FromBody] 
- bash {"command": "dotnet build src/services/TraditionalEats.ReviewService/TraditionalEats.ReviewService.csproj", "description": "Build ReviewService project", "initial_wait": 180}
```

### Experiment A: usage file

```json
{
 "totalPremiumRequestCost": 1,
 "totalUserRequests": 1,
 "totalNanoAiu": 401291500,
 "aiCreditsStatus": "complete",
 "tokenDetails": {
  "input": {
   "tokenCount": 16927
  },
  "cache_read": {
   "tokenCount": 105687
  },
  "cache_write": {
   "tokenCount": 20925
  },
  "output": {
   "tokenCount": 2300
  }
 },
 "totalApiDurationMs": 24683,
 "sessionStartTime": "2026-10-21T20:57:19.476Z",
 "codeChanges": {
  "linesAdded": 5,
  "linesRemoved": 0,
  "filesModifiedCount": 1,
  "filesModified": [
   "/Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs"
  ]
 },
 "modelMetrics": {
  "gpt-6-luna": {
   "requests": {
    "count": 6,
    "cost": 1
   },
   "usage": {
    "inputTokens": 106842,
    "outputTokens": 1073,
    "cacheReadTokens": 85899,
    "cacheWriteTokens": 20925,
    "reasoningTokens": 268
   },
   "cacheExpiresAt": "2026-10-21T21:27:49.570Z",
   "totalNanoAiu": 401291500,
   "aiCreditsStatus": "complete",
   "tokenDetails": {
    "input": {
     "tokenCount": 18
    },
    "cache_read": {
     "tokenCount": 85899
    },
    "cache_write": {
     "tokenCount": 20925
    },
    "output": {
     "tokenCount": 1073
    }
   }
  },
  "copilot-search-a": {
   "requests": {
    "count": 4,
    "cost": 0
   },
   "usage": {
    "inputTokens": 36697,
    "outputTokens": 1227,
    "cacheReadTokens": 19788,
    "cacheWriteTokens": 0
   },
   "totalNanoAiu": 0,
   "aiCreditsStatus": "complete",
   "tokenDetails": {
    "input": {
     "tokenCount": 16909
    },
    "cache_read": {
     "tokenCount": 19788
    },
    "cache_write": {
     "tokenCount": 0
    },
    "output": {
     "tokenCount": 1227
    }
   }
  }
 },
 "agentMetrics": {
  "main": {
   "totalApiDurationMs": 19234,
   "totalNanoAiu": 401291500,
   "modelMetrics": {
    "gpt-6-luna": {
     "requests": {
      "count": 6,
      "cost": 1
     },
     "usage": {
      "inputTokens": 106842,
      "outputTokens": 1073,
      "cacheReadTokens": 85899,
      "cacheWriteTokens": 20925,
      "reasoningTokens": 268
     },
     "totalNanoAiu": 401291500,
     "tokenDetails": {
      "input": {
       "tokenCount": 18
      },
      "cache_read": {
       "tokenCount": 85899
      },
      "cache_write": {
       "tokenCount": 20925
      },
      "output": {
       "tokenCount": 1073
      }
     }
    }
   }
  },
  "call_DicUGFdi1sTCKOznDiuBTHTu": {
   "agentName": "search-subagent",
   "agentDisplayName": "Search Subagent",
   "totalApiDurationMs": 5449,
   "totalNanoAiu": 0,
   "modelMetrics": {
    "copilot-search-a": {
     "requests": {
      "count": 4,
      "cost": 0
     },
     "usage": {
      "inputTokens": 36697,
      "outputTokens": 1227,
      "cacheReadTokens": 19788,
      "cacheWriteTokens": 0
     },
     "totalNanoAiu": 0,
     "tokenDetails": {
      "input": {
       "tokenCount": 16909
      },
      "cache_read": {
       "tokenCount": 19788
      },
      "cache_write": {
       "tokenCount": 0
      },
      "output": {
       "tokenCount": 1227
      }
     }
    }
   }
  }
 },
 "providerModelMetrics": [
  {
   "provider": {
    "id": "copilot",
    "label": "GitHub Copilot",
    "kind": "copilot"
   },
   "modelId": "gpt-6-luna",
   "metrics": {
    "requests": {
     "count": 6,
     "cost": 1
    },
    "usage": {
     "inputTokens": 106842,
     "outputTokens": 1073,
     "cacheReadTokens": 85899,
     "cacheWriteTokens": 20925,
     "reasoningTokens": 268
    },
    "totalNanoAiu": 401291500,
    "aiCreditsStatus": "complete",
    "tokenDetails": {
     "input": {
      "tokenCount": 18
     },
     "cache_read": {
      "tokenCount": 85899
     },
     "cache_write": {
      "tokenCount": 20925
     },
     "output": {
      "tokenCount": 1073
     }
    }
   },
   "modelDisplayName": "GPT-6 Luna"
  },
  {
   "provider": {
    "id": "copilot",
    "label": "GitHub Copilot",
    "kind": "copilot"
   },
   "modelId": "copilot-search-a",
   "metrics": {
    "requests": {
     "count": 4,
     "cost": 0
    },
    "usage": {
     "inputTokens": 36697,
     "outputTokens": 1227,
     "cacheReadTokens": 19788,
     "cacheWriteTokens": 0
    },
    "totalNanoAiu": 0,
    "aiCreditsStatus": "complete",
    "tokenDetails": {
     "input": {
      "tokenCount": 16909
     },
     "cache_read": {
      "tokenCount": 19788
     },
     "cache_write": {
      "tokenCount": 0
     },
     "output": {
      "tokenCount": 1227
     }
    }
   },
   "modelDisplayName": "Copilot Agent A"
  }
 ],
 "currentModel": "gpt-6-luna",
 "lastCallInputTokens": 20928,
 "lastCallOutputTokens": 91
}
```

### Experiment B: usage file

```json
{
 "totalPremiumRequestCost": 1,
 "totalUserRequests": 1,
 "totalNanoAiu": 336172500,
 "aiCreditsStatus": "complete",
 "tokenDetails": {
  "input": {
   "tokenCount": 15
  },
  "cache_read": {
   "tokenCount": 87060
  },
  "cache_write": {
   "tokenCount": 15321
  },
  "output": {
   "tokenCount": 1149
  }
 },
 "totalApiDurationMs": 15924,
 "sessionStartTime": "2026-10-21T21:00:20.280Z",
 "codeChanges": {
  "linesAdded": 5,
  "linesRemoved": 0,
  "filesModifiedCount": 1,
  "filesModified": [
   "/Users/mohammedkhan/iq/Eats/src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs"
  ]
 },
 "modelMetrics": {
  "gpt-6-luna": {
   "requests": {
    "count": 5,
    "cost": 1
   },
   "usage": {
    "inputTokens": 102396,
    "outputTokens": 1149,
    "cacheReadTokens": 87060,
    "cacheWriteTokens": 15321,
    "reasoningTokens": 495
   },
   "cacheExpiresAt": "2026-10-21T21:30:37.022Z",
   "totalNanoAiu": 336172500,
   "aiCreditsStatus": "complete",
   "tokenDetails": {
    "input": {
     "tokenCount": 15
    },
    "cache_read": {
     "tokenCount": 87060
    },
    "cache_write": {
     "tokenCount": 15321
    },
    "output": {
     "tokenCount": 1149
    }
   }
  }
 },
 "agentMetrics": {
  "main": {
   "totalApiDurationMs": 15924,
   "totalNanoAiu": 336172500,
   "modelMetrics": {
    "gpt-6-luna": {
     "requests": {
      "count": 5,
      "cost": 1
     },
     "usage": {
      "inputTokens": 102396,
      "outputTokens": 1149,
      "cacheReadTokens": 87060,
      "cacheWriteTokens": 15321,
      "reasoningTokens": 495
     },
     "totalNanoAiu": 336172500,
     "tokenDetails": {
      "input": {
       "tokenCount": 15
      },
      "cache_read": {
       "tokenCount": 87060
      },
      "cache_write": {
       "tokenCount": 15321
      },
      "output": {
       "tokenCount": 1149
      }
     }
    }
   }
  }
 },
 "providerModelMetrics": [
  {
   "provider": {
    "id": "copilot",
    "label": "GitHub Copilot",
    "kind": "copilot"
   },
   "modelId": "gpt-6-luna",
   "metrics": {
    "requests": {
     "count": 5,
     "cost": 1
    },
    "usage": {
     "inputTokens": 102396,
     "outputTokens": 1149,
     "cacheReadTokens": 87060,
     "cacheWriteTokens": 15321,
     "reasoningTokens": 495
    },
    "totalNanoAiu": 336172500,
    "aiCreditsStatus": "complete",
    "tokenDetails": {
     "input": {
      "tokenCount": 15
     },
     "cache_read": {
      "tokenCount": 87060
     },
     "cache_write": {
      "tokenCount": 15321
     },
     "output": {
      "tokenCount": 1149
     }
    }
   },
   "modelDisplayName": "GPT-6 Luna"
  }
 ],
 "currentModel": "gpt-6-luna",
 "lastCallInputTokens": 23894,
 "lastCallOutputTokens": 244
}
```

### Rollback

Each run changed only `src/services/TraditionalEats.ReviewService/Controllers/ReviewController.cs`, which was
unmodified at HEAD before the experiment. After each run it was restored with
`git show HEAD:<file> > <file>`; no reset, checkout, stash or clean was used. The `dotnet build` outputs went to the
existing, git-ignored `bin/` and `obj/` folders. No commits or pushes were made.
