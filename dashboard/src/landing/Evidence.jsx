import { AttemptLedger } from "./AttemptLedger";
import { FlaskConical } from "./icons";

/* Real, sourced scores from Scale AI's public SWE Atlas Refactoring
   leaderboard (labs.scale.com/leaderboard/sweatlas-refactoring), read on
   2026-10-22. Nemotron 3 is not on this leaderboard yet — that comparison is
   exactly what Principal's own evaluation plan runs, which is why the last
   row is drawn as a target, not a result. Do not add a real-looking bar for
   Principal until an actual run produces one. */
const LEADERBOARD = [
  { name: "GPT 6 Astra", agent: "Codex", score: 59.1, tier: "ceiling" },
  { name: "Fable-5.1", agent: "Claude Code", score: 56.7, tier: "ceiling" },
  { name: "Fable-5", agent: "Claude Code", score: 54.8, tier: "ceiling" },
  { name: "Opus-4.7", agent: "Claude Code", score: 48.6, tier: "closed" },
  { name: "GLM 5.2", agent: "Mini-SWE-Agent", score: 42.4, tier: "open" },
  { name: "Kimi-K2.5", agent: "Mini-SWE-Agent", score: 21.0, tier: "open" },
];

const ARMS = [
  { id: "A", label: "Single shot, no gates", body: "One candidate, run in the same sandbox but accepted whatever the tests say. What one model call achieves unsupervised." },
  { id: "B", label: "One candidate, gates on", body: "Same single candidate, but it must pass every gate. Isolates the value of verification alone." },
  { id: "C", label: "Full swarm", body: "Three candidates per task plus repairs, same gates. Isolates the added value of parallel search." },
  { id: "D", label: "Reference", body: "Published frontier scores, shown for distance only. Not run by this repo, and a different task set." },
];

export function Evidence() {
  const max = 65;

  return (
    <section className="section" id="evidence">
      <div className="wrap reveal">
        <div className="eyebrow">Evidence, not adjectives</div>
        <h2 className="section-title">
          The gap this project exists to close — shown with real numbers.
        </h2>
        <p className="section-lede">
          Frontier models clear 80%+ on issue-resolution benchmarks and fall sharply
          on multi-file, behaviour-preserving refactors. SWE Atlas Refactoring tasks
          need roughly 2x the lines changed and 1.7x the files edited of SWE-Bench
          Pro tasks — this is the category coding agents are measurably worst at.
        </p>

        <div className="card card--bench">
          <div className="card-head">
            <h3>SWE Atlas Refactoring — public leaderboard</h3>
          </div>
          <div className="card-body">
            <div className="bench">
              {LEADERBOARD.map((m) => (
                <div className="bench-row" key={m.name}>
                  <span className="bench-name">
                    <b>{m.name}</b> · {m.agent}
                  </span>
                  <span className="bench-track">
                    <span
                      className={`bench-fill${
                        m.tier === "open"
                          ? " bench-fill--muted"
                          : m.tier === "ceiling"
                          ? " bench-fill--ceiling"
                          : ""
                      }`}
                      style={{ width: `${(m.score / max) * 100}%` }}
                    />
                  </span>
                  <span className="bench-val">{m.score.toFixed(1)}%</span>
                </div>
              ))}

              <div className="bench-row bench-row--target" aria-label="Principal, target, not yet measured">
                <span className="bench-name">
                  <b>Principal</b> · Nemotron 3 swarm
                </span>
                <span className="bench-track">
                  <span className="bench-fill bench-fill--target" />
                </span>
                <span className="bench-val">target</span>
              </div>
            </div>

            <p className="bench-legend">
              Solid bars are published, third-party scores —{" "}
              <a href="https://labs.scale.com/leaderboard/sweatlas-refactoring" target="_blank" rel="noreferrer">
                Scale AI, SWE Atlas Refactoring leaderboard
              </a>
              , read 2026-10-22. Nemotron 3 has not been benchmarked on this suite yet
              — the hatched bar is this project's target, not a claimed result. The
              PRD's stated bar: <b>beat an unscaffolded
              Nemotron baseline by 2x or more</b>, on the same task subset, same day,
              with every trace published.
            </p>
          </div>
        </div>

        <div className="bento bento--4">
          {ARMS.map((a) => (
            <div className="card" key={a.id}>
              <div className="card-body">
                <div className="ico ico--violet">
                  <FlaskConical aria-hidden="true" />
                </div>
                <h3>
                  Arm {a.id} — {a.label}
                </h3>
                <p>{a.body}</p>
              </div>
            </div>
          ))}
        </div>

        <div className="card card--ledger">
          <div className="card-head">
            <h3>Every attempt in one recorded run</h3>
          </div>
          <div className="card-body">
            <AttemptLedger />
          </div>
        </div>
      </div>
    </section>
  );
}
