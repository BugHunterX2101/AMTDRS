import { ArrowRight, Github, Play } from "./icons";
import { ForkRaceViz } from "./ForkRaceViz";
import { Backdrop } from "../Backdrop";

const TRUST = [
  { k: "Track", v: "Coding & Agentic Engineering" },
  { k: "Runs on", v: "Nebius Token Factory + Sandboxes" },
  { k: "Models", v: "NVIDIA Nemotron 3 — Nano / Super / Ultra" },
  { k: "License", v: "Apache 2.0, source available" },
];

export function Hero() {
  return (
    <header className="hero" id="top">
      <Backdrop from="#a371f7" to="#3fb950" alpha={0.55} />
      <div className="hero-glow" aria-hidden="true" />
      <div className="hero-grid" aria-hidden="true" />
      <div className="wrap hero-inner">
        <div>
          <span className="badge badge--nvidia">
            Nebius × NVIDIA Global AI Hackathon 2026
          </span>
          <h1>
            Refactors that arrive <span className="accent">already verified.</span>
          </h1>
          <p className="hero-sub">
            Principal is an autonomous technical debt remediation swarm. It races
            parallel refactor candidates inside Token Factory Sandboxes and opens a
            pull request only when the repository's own test suite passes — not
            when a model thinks it should.
          </p>
          <div className="hero-actions">
            <a className="btn btn--primary btn--lg" href="/app/">
              <Play aria-hidden="true" />
              Watch a live run
            </a>
            <a
              className="btn btn--lg"
              href="https://github.com/BugHunterX2101/AMTDRS"
              target="_blank"
              rel="noreferrer"
            >
              <Github aria-hidden="true" />
              View source
            </a>
            <a className="btn btn--ghost btn--lg" href="#how-it-works">
              How it works
              <ArrowRight aria-hidden="true" />
            </a>
          </div>
          <p className="hero-note">
            The hosted console takes a bundled fixture repo through a real green
            baseline, a real code graph and a real blast radius with zero
            credentials — nothing to sign up for. Patch generation needs your own
            inference key. Free-tier host, first load may take 30–60s to wake up.
          </p>
        </div>

        <div className="card" aria-label="Simulated candidate race">
          <div className="card-head">
            <span className="dots" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            <h3>fork race — task 07 · auth.session.create</h3>
          </div>
          <div className="card-body">
            <ForkRaceViz compact />
          </div>
        </div>
      </div>

      <div className="trustbar">
        <div className="wrap trustbar-inner">
          {TRUST.map((t) => (
            <div className="trust-item" key={t.k}>
              <span className="k">{t.k}</span>
              <span className="v">{t.v}</span>
            </div>
          ))}
        </div>
      </div>
    </header>
  );
}
