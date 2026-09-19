import { useReveal } from "./useReveal";
import { InlineText } from "./InlineText";
import { ChevronDown } from "./icons";

const ITEMS = [
  {
    q: "Does a model ever decide whether a patch merges?",
    a: "No. The accept decision is a `pytest` / `vitest` exit code from a real run inside a sandbox — never a score from a language model. This isn't a policy, it's an architecture contract: the `gates` module is not permitted to import the `models` module, and that rule is checked in CI on every commit, not just claimed in a doc.",
  },
  {
    q: "Can an agent edit a test file to make itself pass?",
    a: "No. `apply_patch` deterministically rejects any diff that touches a test file, before it's ever applied. An agent that could edit the tests could pass any test, which is why this is treated as the single highest-value guardrail in the whole system.",
  },
  {
    q: "Can an agent touch files outside its assigned scope?",
    a: "No. Each coder receives exactly one file to modify plus read-only context. A diff touching any other path is rejected by a deterministic check against the blast radius — the transitive closure of imports and calls, computed before any model runs. A scope violation is a structural impossibility, not a rule the model is asked to follow.",
  },
  {
    q: "What happens when nothing verifies?",
    a: "Principal stops and returns `NoPR`, along with a report of exactly what it tried and where each attempt died. That's treated as a correct outcome, not a failure state — shipping a plausible-looking diff of unknown correctness is worse than shipping nothing, because it converts a bounded engineering task into an unbounded review task.",
  },
  {
    q: "What's actually in scope today?",
    a: "One refactor class — interface evolution: changing how a module exposes functionality and propagating the change through every consumer — on Python and TypeScript repositories up to roughly 2,000 files, with `pytest`, `vitest` or `jest` as the test runner. Decomposition, extraction and relocation are on the roadmap, not V1.",
  },
  {
    q: "Is any of this actually benchmarked?",
    a: "The evaluation plan runs a fixed subset of Scale AI's SWE Atlas Refactoring benchmark — Python and TypeScript, interface-evolution tasks only — across four arms: an unscaffolded Nemotron baseline, sandbox-with-retry alone, the full Principal swarm, and published frontier scores for reference. Every model call is logged and every trace is published to the repo, specifically so the result doesn't have to be taken on trust.",
  },
  {
    q: "Who actually holds the GitHub credential?",
    a: "Only the orchestrator, and only after the integration gate is green. No agent role — Cartographer, Planner, Coder, Repairer or Reporter — ever receives a token that can reach GitHub. Agents produce diffs; opening the PR is deterministic code, not a tool an LLM is trusted with.",
  },
  {
    q: "Is the hosted demo safe to point at my own repository?",
    a: "The public demo runs against a bundled fixture repo with zero credentials configured, by design — there's nothing to sign into. Running it against a real repository means supplying your own Token Factory key and a scoped GitHub token, either self-hosted from the Apache-2.0 source or through a managed Team plan.",
  },
];

export function FAQ() {
  const [ref, shown] = useReveal();
  return (
    <section className="section section--tight" id="faq" ref={ref}>
      <div className={`wrap reveal${shown ? " is-shown" : ""}`}>
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
