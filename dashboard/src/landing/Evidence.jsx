import { useReveal } from "./useReveal";
import { ForkRaceViz } from "./ForkRaceViz";
import { FlaskConical } from "./icons";

/* Real, sourced scores from Scale AI's public SWE Atlas Refactoring
   leaderboard (labs.scale.com/leaderboard/sweatlas-refactoring), read on
   2026-09-19. Nemotron 3 is not on this leaderboard yet — that comparison is
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
  { id: "A", label: "Baseline", body: "Nemotron 3 Super alone, full repo in context, one attempt, no sandbox. What the open model does unaided." },
  { id: "B", label: "Sandbox, no swarm", body: "Same model, one sandbox, tests available, retry on failure. Isolates the value of verification alone." },
  { id: "C", label: "Principal", body: "Full swarm — blast radius, parallel forks, tiered routing. Isolates the value of the full architecture." },
  { id: "D", label: "Reference", body: "Published frontier scores on the identical task subset. The honest distance still remaining." },
];

export function Evidence() {
  const [ref, shown] = useReveal();
  const max = 65;

  return (
    <section className="section" id="evidence" ref={ref}>
      <div className={`wrap reveal${shown ? " is-shown" : ""}`}>
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

        <div className="card" style={{ marginTop: 40 }}>
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
                      style={{ width: shown ? `${(m.score / max) * 100}%` : "0%" }}
                    />
                  </span>
                  <span className="bench-val">{m.score.toFixed(1)}%</span>
                </div>
              ))}

              <div className="bench-row" aria-label="Principal, target, not yet measured">
                <span className="bench-name">
                  <b>Principal</b> · Nemotron 3 swarm
                </span>
                <span className="bench-track" style={{ borderStyle: "dashed" }}>
                  <span
                    style={{
                      display: "block",
                      height: "100%",
                      width: shown ? "100%" : "0%",
                      transition: "width 1.1s var(--ease) 0.3s",
                      background:
                        "repeating-linear-gradient(135deg, transparent 0 6px, rgba(118,185,0,0.35) 6px 12px)",
                      borderRadius: 6,
                    }}
                  />
                </span>
                <span className="bench-val" style={{ color: "var(--nvidia)" }}>
                  target
                </span>
              </div>
            </div>

            <p className="bench-legend">
              Solid bars are published, third-party scores —{" "}
              <a
                href="https://labs.scale.com/leaderboard/sweatlas-refactoring"
                target="_blank"
                rel="noreferrer"
                style={{ color: "var(--muted)", textDecoration: "underline" }}
              >
                Scale AI, SWE Atlas Refactoring leaderboard
              </a>
              , read 2026-09-19. Nemotron 3 has not been benchmarked on this suite yet
              — the hatched bar is this project's target, not a claimed result. The
              PRD's stated bar: <b style={{ color: "var(--text)" }}>beat an unscaffolded
              Nemotron baseline by 2x or more</b>, on the same task subset, same day,
              with every trace published.
            </p>
          </div>
        </div>

        <div className="bento" style={{ marginTop: 24, gridTemplateColumns: "repeat(4, 1fr)" }}>
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

        <div className="card" style={{ marginTop: 24 }}>
          <div className="card-head">
            <h3>The checkpoint-fork race, in slow motion</h3>
          </div>
          <div className="card-body">
            <ForkRaceViz />
          </div>
        </div>
      </div>
    </section>
  );
}
