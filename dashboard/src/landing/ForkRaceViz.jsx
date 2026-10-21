import { GitBranch } from "./icons";

/**
 * A looping recreation of the thing that actually happens inside a Principal
 * job: N candidates fork from one green checkpoint and race to a passing test
 * run. First green wins; everything still running at that instant is
 * cancelled, not deleted, and a cancelled candidate is dimmed in place because
 * a discarded attempt is evidence, never noise.
 *
 * It is static markup. The timeline lives in CSS (landing.css, "fork race"):
 * transform and opacity keyframes, so it costs no JavaScript, no React renders
 * and nothing on the main thread. It used to be a setInterval re-rendering four
 * rows every 60ms for as long as the page was open.
 *
 * Illustrative, not a replay of a real job. The console at /app renders the same
 * shapes from real Server-Sent Events, and the caption says so, which matters on
 * a page whose argument is "trust the evidence, not the pitch."
 */
const ROWS = [
  { id: "c1", label: "candidate 1", cls: "fork-row--lose fork-row--lose-a", end: "cancelled" },
  { id: "c2", label: "candidate 2", cls: "fork-row--win", end: "✓ green — wins" },
  { id: "c3", label: "candidate 3", cls: "fork-row--lose fork-row--lose-b", end: "cancelled" },
  { id: "c4", label: "repair", cls: "fork-row--standby", end: "" },
];

export function ForkRaceViz({ compact = false }) {
  return (
    <div className={`forkviz${compact ? " forkviz--compact" : ""}`}>
      <div className="fork-baseline">
        <GitBranch aria-hidden="true" />
        <span>C0 — green baseline checkpoint</span>
      </div>
      <div className="fork-rows" role="list" aria-label="Simulated candidate race">
        {ROWS.map((r) => (
          <div key={r.id} role="listitem" className={`fork-row ${r.cls}`}>
            <span className="branch" aria-hidden="true">└─</span>
            <span>{r.label}</span>
            <span className="fork-track" aria-hidden="true">
              <span className="fork-fill" />
            </span>
            <span className="verdict">
              <span className="v-run">{r.id === "c4" ? "standby" : "running…"}</span>
              {r.end && <span className="v-end">{r.end}</span>}
            </span>
          </div>
        ))}
      </div>
      {!compact && (
        <p className="forkviz-caption">
          Simulated for illustration — the same shapes render from real Server-Sent
          Events in the live console. Losers are cancelled, not deleted: every
          candidate's diff and test log stays inspectable. The repair attempt only
          runs when all three first candidates fail.
        </p>
      )}
    </div>
  );
}
