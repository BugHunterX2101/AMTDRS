import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import run from "./run-01M5H0RM.json";
import { clock, stateAt } from "./model";
import { createReplay, SPEED } from "./scene";

const FRAME_MS = 1000 / 30;
const STILL = "(prefers-reduced-motion: reduce)";
const TILT = "(hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference)";

const final = stateAt(run, run.duration);

/**
 * A recorded live run, replayed on its own clock.
 *
 * The canvas is portalled into the hero's full-bleed stage, and the tree is
 * framed into `frame`, an empty box the hero's layout keeps clear of text. The
 * readout is written straight to the DOM from the frame loop (refs, no React
 * state), so a 30 fps replay never re-renders the page.
 */
export default function RunReplay({ stage, frame }) {
  const canvas = useRef(null);
  const hud = useRef({});
  const [still, setStill] = useState(false);

  useEffect(() => {
    const el = canvas.current;
    if (!el || !stage || !frame) return undefined;
    const scene = createReplay(el, run);
    if (!scene) {
      setStill(true);
      return undefined;
    }

    const set = (key, text) => {
      const node = hud.current[key];
      if (node && node.textContent !== text) node.textContent = text;
    };
    const paintHud = (st) => {
      set("clock", `t+${clock(st.t)}`);
      set("phase", st.phase);
      set("verified", `${st.verified}/${run.tasks.length}`);
      set("started", String(st.started));
      set("passed", String(st.passed));
      set("cancelled", String(st.cancelled));
      set("rejected", String(st.rejected));
      if (hud.current.root) hud.current.root.dataset.done = st.done ? "true" : "false";
    };

    const size = () => {
      const s = stage.getBoundingClientRect();
      const f = frame.getBoundingClientRect();
      scene.resize(s.width, s.height, window.devicePixelRatio || 1, {
        x: f.left - s.left,
        y: f.top - s.top,
        width: f.width,
        height: f.height,
      });
    };
    size();
    const ro = new ResizeObserver(size);
    ro.observe(stage);
    ro.observe(frame);

    if (window.matchMedia(STILL).matches) {
      paintHud(scene.draw(run.duration, 0, 1));
      setStill(true);
      return () => {
        ro.disconnect();
        scene.destroy();
      };
    }

    let raf = 0;
    let last = 0;
    let onScreen = true;
    let started = performance.now();
    let pausedAt = 0;
    const loop = (now) => {
      raf = requestAnimationFrame(loop);
      if (now - last < FRAME_MS) return;
      last = now;
      const elapsed = (now - started) / 1000;
      paintHud(scene.draw(scene.runTimeAt(elapsed), elapsed, scene.fadeAt(elapsed)));
    };
    const play = () => {
      if (raf || !onScreen || document.hidden) return;
      if (pausedAt) started += performance.now() - pausedAt;
      pausedAt = 0;
      raf = requestAnimationFrame(loop);
    };
    const pause = () => {
      if (!raf) return;
      cancelAnimationFrame(raf);
      raf = 0;
      pausedAt = performance.now();
    };
    const io = new IntersectionObserver(([e]) => {
      onScreen = e.isIntersecting;
      if (onScreen) play();
      else pause();
    });
    io.observe(stage);
    const onVis = () => (document.hidden ? pause() : play());
    document.addEventListener("visibilitychange", onVis);

    let move = null;
    const host = stage.parentElement;
    if (host && window.matchMedia(TILT).matches) {
      move = (e) => {
        const r = host.getBoundingClientRect();
        scene.setPointer((e.clientX - r.left) / r.width - 0.5, (e.clientY - r.top) / r.height - 0.5);
      };
      host.addEventListener("pointermove", move);
    }
    play();

    return () => {
      pause();
      io.disconnect();
      ro.disconnect();
      document.removeEventListener("visibilitychange", onVis);
      if (move && host) host.removeEventListener("pointermove", move);
      scene.destroy();
    };
  }, [stage, frame]);

  const p = run.provenance;
  const o = run.outcome;
  const reg = (key) => (node) => {
    hud.current[key] = node;
  };

  return (
    <>
      {stage && createPortal(<canvas ref={canvas} className="replay-canvas" aria-hidden="true" />, stage)}
      <figure className="replay" ref={reg("root")} data-done={still ? "true" : "false"}>
        <figcaption className="replay-head">
          <span className="replay-kicker">Replay · recorded live run</span>
          <span className="replay-id mono">
            job {p.job.slice(0, 10)}… · {p.recorded}
          </span>
        </figcaption>

        <div className="replay-time" aria-hidden="true">
          <div className="replay-clock mono">
            <span ref={reg("clock")}>t+{clock(still ? run.duration : 0)}</span>
            <span className="replay-of"> / {clock(run.duration)}</span>
            {!still && <span className="replay-speed">{SPEED}×</span>}
          </div>
          <div className="replay-phase" ref={reg("phase")}>
            {still ? final.phase : "Queued"}
          </div>
        </div>

        <dl className="replay-stats" aria-hidden="true">
          <div>
            <dt>verified</dt>
            <dd className="mono" ref={reg("verified")}>
              {still ? final.verified : 0}/{run.tasks.length}
            </dd>
          </div>
          <div>
            <dt>attempts</dt>
            <dd className="mono" ref={reg("started")}>{still ? final.started : 0}</dd>
          </div>
          <div className="is-pass">
            <dt>passed</dt>
            <dd className="mono" ref={reg("passed")}>{still ? final.passed : 0}</dd>
          </div>
          <div className="is-cancel">
            <dt>cancelled</dt>
            <dd className="mono" ref={reg("cancelled")}>{still ? final.cancelled : 0}</dd>
          </div>
          <div className="is-reject">
            <dt>rejected</dt>
            <dd className="mono" ref={reg("rejected")}>{still ? final.rejected : 0}</dd>
          </div>
        </dl>

        <div className="replay-stamp" aria-hidden="true">
          <span className="stamp-mark">Verified · clean</span>
          <span className="stamp-detail mono">
            {o.strength.detected} of {o.strength.generated} injected faults caught
            <br />
            {o.tokens.toLocaleString("en-US")} tokens · ${o.sandbox_usd.toFixed(4)} sandbox
          </span>
        </div>

        <p className="sr-only">
          A recorded run of Principal on {p.target}: {run.tasks.length} planned tasks and {final.started} attempts.{" "}
          {final.passed} attempts passed their tests, {final.cancelled} were cancelled after a sibling won, and{" "}
          {final.rejected} were rejected at a gate. The run ended {o.verdict}, {o.cause}, after {clock(run.duration)}.
        </p>

        <div className="replay-foot">
          <ul className="replay-legend" aria-hidden="true">
            <li className="lg-base">baseline</li>
            <li className="lg-run">running</li>
            <li className="lg-pass">tests passed</li>
            <li className="lg-cancel">cancelled</li>
            <li className="lg-reject">rejected at a gate</li>
          </ul>
          <p className="replay-note">
            Recorded on {p.recorded} through {p.where}, on {p.sandbox} and {p.inference}. The clock is the run's own;
            the replay plays it {SPEED}× faster and loops.
          </p>
        </div>
      </figure>
    </>
  );
}
