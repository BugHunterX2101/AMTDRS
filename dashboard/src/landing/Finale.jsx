import { ArrowRight, Github } from "./icons";

export function Finale() {
  return (
    <section className="section" data-field-end>
      <div className="wrap reveal">
        <div className="finale">
          <span className="badge badge--green">
            <span className="pulse-dot" aria-hidden="true" />
            Fail closed. No green test, no PR.
          </span>
          <h2 style={{ marginTop: 20 }}>
            Send it a goal and a target symbol. See what comes back.
          </h2>
          <p>
            The hosted console needs no sign-up and runs the whole pipeline live, on
            Nebius Sandboxes and Token Factory: the green baseline, the code graph and
            the blast radius, then the fork tree, every candidate's verdict and the
            full diff as they happen. It starts on the bundled fixture repository; a
            job spends real tokens and sandbox time.
          </p>
          <div className="hero-actions">
            <a className="btn btn--primary btn--lg btn--ring" href="/app/">
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
