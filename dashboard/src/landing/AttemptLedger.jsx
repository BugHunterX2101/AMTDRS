import run from "./replay/run-01M5H0RM.json";
import { clock, stateAt } from "./replay/model";

/**
 * Every attempt of the recorded run in the hero, laid out on the run's own clock.
 *
 * The same run and the same model.js as the replay, so the two figures can never
 * disagree. Static markup: the bars are where the events put them and are drawn
 * by default. Where the browser supports scroll-driven animation, scrolling the
 * figure into view is the clock: a playhead crosses it and each bar is revealed
 * up to the playhead, so scroll position and run time line up.
 */

const D = run.duration;
const final = stateAt(run, D);
const frac = (t) => t / D;
const pct = (t) => `${(t / D) * 100}%`;
const secs = (s) => `${s.toFixed(1)} s`;

const VERDICT = { ok: "tests passed", cancelled: "cancelled" };
const verdictText = (a) => VERDICT[a.verdict] ?? `rejected at the ${a.gate} gate`;

const PHASES = run.events
  .filter((e) => e.k === "state")
  .map((e, i, all) => ({ name: e.v, from: e.t, to: all[i + 1]?.t ?? D }))
  .filter((p) => p.name !== "Done");

const TICKS = Array.from({ length: Math.floor(D / 60) + 1 }, (_, i) => i * 60);

const ROWS = final.tasks.map((task) => {
  const won = task.attempts.find((a) => a.verdict === "ok");
  const first = Math.min(...task.attempts.map((a) => a.started));
  return { ...task, won, first, took: won ? won.ended - first : null };
});

const GATES = [...new Set(ROWS.flatMap((r) => r.attempts).map((a) => a.gate).filter(Boolean))].join(", ");

// The task that edits the refactor's target, and the task that settled last before it opened.
const target = ROWS.find((r) => run.provenance.target.startsWith(`${r.file.replace(/\.py$/, "").replaceAll("/", ".")}.`));
const before =
  target &&
  ROWS.filter((r) => r !== target && r.won && r.won.ended <= target.first).sort((a, b) => b.won.ended - a.won.ended)[0];

export function AttemptLedger() {
  const p = run.provenance;

  return (
    <figure className="ledger">
      <dl className="ledger-sum">
        <div>
          <dt>tasks</dt>
          <dd>{final.tasks.length}</dd>
        </div>
        <div>
          <dt>attempts</dt>
          <dd>{final.started}</dd>
        </div>
        <div className="is-pass">
          <dt>tests passed</dt>
          <dd>{final.passed}</dd>
        </div>
        <div className="is-cancel">
          <dt>cancelled</dt>
          <dd>{final.cancelled}</dd>
        </div>
        <div className="is-reject">
          <dt>rejected</dt>
          <dd>{final.rejected}</dd>
        </div>
        <div>
          <dt>wall clock</dt>
          <dd>{clock(D)}</dd>
        </div>
      </dl>

      <div className="ledger-chart" aria-hidden="true" style={{ "--minute": pct(60) }}>
        <div className="ledger-row ledger-row--phases">
          <div className="ledger-file" />
          <div className="ledger-phases">
            {PHASES.map((ph) => (
              <span
                key={ph.name}
                className={`ph ph--${ph.name.toLowerCase()}${frac(ph.to - ph.from) < 0.1 ? " is-narrow" : ""}`}
                style={{ left: pct(ph.from), width: pct(ph.to - ph.from) }}
                title={`${ph.name} · ${clock(ph.from)} to ${clock(ph.to)}`}
              >
                <span>{ph.name}</span>
              </span>
            ))}
          </div>
        </div>

        {ROWS.map((r) => (
          <div className="ledger-row" key={r.id}>
            <div className="ledger-file">
              <span className="seq">{String(r.seq).padStart(2, "0")}</span>
              <span className="path">{r.file}</span>
              {r.took !== null && <span className="took">{secs(r.took)}</span>}
            </div>
            <div className="ledger-lanes">
              {r.attempts.map((a) => (
                <span
                  key={a.id}
                  className={`bar bar--${a.verdict}`}
                  style={{
                    left: pct(a.started),
                    width: pct(a.ended - a.started),
                    "--n": a.n,
                    "--s": frac(a.started),
                    "--e": frac(a.ended),
                  }}
                  title={`${r.file} · attempt ${a.n} · ${verdictText(a)} · ${clock(a.started)} to ${clock(a.ended)} (${secs(a.ended - a.started)})`}
                />
              ))}
            </div>
          </div>
        ))}

        <div className="ledger-row ledger-row--axis">
          <div className="ledger-file" />
          <div className="ledger-axis">
            {TICKS.map((t) => (
              <span key={t} style={{ left: pct(t) }}>
                {clock(t)}
              </span>
            ))}
          </div>
        </div>

        <div className="ledger-playhead">
          <span />
        </div>
      </div>

      <ul className="ledger-key" aria-hidden="true">
        <li className="k-pass">tests passed, kept</li>
        <li className="k-cancel">cancelled when a sibling passed</li>
        <li className="k-reject">rejected at the {GATES} gate</li>
      </ul>

      <div className="sr-only">
        <p>Every attempt in recorded job {p.job}, by task, on the run's own clock of {clock(D)}.</p>
        <ul>
          {ROWS.map((r) => (
            <li key={r.id}>
              {r.file}:{" "}
              {r.attempts.map((a) => `attempt ${a.n} ran ${clock(a.started)} to ${clock(a.ended)}, ${verdictText(a)}`).join("; ")}.
            </li>
          ))}
        </ul>
      </div>

      <figcaption className="ledger-note">
        Each task races three candidates from the same checkpoint. The first to pass the repository's tests is kept
        and the others are cancelled where they stand; a candidate whose diff cannot be parsed is rejected at the{" "}
        {GATES} gate. The time beside each file runs from its first candidate starting to its winner passing.
        {target && before && (
          <>
            {" "}
            The task on the target, <span className="mono">{target.file}</span>, opened{" "}
            {(target.first - before.won.ended).toFixed(2)} s after <span className="mono">{before.file}</span> settled.
          </>
        )}{" "}
        Recorded on {p.recorded} through {p.where}, on {p.sandbox} and {p.inference}: job{" "}
        <span className="mono">{p.job.slice(0, 10)}…</span>, the same run the hero replays.
      </figcaption>
    </figure>
  );
}
