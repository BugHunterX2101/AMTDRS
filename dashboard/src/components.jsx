import { useEffect, useRef, useState } from "react";

/* ------------------------------------------------------------------ header -- */

export function PanelHeader({ children }) {
  return (
    <header>
      <h2>{children}</h2>
    </header>
  );
}

/* What this run is actually executing on. A job records its own provenance when
   it is created, so a finished run keeps the label it was measured under even if
   the server is later restarted against different backends. Before any job
   exists it falls back to what /healthz verified at startup. */
export function runMode(provenance, health) {
  if (provenance) {
    if (provenance.live) {
      return { live: true, label: "LIVE · Nebius sandbox + inference", title: "Every check ran in a real Nebius sandbox, with real Token Factory model calls." };
    }
    return {
      live: false,
      label: `NOT LIVE · sandbox ${provenance.sandbox ?? "unknown"} · inference ${provenance.inference ?? "unknown"}`,
      title: "A local stand-in or an unconfigured backend was used. These results are not a measurement of the Nebius platform.",
    };
  }
  if (!health) return { live: false, label: "…", title: "" };
  if (health.live) {
    return { live: true, label: "LIVE · Nebius verified at startup", title: "Token Factory and Sandboxes both answered a real call when the server started." };
  }
  return {
    live: false,
    label: health.sandboxes_configured ? "Nebius configured · not verified" : `LOCAL STAND-IN · ${health.sandbox ?? "…"}`,
    title: "No successful live call has been made yet, so runs here are not a platform measurement.",
  };
}

export function Masthead({ health, provenance, connected, finished, jobId, running, onCancel }) {
  const mode = runMode(provenance, health);
  // "disconnected" on a run that simply ended read as a fault. A finished run
  // has no stream to be connected to, so say that instead.
  const stream = finished ? "finished" : connected ? "streaming" : running ? "reconnecting…" : "idle";
  return (
    <header className="masthead">
      <a href="/" className="home-link" aria-label="Back to the overview page">
        <svg
          width="15"
          height="15"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M19 12H5" />
          <path d="m11 18-6-6 6-6" />
        </svg>
      </a>
      <div>
        <h1>Principal</h1>
        <div className="tagline">
          Verification-gated parallel refactoring. No green test, no PR.
        </div>
      </div>
      <div className="spacer" />
      <span className="badge nvidia">NVIDIA Nemotron 3</span>
      <span className={`badge ${mode.live ? "live" : "warn"}`} title={mode.title}>
        {mode.label}
      </span>
      {jobId && (
        <span className={`badge ${connected ? "live" : "off"}`} role="status">
          {stream}
        </span>
      )}
      {running && (
        <button type="button" className="cancel-btn" onClick={onCancel}>
          Cancel run
        </button>
      )}
    </header>
  );
}

/* -------------------------------------------------------------------- form -- */

// The bundled fixture, published as its own commit. A local path cannot be
// cloned by the live sandbox, so the published commit works both live and on
// the local stand-in.
const DEFAULTS = {
  repo_url: "https://github.com/BugHunterX2101/AMTDRS",
  commit_sha: "3d11f5598b40ce6221f4315543f0d1243b480676",
  target_fqn: "src.auth.session.create",
  goal: "Make the ttl parameter keyword-only and update every call site.",
  routine: "interface_evolution",
};

const ROUTINES = [
  { value: "interface_evolution", label: "Interface evolution — change a signature" },
  { value: "relocation", label: "Relocation — move a symbol" },
];

export function JobForm({ onStart, running }) {
  const [form, setForm] = useState(DEFAULTS);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  return (
    <form
      className="body"
      onSubmit={(e) => {
        e.preventDefault();
        onStart(form);
      }}
    >
      <div className="field">
        <label htmlFor="repo">Repository</label>
        <input id="repo" value={form.repo_url} onChange={set("repo_url")} />
      </div>
      <div className="field">
        <label htmlFor="commit">Commit</label>
        <input id="commit" value={form.commit_sha} onChange={set("commit_sha")} />
      </div>
      <div className="field">
        <label htmlFor="target">Target symbol</label>
        <input id="target" value={form.target_fqn} onChange={set("target_fqn")} />
      </div>
      <div className="field">
        <label htmlFor="goal">Goal</label>
        <textarea id="goal" value={form.goal} onChange={set("goal")} />
      </div>
      <div className="field">
        <label htmlFor="routine">Routine</label>
        <select id="routine" value={form.routine} onChange={set("routine")}>
          {ROUTINES.map((r) => (
            <option key={r.value} value={r.value}>
              {r.label}
            </option>
          ))}
        </select>
      </div>
      <button className="primary" type="submit" disabled={running}>
        {running ? "Running…" : "Start job"}
      </button>
    </form>
  );
}

/* ---------------------------------------------------------------- pipeline -- */

const STAGES = [
  "Ingesting", "Baselining", "Mapping", "Characterising", "Planning", "Executing",
  "CleanupPlanning", "CleanupExecuting", "Integrating", "Publishing",
];
const STAGE_LABEL = { CleanupPlanning: "Cleanup planning", CleanupExecuting: "Cleanup executing" };
const TERMINAL_STATES = ["Done", "NoPR", "Aborted", "NoSafetyNet"];

// Stages the job finished are green, and the one a stopped job stopped in takes
// the colour of how it stopped. A stage it passed without entering (no cleanup
// needed, say) is marked skipped; the stages it never reached stay plain.
export function Pipeline({ state, reached, visited = [] }) {
  const terminal = TERMINAL_STATES.includes(state);
  const idx = state === "Done" ? STAGES.length : STAGES.indexOf(terminal ? reached : state);
  return (
    <div className="pipeline">
      {STAGES.map((s, i) => {
        let cls = "stage";
        if (i < idx) cls += visited.includes(s) ? " done" : " skipped";
        else if (i === idx) cls += terminal ? (state === "Aborted" ? " failed" : " nopr") : " active";
        return (
          <span key={s} className={cls}>
            {STAGE_LABEL[s] || s}
          </span>
        );
      })}
      {terminal && (
        <span
          className={`stage ${
            state === "Done" ? "done" : state === "Aborted" ? "failed" : "nopr"
          }`}
        >
          {state}
        </span>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------- stats -- */

export function Stats({ job, status }) {
  const attempts = Object.values(job.attempts);
  const tasks = Object.values(job.tasks);
  return (
    <div className="stats">
      <Stat k="Tasks" v={`${tasks.filter((t) => t.state === "verified").length}/${tasks.length}`} />
      <Stat k="Candidates" v={attempts.length} />
      <Stat k="Radius" v={job.radius ? `${job.radius.files.length} files` : "—"} />
      <Stat
        k="Tokens"
        v={status?.budget ? compact(status.budget.spent) : "—"}
      />
    </div>
  );
}

function Stat({ k, v }) {
  return (
    <div className="stat">
      <div className="k">{k}</div>
      <div className="v">{v}</div>
    </div>
  );
}

function compact(n) {
  if (n == null) return "—";
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(n);
}

/* --------------------------------------------------------------- economics -- */

/* The cost argument, made concrete from data the run already produces.
   Gates 1 and 2 ("scope", "syntax") run locally and never allocate a sandbox,
   so a candidate that dies there is genuinely free. That ratio — how much of
   the search was paid for and how much was not — is the whole reason generous
   fan-out is affordable, and it was previously visible nowhere in the UI.

   Every number here comes from a real field: budget from GET /jobs/{id},
   gate names from the attempt.verdict events. Nothing is modelled. */
// Every gate a candidate can stop at without a sandbox: the two local gates, a
// reply with no usable diff, and a model call that failed outright.
const FREE_GATES = new Set(["scope", "syntax", "diffparse", "model"]);

export function Economics({ job, status }) {
  const attempts = Object.values(job.attempts);
  // A cancelled loser was stopped, not judged, so it is in neither column.
  const settled = attempts.filter((a) => a.verdict && a.verdict !== "running" && a.verdict !== "cancelled");
  const free = settled.filter((a) => a.gate && FREE_GATES.has(a.gate));
  const billed = settled.filter((a) => !a.gate || !FREE_GATES.has(a.gate));

  const spent = status?.budget?.spent;
  const limit = status?.budget?.limit;
  const pct = spent != null && limit ? Math.min(100, (spent / limit) * 100) : null;

  if (attempts.length === 0 && spent == null) {
    return (
      <div className="body">
        <p className="empty">
          Cost accrues only where a candidate reaches a sandbox. Gates 1 and 2 run
          in-process, so most rejected candidates are discarded before anything
          billable happens — this panel fills in once a run starts.
        </p>
      </div>
    );
  }

  return (
    <div className="body">
      <div className="stats" style={{ marginBottom: 12 }}>
        <Stat k="Free rejections" v={free.length} />
        <Stat k="Reached a sandbox" v={billed.length} />
      </div>

      {settled.length > 0 && (
        <div className="costbar" title={`${free.length} of ${settled.length} settled candidates cost nothing`}>
          <span
            className="costbar-free"
            style={{ width: `${(free.length / settled.length) * 100}%` }}
          />
        </div>
      )}

      {settled.length > 0 && (
        <p className="hint" style={{ marginTop: 10 }}>
          {free.length} of {settled.length} settled candidates were rejected before a
          sandbox was allocated: at a local gate, or because the reply held no usable
          diff. Those cost model tokens but no sandbox time.
        </p>
      )}

      {pct != null && (
        <div style={{ marginTop: 14 }}>
          <div className="kv-line">
            <span>Token budget</span>
            <span className="mono">
              {compact(spent)} / {compact(limit)}
            </span>
          </div>
          <div className="costbar" style={{ marginTop: 6 }}>
            <span className="costbar-spend" style={{ width: `${pct}%` }} />
          </div>
        </div>
      )}

      {status?.budget?.refusals > 0 && (
        <p className="hint" style={{ marginTop: 10, color: "var(--amber)" }}>
          {status.budget.refusals} call(s) refused by the budget cap. The job aborts
          rather than quietly spending past its limit.
        </p>
      )}

      {status?.rate_limit?.remaining_tokens != null && (
        <div className="kv-line" style={{ marginTop: 10 }}>
          <span>Rate-limit headroom</span>
          <span className="mono">{compact(status.rate_limit.remaining_tokens)} tok</span>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ radius -- */

export function RadiusPanel({ radius }) {
  if (!radius) {
    return (
      <div className="body">
        <p className="empty">
          The blast radius is computed from tree-sitter call and import edges before
          anything is generated. Files outside it are rejected by gate 1.
        </p>
      </div>
    );
  }
  const target = radius.target?.path;
  return (
    <div className="body">
      <div className="filelist">
        {radius.files.map((f) => (
          <div key={f} className={f === target ? "target" : ""}>
            {f === target ? "◆ " : "  "}
            {f}
          </div>
        ))}
      </div>
      <div style={{ marginTop: 10, fontSize: 12, color: "var(--muted)" }}>
        {radius.call_sites?.length ?? 0} call sites · {radius.tests?.length ?? 0} test files
      </div>

      {radius.unresolved?.length > 0 && (
        <div className="unresolved">
          <div className="h">{radius.unresolved.length} calls cannot be proved statically</div>
          {radius.unresolved.map((u, i) => (
            <div key={i}>
              <code>
                {u.file}:{u.line}
              </code>{" "}
              — {u.name}
            </div>
          ))}
          <div style={{ marginTop: 6, color: "var(--muted)" }}>
            Listed in the pull request for a human to check. Hiding these would be worse
            than not reporting them.
          </div>
        </div>
      )}
    </div>
  );
}

/* -------------------------------------------------------------- task board -- */

export function TaskBoard({ job, onSelect }) {
  const tasks = Object.values(job.tasks);
  if (tasks.length === 0) {
    // Say what actually happened. A run that ended before planning has no tasks,
    // and describing how planning works under it reads as if it had run.
    const ended = ["Done", "NoPR", "Aborted", "NoSafetyNet"].includes(job.state);
    return (
      <div className="body">
        <p className="empty">
          {ended
            ? "No tasks were planned: the run ended before the goal was decomposed."
            : "Nemotron 3 Ultra decomposes the goal into single-file tasks. Each task then becomes a race between independent candidates — first to pass its tests wins, and the losers are cancelled."}
        </p>
      </div>
    );
  }
  return (
    <div className="body">
      {tasks.map((t) => (
        <div className="task" key={t.id}>
          <div className="head">
            <span className={`dot ${t.state}`} />
            <span className="file">{t.file}</span>
            <span className="title">{t.title}</span>
          </div>
          <div className="candidates">
            {Object.keys(t.attempts).map((aid) => {
              const a = job.attempts[aid];
              if (!a) return null;
              const settled = t.state === "verified" || t.state === "discarded";
              const lost = settled && t.winner && t.winner !== a.id;
              const label = `${a.kind === "repair" ? "repair" : `candidate ${a.n}`}, verdict ${a.verdict}`;
              return (
                <div
                  key={aid}
                  role="button"
                  tabIndex={0}
                  aria-label={label}
                  className={[
                    "candidate",
                    verdictClass(a.verdict, a.gate),
                    lost ? "cancelled" : "",
                    a.kind === "repair" ? "repair" : "",
                  ].join(" ")}
                  onClick={() => onSelect(a)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect(a);
                    }
                  }}
                >
                  <div className="n">
                    {a.kind === "repair" ? "repair" : `candidate ${a.n}`}
                  </div>
                  <div className="verdict">{a.verdict}</div>
                  <div className="gate">{a.gate ? `gate: ${a.gate}` : shortModel(a.model)}</div>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

// Verdicts are the server's VerdictKind values: "ok" is the only pass. An
// "error" at the diffparse gate is a reply with no usable diff, rejected like
// any other gate failure (the landing page's ledger draws it the same way); an
// "error" from the model call itself is the platform's, not the candidate's.
function verdictClass(v, gate) {
  if (v === "ok") return "pass";
  if (v === "running") return "running";
  if (v === "cancelled") return "cancelled";
  if (v === "error" && gate === "model") return "error";
  return "fail";
}

function shortModel(m) {
  if (!m) return "";
  const tail = m.split("/").pop();
  return tail.length > 22 ? `${tail.slice(0, 22)}…` : tail;
}

/* --------------------------------------------------------------- fork tree -- */

/**
 * The image tree needs no bookkeeping of its own: sandbox images are
 * content-addressed, so `parent_image` and `result_image` on each attempt
 * already describe the graph the platform maintains.
 */
export function ForkTree({ nodes }) {
  if (!nodes || nodes.length === 0) {
    return (
      <div className="body">
        <p className="empty">
          Every candidate forks from the one green baseline image. The expensive
          install is paid once per job, not once per candidate — and a candidate that
          breaks the build breaks only its own fork.
        </p>
      </div>
    );
  }
  const roots = [...new Set(nodes.map((n) => n.parent_image).filter(Boolean))].filter(
    (p) => !nodes.some((n) => n.result_image === p)
  );
  return (
    <div className="body tree">
      {roots.map((r) => (
        <div key={r}>
          <div className="node root">● C0 {short(r)}</div>
          {nodes
            .filter((n) => n.parent_image === r)
            .map((n) => (
              <div key={n.attempt_id} className="node">
                {"  └─ "}
                <span className={n.verdict === "ok" ? "pass" : "fail"}>
                  {n.verdict === "ok" ? "✓" : n.verdict === "cancelled" ? "–" : "✗"}
                </span>{" "}
                {n.kind === "repair" ? "repair" : `cand ${n.n}`}{" "}
                <span className="uuid">{short(n.result_image) || "—"}</span>
              </div>
            ))}
        </div>
      ))}
    </div>
  );
}

function short(uuid) {
  return uuid ? String(uuid).slice(0, 12) : "";
}

/* ---------------------------------------------------------------- outcome -- */

/* Platform errors reach the page as raw exception text. A visitor who opens the
   hosted console without an inference key should read what happened and what
   still worked, not a Python dict. Anything unrecognised is passed through,
   bounded so a stack trace cannot take over the card. */
export function friendlyReason(text) {
  if (!text) return text;
  if (/HTTP 401|unauthenticated|Unable authenticate/i.test(text)) {
    return (
      "No valid inference key is configured on this server, so planning could not start. " +
      "The baseline, code graph and blast radius shown here ran for real; generating patches " +
      "needs a Token Factory key."
    );
  }
  if (/HTTP 403/i.test(text)) {
    return "The platform refused the request (403). The key may lack access to this project's Sandboxes.";
  }
  if (/HTTP 429|rate.?limit/i.test(text)) {
    return "The platform is rate-limiting this key. Nothing was concluded about the change; run it again shortly.";
  }
  return text.length > 400 ? `${text.slice(0, 400)}…` : text;
}

const VERDICT_CLASS = { VERIFIED: "pr", UNVERIFIED: "nopr", FAILED: "aborted", ERROR: "error" };
const VERDICT_TITLE = {
  VERIFIED: "Verified",
  UNVERIFIED: "Unverified — needs a human decision",
  FAILED: "Failed — a check found a defect",
  ERROR: "Error — the platform failed, not the patch",
};

/* The product's own classification, computed server-side from the run record
   (principal/outcome.py). The browser only displays it; it never re-derives it,
   so the banner, the evidence bundle and the CLI cannot disagree. */
function Verdict({ verdict, prUrl }) {
  return (
    <div className={`outcome ${VERDICT_CLASS[verdict.outcome] ?? "nopr"}`} role="status">
      <h2>{VERDICT_TITLE[verdict.outcome] ?? verdict.outcome}</h2>
      <p>{verdict.headline}</p>
      {verdict.reasons?.length > 0 && (
        <ul className="reasons">
          {verdict.reasons.map((r, i) => (
            <li key={i}>{friendlyReason(r)}</li>
          ))}
        </ul>
      )}
      <p className="hint" style={{ marginTop: 8 }}>
        {verdict.pr_offered
          ? verdict.outcome === "VERIFIED"
            ? "A draft pull request was opened."
            : "A draft pull request was opened and flagged for human review."
          : verdict.outcome === "ERROR"
          ? "Nothing was offered. This result says nothing about the change itself, so run it again."
          : "Nothing was offered and the repository is unchanged."}
      </p>
      {prUrl && (
        <p style={{ marginTop: 6 }}>
          <a href={prUrl} target="_blank" rel="noreferrer">
            {prUrl}
          </a>
        </p>
      )}
    </div>
  );
}

export function Outcome({ job, verdict, prUrl, health, pending = false }) {
  if (verdict) return <Verdict verdict={verdict} prUrl={prUrl} />;
  // The verdict comes from a status request that lands after the stream says the
  // run is over. Holding its place stops the page jumping when it arrives.
  if (pending) {
    return (
      <div className="outcome pending" role="status" aria-live="polite">
        <h2>Reading the verdict…</h2>
        <p>Classifying the run from its stored record.</p>
      </div>
    );
  }
  if (prUrl) {
    return (
      <div className="outcome pr">
        <h2>Draft pull request opened</h2>
        <p>
          The full suite passed with all patches applied together.
          <br />
          <a href={prUrl} target="_blank" rel="noreferrer">
            {prUrl}
          </a>
        </p>
      </div>
    );
  }
  if (job.state === "NoPR") {
    return (
      <div className="outcome nopr">
        <h2>No pull request — and that is the correct outcome</h2>
        <p>
          {job.stopReason || "The change could not be verified."}
          <br />
          <br />
          Principal fails closed. A refactoring tool that sometimes ships an unverified
          change converts a bounded engineering task into an unbounded review task, so
          declining to ship is a success state, not a failure.
        </p>
      </div>
    );
  }
  if (job.state === "NoSafetyNet") {
    return (
      <div className="outcome nopr">
        <h2>No safety net — refactoring declined before any patch was attempted</h2>
        <p>
          {job.stopReason || "The target has no test coverage, and the generated" +
            " characterisation suite did not clear the mutation-score floor."}
          <br />
          <br />
          Writing tests that authorise their own change would prove nothing — the same
          model would be grading its own homework. Principal instead measures whether the
          generated suite actually detects real changes to the target, and refuses to
          proceed when it can't, rather than refactoring against an oracle known to be
          blind.
        </p>
      </div>
    );
  }
  if (job.state === "Aborted") {
    return (
      <div className="outcome aborted">
        <h2>Aborted</h2>
        <p>{friendlyReason(job.stopReason) || "The job stopped before it could do any work."}</p>
      </div>
    );
  }
  if (job.state === "idle") {
    return (
      <div className="outcome idle">
        <h2>Nothing has run yet</h2>
        <p>
          Fill in a goal and a target symbol on the left, then Start job. Every
          candidate that gets generated either dies at a gate for free or earns its
          way onto this card — there is no state in between where an unverified diff
          is the answer.
        </p>
        {/* Only where it is true: the hosted service always has a key. */}
        {health && !health.inference_configured && (
          <p className="hint">
            This server has no inference key configured, so a run gets as far as the
            verified green baseline, the code graph and the blast radius — all of it
            real — and then stops at planning, because there is no model to plan with.
          </p>
        )}
      </div>
    );
  }
  return null;
}

/* ----------------------------------------------------------- impact + strength -- */

const SEVERITY_ORDER = { block: 0, warn: 1, info: 2 };

/* Did the change reach everything it should have, and nothing it should not?
   Every line here is a field of the impact.result event: nothing is inferred in
   the browser. */
export function ImpactPanel({ impact, running }) {
  if (!impact) {
    return (
      <div className="body">
        <p className="empty">
          {running
            ? "After the patches are integrated, the change is compared with the blast radius: files and call sites it should have reached, files it should not have touched, and dynamic paths no test exercises."
            : "No blast-radius result was recorded for this run — the run ended before integration."}
        </p>
      </div>
    );
  }
  const findings = [...(impact.findings || [])]
    .filter((f) => f.severity !== "info")
    .sort((a, b) => (SEVERITY_ORDER[a.severity] ?? 3) - (SEVERITY_ORDER[b.severity] ?? 3));
  return (
    <div className="body">
      <div className="stats" style={{ marginBottom: 12 }}>
        <Stat k="Files changed" v={`${impact.actual_files?.length ?? 0}/${impact.expected_files?.length ?? 0}`} />
        <Stat
          k="Call sites"
          v={`${impact.call_sites_changed ?? 0}/${impact.expected_call_sites ?? 0}`}
        />
      </div>
      {impact.unexpected_files?.length > 0 && (
        <div className="kv-line">
          <span>Outside the radius</span>
          <span className="mono">{impact.unexpected_files.join(", ")}</span>
        </div>
      )}
      {impact.dynamic_paths?.length > 0 && (
        <div className="kv-line">
          <span>Dynamic paths</span>
          <span className="mono">
            {impact.dynamic_paths.filter((d) => d.exercised_by_tests).length}/{impact.dynamic_paths.length} exercised
          </span>
        </div>
      )}
      {findings.length === 0 ? (
        <p className="hint" style={{ marginTop: 10 }}>
          No block or warning: every expected call site was updated and nothing outside the radius changed.
        </p>
      ) : (
        <ul className="findings">
          {findings.map((f, i) => (
            <li key={i} className={`finding ${f.severity}`}>
              <span className="sev">{f.severity}</span> {f.detail}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const pct = (x) => (x == null ? "—" : `${Math.round(x * 100)}%`);

/* Would the tests that just passed have noticed a mistake? An injected-fault
   measurement, labelled as one — never a proof. */
export function StrengthPanel({ strength, running }) {
  if (!strength) {
    return (
      <div className="body">
        <p className="empty">
          {running
            ? "Once the change passes, small deliberate faults are injected into only the lines it changed and the covering tests are re-run. A fault the tests miss is a located gap."
            : "No strength audit was recorded for this run."}
        </p>
      </div>
    );
  }
  if (strength.status !== "measured") {
    return (
      <div className="body">
        <p className="hint" style={{ color: "var(--amber)" }}>
          Not measured ({strength.status}){strength.reason ? `: ${strength.reason}` : ""}. This is reported
          as unmeasured, never as a passing score.
        </p>
      </div>
    );
  }
  return (
    <div className="body">
      <div className="stats" style={{ marginBottom: 12 }}>
        <Stat k="Faults detected" v={`${strength.detected}/${strength.detected + strength.survived}`} />
        <Stat k="Score / floor" v={`${pct(strength.score)} / ${pct(strength.floor)}`} />
      </div>
      {strength.below_floor && (
        <p className="hint" style={{ color: "var(--amber)" }}>
          Below the floor: the suite may not notice a real mistake in the changed lines.
        </p>
      )}
      {strength.survivors?.length > 0 && (
        <ul className="findings">
          {strength.survivors.map((s, i) => (
            <li key={i} className="finding warn">
              <span className="sev">missed</span> {s.file}:{s.line} — {s.description}
            </li>
          ))}
        </ul>
      )}
      <p className="hint" style={{ marginTop: 10 }}>{strength.disclaimer}</p>
    </div>
  );
}

/* The two post-integration checks share one panel. Separately they were two
   full-height empty cards for most of a run; together they read as one question,
   "how far can this be trusted", answered two ways. */
export function ChecksPanel({ impact, strength, running }) {
  return (
    <div className="checks">
      <section aria-labelledby="chk-impact">
        <h3 id="chk-impact">Blast radius</h3>
        <ImpactPanel impact={impact} running={running} />
      </section>
      <section aria-labelledby="chk-strength">
        <h3 id="chk-strength">Test strength</h3>
        <StrengthPanel strength={strength} running={running} />
      </section>
    </div>
  );
}

/* ------------------------------------------------------------------ evidence -- */

/* The evidence bundle is built server-side from the stored record. These are
   plain links, so what is downloaded is exactly what the API serves. */
export function EvidenceLinks({ jobId, ready }) {
  if (!jobId) return null;
  if (!ready) {
    return (
      <div className="body">
        <p className="empty">
          The evidence bundle — verdict, every gate result, each attempt's diff and test log with
          hashes, and the exact command to reproduce the run — is available when the run finishes.
        </p>
      </div>
    );
  }
  return (
    <div className="body">
      <div className="evidence-links">
        <a className="btn" href={`/jobs/${jobId}/evidence.zip`} download>
          Download bundle (.zip)
        </a>
        <a className="btn" href={`/jobs/${jobId}/evidence.md`} target="_blank" rel="noreferrer">
          Read report
        </a>
        <a className="btn" href={`/jobs/${jobId}/evidence`} target="_blank" rel="noreferrer">
          JSON
        </a>
      </div>
      <p className="hint" style={{ marginTop: 10 }}>
        The zip carries a manifest of SHA-256 hashes; each artifact is re-hashed when the bundle is
        built, so a file changed after the run is flagged rather than trusted.
      </p>
    </div>
  );
}

/* -------------------------------------------------------------------- steps -- */

/* Where you are, in three steps. Derived from the same state the pipeline reads. */
export function Steps({ jobId, state, running }) {
  const terminal = TERMINAL_STATES.includes(state);
  const current = !jobId ? 1 : terminal ? 3 : 2;
  const items = [
    ["1", "Choose a target", "a repository, commit, symbol and goal"],
    ["2", running ? "Watching the run" : "Run", "candidates are generated, then checked in sandboxes"],
    ["3", "Read the verdict", "outcome, evidence and the exact reproduce command"],
  ];
  return (
    <ol className="steps" aria-label="Progress">
      {items.map(([n, title, sub], i) => (
        <li key={n} className={i + 1 < current ? "done" : i + 1 === current ? "current" : ""}>
          <span className="n">{n}</span>
          <span>
            <strong>{title}</strong>
            <small>{sub}</small>
          </span>
        </li>
      ))}
    </ol>
  );
}

/* -------------------------------------------------------------------- log -- */

export function EventLog({ events }) {
  return (
    <div className="body log">
      {events.length === 0 && <p className="empty">The event log is append-only and resumable.</p>}
      {events.map((e, i) => (
        <div className="row" key={`${e.seq}-${i}`}>
          <span className="seq">{e.seq}</span>
          <span className="kind">{e.kind}</span>
          <span className="data">{JSON.stringify(e.payload).slice(0, 120)}</span>
        </div>
      ))}
    </div>
  );
}

/* ----------------------------------------------------------------- drawer -- */

export function EvidenceDrawer({ attempt, onClose }) {
  // The attempt's diff, and its test log when it reached a sandbox. A candidate
  // that died at a local gate still produced a diff worth reading.
  const [artifacts, setArtifacts] = useState([]);
  const asideRef = useRef(null);
  const ids = attempt ? [attempt.diffArtifactId, attempt.artifactId].filter(Boolean) : [];

  useEffect(() => {
    setArtifacts([]);
    if (ids.length === 0) return undefined;
    let cancelled = false;
    Promise.all(
      ids.map((id) =>
        fetch(`/artifacts/${id}`)
          .then((r) => (r.ok ? r.json() : null))
          .catch(() => null)
      )
    ).then((found) => !cancelled && setArtifacts(found.filter(Boolean)));
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Move focus into the drawer when it opens so keyboard/screen-reader users
  // land on the new content instead of a stale focus target behind the scrim.
  useEffect(() => {
    if (attempt) asideRef.current?.focus();
  }, [attempt]);

  if (!attempt) return null;

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} aria-hidden="true" />
      <aside
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="drawer-heading"
        tabIndex={-1}
        ref={asideRef}
      >
        <header>
          <h2 id="drawer-heading">
            {attempt.kind === "repair" ? "repair" : `candidate ${attempt.n}`} · {attempt.verdict}
          </h2>
          <div style={{ flex: 1 }} />
          <button onClick={onClose}>Close</button>
        </header>
        <div className="body">
          <dl className="kv">
            <dt>Attempt</dt>
            <dd>{attempt.id}</dd>
            <dt>Model</dt>
            <dd>{attempt.model}</dd>
            <dt>Temperature</dt>
            <dd>{attempt.temperature ?? "—"}</dd>
            <dt>Gate</dt>
            <dd>{attempt.gate ?? "—"}</dd>
            <dt>Sandbox operation</dt>
            <dd>{attempt.operationId ?? "— (died at a local gate, no sandbox used)"}</dd>
          </dl>

          {attempt.reason && (
            <>
              <div className="hint" style={{ marginBottom: 14 }}>
                {attempt.reason}
              </div>
            </>
          )}

          {attempt.failing?.length > 0 && (
            <>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 6 }}>
                Failing tests
              </div>
              <pre className="code">{attempt.failing.join("\n")}</pre>
            </>
          )}

          {artifacts.length > 0 ? (
            artifacts.map((art) => (
              <div key={art.id} style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 6 }}>
                  {art.kind === "diff" ? "diff" : "test log"} · sha256 {String(art.sha256).slice(0, 12)}
                </div>
                {art.kind === "diff" ? <Diff text={art.content} /> : <pre className="code">{art.content}</pre>}
              </div>
            ))
          ) : (
            <p className="empty">
              {ids.length > 0
                ? "Loading evidence…"
                : "No artifact for this attempt. Losing candidates are kept, not deleted — but one that never produced a diff has nothing to show."}
            </p>
          )}
        </div>
      </aside>
    </>
  );
}

function Diff({ text }) {
  const lines = String(text || "").split("\n").slice(0, 600);
  return (
    <pre className="code">
      {lines.map((l, i) => {
        let cls = "";
        if (l.startsWith("+") && !l.startsWith("+++")) cls = "add";
        else if (l.startsWith("-") && !l.startsWith("---")) cls = "del";
        else if (l.startsWith("@@")) cls = "hunk";
        return (
          <span key={i} className={cls}>
            {l}
            {"\n"}
          </span>
        );
      })}
    </pre>
  );
}
