# Devpost submission

Everything the submission form asks for, in the order the form asks for it, plus a checklist against the official rules. Paste the quoted blocks straight into Devpost.

**Placeholders you must fill before submitting are marked `⟨LIKE THIS⟩`.**

---

## 1. Track

> **Coding and Agentic Engineering** — *agents that write, run and test code in Token Factory Sandboxes.*

Principal is a coding agent whose entire correctness argument is that it writes code, runs it, and tests it inside Token Factory Sandboxes. Gate 3 and gate 4 are real `pytest` runs in isolated microVMs, and the accept decision is a process exit code from one of those runs — not a model's opinion. The track fit is structural, not thematic.

## 2. Project name

> **Principal — Autonomous Technical Debt Remediation Swarm**

## 3. Elevator pitch (Devpost's short tagline, 200 characters)

> Refactors a repo across dozens of files by racing parallel candidate patches in Token Factory Sandboxes. No green test, no PR — ever. Powered by NVIDIA Nemotron 3 Nano, Super and Ultra.

## 4. Text description — features and functionality

*Paste this into the Devpost "About the project" field. It is structured to the headings Devpost provides.*

### Inspiration

Ask any coding agent to "remove this deprecated parameter" and it does a good job on one file. The work that actually costs engineering teams money is the other kind: a signature change touching forty call sites across nine modules, where being right in eight modules and wrong in the ninth is *worse than not starting* — because now a human has to review a large diff hunting for the one mistake.

That task is hard for a single agent for two reasons. It is **wide** — the necessary context exceeds what fits usefully in one window. And it is **unverifiable by inspection** — the only honest signal about whether a refactor is correct is whether the test suite still passes.

So we stopped trying to make one agent careful enough, and instead made failure cheap enough to be wrong on purpose.

### What it does

Principal takes a repository, a commit, a target symbol and a goal in English, and produces either a **draft pull request whose tests are verified green**, or a **report explaining exactly why it declined to open one**.

Between those two outcomes it:

1. **Builds a verified green baseline** in a Token Factory Sandbox — clone, install, run the full suite — and refuses to continue if the suite is already red. It also captures a per-test coverage map.
2. **Maps the blast radius** with tree-sitter: symbols, call edges, import edges, then a reverse-edge BFS from the target. If the radius is too large it *aborts* rather than silently working on part of it.
3. **Plans** with Nemotron 3 Ultra: an ordered, dependency-aware set of single-file tasks arranged into waves.
4. **Executes each task as a race.** Three independent candidate patches from Nemotron 3 Nano, each pushed through four gates, **first to go green wins**. Losers are cancelled. Failed candidates get a bounded repair from Nemotron 3 Super.
5. **Verifies the whole thing together** — every accepted patch applied at once, full suite, one final sandbox run.
6. **Opens a draft PR** with an evidence table: what changed, which tests ran, coverage delta, and an explicit list of what a human must still check.

Every state change streams live to a dashboard showing the sandbox fork tree as it grows.

### The guarantee

**Fail closed. No green test, no PR.**

`NoPR` is a *success* state. A refactoring tool that sometimes ships an unverified change is worth less than no tool at all, because it converts a bounded engineering task into an unbounded review task.

There is **no model anywhere in the accept path** — no scoring function, no LLM judge. The decision is made by `pytest`. This is enforced by an `import-linter` contract in CI (`gates` may not import `models`), so it is a property a machine checks, not a claim in a README.

### How we built it

**Four gates, ordered by cost:**

| Gate | Proves | Runs | Cost |
|---|---|---|---|
| 1. Scope | the patch touches only files in the blast radius | local | microseconds |
| 2. Syntax | every changed file still parses (tree-sitter) | local | milliseconds |
| 3. Local tests | tests covering the changed lines pass | **Sandbox** | seconds |
| 4. Integration | all accepted patches pass together | **Sandbox**, once | seconds |

Gates 1 and 2 never touch a sandbox. That ordering is the whole economic argument: you can afford to be wrong eight times out of nine when eight of those failures are free.

**Token Factory Sandboxes are load-bearing, not incidental.** Sandbox images are content-addressed — running a command against an image returns a *new* image, and the same command against the same parent returns the same UUID. Two consequences carry the design:

- The expensive part (dependency installation) is paid **once per job**, not once per candidate. Three candidates across twelve tasks is one install instead of thirty-six.
- **Failure is free because it is private.** A candidate that breaks the build breaks only its own fork. No shared working tree, no cleanup, no interference. That is what makes "generate three, race them, discard two" reasonable rather than reckless.

**Three Nemotron 3 models, routed by job shape:** Nano for high-volume error-tolerant candidate generation (a verification gate catches its mistakes, so cheap and fast is correct); Super for repair, which is narrow but needs real reasoning about *why* a test failed; Ultra for planning, called once per job, where every downstream decision inherits its mistakes.

**Per-test coverage drives test selection.** The baseline runs `pytest --cov-context=test`, producing a test→line map. Each fork then runs only the tests that actually cover the lines it changed, which is what keeps gate 3 at seconds rather than minutes.

### Challenges we ran into

**Empty `content` on reasoning models.** Reasoning-tuned models can return their output in `reasoning_content` and leave `content` as an empty string — which is not an error, so it propagates silently and surfaces three layers away as "the agent generated no patch." We stopped trusting the field and built a boot-time 3×3 capability probe: every model against every output protocol, each pinned to the protocol it actually demonstrated.

**Cancelling remote work.** Racing candidates means cancelling losers, but the sandbox operation UUID is not exposed on the SDK's public surface — cancelling a local `asyncio.Task` leaves the remote operation running and billable. We capture the id from the client's internals to make cancellation and crash recovery possible. It works; it should not be necessary. (Written up properly in our feedback.)

**Keeping the unresolved-calls list honest.** Dynamic dispatch — `registry[name](user)` — cannot be statically proved to reach the target, so we report it for human review. The naive version floods that list with builtins until nobody reads it. Distinguishing a genuinely unresolvable call from `len()` is the difference between 9 noise entries and exactly 2 real ones on our fixture — and a risk list nobody reads is worse than none.

**Making the blast radius raise instead of truncate.** A truncated radius produces a patch that *looks* complete and is not. That is the most dangerous failure this system could have, so exceeding the cap is a hard abort.

### Accomplishments we're proud of

- A hard behavioural guarantee that is **enforced by CI**, not asserted in prose.
- An error taxonomy with **three** honest buckets — verified / failed on merit / excluded as infrastructure — because folding a sandbox timeout into "the patch was wrong" understates the real success rate *and* hides a platform problem.
- Three behaviour checks beyond "tests pass", because a patch that deletes a function *and its tests* passes: public API delta against declared removals, a coverage floor, and a test-count invariant.
- It runs **end to end with no credentials at all**, against a bundled fixture, so anyone can verify the setup before spending a token.

### What we learned

Verification is cheaper than caution. Once failure is private and free, the optimal strategy stops being "one careful attempt" and becomes "many cheap attempts and an uncompromising gate." Almost every hard decision in this project followed from taking that seriously.

### What's next

Cross-repository refactors; language coverage beyond Python and TypeScript; learning per-repository conventions from merged PRs; and — the moment egress control lands on `run()` — a security story we can state without qualification.

## 5. Built with

`python` · `fastapi` · `nebius-token-factory` · `nebius-sandboxes` · `nvidia-nemotron-3` · `contree-sdk` · `openai-api` · `tree-sitter` · `sqlite` · `react` · `vite` · `mcp` · `pytest` · `docker` · `render`

## 6. Links

| Field | Value |
|---|---|
| Public repository | [github.com/BugHunterX2101/AMTDRS](https://github.com/BugHunterX2101/AMTDRS) |
| Working demo / hosted app | [amtdrs.onrender.com](https://amtdrs.onrender.com) — Render free tier, zero-credential FakeSandbox mode |
| Demo video (YouTube, public, < 3 min) | ⟨https://youtube.com/watch?v=...⟩ |

## 7. Feedback on Nebius and NVIDIA tools

The full engineering write-up is **[`docs/FEEDBACK.md`](FEEDBACK.md)**. Paste this condensed version into the Devpost feedback field and link the full document.

> **What worked exceptionally well.** Content-addressed sandbox images are the best design decision in the Sandboxes API — they are why our baseline install is paid once per job rather than once per candidate, and why a failed candidate costs nothing. OpenAI API compatibility meant our entire model layer is the stock `openai` SDK with one changed `base_url`. The Nemotron 3 Nano/Super/Ultra split mapped cleanly onto three genuinely different jobs in our system, and Nano's price/performance is the reason wide fan-out is affordable at all.
>
> **Highest-value fixes, in order.**
> **(1)** Expose the sandbox operation UUID on the public API — an `on_operation` callback on `run()` would do it. Cancelling speculative work is the characteristic need of agentic workloads, and today it requires monkeypatching a private method.
> **(2)** When a reasoning model returns empty `content` with text in `reasoning_content`, populate `content` — or expose response-format capabilities on `GET /v1/models`. An empty string is not an error, so it fails silently and gets misattributed to the prompt.
> **(3)** Give Sandboxes a zero-cost entitlement/health endpoint. Access is per-project but a working inference key tells you nothing, and the failure is a bare 403 that does not name the missing entitlement.
> **(4)** Add egress control to `run()` (`network="none"`). Running untrusted model-generated code is the defining use case of this track, and without it "isolated sandbox" is a claim we cannot fully make.
> **(5)** Normalise model id capitalisation — three tiers of one family currently use three different conventions.

## 8. Pre-existing project statement

> Principal was created entirely during the hackathon submission period (26 August – 30 October 2026). It is not a pre-existing project and no part of it was published before the submission period opened. The full commit history in the public repository is the record.

---

## Rules compliance checklist

Every requirement from [the official rules](https://nebiusglobalaihackathon.devpost.com/rules), with where it is satisfied.

### Project requirements

| Requirement | Status | Where |
|---|---|---|
| A working software application | ✅ | Runs end to end; `principal doctor` verifies any install |
| Runs on Nebius Token Factory **or** Nebius AI Cloud | ✅ | Token Factory for **both** inference and Sandboxes |
| Uses at least one NVIDIA open source model | ✅ | Three: Nemotron 3 Nano, Super, Ultra |
| Fits one of the four tracks | ✅ | Coding and Agentic Engineering |

### Submission requirements

| Requirement | Status | Where |
|---|---|---|
| URL to a working demo / hosted application | ✅ | [amtdrs.onrender.com](https://amtdrs.onrender.com) — verified live: `/healthz`, dashboard, `/debug/blast-radius` all responding |
| Text description of features and functionality | ✅ | §4 above |
| URL to a public repo (GitHub/GitLab/Bitbucket) | ✅ | [github.com/BugHunterX2101/AMTDRS](https://github.com/BugHunterX2101/AMTDRS) — public, verified |
| Repo contains all source, assets and instructions | ✅ | Source, fixtures, prompts, benchmark, dashboard, docs |
| Open source license file (Apache 2.0 / MIT / MPL 2.0) | ✅ | [`LICENSE`](../LICENSE) — unmodified Apache 2.0 |
| License detectable in the repo's **About** section | ✅ | File named exactly `LICENSE`, verbatim text, `license = "Apache-2.0"` in `pyproject.toml` |
| README with setup instructions | ✅ | [Quickstart](../README.md#quickstart--no-credentials-needed) and [Full setup](../README.md#full-setup--with-nebius-token-factory) |
| README with clear guidance for running the project | ✅ | [Running it](../README.md#running-it) — CLI, API, dashboard, MCP, benchmark |
| Highlight NVIDIA Nemotron / NVIDIA open source model usage | ✅ | [How NVIDIA models and Nebius services are used](../README.md#how-nvidia-models-and-nebius-services-are-used) |
| Highlight where Token Factory accelerated the workflow | ✅ | Same section — Sandboxes subsection, with the concrete 1-vs-36-installs argument |
| Highlight other Nebius tools and services used | ✅ | Same section — component table |
| Demo video < 3 minutes | ⟨record⟩ | Script and shot list in [`DEMO_VIDEO.md`](DEMO_VIDEO.md) |
| Video shows the project functioning (key modules in action) | ⟨record⟩ | Shot list is built around exactly this |
| Video public on YouTube, link on the submission form | ⟨upload⟩ | Must be **Public** or **Unlisted-and-linked**; Private fails judging |
| No third-party trademarks or copyrighted music in the video | ⟨verify⟩ | Use silence or a license-cleared track; see `DEMO_VIDEO.md` |
| Identify the track | ✅ | §1 above, and the README header table |
| Feedback on Token Factory, AI Cloud and NVIDIA tools | ✅ | [`FEEDBACK.md`](FEEDBACK.md) + condensed version in §7 |
| Written explanation if the project pre-existed | ✅ (N/A) | §8 — created during the submission period |
| All materials in English | ✅ | Throughout |
| Complies with third-party open source licenses | ✅ | [`NOTICE`](../NOTICE) lists every dependency and its license |

### Bonus awards

| Award | Eligible | Note |
|---|---|---|
| Most Valuable Feedback ($100 + swag, ×10) | ✅ | Complete the feedback section on the form; [`FEEDBACK.md`](FEEDBACK.md) is deliberately specific and reproducible |
| Best Use of Tavily ($3,000) | ❌ | Requires a functional runtime call to the Tavily API. Principal makes none — claiming it would be false. |

> A project is eligible for **one Overall award OR one Track award, plus one Bonus award.**

---

## Pre-submission checklist

Do these in order. The first four are the ones that silently fail.

1. **Make the repository public.** A private repo is an automatic fail and is the single most common way a finished project loses.
2. **Confirm the About section shows "Apache-2.0."** GitHub's license detector requires the file to be named exactly `LICENSE` with unmodified text. If the badge is missing, the requirement is not met no matter what the file says.
3. **Set the YouTube video to Public** (or Unlisted). Verify in a private browsing window that the link plays without signing in.
4. **Verify the demo URL from a different network.** A link that only resolves on your machine is not a hosted demo.
5. Confirm the video is under 3:00. Judges are not required to watch past it.
6. Confirm the video contains no third-party trademarks and no copyrighted music.
7. Run `principal doctor` on a clean clone and confirm it passes — this is what a judge will do first.
8. Fill every `⟨placeholder⟩` in this document and in the README header table.
9. Select **Coding and Agentic Engineering** on the form.
10. Complete the feedback section on the form and link `FEEDBACK.md`.
11. Submit before **Friday 30 October 2026, 10:00 Pacific**. Devpost closes on the minute.
