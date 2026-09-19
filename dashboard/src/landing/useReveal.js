import { useEffect, useRef, useState } from "react";

/**
 * Fades/slides an element in the first time it crosses the viewport, then
 * disconnects — a one-shot reveal, not a scroll-jacking effect. It is a
 * no-op under prefers-reduced-motion because that media query (landing.css)
 * zeroes out every animation/transition duration globally, so a reduced-
 * motion user sees the same opacity flip with no perceptible motion.
 */
export function useReveal() {
  const ref = useRef(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setShown(true);
          io.disconnect();
        }
      },
      { threshold: 0.15, rootMargin: "0px 0px -8% 0px" }
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return [ref, shown];
}
