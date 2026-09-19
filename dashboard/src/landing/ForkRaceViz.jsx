import { useEffect, useState } from "react";
import { GitBranch } from "./icons";

/**
 * A looping, self-driving recreation of the thing that actually happens
 * inside a Principal job: N candidates fork from one green checkpoint and
 * race to a passing test run. First green wins; everything still running at
 * that instant is cancelled, not deleted — a losing candidate is dimmed in
 * place, because a discarded attempt is evidence, never noise.
 *
 * This is illustrative motion graphics, not a replay of a real job — the
 * dashboard at /app renders the same shapes from real SSE events. Making
 * that distinction legible matters on a page whose whole argument is "trust
 * the evidence, not the pitch," so the caption says so explicitly.
 */
const TEMPLATE = [
  { id: "c1", label: "candidate 1", tag: "Nano · t0.0", min: 2400, max: 3800 },
  { id: "c2", label: "candidate 2", tag: "Nano · t0.4", min: 1900, max: 3200 },
  { id: "c3", label: "candidate 3", tag: "Nano · t0.8", min: 2200, max: 3600 },
  { id: "c4", label: "repair", tag: "Super · retry", min: 3400, max: 4400 },
];

const HOLD_MS = 2400;
const TICK_MS = 60;

function freshCycle() {
  return TEMPLATE.map((c) => ({
    ...c,
    ms: c.min + Math.random() * (c.max - c.min),
    progress: 0,
    verdict: "running",
  }));
}

export function ForkRaceViz({ compact = false }) {
  const [rows, setRows] = useState(freshCycle);

  useEffect(() => {
    let holding = false;
    let holdStart = 0;

    const id = setInterval(() => {
      const now = performance.now();

      setRows((prev) => {
        if (holding) {
          return now - holdStart >= HOLD_MS ? freshCycle() : prev;
        }
        if (prev.some((r) => r.verdict === "pass")) return prev;

        // Advance every still-running candidate, then find whichever one (if
        // any) crossed the line this tick — array order breaks ties, which
        // only matters on the rare tick where two candidates finish together.
        const advanced = prev.map((r) =>
          r.verdict === "running"
            ? { ...r, progress: Math.min(100, r.progress + (100 * TICK_MS) / r.ms) }
            : r
        );
        const winner = advanced.find((r) => r.verdict === "running" && r.progress >= 100);
        if (!winner) return advanced;

        holding = true;
        holdStart = now;
        return advanced.map((r) => {
          if (r.id === winner.id) return { ...r, progress: 100, verdict: "pass" };
          return r.verdict === "running" ? { ...r, verdict: "cancelled" } : r;
        });
      });
    }, TICK_MS);

    return () => clearInterval(id);
  }, []);

  return (
    <div className={`forkviz${compact ? " forkviz--compact" : ""}`}>
      <div className="fork-baseline">
        <GitBranch aria-hidden="true" />
        <span>C0 — green baseline checkpoint</span>
      </div>
      <div className="fork-rows" role="list" aria-label="Simulated candidate race">
        {rows.map((r) => (
          <div
            key={r.id}
            role="listitem"
            className={`fork-row${r.verdict === "pass" ? " is-pass" : ""}${
              r.verdict === "fail" ? " is-fail" : ""
            }${r.verdict === "cancelled" ? " is-cancelled" : ""}`}
          >
            <span className="branch">└─</span>
            <span>{r.label}</span>
            <span className="fork-track">
              <span className="fork-fill" style={{ width: `${r.progress}%` }} />
            </span>
            <span className="verdict">
              {r.verdict === "running" && "running…"}
              {r.verdict === "pass" && "✓ green — wins"}
              {r.verdict === "cancelled" && "cancelled"}
              {r.verdict === "fail" && "✗ failed"}
            </span>
          </div>
        ))}
      </div>
      {!compact && (
        <p className="forkviz-caption">
          Simulated for illustration — the same shapes render from real Server-Sent
          Events in the live console. Losers are cancelled, not deleted: every
          candidate's diff and test log stays inspectable.
        </p>
      )}
    </div>
  );
}
