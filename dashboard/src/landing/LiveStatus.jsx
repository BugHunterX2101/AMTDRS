import { useEffect, useState } from "react";

const EVERY_MS = 60_000;
// /healthz names the sandbox class; say which platform that is.
const SANDBOX = { ContreeSandbox: "Nebius Sandboxes", FakeSandbox: "the in-process stand-in" };

/**
 * What the hosted service reports about itself, right now.
 *
 * Reads /healthz (same origin, read-only, costs nothing) on load and then once a
 * minute while the tab is visible. It shows only what the endpoint says: "live"
 * means inference and the sandbox both verified at the service's startup. Nothing
 * here can start a job, because a job spends the owner's quota.
 */
export function LiveStatus() {
  const [s, setS] = useState({ kind: "checking" });

  useEffect(() => {
    let alive = true;
    let timer = 0;
    const check = async () => {
      try {
        const res = await fetch("/healthz", { cache: "no-store" });
        if (!res.ok) throw new Error(String(res.status));
        const h = await res.json();
        if (!alive) return;
        setS({ kind: h.live ? "live" : "standin", sandbox: h.sandbox, at: new Date() });
      } catch {
        if (alive) setS({ kind: "down", at: new Date() });
      }
    };
    const schedule = () => {
      clearInterval(timer);
      if (!document.hidden) timer = setInterval(check, EVERY_MS);
    };
    const onVis = () => {
      if (!document.hidden) check();
      schedule();
    };
    check();
    schedule();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      alive = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, []);

  const time = s.at?.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const sandbox = SANDBOX[s.sandbox] || s.sandbox;
  const label = {
    checking: "Checking the hosted service…",
    live: "Live now · Token Factory and Nebius Sandboxes verified",
    standin: `Hosted service is up, not live (sandbox: ${sandbox})`,
    down: "Hosted service did not answer (it may be waking up)",
  }[s.kind];

  return (
    <p className={`live live--${s.kind}`} role="status">
      <span className="live-dot" aria-hidden="true" />
      <span>{label}</span>
      {time && <span className="live-at mono">checked {time}</span>}
    </p>
  );
}
