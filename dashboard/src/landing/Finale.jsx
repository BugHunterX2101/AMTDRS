import { useReveal } from "./useReveal";
import { ArrowRight, Github } from "./icons";

export function Finale() {
  const [ref, shown] = useReveal();
  return (
    <section className="section" ref={ref}>
      <div className={`wrap reveal${shown ? " is-shown" : ""}`}>
        <div className="finale">
          <span className="badge badge--green">
            <span className="pulse-dot" aria-hidden="true" />
            Fail closed. No green test, no PR.
          </span>
          <h2 style={{ marginTop: 20 }}>
            Send it a goal and a target symbol. See what comes back.
          </h2>
          <p>
            The hosted console needs no sign-up and no credentials. It runs a real
            baseline, a real code graph and a real blast radius against a bundled
            fixture repository in front of you — the deterministic half, the half
            a model cannot fake. Add your own Token Factory key and the same
            console shows the fork tree, every candidate's verdict and the full
            diff one click away.
          </p>
          <div className="hero-actions">
            <a className="btn btn--primary btn--lg" href="/app/">
              Open the console
              <ArrowRight aria-hidden="true" />
            </a>
            <a
              className="btn btn--lg"
              href="https://github.com/BugHunterX2101/AMTDRS"
              target="_blank"
              rel="noreferrer"
            >
              <Github aria-hidden="true" />
              Read the source
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}
