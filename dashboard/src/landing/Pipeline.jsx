import { useState } from "react";
import { useReveal } from "./useReveal";
import { InlineText } from "./InlineText";

const GATES = [
  {
    n: 1,
    label: "Scope",
    where: "local · in-process",
    cost: "microseconds",
    kind: "local",
    body: "The patch is checked against the target's blast radius — the transitive closure of imports and calls out to depth 3, computed by tree-sitter before any model runs. A diff touching a file outside that set is rejected here, deterministically. This is not a prompt instruction the model could ignore; the coder agent is never handed a tool call that could apply such a diff in the first place.",
    reject: "Rejects: any file outside the declared blast radius.",
  },
  {
    n: 2,
    label: "Syntax",
    where: "local · tree-sitter",
    cost: "milliseconds",
    kind: "local",
    body: "Every changed file must still parse after the patch is applied — Python and TypeScript grammars, error-tolerant so a file that doesn't fully parse doesn't crash the gate. A patch that produces a syntactically broken file dies here, before a sandbox is ever touched, which is most of why generous fan-out stays affordable.",
    reject: "Rejects: unparseable output, malformed hunks, no-op diffs.",
  },
  {
    n: 3,
    label: "Local tests",
    where: "Token Factory Sandbox",
    cost: "seconds",
    kind: "sandbox",
    body: "Only the tests that the code graph's tests relation says actually exercise the changed file are run — not the full suite, in every one of up to 8 concurrent forks. That's the difference between a 20-minute job and a two-hour one. A candidate that fails gets at most two repair attempts with the stack trace (not the original goal) fed back in, then it's discarded silently.",
    reject: "Rejects: any non-zero `pytest` / `vitest` exit code for the covered tests.",
  },
  {
    n: 4,
    label: "Integration",
    where: "Token Factory Sandbox · once",
    cost: "seconds",
    kind: "sandbox",
    body: "Surviving patches pass in isolation, but patches that each pass alone can still conflict with each other. Every accepted patch is applied together in dependency order onto a fresh fork of the green baseline, and the entire suite runs exactly once, for real. Only if that run is green does the orchestrator — never an agent — open the draft PR with a token no model ever holds.",
    reject: "Rejects: any regression introduced by combining otherwise-green patches.",
  },
  {
    n: 5,
    label: "Security",
    where: "local · differential scan",
    cost: "milliseconds",
    kind: "local",
    body: "A deterministic scanner — not a model — walks the baseline tree and the patched tree separately and diffs the findings: hardcoded secrets, eval/exec on untrusted input, unsafe deserialisation, shell and SQL injection shapes, disabled TLS verification. Only fingerprints absent from the baseline and present in the patch are reported, so a pre-existing issue elsewhere in the file never blocks an unrelated refactor. A patch that introduces a new one is discarded before publishing, no exceptions.",
    reject: "Rejects: any finding whose fingerprint is new relative to the baseline scan.",
  },
  {
    n: 6,
    label: "Blast-radius check",
    where: "local · deterministic",
    cost: "milliseconds",
    kind: "local",
    check: true,
    body: "Green tests only prove the tests are green. After integration, what the patch actually changed is compared with what the code graph says it should have reached: call sites still using the old form, files outside the radius, and dynamic paths such as `getattr` or a registry that nothing in the suite exercises. A definite incompatibility fails the run. A path no test reaches is reported as unverified instead of being waved through.",
    reject: "Fails: a caller or dependent the patch left incompatible. Flags: anything no test exercises.",
  },
  {
    n: 7,
    label: "Strength audit",
    where: "Token Factory Sandbox",
    cost: "seconds",
    kind: "sandbox",
    check: true,
    body: "Would the tests that just passed have noticed a mistake? Small deliberate faults — a flipped comparison, an off-by-one — are injected one at a time into only the lines the refactor changed, and the covering tests re-run. A fault the suite misses is a located gap. This is a measurement, not a proof: a high score does not show equivalence, and a run that could not be measured says so rather than defaulting to a flattering number.",
    reject: "Flags: tests that miss injected faults in the changed lines. Never decides alone.",
  },
];

export function Pipeline() {
  const [open, setOpen] = useState(3);
  const [ref, shown] = useReveal();

  return (
    <section className="section" id="how-it-works" ref={ref}>
      <div className={`wrap reveal${shown ? " is-shown" : ""}`}>
        <div className="eyebrow">How it works</div>
        <h2 className="section-title">
          Five gates reject. Two checks say how far to trust what is left.
        </h2>
        <p className="section-lede">
          Scope, syntax, security and the blast-radius check never touch a sandbox —
          that's deliberate. Most bad candidates die for free, in-process, which is
          what makes it affordable to generate several competing attempts per task
          instead of one. None of the seven involves a model.
        </p>

        <div className="gates" style={{ marginTop: 40 }}>
          {GATES.map((g) => {
            const isOpen = open === g.n;
            return (
              <div key={g.n}>
                <button
                  type="button"
                  className={`gate gate--${g.kind}`}
                  aria-expanded={isOpen}
                  aria-controls={`gate-detail-${g.n}`}
                  onClick={() => setOpen(isOpen ? null : g.n)}
                >
                  <span className="num">{String(g.n).padStart(2, "0")}</span>
                  <span>
                    <span className="label">{g.label}</span>
                    <span className="where"> · {g.where}</span>
                  </span>
                  <span className="cost">{g.cost}</span>
                </button>
                {isOpen && (
                  <div className="gate-detail" id={`gate-detail-${g.n}`} role="region">
                    <p>
                      <InlineText text={g.body} />
                    </p>
                    <p className="rejects">
                      <InlineText text={g.reject} />
                    </p>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
