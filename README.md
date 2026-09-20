# Principal

**An autonomous technical debt remediation swarm. It refactors a real repository across dozens of files, and it will not open a pull request unless the tests are green.**

Built for the **[Nebius x NVIDIA Global AI Hackathon 2026](https://nebiusglobalaihackathon.devpost.com/)**.

| | |
|---|---|
| **Track** | **Coding and Agentic Engineering** — agents that write, run and test code in Token Factory Sandboxes |
| **Runs on** | Nebius Token Factory (inference + Sandboxes) |
| **Models** | NVIDIA Nemotron 3 — Nano 30B-A3B, Super 120B-A12B, Ultra 550B-A55B |
| **License** | Apache 2.0 |
| **Repository** | [github.com/BugHunterX2101/AMTDRS](https://github.com/BugHunterX2101/AMTDRS) |
| **Live demo** | **[amtdrs.onrender.com](https://amtdrs.onrender.com)** — landing page at `/`, operator console at `/app/`; baseline, code graph and blast radius run with zero credentials against the bundled fixture; free-tier cold start after 15 min idle |
| **Demo video** | *(3 minutes — see [`docs/DEMO_VIDEO.md`](docs/DEMO_VIDEO.md) for the shot list)* |
| **Verification** | 113 tests passing · ruff clean · 3/3 architecture contracts held — [reproduce every number](#every-claim-in-this-readme-and-how-to-check-it) |

---

## Contents

- [The business case](#the-business-case) — why this problem is worth money
- [Why *wide* refactors specifically](#why-wide-refactors-specifically) — where the measured gap is
- [The approach](#the-approach) — many cheap attempts, one hard gate
- [What a run costs](#what-a-run-costs) — real unit economics, real list prices
- [Where this sits in the market](#where-this-sits-in-the-market) — honest competitive position
- [Architecture](#architecture) — diagrams, decisions, and the code to read
- [How NVIDIA models and Nebius services are used](#how-nvidia-models-and-nebius-services-are-used)
- [Every claim in this README, and how to check it](#every-claim-in-this-readme-and-how-to-check-it)
- [Quickstart — no credentials needed](#quickstart--no-credentials-needed)
- [Running it](#running-it) · [Deploying](#deploying-the-hosted-demo) · [Safety](#safety)

---

## The business case

### The bottleneck moved, and the tools did not

Three independent bodies of evidence say the same thing about where 2026 engineering money actually goes.

- **Technical debt is a top-line budget item, not a backlog label.**
  - Deloitte's 2026 Global Technology Leadership Study puts it at **21–40% of total IT spend**; McKinsey's estimate lands in the same 20–40% band.
  - CAST's 2025 *Coding in the Red* analysis of **10 billion lines across 47,000 applications** found **45% of that code is fragile** and **31% too rigid to change without breaking something**.
  - This is not a niche complaint — it is the single largest discretionary line in most engineering budgets.

- **AI adoption already happened. Trust did not follow.** *(Stack Overflow Developer Survey 2026)*
  - **84%** of developers now use AI tools.
  - **29%** trust the accuracy of what they produce — down from 40% the prior year.
  - **46%** actively distrust it.
  - **45%** name *"AI solutions that are almost right, but not quite"* as their single biggest frustration.
  - **66%** report spending more time fixing almost-right AI code than they saved generating it.

- **Speed without verification makes things worse.** *(Google DORA 2025)*
  - **90%** of organizations have adopted AI in software development.
  - AI acts as an **amplifier**: it raises throughput *and* raises instability.
  - Faster generation into a weak verification system produces more unreviewed change, not more shipped value.

Read together, these are one finding:

> **Generation is solved and cheap. Verification is unsolved and expensive, and it is now the constraint.**

Every additional line of plausible-looking generated code adds review load, and review load is paid in senior engineering hours — the most expensive input a software organization buys.

### The specific, expensive job this targets

Not all technical debt is equal. The kind that costs real money has a shape:

> A signature change that touches **forty call sites across nine modules**, where being right in eight modules and wrong in the ninth is *worse than not starting* — because now a human must review a large diff to find the one mistake.

- **What it looks like concretely:**
  - deprecating a parameter, or making an argument keyword-only
  - renaming a widely-used internal API
  - threading a new context object through a call chain
  - migrating off a retired helper

- **Why these specific tasks rot:** they block framework upgrades, sit in a "modernization" epic for four quarters, and every engineer avoids them — the diff is too wide to review confidently and too mechanical to be interesting.

- **Who has this problem:** platform, infrastructure and developer-experience teams at organizations with codebases large enough that a cross-cutting change is a project rather than an afternoon.

- **Where the cost actually sits:** a wide refactor is not expensive because it is hard to *write* — it is expensive because it is hard to *trust*. The money goes to review, and to the risk of a partial migration reaching production.

### What Principal actually sells

- **Not generated code — a verified decision.** Anyone can generate a forty-file diff today; the frontier models are good at it. Nobody can currently hand you one with a machine-checkable guarantee that it does not break the suite, produced without a human babysitting the loop.

- **`NoPR` is a product feature with direct financial value.** It means: *"I tried, here is exactly what I tried, I could not verify it, and I am not going to waste your review time."* That converts an unbounded review task back into a bounded one.

- **Why half-measures are worth less than nothing.** A refactoring tool that *sometimes* ships an unverified change poisons every diff it produces with the possibility that this is one of the bad ones.

---

## Why *wide* refactors specifically

The claim that this is an unsolved problem is not a guess. It is measured, and the measurement is public.

[SWE Atlas Refactoring](https://labs.scale.com/leaderboard/sweatlas-refactoring) is the closest public proxy for the work described above:

- **70 tasks**, drawn from **10 production repositories**
- **6 languages** — Go, TypeScript, Python, C, C++, JavaScript
- **4 refactor types** — decomposition, interface evolution, extraction, relocation

Leaderboard, **read 2026-09-20**:

| Rank | System | Score |
|---:|---|---:|
| 1 | GPT 6 Astra (Codex) xHigh | **59.05** ±6.43 |
| 2 | Fable-5.1 (Claude Code) xHigh | 56.67 ±6.52 |
| 3 | Fable-5 (Claude Code) xHigh | 54.76 ±6.76 |
| 4 | Opus-4.7 (Claude Code) | 48.57 ±6.73 |
| 5 | Opus 4.8 (Claude Code) | 46.67 ±6.75 |
| … | … | … |
| 15 | Kimi-K2.5 (Mini-SWE-Agent) | 20.95 ±6.00 |

What that table means:

- The same class of model scores **above 80% on SWE-Bench Verified** issue resolution.
- The gap between *"fix this bug"* and *"change this interface everywhere"* is therefore roughly **twenty-five points**.
- That gap has **persisted across model generations** — the field has been climbing this specific number for months rather than treating it as solved.

> A leaderboard moves. These figures were re-verified against the live page on 2026-09-20; re-check before quoting them.

**The thesis of this project: the remaining gap is a scaffolding problem more than a weights problem.**

- A model right 60% of the time on a wide refactor is **unusable** if you ship its first answer.
- The same model is **very useful** if you can generate several answers cheaply and let a test suite pick.
- That is an engineering problem, and it is the one Principal solves.

---

## The approach

Principal replaces *one careful attempt* with *many cheap attempts and a hard gate*. For each unit of work it:

1. generates **several independent candidate patches in parallel**;
2. pushes each through **four gates**;
3. **races them — the first one to go green wins**;
4. **cancels the losers**, whose failure costs nothing because it happened inside a private sandbox nobody ever sees.

Two properties make that more than a trick:

- **No scoring function, no model in the accept path.** The decision is a `pytest` exit code, not a language model's opinion of a diff.
- **Losing is free.** A failed candidate corrupts only its own fork — there is no shared working tree to clean up.

This is affordable because of how the gates are ordered:

| Gate | What it proves | Where it runs | Cost |
|---|---|---|---|
| **1. Scope** | the patch only touches files the blast radius says it may | local | microseconds |
| **2. Syntax** | every changed file still parses | local, tree-sitter | milliseconds |
| **3. Local tests** | the tests that cover the changed code pass | Token Factory Sandbox | seconds |
| **4. Integration** | all accepted patches still pass together | Token Factory Sandbox, once | seconds |

Gates 1 and 2 never touch a sandbox. Most bad candidates die there, which is exactly what makes generous fan-out affordable — **you can afford to be wrong eight times out of nine if eight of those failures are free.**

### The guarantee

**Fail closed. No green test, no PR.**

`NoPR` is a success state, not a failure state. If Principal cannot verify the change, it stops and publishes a report explaining precisely where it stopped and what it tried.

---

## What a run costs

The economics are the reason the architecture is shaped this way, so here they are with real numbers rather than adjectives.

**Published Nebius Token Factory list prices, read 2026-09-20:**

| Tier | Model | Input / 1M | Output / 1M |
|---|---|---:|---:|
| Nano | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | $0.05 | $0.20 |
| Super | `nvidia/nemotron-3-super-120b-a12b` | $0.30 | $0.90 |
| Ultra | `nvidia/Nemotron-3-Ultra-550b-a55b` | $1.00 | $3.00 |

Principal ships a **default per-job budget of 2,000,000 tokens** (`token_budget_default`, [`principal/config.py`](principal/config.py)), enforced by a reserve-and-refuse budget manager that **refuses rather than truncates**. That default gives a hard, arithmetic ceiling on what one job can cost:

- **Pathological worst case** — every one of the 2M tokens billed as Ultra *output*: **$6.00**.
- **Realistic shape** — planning is *one* Ultra call per job; candidate generation is Nano, which is where nearly all volume lives (3 candidates × up to 12 tasks); repair is Super and only fires on a red candidate. A Nano-dominated 2M-token mix lands **well under $1**.

Set against the human cost of the same job:

- US in-house mid-to-senior engineering time runs roughly **$85–110/hour** fully loaded in 2026.
- A wide refactor — write it, chase the call sites, fix what broke, then get it reviewed — is conservatively **a half-day to two days** of engineer *plus* reviewer time.
- That is **several hundred to a couple of thousand dollars** of the most expensive input a software organization buys.

**The honest framing:**

- Principal **does not remove the reviewer.** The reviewer is inside the trust boundary by design, and a human still merges.
- What it removes is the **unbounded part** of the review: the reviewer is no longer auditing forty edits hunting for one mistake, they are sanity-checking a change that already passed a real suite in a real sandbox, with per-candidate evidence attached.
- **The token cost is not the interesting number.** The interesting number is that **the cost of being wrong fell to zero** — wrong attempts die in a private fork that nobody reviews and nothing ever sees.

> These are list prices and public salary ranges, not a customer case study. No production deployment has been measured. The arithmetic above is reproducible; the ROI claim is a reasoned argument from it, and is labelled as such deliberately.

---

## Where this sits in the market

Two mature categories already address parts of this problem. Neither covers the middle, and that gap is the wedge.

| | **Coding agents**<br>(Codex, Claude Code, Cursor, Devin) | **Deterministic mass refactoring**<br>([OpenRewrite](https://docs.openrewrite.org/) / [Moderne](https://moderne.ai/)) | **Principal** |
|---|---|---|---|
| Handles a *novel*, bespoke refactor | ✅ yes | ❌ needs a pre-written recipe | ✅ yes |
| Correctness signal | human review | AST transform is correct by construction | **real test-suite exit code** |
| Will it ship something unverified? | yes — that is what review is for | no | **no — fails closed** |
| Scales across many repos | partially | ✅ its whole design | one repo per job |
| Measured ceiling on wide refactors | ~59 on SWE Atlas | n/a — not a model | gated by the same models, but **races several attempts** |

- **Coding agents** — general but unverified.
  - They produce a diff and hand you the review problem.
  - The SWE Atlas number above is the measured ceiling on that approach for this task class.

- **OpenRewrite / Moderne** — the serious incumbent, and proof the market is real.
  - Moderne raised a **$30M Series B in February 2025** (Acrew Capital, with Intel Capital, Amex Ventures, Morgan Stanley and others) explicitly to attack enterprise technical debt.
  - Their approach is deterministic AST transformation via recipes: exceptionally safe and auditable.
  - But it **requires someone to have written the recipe first** — excellent for the hundredth JUnit-4-to-5 migration, structurally unable to help with the one-off signature change nobody has ever written a recipe for.

- **Principal takes the third position** — LLM generality for the diff, deterministic machinery for the decision.
  - The model proposes; a real test run in a real sandbox disposes.
  - The contribution is not the model and not the gate, but the fact that **no model sits anywhere in the accept path**.

---

## Architecture

### System context

```mermaid
flowchart LR
    DEV["Developer"] -->|"goal + target symbol"| P((Principal))
    P -->|"draft PR"| GH["GitHub"]
    REV["Human reviewer"] -->|"approves, merges"| GH
    P <-->|"chat completions"| TF["Token Factory\ninference"]
    P <-->|"checkpoint / fork / run"| SBX["Token Factory\nSandboxes"]
    P -->|"clone at commit"| REPO[("Target repository")]
    SBX -->|"test results,\ncoverage"| P

    style P fill:#2563eb,color:#fff,stroke:#1e40af
    style SBX fill:#059669,color:#fff,stroke:#047857
    style TF fill:#059669,color:#fff,stroke:#047857
```

Principal never merges anything. The reviewer is inside the trust boundary by design, not an optional extra — every guarantee below assumes a human approves before code lands.

### Containers

```mermaid
flowchart TB
    UI["Dashboard\n(React + SSE)"] --> API["HTTP API\n(FastAPI + SSE)"]
    subgraph ORCH["Orchestrator process — one SQLite file, one Python process"]
        API --> JOB["Job state machine"]
        JOB --> SCHED["Scheduler\n(semaphores, candidate race)"]
        SCHED --> AGENTS["Agents\n(planner, coder, repairer, reporter)"]
        SCHED --> GATES["Gate pipeline\n(scope, syntax, tests, behaviour)"]
        JOB --> PUB["Publisher\n(the only GitHub credential)"]
        JOB --> STORE[("SQLite\n+ event log")]
    end
    AGENTS --> MODELS["Model client"]
    GATES --> SANDBOX["Sandbox client"]
    MCP["code-graph MCP\n(5 read-only tools)"] --> STORE
    SCHED -. optional .-> MCP
    MODELS --> TF2["Token Factory\ninference"]
    SANDBOX --> SBX2["Token Factory\nSandboxes"]
    PUB --> GH2["GitHub API"]

    style JOB fill:#2563eb,color:#fff
    style GATES fill:#dc2626,color:#fff
    style SANDBOX fill:#059669,color:#fff
    style SBX2 fill:#059669,color:#fff
    style TF2 fill:#059669,color:#fff
```

- **Nine components** in one process, one mounted MCP sub-app, one static frontend.
- **Dependencies point downward only**, enforced by `import-linter` in CI:
  - `agents` cannot import `sandbox`
  - `gates` cannot import `models` or `agents`
- That second rule is the architecture's central claim — **no model in the accept path** — expressed as something a machine checks rather than a sentence in a README.

### The verification pipeline

```mermaid
flowchart TD
    G["goal + repo + target symbol"] --> ING
    subgraph ING["1. INGEST"]
        direction TB
        I1["clone, install, run full suite"] --> I2["C0 — green baseline image\n+ per-test coverage map"]
    end
    ING -->|"suite already red?"| ABORT1["Aborted"]
    ING --> MAP

    subgraph MAP["2. MAP"]
        direction TB
        M1["tree-sitter → symbols, call edges, import edges"] --> M2["blast radius = reverse BFS,\ndepth 3, cap 40"]
    end
    MAP -->|"radius exceeds cap"| ABORT2["Aborted\n(raises, never truncates)"]
    MAP --> PLAN

    PLAN["3. PLAN — Nemotron 3 Ultra\nordered tasks, one file each,\ndependency-aware waves"] --> EXEC

    subgraph EXEC["4. EXECUTE — per task"]
        direction TB
        E0["3 candidates, Nemotron 3 Nano,\ntemperatures 0.0 / 0.4 / 0.8"]
        E0 --> EG1["gate 1: scope"]
        EG1 --> EG2["gate 2: syntax"]
        EG2 --> EG3["gate 3: local tests\n(sandbox)"]
        EG3 -->|"first green"| WIN["winner — losers cancelled"]
        EG3 -->|"all red"| REP["repair, Nemotron 3 Super\nbounded, stack trace only"]
        REP --> EG3
    end
    EXEC -->|">30% tasks discarded"| NOPR1["NoPR"]
    EXEC --> INTEG

    subgraph INTEG["5. INTEGRATE"]
        direction TB
        N1["all accepted patches together,\nfull suite, gate 4"] --> N2["API delta · coverage floor ·\ntest count invariant"]
    end
    INTEG -->|"green"| PR["Draft pull request\n+ evidence table"]
    INTEG -->|"red / behaviour check fails"| NOPR2["NoPR report\n(a success state)"]

    style ABORT1 fill:#dc2626,color:#fff
    style ABORT2 fill:#dc2626,color:#fff
    style NOPR1 fill:#d97706,color:#fff
    style NOPR2 fill:#d97706,color:#fff
    style PR fill:#059669,color:#fff
    style WIN fill:#059669,color:#fff
```

### Job state machine

```mermaid
stateDiagram-v2
    [*] --> Ingesting
    Ingesting --> Baselining: image ready
    Ingesting --> Aborted: import failed
    Baselining --> Mapping: suite green
    Baselining --> Aborted: suite already red
    Mapping --> Planning: graph built
    Mapping --> Aborted: blast radius over cap
    Planning --> Executing: task list valid
    Planning --> Aborted: plan unparseable twice
    Executing --> Integrating: all tasks settled
    Executing --> NoPR: over 30% discarded
    Integrating --> Publishing: full suite green
    Integrating --> NoPR: integration red or behaviour check failed
    Publishing --> Done
    Aborted --> [*]
    NoPR --> [*]
    Done --> [*]
```

`NoPR` is drawn with the same visual weight as `Done` throughout the dashboard, on purpose. It is a correct result — the system did its job and correctly declined to ship — and rendering it as an error would teach the opposite of the intended lesson.

### One attempt, in sequence

```mermaid
sequenceDiagram
    participant S as Scheduler
    participant C as Coder (Nano)
    participant M as Model client
    participant GA as Gates
    participant SB as Sandbox

    S->>C: task + context pack
    C->>M: complete(Nano, temperature)
    M-->>C: text, parsed for a diff block
    C-->>S: unified diff
    S->>GA: evaluate(diff, task)
    Note over GA: gate 1 scope, gate 2 syntax — local, no sandbox
    alt rejected locally
        GA-->>S: verdict scope/syntax — zero sandbox spend
    else accepted locally
        GA->>SB: fork C0, apply diff, run selected tests
        SB-->>GA: exit code, log, result image
        GA-->>S: verdict ok or red
    end
```

The `alt` branch is the cost model made visible. A rejected candidate spends one cheap completion and zero sandbox operations, which is why three candidates per task is affordable at all.

### Design decisions worth reading the code for

- **The blast radius raises, it does not truncate.**
  - A reverse-edge BFS over call and import edges, **depth 3, capped at 40 files**.
  - Over the cap, Principal **aborts with `RADIUS_TOO_LARGE`** rather than silently working on the first 40.
  - *Why it matters:* a truncated radius produces a patch that **looks complete and is not** — the single most dangerous failure mode this system could have.

- **Unresolved call sites are reported, not hidden.**
  - A dynamic dispatch — `registry[name](user)` — cannot be proved to reach the target, so Principal lists it in the PR body under *"what a human must check"*.
  - Getting that list *short and honest* took real work: naive reporting floods it with builtins until nobody reads it.
  - [`principal/graph/resolve.py`](principal/graph/resolve.py) separates a genuinely unresolvable dynamic call from `len()` — on the bundled fixture, the difference between noise and **exactly 2 real entries**, both in `src/registry.py`.

- **Three behaviour-preservation checks beyond "tests pass".** Tests passing is necessary, not sufficient — a patch that deletes a function *and its tests* passes. So:
  - public API delta is checked against the plan's **declared** removals,
  - coverage **may not fall**,
  - the test count **may not shrink**.

- **Per-test coverage drives test selection.**
  - The baseline runs `pytest --cov-context=test`, yielding a test→line map.
  - Each fork runs only the tests that actually cover the lines it changed — what keeps gate 3 at **seconds rather than minutes**.

- **The dependency rule is enforced by CI, not by discipline.**
  - `import-linter` runs as `lint-imports` — **not** `python -m importlinter.cli lint`, which silently exits 0 without evaluating a contract.
  - Caught and fixed during development; it is exactly the kind of bug that makes a safety claim decorative.

- **The event log is the product.**
  - Every state change is an **append-only row**.
  - The API streams it as SSE with `Last-Event-ID` resume, and every job writes a JSONL trace.
  - A demo laptop that sleeps for ten seconds reconnects into a live view, not an empty one.
  - Because the run id lives in the URL (`/app/?job=<id>`), a finished run is reconstructible **from a link alone**.

- **Every safety claim has a fixture.**
  - [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) states the threat matrix and residual risks explicitly.
  - `tests/unit/test_adversarial_corpus.py` asserts the exact gate verdict for **10 named adversarial diffs**.
  - That includes `correct.diff`, which **must pass** — because a gate suite that only proves things get *rejected* is satisfied by a gate that rejects everything.

---

## How NVIDIA models and Nebius services are used

> *This section answers the hackathon's requirement to show how NVIDIA Nemotron and other NVIDIA open source models were used, where Token Factory accelerated the workflow, and which other Nebius tools and services the project depends on.*

### NVIDIA Nemotron 3 — a hybrid architecture, used the way it was designed to be used

Nemotron 3 is a **hybrid Mamba-Transformer Mixture-of-Experts** family:

- **Mamba-2 layers** — linear-time long-range sequence modelling
- **Transformer attention layers** — precision on code and math
- **MoE routing** — only a fraction of parameters activate per token
- *Nano 30B-A3B, concretely:* 23 Mamba-2 layers, 23 MoE layers, 6 attention layers — **3.5B of 30B parameters active** per token

That combination is not incidental to this project:

- It is **why wide fan-out is economically viable at all** over long, low-density prompts — a whole blast radius, every call site, with surrounding context.
- A pure quadratic-attention model at the same accuracy would cost far more per candidate, and the entire *"generate three, race them"* design would collapse.

Principal uses **three** tiers rather than one, because the work is genuinely three different jobs with three different cost-to-difficulty ratios. Every model call is tagged with its tier and its token cost recorded in the run metadata, so **the routing is measurable rather than asserted**.

| Tier | Model | Scale | What it does | Why this tier |
|---|---|---|---|---|
| **Nano** | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | 30B total, 3.5B active | generates candidate patches; summarises failing test output | Candidate generation is the highest-volume call in the system — three per task, dozens per job — and the one where being wrong is cheapest, because gate 3 catches it. A 3.5B active-parameter MoE at $0.05/$0.20 per 1M makes wide fan-out economically possible; the design collapses if every candidate costs Ultra money. |
| **Super** | `nvidia/nemotron-3-super-120b-a12b` | 120B total, 12B active | repairs a candidate that failed its tests | Repair is narrow and well-specified — here is the diff, here is the failure, fix it — but needs real reasoning about *why* a test failed. Documented latent-MoE routing to ~4× the effective experts at the same inference cost buys that without Ultra's latency inside a retry loop. |
| **Ultra** | `nvidia/Nemotron-3-Ultra-550b-a55b` | 550B total, 55B active | decomposes the goal into an ordered, dependency-aware task plan | Planning runs **once per job** and every downstream decision inherits its mistakes. This is the one place where paying the most for the best long-horizon reasoning is unambiguously correct. |

The planner tier is **configurable** (`PRINCIPAL_PLANNER_TIER=super|ultra`) specifically so the Super-versus-Ultra question can be *measured* rather than assumed — Super is documented for long-horizon agentic planning at a fraction of Ultra's active parameters, and `bench/` exists to answer whether that holds for this specific planning task rather than take the claim on faith.

#### The `reasoning_content` trap, and the boot-time capability probe

The trap, in order:

1. Reasoning models served over an OpenAI-compatible API can return their output in `reasoning_content` and leave `content` **empty**.
2. Code that reads `choices[0].message.content` and trusts it gets an empty string.
3. **An empty string is not an error** — it is a *successful* API call that silently produces nothing.
4. So the failure surfaces three layers away as *"the agent generated no patch"*. This is the part that costs a hackathon weekend.

Principal does not guess:

- At boot it runs a **3×3 capability probe** — each of the three models against each of the three output protocols (JSON schema / tool calls / fenced text).
- Each model is **pinned to the protocol it actually demonstrated**, and the results print as a table at startup.
- `PRINCIPAL_FORCE_PROTOCOL` pins one protocol for everything if you need determinism.

```
model                                    schema  tools  text   chosen
nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B    ok      ok     ok     schema
nvidia/nemotron-3-super-120b-a12b        ok      ok     ok     schema
nvidia/Nemotron-3-Ultra-550b-a55b        ok      ok     ok     schema
```

This turns an entire class of silent, misattributed failure into fifteen seconds of startup cost and a table you can read.

### Nebius Token Factory — Sandboxes, and why they are the whole design

Principal uses Token Factory for two distinct things.

**1. Inference** — the OpenAI-compatible endpoint at `https://api.tokenfactory.nebius.com/v1/`.

- The entire model layer is the official `openai` Python SDK pointed at a different `base_url` — **zero custom HTTP client, zero bespoke retry logic**.
- Rate-limit headroom is read straight off the response headers via `with_raw_response`.
- Default allowance: **60 requests/min and 400,000 tokens/min**, scaling **+20% per 15-minute window** sustained above 80% utilisation, to a ceiling of **20× base**.
- That is exactly why the client surfaces `x-ratelimit-remaining-*` headers **live on the dashboard** rather than discovering a slowdown by guessing.

**2. Sandboxes** — not a convenience; the reason the architecture works at all.

- Every sandbox gets **its own microVM, built to run untrusted code**.
- Git-like branching: **fork execution state at any checkpoint**, run parallel explorations, roll back instantly.
- The Contree SDK exposes this as **one primitive** rather than separate `fork`/`checkpoint` calls — `image.run(shell=..., disposable=False)` returns a *new* image, and it is **content-addressed**:

```python
# Two runs of the identical command from the identical parent return the
# identical image UUID — no new execution, no new cost.
same1 = await image.run(shell="echo same", disposable=False)
same2 = await image.run(shell="echo same", disposable=False)
assert same1.uuid == same2.uuid
```

Two consequences fall directly out of that, and both are load-bearing:

- **Checkpoint-fork is the unit of state.** The green baseline `C0` is built once — clone, install, run the suite — and every candidate for every task forks from that same image. Dependency installation, typically the dominant cost of any CI-like workload, is paid **once per job**, not once per candidate. With three candidates across a dozen tasks, that is the difference between **one install and thirty-six**.
- **Failure is free because it is private.** A candidate that breaks the build breaks *its own fork*. There is no shared mutable working tree to corrupt, no cleanup, no rollback, no interference between parallel attempts. This is what makes "generate three, race them, discard two" reasonable rather than reckless.

**The honest comparison.** Building this on ordinary containers would force one of two losses:

- **serialise the work** — losing the parallelism the whole thesis depends on, or
- **re-install dependencies per candidate** — losing the economics.

> **Sandboxes accelerated the workflow by making the parallel search *affordable*, not merely by making it faster.**

Two more things fall out for free:

- **The fork tree needs no bookkeeping.** `attempt.parent_image` and `attempt.result_image` already describe the graph the platform maintains, so the dashboard's fork-tree visualisation is just a render of it.
- **The correctness signal itself lives here.** Gates 3 and 4 are real `pytest` runs, on real installed dependencies, in an isolated microVM. The accept decision is a **process exit code from a genuine test run** — not a model's opinion of a diff.

Documented beta service limits: **50 concurrent operations**, **180-day checkpoint retention** for tagged images.

### Other Nebius and NVIDIA components

| Component | Use |
|---|---|
| Token Factory OpenAI-compatible API | all inference; `openai` SDK with a changed `base_url` |
| Token Factory Sandboxes (`contree-sdk`) | baseline build, all four gates, integration verification |
| Token Factory rate-limit headers | live budget headroom, surfaced in `GET /jobs/{id}` and the dashboard |
| Token Factory model registry (`GET /v1/models`) | model ids resolved at boot, so a typo fails in five seconds rather than mid-job |
| NVIDIA Nemotron 3 Nano / Super / Ultra | candidates / repairs / planning |

Detailed, specific engineering feedback on all of the above — **nine issues ranked by what they cost**, several verified against the installed SDK source rather than the docs — is in **[`docs/FEEDBACK.md`](docs/FEEDBACK.md)**.

---

## Every claim in this README, and how to check it

Claims in a hackathon README are cheap. These are the commands that make them expensive to fake. Every row was re-run on **2026-09-20** against the committed tree.

| Claim | Command | Result |
|---|---|---|
| 113 tests pass | `pytest -q` | `113 passed` |
| No lint violations | `ruff check principal mcp_code_graph bench tests spikes` | `All checks passed!` |
| No model in the accept path (+2 more contracts) | `lint-imports` | `Contracts: 3 kept, 0 broken` |
| Blast radius finds 7 source files | `principal radius --repo tests/fixtures/mini_repo --commit deadbeef --target src.auth.session.create` | `files: 7` |
| …and 24 call sites, 14 of which name `create` (8 in `src/`) | same command | `call_sites: 24` |
| …and exactly 2 genuinely unresolvable dynamic dispatches | same command | both in `src/registry.py`, lines 14–15 |
| …at reverse-BFS depth 3 | same command | `depth_reached: 3` |
| Fixture repo is 14 Python modules with a green suite | `pytest tests/fixtures/mini_repo --collect-only -q` | `14 tests collected` |
| Adversarial corpus is 10 named diffs with asserted verdicts | `ls tests/fixtures/diffs/` | 10 files, incl. `correct.diff` which must **pass** |
| 8 HTTP endpoints | `grep '@router\.' principal/api/routes.py` | 8 routes |
| 5 read-only MCP tools, no write tool | `mcp_code_graph/tools.py` | `find_symbol`, `callers_of`, `blast_radius`, `tests_covering`, `read_span` |
| SWE Atlas leader at 59.05 | [labs.scale.com](https://labs.scale.com/leaderboard/sweatlas-refactoring) | re-read 2026-09-20 |
| Default budget cannot exceed $6.00/job | `token_budget_default = 2_000_000` × $3.00/1M Ultra output | arithmetic, worst case |

**Scale of the thing, for calibration:**

- **~7,300 lines of Python** across `principal/`, `mcp_code_graph/` and `bench/`
- **~1,300 lines** of tests
- **~4,600 lines** of dashboard

**What is *not* claimed**, stated plainly because a README that only lists strengths is not evidence:

- **No production deployment has been measured.** The ROI argument above is arithmetic from list prices and public salary ranges, not a case study.
- **`bench/tasks.json` currently pins one task**, not a suite. The harness ([`bench/harness.py`](bench/harness.py)) drives a real `run_job()` per `(task, arm)` and re-verifies independently, and it takes as many tasks as you add — but the published task set is one, and calling it a benchmark suite today would be overstating it.
- **Gate 3 is Python-only.** The code graph parses TypeScript (there is a `typescript.scm` grammar and the graph/radius work), but test execution is `pytest`. TypeScript stops at static analysis.
- **Residual risks are enumerated, not waved away**, in [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) — an untested private symbol can still be deleted; sandbox network egress is not yet disableable through the platform SDK.

---

## Quickstart — no credentials needed

Principal runs its entire **deterministic half** with **no Nebius account at all** — against a bundled fixture repository of 14 Python modules, using an in-process sandbox that executes real `git` and real `pytest`.

- ✅ **What you get for free:** a genuinely verified green baseline, real per-test coverage, a real tree-sitter code graph, a real blast radius.
- ⛔ **Where it stops:** planning — generating a patch needs an inference key.

That boundary is deliberate, and it is the useful one — **everything you can check for free is the half a model cannot fake.**

```bash
git clone https://github.com/BugHunterX2101/AMTDRS.git
cd AMTDRS

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

principal doctor
```

`doctor` checks:

- the Python version and every import
- the database schema and migration state
- and — *only if credentials are present* — inference, model ids, and sandbox access as **two separate probes**

With no credentials you should see passes and three warnings:

```
  [PASS] python                     3.11.9 (need >= 3.11)
  [PASS] import tree_sitter         source parsing
  [PASS] import contree_sdk         Token Factory Sandboxes client
  [PASS] database                   principal.db — 12 tables, migrations applied
  [WARN] nebius_api_key             unset — Principal will run against the in-process FakeSandbox…
  doctor: ready
```

Now show the static analysis, which needs nothing but the source tree:

```bash
principal radius \
  --repo tests/fixtures/mini_repo \
  --commit deadbeef \
  --target src.auth.session.create
```

In about two seconds that prints everything a change to `create()` can reach:

- **7 source files** and **2 test files** inside the radius
- **24 call sites**, each with its confidence — **14 naming `create` itself**, 8 of those in `src/`
- **2 genuinely unresolvable dynamic dispatches**, both in `src/registry.py`
- at **reverse-BFS depth 3**

Then run a job against the fixture:

```bash
principal run \
  --repo tests/fixtures/mini_repo \
  --commit deadbeef \
  --target src.auth.session.create \
  --goal "make ttl keyword-only and update every call site" \
  --fake-sandbox
```

With no `NEBIUS_API_KEY` set, that will:

1. clone and install the fixture,
2. run its **14 tests green** to build the verified `C0` baseline,
3. build the code graph and compute the blast radius,
4. then **abort at `Planning` with a 401** — the correct behaviour, and exactly the boundary described above.

Set a key and the same command runs through to `Done` or `NoPR`.

And the tests:

```bash
make test          # or: pytest — 113 tests, unit + integration
make lint          # ruff + import-linter (the architecture contracts)
```

## Full setup — with Nebius Token Factory

```bash
cp .env.example .env
```

Fill in two values:

| Variable | Where it comes from | Notes |
|---|---|---|
| `NEBIUS_API_KEY` | Token Factory console → API keys | Used for inference **and**, by default, as the Sandboxes bearer token. |
| `NEBIUS_PROJECT_ID` | Token Factory console → your project | Sent as the mandatory `Project` header by the Contree SDK. |

> **Sandbox access is granted per project, not per key.**
>
> - A key that does inference perfectly may still get `403` on Sandboxes — **the single most common setup failure**.
> - That is why `principal doctor` probes them as **two separate checks**: it does one trivial disposable sandbox run and tells you which of the two is broken.
> - If your account issues a **separate** Sandboxes token, set `CONTREE_TOKEN` — it is preferred over `NEBIUS_API_KEY` automatically for every sandbox call.

Then:

```bash
principal doctor          # expect PASS on inference, all three models, and sandboxes
principal serve           # http://127.0.0.1:8000
```

### Optional: opening pull requests

Principal only opens a PR when gate 4 is green. To enable it:

| Variable | Value |
|---|---|
| `GITHUB_TOKEN` | A **fine-grained** token with exactly `contents: write` and `pull_requests: write` |
| `PRINCIPAL_FORK_REPO` | `owner/name` of the fork Principal pushes branches to |

> **Scope the token to a fork you own — never to an upstream repository.**
>
> - Principal opens **draft** PRs only.
> - Leave both unset and jobs still run to completion and still produce the full verification report — only the publish step is skipped.

## Running it

### HTTP API

```bash
principal serve --host 0.0.0.0 --port 8000
```

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/healthz` | liveness + what the process is actually configured to talk to |
| `POST` | `/jobs` | start a job — returns `202` and an SSE stream URL |
| `GET` | `/jobs/{id}` | state, counts, token budget, live rate-limit headroom |
| `GET` | `/jobs/{id}/events` | SSE, resumable via `Last-Event-ID` |
| `GET` | `/jobs/{id}/tree` | the sandbox fork tree |
| `GET` | `/artifacts/{id}` | any diff, test log or report by id |
| `POST` | `/jobs/{id}/cancel` | cancels in-flight sandbox operations too |
| `GET` | `/debug/blast-radius` | blast radius of any symbol, no job required |

Errors are a **documented envelope, not a bare 500**:

- `PrincipalError` carries a machine-readable `code`, a `retryable` flag and the `job_id`.
- An app-level handler maps each code to the right status — `RADIUS_TOO_LARGE` → **422**, `TARGET_NOT_FOUND` → **404**, `BUDGET_EXHAUSTED` → **429**, anything unmapped → **400**.

```bash
curl -X POST localhost:8000/jobs -H 'content-type: application/json' -d '{
  "repo_url": "tests/fixtures/mini_repo",
  "commit_sha": "deadbeef",
  "target_fqn": "src.auth.session.create",
  "goal": "make ttl keyword-only and update every call site"
}'
```

### Dashboard

Two pages, built into one `dist/` and served by `principal serve`:

| Path | Page |
|---|---|
| `/` | landing page — what Principal is and why the guarantee is the product |
| `/app/` | operator console — the live run |
| `/app/?job=<job-id>` | any past run, replayed from the event log by the same reducer |

```bash
cd dashboard && npm install && npm run build
```

The console renders:

- the **live fork tree** and per-candidate gate progress
- an **evidence drawer** — diff, test output and coverage delta for any attempt
- **run economics** and the NoPR report
- a **cancel** control that stops a job in flight, sandbox operations included

Two details that matter on a demo:

- Because the run id lives in the URL, **a reload or a slept laptop resumes the same run** rather than losing it — and a run can be linked to.
- `PRINCIPAL_SLOW_MO_MS=250` paces event emission so the fan-out is legible on video.

### Code-graph MCP server

The static analysis is exposed as **five read-only MCP tools**:

| Tool | Returns |
|---|---|
| `find_symbol` | a symbol by name, with its definition span |
| `callers_of` | every recorded caller of an FQN |
| `blast_radius` | the reverse-BFS radius of a symbol |
| `tests_covering` | the tests whose coverage touches a symbol |
| `read_span` | an exact line range of a file |

Two ways to reach them:

- `principal serve` mounts them over **streamable HTTP at `/mcp/`**
- `python -m mcp_code_graph.server` runs the identical server object over **stdio** for any MCP client

> **Read-only by construction** — there is no write tool, no shell tool, and nothing that names a sandbox. An agent holding this toolset can look at the code and nothing else.

### Benchmark

```bash
make bench
```

- **Three arms** over the same task set:
  - **A** — single-shot, no gates
  - **B** — single candidate, gates on
  - **C** — full swarm
- **Three result buckets** — verified / failed on merit / excluded as infrastructure.
- *Why three and not two:* collapsing a sandbox timeout into "the patch was wrong" is dishonest in both directions — it understates the verified refactor rate **and** hides a platform problem that belongs in the tooling feedback.

> Per the honesty note above, the published task set is currently **one pinned task**, not a suite.

## Deploying the hosted demo

Because the app runs with no credentials against the bundled fixture, any container host works:

```bash
docker build -t principal .
docker run -p 8000:8000 principal
```

With credentials, pass them through:

```bash
docker run -p 8000:8000 -e NEBIUS_API_KEY=... -e NEBIUS_PROJECT_ID=... principal
```

**Live free-tier deploy:**

- A `render.yaml` Blueprint is included, and it is what serves [amtdrs.onrender.com](https://amtdrs.onrender.com).
- Render builds the **exact Dockerfile above**, with no credit card required, and auto-deploys on every push to `main`.
- [`docs/DEPLOY.md`](docs/DEPLOY.md) has the setup steps — and why Cloud Run and Hugging Face Spaces were tried first and ruled out for a genuinely zero-cost path on this account.

---

## Repository structure

```text
AMTDRS/
├── principal/                       the orchestrator — one process, one SQLite file
│   ├── cli.py                       serve | doctor | run | radius
│   ├── config.py                    every tunable, recorded into each job's metadata
│   ├── errors.py                    error taxonomy — retryable? system's fault or platform's?
│   ├── diffs.py                     sentinel extraction + unified-diff parsing (no model here)
│   │
│   ├── db/
│   │   ├── schema.sql                code graph + run-record tables
│   │   ├── store.py                  typed row access, no ORM
│   │   └── events.py                 append event + row, one transaction — the event log is the product
│   │
│   ├── graph/                        tree-sitter parsing → the code graph
│   │   ├── languages/                grammar registry (python.scm, typescript.scm)
│   │   ├── parse.py                  tree-sitter wrapper, query execution
│   │   ├── build.py                  two-pass construction: symbols, then edges
│   │   ├── resolve.py                static vs. heuristic callee resolution
│   │   ├── radius.py                 reverse-edge BFS, depth 3, cap 40 — raises, never truncates
│   │   └── embed.py                  convention examples only
│   │
│   ├── sandbox/                      Contree SDK wrapper — the only writes in the system
│   │   ├── client.py                 operation-id capture for cancellation + crash recovery
│   │   ├── baseline.py               C0 construction, tagging, coverage
│   │   ├── fake.py                   in-process sandbox with real content-addressed semantics
│   │   ├── runner.py                 one run per attempt, sentinel parsing
│   │   └── scripts.py                shell templates, one per phase
│   │
│   ├── models/                       Token Factory client
│   │   ├── registry.py               resolve model ids against list-models
│   │   ├── probe.py                  the 3×3 capability probe
│   │   ├── protocols.py              schema / tools / text adapters
│   │   ├── client.py                 budget, retries, reasoning_content extraction
│   │   └── cache.py                  content-addressed disk cache
│   │
│   ├── agents/                       one pure function per role, no shared memory
│   │   ├── contracts.py              pydantic models, repair-kind taxonomy
│   │   ├── planner.py                Ultra — goal → ordered task list
│   │   ├── coder.py                  Nano — task → unified diff
│   │   ├── repairer.py               Super — failing diff + stack trace → revised diff
│   │   ├── reporter.py               artifacts → PR title/body/risk list
│   │   └── prompts/*.md
│   │
│   ├── gates/                        deterministic, total, never raises
│   │   ├── pipeline.py               the Verdict type + ordering
│   │   ├── scope.py                  gate 1 — local
│   │   ├── syntax.py                 gate 2 — local
│   │   ├── tests.py                  gate 3 — selection + sandbox run
│   │   ├── apply.py                  diff application onto the local snapshot
│   │   └── behaviour.py              API delta, coverage floor, test-count invariant
│   │
│   ├── orchestrator/
│   │   ├── job.py                    the state machine
│   │   ├── scheduler.py              semaphores, the candidate race, first-green-wins
│   │   ├── ordering.py               topological + file-conflict wave splitting
│   │   └── budget.py                 reserve-and-refuse, never truncate
│   │
│   ├── publish/
│   │   ├── github.py                 the only GitHub-credential holder, one call site
│   │   └── prbody.py                 evidence-table PR body
│   │
│   └── api/
│       ├── app.py                    FastAPI, lifespan (capability probe, sandbox probe, MCP mount)
│       ├── routes.py                 the eight endpoints incl. /healthz
│       ├── stream.py                 SSE from the event table — subscribe first, then replay
│       └── schemas.py                request/response models
│
├── mcp_code_graph/                  5 read-only MCP tools over the code graph
│   ├── server.py                     FastMCP adapter, mounted at /mcp/ and runnable over stdio
│   └── tools.py                      find_symbol, callers_of, blast_radius, tests_covering, read_span
│
├── dashboard/                        React + Vite, two pages, one dist/
│   ├── index.html                    landing page entry          → /
│   ├── app/index.html                operator console entry      → /app/
│   ├── vite.config.js                multi-page build, dev proxy to the API
│   └── src/
│       ├── App.jsx                   console layout; run id lives in ?job=
│       ├── components.jsx            Masthead, TaskBoard, ForkTree, Outcome, Economics, EvidenceDrawer…
│       ├── Backdrop.jsx              WebGL2 node-lattice backdrop, one draw call, verdict-tinted
│       ├── useJobStream.js           the event-log reducer — resumable via Last-Event-ID
│       ├── styles.css                console styles, dual-tone section accents
│       └── landing/                  Hero, Pipeline, Evidence, Compare, Pricing, FAQ, Footer…
│
├── bench/                            three arms, honest three-bucket scoring
│   ├── harness.py                    drives real run_job() per (task, arm), independent re-verification
│   ├── arms.py                       A: no gates · B: gates, one candidate · C: full swarm
│   ├── score.py                      verified / failed-on-merit / excluded-as-infrastructure
│   └── tasks.json                    pinned task definitions, published
│
├── spikes/                           throwaway, answer-one-question scripts
│   ├── spike_sandbox.py              sandbox reachability, content-addressed dedup, cancellation
│   └── spike_protocol.py             the 3×3 capability probe, standalone with raw response inspection
│
├── tests/
│   ├── conftest.py                   hermetic fixtures — temp DB, FakeSandbox, no network
│   ├── fixtures/
│   │   ├── mini_repo/                14 modules: 8 static call sites, 1 aliased import, 1 dynamic
│   │   │                             dispatch (2 unresolved edges), 1 uncovered private symbol, 14 tests
│   │   └── diffs/                    the adversarial corpus — 10 named fixtures, exact verdicts
│   ├── unit/                         gates, radius, ordering, MCP tools, bench scoring, adversarial corpus
│   └── integration/                  real baseline → coverage → attempt pipeline against FakeSandbox
│
├── docs/
│   ├── PRD.md                        product requirements
│   ├── DESIGN.md                     engineering design document
│   ├── SPEC.md                       technical specification
│   ├── THREAT_MODEL.md               threat matrix + residual risks, stated explicitly
│   ├── FEEDBACK.md                   Nebius/NVIDIA tooling feedback, verified against installed SDKs
│   ├── SUBMISSION.md                 Devpost text + requirement-by-requirement checklist
│   ├── DEMO_VIDEO.md                 3-minute shot list and script
│   └── DEPLOY.md                     Render.com deployment steps
│
├── Dockerfile                        two-stage build (dashboard, then API) — serves with zero credentials
├── render.yaml                       Render Blueprint — what deploys amtdrs.onrender.com
├── Makefile                          install · doctor · test · lint · bench · demo · docker
├── pyproject.toml                    deps, ruff config, import-linter contracts
├── LICENSE                           Apache-2.0, verbatim, detectable by GitHub's own scanner
└── NOTICE                            third-party licenses, trademark attribution
```

---

## Safety

- **Draft PRs only.** A human merges.
- **Never pushes to upstream.** The token is scoped to a fork; this is documented in `.env.example` and enforced by configuration.
- **Sandboxed execution.** All generated code runs in an isolated Token Factory microVM, never on the host.
- **Scope gate.** A patch that touches a file outside its blast radius is rejected before it runs. Dependency manifests and test files are separately blocked, so a candidate cannot make itself pass by editing the tests.
- **Fail closed.** No green test, no PR — always.
- **Every claim named and tested.** [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) states the residual risks a verification system this size cannot fully close, rather than implying there are none.

## Project provenance

- Principal was **created entirely during the hackathon submission period** (26 August – 30 October 2026).
- It is **not** a pre-existing project, and no part of it was published before the submission period opened.
- The full commit history in this repository is the record.

## Documentation

| Document | Contents |
|---|---|
| [`docs/FEEDBACK.md`](docs/FEEDBACK.md) | Engineering feedback on Token Factory, Sandboxes, Nemotron 3 and the SDKs |
| [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) | Threat matrix, trust boundaries, residual risks stated explicitly |
| [`docs/SUBMISSION.md`](docs/SUBMISSION.md) | The Devpost submission text and the requirement-by-requirement checklist |
| [`docs/DEMO_VIDEO.md`](docs/DEMO_VIDEO.md) | Shot list and script for the three-minute demo |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | Deploying the hosted demo on Render's free tier |
| [`docs/PRD.md`](docs/PRD.md) | Product requirements |
| [`docs/DESIGN.md`](docs/DESIGN.md) | Engineering design document |
| [`docs/SPEC.md`](docs/SPEC.md) | Technical specification |

## Sources

Market and benchmark figures cited above, with the dates they were read.

| Claim | Source |
|---|---|
| Technical debt is 21–40% of IT spend | [Deloitte 2026 Global Technology Leadership Study](https://www.deloitte.com/us/en/insights/topics/leadership/global-technology-leadership-study.html) |
| 45% of code fragile, 10B LOC / 47k apps | [CAST, *Coding in the Red* (2025)](https://www.castsoftware.com/) |
| 84% AI adoption · 29% trust · 46% distrust · 45% "almost right" | [Stack Overflow Developer Survey 2026](https://stackoverflow.blog/2026/02/18/closing-the-developer-ai-trust-gap/) |
| 90% org adoption; AI amplifies throughput *and* instability | [DORA, *State of AI-assisted Software Development* 2025](https://dora.dev/dora-report-2025/) |
| SWE Atlas Refactoring leaderboard, 70 tasks / 10 repos / 6 languages | [Scale AI Labs](https://labs.scale.com/leaderboard/sweatlas-refactoring) |
| Nemotron 3 list pricing on Token Factory | [Nebius Token Factory — Nemotron](https://nebius.com/services/token-factory/nemotron) |
| Sandboxes: microVM per sandbox, 50 concurrent ops, 180-day retention | [Token Factory Sandboxes docs](https://docs.tokenfactory.nebius.com/sandboxes/overview) |
| US engineer hourly cost $85–110 fully loaded | [2026 software development rate surveys](https://www.fullstack.com/labs/resources/blog/software-development-price-guide-hourly-rate-comparison) |
| Moderne $30M Series B for enterprise code modernization | [GlobeNewswire, Feb 2025](https://www.globenewswire.com/news-release/2025/02/11/3024163/0/en/moderne-secures-30m-to-drive-billions-in-enterprise-code-modernization-savings-based-on-its-innovative-tech-used-by-aws-microsoft-and-broadcom-ai-assistants.html) · [TechCrunch](https://techcrunch.com/2025/02/11/moderne-raises-30m-to-solve-technical-debt-across-complex-codebases/) |

## License

Apache License 2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
