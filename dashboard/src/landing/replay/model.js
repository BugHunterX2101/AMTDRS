/**
 * The state of a recorded run at a moment on its own clock.
 *
 * Pure: (run, t) -> state. The renderer and the HUD both read this, so the
 * picture and the numbers next to it can never disagree, and the same function
 * gives the finished state for a reduced-motion still.
 *
 * Every attempt is kept, winners and losers alike. A cancelled candidate and one
 * the diff parser rejected are the product doing its job, so they stay in the
 * tree, dimmed, rather than disappearing.
 */

export function stateAt(run, t) {
  const tasks = new Map(run.tasks.map((x) => [x.id, { ...x, settled: false, attempts: [] }]));
  const attempts = new Map();
  let phase = "Queued";
  let baseline = false;
  let plan = false;
  let integration = null;
  let strength = null;
  let impact = null;

  for (const e of run.events) {
    if (e.t > t) break;
    switch (e.k) {
      case "state":
        phase = e.v;
        break;
      case "baseline":
        baseline = true;
        break;
      case "plan":
        plan = true;
        break;
      case "start": {
        const a = { id: e.att, task: e.task, n: e.n, tier: e.tier, started: e.t, ended: null, verdict: null, gate: null };
        attempts.set(e.att, a);
        tasks.get(e.task)?.attempts.push(a);
        break;
      }
      case "verdict": {
        const a = attempts.get(e.att);
        if (a) {
          a.ended = e.t;
          a.verdict = e.v;
          a.gate = e.gate;
        }
        break;
      }
      case "settled": {
        const k = tasks.get(e.task);
        if (k) k.settled = true;
        break;
      }
      case "integration":
        integration = e.pass;
        break;
      case "impact":
        impact = { changed: e.changed, expected: e.expected };
        break;
      case "strength":
        strength = { detected: e.detected, generated: e.generated, score: e.score, floor: e.floor };
        break;
      default:
        break;
    }
  }

  const all = [...attempts.values()];
  const count = (v) => all.filter((a) => a.verdict === v).length;
  return {
    t,
    phase,
    baseline,
    plan,
    integration,
    impact,
    strength,
    tasks: [...tasks.values()].sort((a, b) => a.seq - b.seq),
    started: all.length,
    running: all.filter((a) => a.verdict === null).length,
    passed: count("ok"),
    cancelled: count("cancelled"),
    rejected: all.filter((a) => a.verdict && a.verdict !== "ok" && a.verdict !== "cancelled").length,
    verified: [...tasks.values()].filter((k) => k.settled).length,
    done: phase === "Done",
  };
}

export function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}
