import { ArrowRight } from "./icons";

/* The four outcomes, worded to match principal/outcome.py. The page says what
   the product says, so a visitor who opens the console and sees "UNVERIFIED"
   has already read what it means and what was and was not offered. */
const OUTCOMES = [
  {
    id: "verified",
    name: "Verified",
    meaning: "The tests pass and every check that ran came back clean.",
    offered: "A draft pull request.",
  },
  {
    id: "unverified",
    name: "Unverified",
    meaning:
      "No definite defect, but something is still open: a call site no test exercises, tests that missed injected faults, a partial migration, or nothing could be checked.",
    offered: "A draft pull request titled [UNVERIFIED] for a human to decide, or nothing.",
  },
  {
    id: "failed",
    name: "Failed",
    meaning:
      "A check found a definite defect: the patch breaks code outside the files it changed, the patches fail together, or it introduces a security finding.",
    offered: "Nothing. The repository is unchanged.",
  },
  {
    id: "error",
    name: "Error",
    meaning:
      "The platform failed, not the patch. A timeout or a refused operation says nothing about the change, so it is never counted against it.",
    offered: "Nothing. Run it again.",
  },
];

const BUNDLE = [
  "The verdict and every reason behind it",
  "Each gate's result, including what was not checked",
  "Every attempt's diff and test log, with SHA-256 hashes",
  "Whether it ran LIVE on Nebius Sandboxes, or on a local stand-in",
  "The command that reproduces the run",
];

export function Outcomes() {
  return (
    <section className="section" id="outcomes">
      <div className="wrap reveal">
        <div className="eyebrow">What you get back</div>
        <h2 className="section-title">A green run is not always a safe one. Principal says which.</h2>
        <p className="section-lede">
          Every finished run lands in exactly one of four outcomes, computed from the
          stored record rather than written by a model. The point is that "the tests
          passed" and "this is safe to merge" are different claims, and the tool tells
          you which one it can actually support.
        </p>

        <div className="outcomes" role="list">
          {OUTCOMES.map((o) => (
            <article className={`outcome-card outcome-card--${o.id}`} key={o.id} role="listitem">
              <h3>
                <span className="dot" aria-hidden="true" />
                {o.name}
              </h3>
              <p>{o.meaning}</p>
              <p className="offered">
                <b>Offered</b> {o.offered}
              </p>
            </article>
          ))}
        </div>

        <div className="bundle">
          <div>
            <h3>Every run ships its evidence</h3>
            <p>
              A downloadable bundle, built from the stored record. Each artifact is
              re-hashed when the bundle is built, so a file changed after the run is
              flagged rather than trusted.
            </p>
            <a className="btn btn--ghost" href="/app/">
              See it on a run
              <ArrowRight aria-hidden="true" />
            </a>
          </div>
          <ul>
            {BUNDLE.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
