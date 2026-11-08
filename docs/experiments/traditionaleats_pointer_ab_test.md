# Pointer prompt A/B test: vague tasks, two runs each side

Date: 2026-10-22. Runner: GitHub Copilot CLI 1.0.98, headless (`copilot -p`).

**Result:** on two vague tasks, a ~310-token pointer (file, line range, symbol, no source code) used **35.6% fewer
reported tokens** and **35.7% fewer AI credits** than the same agent with no Smart Token. All 8 runs passed.
The earlier full-snippet pack did not show a reliable saving. This comparison is the one that did.

## What was compared

- **Baseline:** the task text only. The agent searched the repo itself. Smart Token was denied (`shell(smarttoken)`).
- **Pointer:** the same task, plus up to 6 locations from `smarttoken ask --pack pointer`. No source code in the prompt.

Same flags both sides: `--model auto --auto-tier balance --allow-all-tools --disable-builtin-mcps --no-ask-user`,
git write commands denied. The router chose `gpt-6-luna` for every run. One premium request per run.

Eats (`/Users/mohammedkhan/iq/Eats`) started and ended at commit `59ff18f7` on `dev`, with a clean tree.
Each run's diff was saved, the acceptance harness was rebuilt against that diff, then the files were restored
from `HEAD` before the next run. Nothing was committed or pushed.

Order was alternated so a warm prompt cache would not always favor the second arm:

1. stars baseline, stars pointer, stars pointer, stars baseline
2. reply pointer, reply baseline, reply baseline, reply pointer

## Tasks (written before the runs, no file names, no error strings)

**Stars.** Someone can edit a review and set a star rating that isn't a real rating, such as zero or ten.
Stop that from being saved. Changing only the comment, with no new rating, must still work. Keep the change small.

Pass means: ratings 0, 6 and −1 return HTTP 400 and stay stored as 3; a comment-only update still saves; ratings
1 and 5 still save. 9 checks.

**Reply.** A restaurant owner can reply to a review with a blank message. Don't save an empty reply.
A reply with actual text must still be saved. Keep the change small.

Pass means: `""` and `"   "` return HTTP 400 and are not kept; `"Thanks for coming"` is saved. 5 checks.

Both fail on the unchanged code (6 star checks, 3 reply checks).

## Pointers that were sent

309 tokens for stars, 313 for reply (cl100k, local count). The full pack for the same tasks would have been about
3,500 tokens. The pointer named the right methods and also some wrong ranges:

- Stars included `UpdateReviewAsync` and `CreateReview`, and also Elo rating math and the star-widget component.
- Reply included `AddRestaurantResponseAsync`, and also `GetReviewsByRestaurant` and a restaurant-country method.

## Results

| Run | Arm | Reported tokens | AI credits | Model calls | `rg` | `view` | Checks |
|---|---|---:|---:|---:|---:|---:|---|
| stars 1 | baseline | 228,938 | 0.676 | 10 | 4 | 4 | 9/9 |
| stars 2 | pointer | 154,526 | 0.406 | 9 | 1 | 12 | 9/9 |
| stars 3 | pointer | 147,926 | 0.366 | 9 | 2 | 14 | 9/9 |
| stars 4 | baseline | 212,526 | 0.523 | 9 | 5 | 2 | 9/9 |
| reply 1 | pointer | 134,574 | 0.384 | 8 | 1 | 14 | 5/5 |
| reply 2 | baseline | 197,044 | 0.546 | 9 | 8 | 3 | 5/5 |
| reply 3 | baseline | 276,896 | 0.680 | 11 | 10 | 5 | 5/5 |
| reply 4 | pointer | 152,456 | 0.403 | 9 | 2 | 15 | 5/5 |

Reported tokens = fresh input + cache read + cache write + output, from Copilot's `--usage-output-file`.
Not estimated from the prompt length.

| | Baseline total | Pointer total | Reduction |
|---|---:|---:|---:|
| Stars (2 vs 2) | 441,464 | 302,452 | 31.5% |
| Reply (2 vs 2) | 473,940 | 287,030 | 39.4% |
| **Both** | **915,404** | **589,482** | **35.6%** |
| AI credits, both | 2.425 | 1.558 | 35.7% |

Every pointer run was cheaper than every baseline run on the same task. The second baseline on reply was the
most expensive run, so running later did not by itself make a run cheaper.

## What the saving is

The pointer did not stop the agent from reading code. Pointer runs opened more files (`view` 12–15 vs 2–5).
They searched much less (`rg` 1–2 vs 4–10). Search rounds are what piled tokens into the cache. Naming
`UpdateReviewAsync` and `AddRestaurantResponseAsync` replaced most of that searching, even with a few wrong
lines in the list.

The 310-token prompt is about 0.2% of what the pointer runs consumed. The saving is fewer search steps, not
"the job cost 310 tokens."

## Measurement mistake, corrected

The harness was first run with `dotnet run --no-build`, which executed the binary built before any edit.
That reported every run as a failure, matching the unchanged code. The diffs on disk already contained the
fixes. Each diff was applied again, the harness was rebuilt, and then the tree was restored. The table above
is from that rebuild. All 8 passed.

## Limits

- Two runs per arm, two tasks, one repo area (reviews). Enough to see the same direction every time, not enough
  to quote a stable percentage for every kind of task.
- The model could not be pinned. This account only accepts `auto`. All 8 main agents were `gpt-6-luna`.
- No dollar price is published for this model through Copilot. Credits are the billed unit.
- The pointer still wastes some of its lines on nearby but wrong symbols. A tighter choice of method, without
  naming Eats files in the rules, is the next improvement. It was not changed during these runs.

## Repository after the test

```
branch dev
head 59ff18f7
status clean
ReviewController.cs sha256 prefix c6db6f918cacf614
ReviewService.cs    sha256 prefix 43843b88adfdbe9f
```

Those prefixes match the tree from before the first A/B test. Raw usage, events and diffs are in
`/tmp/abtest/round3/`.
