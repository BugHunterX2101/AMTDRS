import { useEffect, useState } from "react";

/* ------------------------------------------------------------------ header -- */

export function Masthead({ health, connected, jobId }) {
  return (
    <header className="masthead">
      <div>
        <h1>Principal</h1>
        <div className="tagline">
          Verification-gated parallel refactoring. No green test, no PR.
        </div>
      </div>
      <div className="spacer" />
      <span className="badge nvidia">NVIDIA Nemotron 3</span>
      <span className={`badge ${health?.sandboxes_configured ? "live" : "off"}`}>
        {health?.sandboxes_configured ? "Token Factory Sandboxes" : `local · ${health?.sandbox ?? "…"}`}
      </span>
      {jobId && (
        <span className={`badge ${connected ? "live" : "off"}`}>
          {connected ? "streaming" : "disconnected"}
        </span>
      )}
    </header>
  );
}

/* -------------------------------------------------------------------- form -- */

const DEFAULTS = {
  repo_url: "tests/fixtures/mini_repo",
  commit_sha: "deadbeef",
  target_fqn: "src.auth.session.create",
  goal: "Make the ttl parameter keyword-only and update every call site.",
};

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
      <button className="primary" type="submit" disabled={running}>
        {running ? "Running…" : "Start job"}
      </button>
    </form>
  );
}

/* ---------------------------------------------------------------- pipeline -- */

const STAGES = ["Ingesting", "Baselining", "Mapping", "Planning", "Executing", "Integrating", "Publishing"];

export function Pipeline({ state }) {
  const idx = STAGES.indexOf(state);
  const terminal = ["Done", "NoPR", "Aborted"].includes(state);
  return (
    <div className="pipeline">
      {STAGES.map((s, i) => {
        let cls = "stage";
        if (terminal || (idx >= 0 && i < idx)) cls += state === "Aborted" ? "" : " done";
        if (s === state) cls += " active";
        return (
          <span key={s} className={cls}>
            {s}
          </span>
        );
      })}
      {terminal && <span className={`stage ${state === "Done" ? "done" : "failed"}`}>{state}</span>}
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
    return (
      <div className="body">
        <p className="empty">
          Nemotron 3 Ultra decomposes the goal into single-file tasks. Each task then
          becomes a race between independent candidates — first to pass its tests wins,
          and the losers are cancelled.
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
              return (
                <div
                  key={aid}
                  className={[
                    "candidate",
                    verdictClass(a.verdict),
                    lost ? "cancelled" : "",
                    a.kind === "repairer" ? "repair" : "",
                  ].join(" ")}
                  onClick={() => onSelect(a)}
                >
                  <div className="n">
                    {a.kind === "repairer" ? "repair" : `candidate ${a.n}`}
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

function verdictClass(v) {
  if (v === "pass" || v === "green") return "pass";
  if (v === "running") return "running";
  if (v === "error") return "error";
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
                <span className={n.verdict === "pass" ? "pass" : "fail"}>
                  {n.verdict === "pass" ? "✓" : "✗"}
                </span>{" "}
                {n.kind === "repairer" ? "repair" : `cand ${n.n}`}{" "}
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

export function Outcome({ job }) {
  if (job.prUrl) {
    return (
      <div className="outcome pr">
        <h3>Draft pull request opened</h3>
        <p>
          Every gate passed, including the full suite with all patches applied together.
          <br />
          <a href={job.prUrl} target="_blank" rel="noreferrer">
            {job.prUrl}
          </a>
        </p>
      </div>
    );
  }
  if (job.state === "NoPR") {
    return (
      <div className="outcome nopr">
        <h3>No pull request — and that is the correct outcome</h3>
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
  if (job.state === "Aborted") {
    return (
      <div className="outcome aborted">
        <h3>Aborted</h3>
        <p>{job.stopReason || "The job stopped before it could do any work."}</p>
      </div>
    );
  }
  return null;
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
  const [artifact, setArtifact] = useState(null);

  useEffect(() => {
    setArtifact(null);
    if (!attempt?.artifactId) return;
    let cancelled = false;
    fetch(`/artifacts/${attempt.artifactId}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => !cancelled && setArtifact(d))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!attempt) return null;

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} />
      <aside className="drawer">
        <header>
          <h2>
            {attempt.kind === "repairer" ? "repair" : `candidate ${attempt.n}`} · {attempt.verdict}
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

          {artifact ? (
            <>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 6 }}>
                {artifact.kind}
              </div>
              <Diff text={artifact.content} />
            </>
          ) : (
            <p className="empty">
              {attempt.artifactId
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
