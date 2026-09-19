import { useReveal } from "./useReveal";
import { InlineText } from "./InlineText";
import { Scale, XCircle, Lock, GitBranch, Shield, Layers } from "./icons";

const CARDS = [
  {
    icon: Scale,
    color: "",
    title: "The test suite is the only judge",
    body: "No LLM-as-judge sits in the accept path, ever. The decision to keep a candidate is a `pytest` exit code from a real run in a sandbox. If a reviewer model is added later, it ranks among already-green candidates — it never gates.",
    span: true,
  },
  {
    icon: XCircle,
    color: "ico--amber",
    title: "Fails closed, silently",
    body: "A candidate that fails is discarded without ceremony. `NoPR` is a correct outcome, not an error — shipping an unverified change is worse than shipping nothing.",
  },
  {
    icon: Lock,
    color: "ico--green",
    title: "Isolation by construction",
    body: "Agents can't reach the main branch because no credential that reaches it is ever in their hands — not because a prompt tells them not to.",
  },
  {
    icon: GitBranch,
    color: "ico--violet",
    title: "A coder can't widen its own scope",
    body: "Each coder gets exactly one file plus read-only context. A diff touching any other path is rejected by a deterministic check — `apply_patch` enforces the blast radius in code, not in a prompt.",
    span: true,
  },
  {
    icon: Shield,
    color: "",
    title: "No agent holds a GitHub token",
    body: "The orchestrator opens the draft PR after verification finishes, using a scoped credential no model ever sees. Agents produce diffs. That is all they produce.",
  },
  {
    icon: Layers,
    color: "ico--green",
    title: "Every claim has an artifact",
    body: "A test log, a diff, a sandbox operation id. No summary in the dashboard that can't be clicked through to the exact evidence it's summarizing.",
  },
];

export function Features() {
  const [ref, shown] = useReveal();
  return (
    <section className="section section--tight" id="guardrails" ref={ref}>
      <div className={`wrap reveal${shown ? " is-shown" : ""}`}>
        <div className="eyebrow">Guardrails, enforced in code</div>
        <h2 className="section-title">
          Six ways the swarm is kept honest — none of them a prompt.
        </h2>
        <p className="section-lede">
          Anything not in this list is deterministic Python. The fewer decisions a
          model gets to make, the fewer places the system can be wrong.
        </p>

        <div className="bento" style={{ marginTop: 40 }}>
          {CARDS.map((c) => (
            <div className={`card${c.span ? " span-2" : ""}`} key={c.title}>
              <div className="card-body">
                <div className={`ico ${c.color}`}>
                  <c.icon aria-hidden="true" />
                </div>
                <h3>{c.title}</h3>
                <p>
                  <InlineText text={c.body} />
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
