# Live runs, benchmark and grounding record

The detail behind the results table in the [README](../README.md#results). Every figure here was read from a stored job record, an evidence bundle or a harness report. Each job is one run, not a rate.

## Live runs

Six end-to-end jobs have been run on the real Nemotron models and the real Nebius sandbox. Each is a single run, not a rate.

## Run 1: weak-tests repository, UNVERIFIED

The first job ran on the weak-tests repository ([`principal-weak-repo`](https://github.com/BugHunterX2101/principal-weak-repo)) with **Nemotron 3 Nano** coders and **Nebius Sandboxes** (its trace records real platform operation ids, not `fake-op-N`). Goal: *"Improve `pricing.calc_total`: remove magic constants and add type hints without changing behaviour."*

- State **Done** — both tasks verified, integration **pass**, security **0 introduced findings**.
- Outcome **UNVERIFIED**, not VERIFIED: the strength audit caught **0 of 1** injected faults (floor 60%), and `src/reports.py:11–12` reaches the target through `getattr`, which no test executes.
- **6 attempts**, **17,822 tokens** of a 2,000,000 budget, sandbox cost **$0.0037**.

That is a single run, not a rate. Principal produced a change whose tests pass, and then **refused to call it verified** — which is the point. *(This job predates provenance recording, so its stored evidence bundle reads "NOT LIVE · unknown"; the label is conservative, and newer runs record LIVE/NOT LIVE at start.)*

## Run 2: weak-tests repository, cache off, UNVERIFIED

Same repository and goal, with the model cache off (every call went to the platform), after the token ledger was fixed:

- **Different result — the same job is not deterministic.** All three Nano candidates and both Super repairs failed (two unparseable diffs, one empty reply, one context mismatch, one rejected patch), so the task was discarded, **no change was produced**, and the outcome was **UNVERIFIED** with the repository left untouched. The first run above produced a change; this one did not.
- **Models, per role, from the ledger:** planner on **Nemotron 3 Ultra** (`nvidia/Nemotron-3-Ultra-550b-a55b`, 1 call, 2,824 tokens), coders on **Nemotron 3 Nano** (2 billed calls, 20,622 tokens), repairers on **Nemotron 3 Super** (2 calls, 3,265 tokens).
- **The ledger reconciles: 26,711 tokens charged, 26,711 attributed, 0 unattributed.** Before the fix the same job charged 26,626 against 22,761 attributed, because a cancelled or failed model call kept its up-front reservation. Two bugs caused it: a cancelled call never refunded its reservation, and the characterisation step charged its call twice (the second one did not fire in this run — it is covered by a test, not by this measurement).
- It is still one run. It shows the accounting is sound and the platform is reachable; it does not show a success rate.

## Run 3: `mini_repo`, UNVERIFIED

A repository with strong tests: the bundled `mini_repo` fixture, published as the orphan branch `fixture/mini-repo` so the sandbox can clone it. Goal: make `create()`'s `ttl` keyword-only and update every call site.

- Outcome **UNVERIFIED**, state `NoPR`: the planner (Ultra) split the goal into **6 tasks**, **4 settled** with a winning candidate that passed the local tests, **2 were discarded** after Nano and Super both failed to produce a usable diff, and 2 of 6 exceeds the 30% discard cap, so the job stopped with the repository unchanged. **It did not reach VERIFIED.**
- **The ledger reconciles with many cancelled candidates in play: 58,409 tokens charged, 58,409 attributed, 0 unattributed.** Most failures were `DIFF_UNPARSEABLE` (no `<<<DIFF … DIFF>>>` block in the reply); at least one Nano reply used 2,985 of its 3,000-token cap, which may be truncation, though this run does not prove it.
- At this point three live runs on two repositories existed and none was VERIFIED; the first run's tests passed but could not cover a dynamic call. That was evidence about the pipeline's honesty, **not** a claim that it succeeds on this task.

## Run 4: `mini_repo` after the diff-parser fix, UNVERIFIED

Same goal, after the diff-parser fix (a reply ending its block with an indented ` DIFF>>>` or a bare `>>>` is now read; hunk matching is unchanged):

- State **Done**, outcome **UNVERIFIED**. The planner split the goal into **7 tasks**: **5 verified**, **2 discarded** (2 of 7 is under the 30% cap, so the job went on to integration). Integration **passed**, security found **0 introduced** findings, and the strength audit detected **8 of 10** injected faults (score 80%, floor 60%).
- **It is not VERIFIED, and should not be.** One of the two discarded tasks was `src/auth/session.py`, the file that defines `create()`. The five call sites now pass `ttl` by keyword, which the old signature still accepts, but `ttl` is **not** keyword-only. The other discarded task left `src/legacy/compat.py` on the old form. Both are listed as open caveats.
- **The ledger reconciles: 69,748 tokens charged, 69,748 attributed, 0 unattributed, across 21 model calls.** Of the attempts, 5 passed, 2 failed tests, 10 were rejected as unusable diffs and 2 were model errors; 6 have no verdict, which in a finished run means they were cancelled after another candidate won (this run predates recording them as `cancelled`). No pull request was opened, because publishing is not configured on this machine.
- The reporter's single call used exactly its 2,000-token output cap, so its reply was probably cut off and the deterministic fallback report used instead. That is an inference from the count, not something this run recorded. The cap is now 6,000 tokens, and a fallback is logged.

## Why `session.py` kept failing, and what changed

Every task was verified alone against `C0`. Making `ttl` keyword-only breaks every positional caller, so the definition's task could never pass on a tree whose callers had not been updated — no model could have produced a passing diff. Three fixes followed, each covered by tests:

- **Dependency-aware verification**: a task is tested on `C0` plus the verified diffs of the tasks it depends on, and the planner is told to order a breaking change so consumers come first.
- **Blank-line tolerance when locating a hunk**, only as a fallback when the exact context is not in the file: a blank context line may match one blank file line or none, and blank lines the hunk left out are kept. Every line with content still has to match exactly. This was a real Nano reply, rejected for writing one blank line where the file had two.
- **The fixture's own tests were changed.** `mini_repo`'s tests called `create(user, 120)` positionally, so the goal *"make `ttl` keyword-only"* could not pass them — any correct change was red. The tests now pass `ttl` by keyword, and `src/registry.py`'s `dispatch()` forwards keyword arguments; the assertions are unchanged, and a hand-written full migration passes all 14 tests. This changes the benchmark, so it is stated here: the fixture branch moved from `654e86c` to [`3d11f55`](https://github.com/BugHunterX2101/AMTDRS/tree/fixture/mini-repo).

## Run 5: fixture `3d11f55`, dependency-aware verification, UNVERIFIED

Job `01M5FTFZPQ3VS87HF00F0RN2K5`, before the blank-line fix:

- State `NoPR`, outcome **UNVERIFIED**.
- The planner made `session.py` depend on all five consumer tasks. 4 consumers verified; `refresh.py` was discarded after every reply failed to locate its hunks (one blank line where the file had two), so `session.py` was tested without it and failed red twice. 2 of 6 discarded exceeds the cap.
- Ledger **56,261 = 56,261** across 20 calls.

## Run 6: VERIFIED

Job `01M5FTZVBPDH18S99QN4BW9ANB`, same fixture and goal, model cache off, after the blank-line fix:

- State **Done**, outcome **VERIFIED**. **6 of 6 tasks verified**, all by a first-round Nano candidate (no Super repair needed); 12 other candidates were cancelled once a sibling won.
- The definition changed: `def create(user, ttl=DEFAULT_TTL)` → `def create(user, *, ttl=DEFAULT_TTL)`. The impact check counted **9 of 9 call sites changed**, 0 unexpected files, and both dynamic paths in `src/registry.py` **exercised by tests**.
- Integration **passed**, security **0 introduced** findings, strength audit **7 of 9** injected faults detected (78%, floor 60%; the two survivors are `120 → 121` and `10 → 11` in literal TTLs).
- **19,855 tokens** across 10 model calls (ledger 19,855 = 19,855). No pull request, because publishing is not configured on this machine.
- **One run, not a rate.** It is the first VERIFIED result, it came after the fixture's tests were changed (see "Why `session.py` kept failing"), and the previous five runs on these two repositories did not reach VERIFIED.

## The weak-tests repository now has tests for its callers

[`principal-weak-repo`](https://github.com/BugHunterX2101/principal-weak-repo) commit `4ec27e0` adds tests that cover `calc_total`'s keyword and explicit-tax uses, its two callers and the `getattr` path in `src/reports.py`. The benchmark stays pinned to `3fd067a`, the weak version, because that is the case it measures; no live run has been made at `4ec27e0`.

## Live benchmark

`bench/tasks.json` pins **two tasks, not a suite**: the bundled `mini_repo`, and the weak-tests repository where a green change can still be unverified. The two live passes made so far, both on 2026-10-22, are recorded below.

> **Both tasks point at public git URLs**, because the Nebius sandbox cannot clone a local path (see the [README quickstart](../README.md#quickstart--no-credentials-needed)). The `mini_repo` task uses the fixture's published branch, `fixture/mini-repo` at `3d11f55`, the same repository and commit as live run 6.

**Live benchmark, 2026-10-22** (`python -m bench.harness`, real Nebius sandbox and real inference, labelled LIVE by the report, one run per cell):

| Arm | Attempted | Verified | Unverified | Failed on merit | Infrastructure exclusions | False accepts | Tokens (mini_repo / weak_repo) |
|---|---|---|---|---|---|---|---|
| A | 2 | 0 | 0 | 2 | 0 | — | 38,586 / 30,730 |
| B | 2 | 0 | 1 | 1 | 0 | — | 50,084 / 26,281 |
| C | 2 | 0 | 1 | 1 | 0 | — | 81,736 / 38,298 |

No arm reached VERIFIED on either task, so there is no verified rate to compare. The result is two tasks and one run per cell: it shows nothing about which arm is better, and the differences in tokens and time are within what one run can show. The false-accepts column is `—` everywhere because no run claimed *verified*, so the oracle on `weak_repo` was never exercised live. It is covered only by the unit tests in `tests/unit/test_bench_oracle.py`. Causes recorded: the mini_repo B and C runs ended UNVERIFIED with caveats open; the weak_repo B and C runs ended UNVERIFIED because a task was discarded (1 of 3, over the 30% cap); arm A failed on both tasks (2 of 6 and 3 of 4 tasks discarded). The causes of the discards were then investigated, and the second run below follows the fixes.

**Live benchmark, 2026-10-22, after the fixes** (same harness, real Nebius sandbox and real inference, one run per cell, code at commit `d5c15f9`):

| Arm | Attempted | Verified | Unverified | Failed on merit | Infrastructure exclusions | False accepts | Tokens (mini_repo / weak_repo) |
|---|---|---|---|---|---|---|---|
| A | 2 | 1 | 0 | 1 | 0 | 0/1 | 36,071 / 42,630 |
| B | 2 | 0 | 2 | 0 | 0 | — | 25,539 / 19,081 |
| C | 2 | 1 | 1 | 0 | 0 | — | 28,354 / 28,482 |

What each cell was:

- **mini_repo, arm C: VERIFIED, clean.** All six planned tasks were verified, including the one that changes `create` itself, and the audits left nothing open. This is the first VERIFIED outcome in the benchmark. Arm B ended UNVERIFIED with 2 of 6 tasks discarded, and arm A failed with 2 of 6 discarded (both over the 30% cap).
- **weak_repo, arm C: UNVERIFIED, caveats open, and that is the fixture working.** All five planned tasks were verified, and the run then stopped short of VERIFIED on two unexercised dynamic dispatches in `src/reports.py` and an injected-fault score of 0.0 against a floor of 0.6 for the changed lines. This repository exists to show a green change that still cannot be called safe. Arm B ended UNVERIFIED with 2 of 5 tasks discarded.
- **weak_repo, arm A: VERIFIED, 0 false accepts of 1 checked.** Arm A has no gates, so the harness counts a green test run as its claim. The independent oracle checked that claim and did not reject it. This is the first time the oracle ran live. No gated arm claimed VERIFIED on `weak_repo`, so the oracle has not yet checked a claim made by arm B or C, and the false-accept rate of the gated arms is still unmeasured.

How the cells were assembled. The `weak_repo` cells are from a rerun, because the first pass at this commit lost its network (a DNS failure, `getaddrinfo failed`) and aborted or emptied all three arms. In the rerun, arm B aborted before any sandbox run with a sandbox API timeout, which the product reports as `exceeded 0s` because the SDK gives no duration. Arm B was run again alone and produced the cell above. The harness files such aborts as infrastructure and they are not counted as failures of the patch; the aborted passes are not in the table.

What this shows and does not show. Each cell is one run on two tasks. The three mini_repo arms of the first pass replayed one cached planner response (identical token counts), so the plan is one sample, not three. A single VERIFIED cell cannot rank arms, and it does not show which of the fixes below produced it. What it does show is that the tasks were being lost for specific, reproducible reasons that are now fixed.

The causes found, each measured on live Nemotron output for one task (`src/pricing.py`, a dozen or so samples per condition, so indicative and not rates):

| Cause | What was measured | Fix |
|---|---|---|
| Nano was cut off before the diff | At a 3,000-token output cap, 4 of 6 calls ended on the cap, 4 of 12 at 6,000, 1 of 12 at 10,000. Nano reasons before it answers, and the reasoning counts against the cap | Every tier gets 10,000 tokens |
| The Spotlighting fence leaked into the reply | With the `<<<...>>>` fence, 8 of 16 Nano and 0 of 12 Super replies ended in the exact `DIFF>>>` closer, against 12 of 16 and 4 of 12 on the unfenced prompt. Models closed with `<<<END_DIFF>>>` or `<<<DIFF>>`, leaving no block to parse | The fence is now `==== BEGIN/END UNTRUSTED REPO DATA ====`, which shares no characters with the sentinels (12 of 16 and 7 of 12). A tolerant parser was tried offline and gained nothing on those samples, so the parser is unchanged |
| A repair with nothing to repair | The Repairer is told the failure, not the goal. When every first-round candidate failed to parse there was no diff to show it, and both Super repairs were spent blind | With no diff to show, Super takes the task as a coder. This is covered by a unit test and was not exercised in either live run |
| A misleading repair prompt | It said the shown file already had the failed patch applied. It is the file before the patch | The wording now says so |
| An aliased call site left out of the plan | The planner saw `compat.py:7 calls make_session` and called it "unclear if it wraps `create`", although the graph had resolved it to `create`. Leaving it out made the task for `create` unable to pass | The call-site line now says it is the target imported under another name. Fresh planner samples planned `compat.py` 6 of 6 times with and without the note, so no gain is shown, and the omission was seen once |

One more thing the discards turned out not to be: most of the Nano samples that still failed to apply failed on merit, with a typo in a context line such as `TAx_RATE`, a hallucinated context line, or a stray space after the `-` marker. Those are rejected on purpose and the parser was not loosened to accept them.

A further full run went through the HTTP API instead of the harness (`principal serve` with credentials, `POST /jobs`, `PRINCIPAL_REQUIRE_LIVE` on, `/healthz` reporting `ContreeSandbox` with inference and sandbox verified): the same `mini_repo` job ended **VERIFIED, clean**, with 6 of 6 tasks verified (including `src/legacy/compat.py`), 63,619 tokens, $0.0115 of sandbox cost and an injected-fault score of 78% against the 60% floor. It is one run and is not a rate.

Not checked. The Linux CI result for these commits has not been read, because the tool for reading it was not available when they were pushed. `characteriser.py` still caps its output at 3,000 tokens, so the truncation cause is fixed for the coder and repairer and not claimed fixed everywhere. The Spotlighting regression was measured on one task only, and the pre-Spotlighting comparison used the earlier prompt from commit `04e9036`.

## Web grounding on `psf/requests`

- **Web grounding has run live for planning only.** A `--dry-run` job on `psf/requests` (real sandbox baseline, real Nemotron Ultra planner) recorded two searches with Tavily `request_id`s, which its `evidence.md` lists. The hosted demo has had a Tavily key since 2026-10-25. Two full jobs on the same repository then ended `NoPR` with every task discarded: the planner's plans were sensible, but the coder models' diffs did not apply (malformed hunk headers, context lines absent from the file, replies cut off at the token cap), so no patch was verified with grounding on. One cause was Principal's: the coder saw only the first 20000 characters of a file, which hid the target function in `utils.py` (36061 characters). After that limit was raised to 120000, a third full job (`01M5T42TJKDQD4DN8ZVCXTGSSC`, Tavily requests `470ecfeb-e4a7-415b-9350-d90bf26e5b2d` and `8f6bd8f4-cf43-4133-8f0a-bffd94884028`) also ended `NoPR`, every candidate rejected at the diff-parse gate. Six Nano replies to one of its tasks, replayed outside the job, show why: two were correct edits whose `@@` line counts did not match the hunk, which the parser then rejected (it now re-derives the counts of a hunk that ends on a context line, and both replies pass it on replay), and four were wrong (bad indentation, context from the other call site, stray patch markers). A fourth full job, run from the hosted site (`01M5T5F2AKQHRFRR8GGFST49S1`, Tavily requests `ea3d21a6-fce5-4806-ba4c-6151549ce2fa` and `93c6a283-a3fb-435b-adef-37be9d5e7d9f`), verified one of its two tasks, the `adapters.py` call sites: a Nano candidate applied and passed the local tests. The other, the `utils.py` signature, was discarded (one candidate applied but its tests errored, and both Super repairs hit the 10000-token cap), so 1 of 2 discarded exceeded the 30% cap and the job ended `NoPR` with nothing shipped. The repair-stage search has only been tested against a mock transport, and whether the references improve a plan or a repair has not been measured: the one plan seen was not checked against an ungrounded one.
