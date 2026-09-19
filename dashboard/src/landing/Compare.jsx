import { useReveal } from "./useReveal";

const ROWS = [
  {
    name: "Copilot / Cursor",
    does: "Human-driven edits, file or selection scoped.",
    stops: "The human still drives the migration file by file.",
    verify: "Human review, per edit",
  },
  {
    name: "Devin & similar agents",
    does: "Full task autonomy, opens PRs unattended.",
    stops: "General-purpose, single long-lived workspace, weak behaviour-preservation guarantees on large refactors.",
    verify: "Model's own judgement",
  },
  {
    name: "OpenRewrite / Batch Changes",
    does: "Deterministic, repo-wide, reliable.",
    stops: "Requires a human to write the codemod. Can't handle changes needing semantic judgement.",
    verify: "N/A — no judgement involved",
  },
  {
    name: "Renovate / Dependabot",
    does: "Fully autonomous, fully trusted, runs in production today.",
    stops: "Dependency bumps only. No structural change.",
    verify: "CI, narrow change class",
  },
  {
    name: "Principal",
    does: "Autonomous interface-evolution refactors across a whole repo.",
    stops: "One refactor class today — the goal is still set by a human.",
    verify: "The repo's own test suite, every time",
    self: true,
  },
];

export function Compare() {
  const [ref, shown] = useReveal();
  return (
    <section className="section section--tight" id="compare" ref={ref}>
      <div className={`wrap reveal${shown ? " is-shown" : ""}`}>
        <div className="eyebrow">Positioning</div>
        <h2 className="section-title">
          The comparison isn't Copilot. It's who you'd trust to merge unattended.
        </h2>
        <p className="section-lede">
          Renovate is the existence proof: engineers already merge fully automated
          PRs without reading every line, because the verification is total and the
          failure mode is a red build, not a silent regression. Principal reproduces
          those properties for a wider class of change.
        </p>

        <div className="compare-scroll" style={{ marginTop: 40 }}>
          <table className="compare">
            <thead>
              <tr>
                <th scope="col">System</th>
                <th scope="col">What it does</th>
                <th scope="col">Where it stops</th>
                <th scope="col">What verifies the change</th>
              </tr>
            </thead>
            <tbody>
              {ROWS.map((r) => (
                <tr key={r.name} className={r.self ? "is-self" : ""}>
                  <td>{r.name}</td>
                  <td>{r.does}</td>
                  <td>{r.stops}</td>
                  <td>{r.verify}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="hero-note" style={{ marginTop: 20 }}>
          OpenRewrite makes you write the recipe. Devin makes you trust the agent.
          Principal makes the test suite the arbiter — the only referee an
          engineering org already trusts.
        </p>
      </div>
    </section>
  );
}
