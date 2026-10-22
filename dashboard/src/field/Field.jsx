import { useEffect, useRef } from "react";
import { createField } from "./fieldScene";

const FRAME_MS = 1000 / 30;
const STILL = "(prefers-reduced-motion: reduce)";
const LEAN = "(hover: hover) and (pointer: fine)";

/**
 * The gate cascade behind a page. `tone` is one of fieldScene.js's TONES and can
 * change at any time (the console passes the live job's mood); `gain` scales its
 * brightness for denser pages. The frame loop writes to the canvas only, so the
 * field never re-renders React.
 */
export function Field({ tone = "idle", gain = 1 }) {
  const canvas = useRef(null);
  const field = useRef(null);
  const still = useRef(false);
  const latest = useRef({ tone, gain });
  latest.current = { tone, gain };

  useEffect(() => {
    const f = field.current;
    if (!f) return;
    f.setTone(tone, { gain });
    if (still.current) f.draw(0, true);
  }, [tone, gain]);

  useEffect(() => {
    const el = canvas.current;
    const f = el && createField(el);
    if (!f) return undefined;
    field.current = f;
    f.setTone(latest.current.tone, { gain: latest.current.gain });
    still.current = window.matchMedia(STILL).matches;

    const scrolled = () => {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      f.setScroll(max > 0 ? window.scrollY / max : 0);
    };
    const size = () => {
      f.resize(window.innerWidth, window.innerHeight, window.devicePixelRatio || 1);
      scrolled();
      if (still.current) f.draw(0, true);
    };
    size();
    window.addEventListener("resize", size);

    if (still.current) {
      return () => {
        window.removeEventListener("resize", size);
        field.current = null;
        f.destroy();
      };
    }

    let raf = 0;
    let last = 0;
    const loop = (now) => {
      raf = requestAnimationFrame(loop);
      if (now - last < FRAME_MS) return;
      last = now;
      f.draw(now / 1000);
    };
    const play = () => {
      if (!raf && !document.hidden) raf = requestAnimationFrame(loop);
    };
    const pause = () => {
      cancelAnimationFrame(raf);
      raf = 0;
    };
    const onVis = () => (document.hidden ? pause() : play());
    document.addEventListener("visibilitychange", onVis);
    window.addEventListener("scroll", scrolled, { passive: true });
    const move = (e) => f.setPointer(e.clientX / window.innerWidth - 0.5, e.clientY / window.innerHeight - 0.5);
    const lean = window.matchMedia(LEAN).matches;
    if (lean) window.addEventListener("pointermove", move, { passive: true });
    play();

    return () => {
      pause();
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("scroll", scrolled);
      window.removeEventListener("resize", size);
      if (lean) window.removeEventListener("pointermove", move);
      field.current = null;
      f.destroy();
    };
  }, []);

  return <canvas ref={canvas} className="field" aria-hidden="true" />;
}
