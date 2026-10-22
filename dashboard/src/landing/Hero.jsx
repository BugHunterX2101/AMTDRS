import { lazy, Suspense, useState } from "react";
import { ArrowRight, Check, Copy, Play } from "./icons";
import { LiveStatus } from "./LiveStatus";

// The replay (WebGL and the recorded run) loads after the headline has painted.
const RunReplay = lazy(() => import("./replay/RunReplay"));

const CLONE = "git clone https://github.com/BugHunterX2101/AMTDRS.git";

const TRUST = [
  { k: "Track", v: "Coding & Agentic Engineering" },
  { k: "Runs on", v: "Nebius Token Factory + Sandboxes" },
  { k: "Models", v: "NVIDIA Nemotron 3 — Nano / Super / Ultra" },
  { k: "License", v: "Apache 2.0, source available" },
];

export function Hero() {
  const [stage, setStage] = useState(null);
  const [frame, setFrame] = useState(null);
  const [copied, setCopied] = useState(false);

  // No clipboard (an insecure origin) leaves the command selectable by hand.
  const copy = () =>
    navigator.clipboard?.writeText(CLONE).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    }, () => {});

  return (
    <header className="hero" id="top">
      {/* Full-bleed stage the replay's canvas is portalled into, behind the text. */}
      <div className="hero-stage" ref={setStage} aria-hidden="true" />
      <div className="hero-scrim" aria-hidden="true" />

      <div className="wrap hero-inner">
        <div className="hero-copy">
          <LiveStatus />
          <h1>
            Refactors that arrive <em>already verified.</em>
          </h1>
          <p className="hero-sub">
            Principal is an autonomous technical debt remediation swarm. It races parallel refactor candidates
            inside Nebius Sandboxes and opens a pull request only when the repository's own test suite
            passes — not when a model thinks it should.
          </p>
          <div className="hero-actions">
            <a className="btn btn--primary btn--lg" href="/app/">
              <Play aria-hidden="true" />
              Open the live console
            </a>
            <a className="btn btn--ghost btn--lg" href="#how-it-works">
              How it works
              <ArrowRight aria-hidden="true" />
            </a>
          </div>
          <div className="cmd">
            <span className="cmd-prompt" aria-hidden="true">$</span>
            <code>{CLONE}</code>
            <button
              type="button"
              className="cmd-copy"
              onClick={copy}
              aria-label={copied ? "Copied" : "Copy the clone command"}
            >
              {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
            </button>
          </div>
          <p className="hero-note">
            The hosted console runs live on Nebius Sandboxes and Token Factory, so a job started there spends real
            tokens and sandbox time. It is a free-tier host: after a quiet spell, the first request waits for it to
            wake.
          </p>
        </div>

        {/* Empty on purpose: the box the replay's tree is framed into. */}
        <div className="hero-window" ref={setFrame} aria-hidden="true" />

        <div className="hero-replay">
          <Suspense fallback={<div className="replay replay--loading" aria-hidden="true" />}>
            <RunReplay stage={stage} frame={frame} />
          </Suspense>
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
