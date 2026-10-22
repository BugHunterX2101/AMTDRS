import { InlineText } from "./InlineText";
import { ChevronDown } from "./icons";

const ITEMS = [
  {
    q: "Does a model ever decide whether a patch merges?",
    a: "No. The accept decision is a `pytest` exit code from a real run inside a sandbox — never a score from a language model. This isn't a policy, it's an architecture contract: the `gates` module is not permitted to import the `models` module, and that rule is checked in CI on every commit, not just claimed in a doc.",
  },
  {
    q: "Can an agent edit a test file to make itself pass?",
    a: "No. The scope gate rejects any diff that touches a test file before the patch is applied and before a sandbox is spent — and an agent has no patch-applying tool to reach around it with, because its only output is diff text. An agent that could edit the tests could pass any test, which is why this is treated as the single highest-value guardrail in the whole system.",
  },
  {
    q: "Can an agent touch files outside its assigned scope?",
    a: "No. Each coder receives exactly one file to modify plus read-only context. A diff touching any other path is rejected by a deterministic check against the blast radius — the transitive closure of imports and calls, computed before any model runs. A scope violation is a structural impossibility, not a rule the model is asked to follow.",
  },
  {
    q: "Could a refactor introduce a security issue that the test suite doesn't catch?",
    a: "A passing test suite says nothing about whether the patch introduced a hardcoded secret, an eval on untrusted input, or a disabled TLS check — none of that is what tests are for. A deterministic scanner runs against the baseline tree and the patched tree separately and reports only findings that are new in the patch, identified by a fingerprint that excludes line number so a refactor's own line shifts don't produce false positives. A patch that introduces a new finding is discarded before publishing. No model makes this call.",
  },
  {
    q: "What happens when nothing verifies?",
    a: "Principal stops and returns `NoPR`, along with a report of exactly what it tried and where each attempt died. That's treated as a correct outcome, not a failure state — shipping a plausible-looking diff of unknown correctness is worse than shipping nothing, because it converts a bounded engineering task into an unbounded review task.",
  },
  {
    q: "What's actually in scope today?",
    a: "Two refactor classes, selected per job through a routine registry: interface evolution — changing how a module exposes functionality and propagating the change through every consumer — and relocation, moving a symbol and reconciling every importer. Repository size is not capped; the change is: its blast radius may reach at most 40 files, three call or import hops deep, or the job stops with RADIUS_TOO_LARGE instead of working on part of it. Live runs so far have been on small repositories. The code graph parses Python and TypeScript today; the verification path runs `pytest`, so Python is what ships end to end and TypeScript stops at analysis until a `vitest` runner lands. Decomposition and extraction — splitting or pulling logic out of a function — are on the roadmap, not V1.",
  },
  {
    q: "What happens when the target has no test coverage?",
    a: "Principal doesn't refactor against a hollow oracle. When the target symbol has no covering test, a Characteriser agent — never shown the refactoring goal — writes tests describing the code's actual current behaviour, which are discarded outright if they don't pass against the unmodified baseline. Their sensitivity is then measured directly: real mutants are seeded into the target and the suite must detect most of them, not merely run. Below that floor, the job stops and reports `NoSafetyNet` — a refusal, not a crash — rather than refactor against tests that would rubber-stamp any change.",
  },
  {
    q: "Is any of this actually benchmarked?",
    a: "Live, and small. `make eval` checks credentials, then runs three arms — a single ungated shot, one gated candidate, and the full swarm — on every task in `bench/tasks.json`, on real Nebius Sandboxes and real Token Factory inference, and stamps the report LIVE or NOT LIVE. The run recorded on 2026-10-22, one run per cell on two fixture repositories, produced the first VERIFIED outcome: the full swarm on `mini_repo`. On `weak_repo`, whose tests pass but are weak, the swarm stopped at UNVERIFIED with an injected-fault score of 0.0 against a 0.6 floor, which is that fixture doing its job. Two tasks and one run per cell show nothing about which arm is better; the README records every cell and its cause. It has not been run against Scale AI's SWE Atlas Refactoring subset, so no comparison to that leaderboard is claimed.",
  },
  {
    q: "What do Verified, Unverified, Failed and Error mean?",
    a: "Verified: the tests pass and every check that ran came back clean. Unverified: the tests pass, or nothing could be checked, but something is still open — a call site no test exercises, or tests that missed injected faults in the changed lines — so a human has to decide. Failed: a check found a definite defect, and nothing is offered. Error: the platform failed, which says nothing about the patch and is never counted against it. Every finished run has an evidence bundle: the verdict, each gate's result, every attempt's diff and test log with SHA-256 hashes, and the command that reproduces the run.",
  },
  {
    q: "Who actually holds the GitHub credential?",
    a: "Only the orchestrator, and only after the integration gate is green. No agent role — Planner, Characteriser, Coder, Repairer or Reporter — ever receives a token that can reach GitHub. Agents produce diffs; opening the PR is deterministic code, not a tool an LLM is trusted with. It's an import contract too: `agents` may not import `publish`.",
  },
  {
    q: "What does the hosted console run on?",
    a: "The real platform, and nothing else. The hosted service will not start unless Token Factory inference and Nebius Sandboxes both verify at startup, and it never falls back to the in-process stand-in. A job there builds a verified green baseline with real `git` and real `pytest` in a Nebius sandbox, parses the repository with tree-sitter, computes the blast radius, then plans and races candidates on Nemotron 3, spending real tokens and sandbox time. Every run is labelled LIVE or NOT LIVE, and a NOT LIVE result, such as a local checkout with no credentials, is never presented as a measurement of the platform.",
  },
];

export function FAQ() {
  return (
    <section className="section section--tight" id="faq">
      <div className="wrap reveal">
        <div className="eyebrow">Questions a skeptical engineer would ask</div>
        <h2 className="section-title">FAQ</h2>

        <div className="faq" style={{ marginTop: 40 }}>
          {ITEMS.map((item, i) => (
            <details key={item.q} name="faq" open={i === 0}>
              <summary>
                <span>{item.q}</span>
                <ChevronDown className="chev" aria-hidden="true" />
              </summary>
              <div className="answer">
                <p>
                  <InlineText text={item.a} />
                </p>
              </div>
            </details>
          ))}
        </div>
      </div>
    </section>
  );
}
