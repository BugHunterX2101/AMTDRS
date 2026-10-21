import { useCallback, useEffect, useState } from "react";
import { useJobStream } from "./useJobStream";
import {
  ChecksPanel,
  Economics,
  EvidenceDrawer,
  EvidenceLinks,
  EventLog,
  ForkTree,
  JobForm,
  Masthead,
  Outcome,
  PanelHeader,
  Pipeline,
  RadiusPanel,
  Stats,
  Steps,
  TaskBoard,
} from "./components";

// The run id lives in the URL, not only in component state. Without this a
// reload — or a laptop that slept long enough for the tab to be discarded —
// drops you back on an empty form with a job still executing on the server and
// no way to find it again, and a run cannot be linked to at all. The event log
// is append-only and every endpoint is keyed by job id, so opening ?job=<id>
// reconstructs a past run exactly as it happened, live or long finished.
const jobIdFromUrl = () => new URLSearchParams(window.location.search).get("job");

const TERMINAL = ["Done", "NoPR", "Aborted", "NoSafetyNet"];

// The room takes the colour of the verdict. Violet to green while work is being
// verified, then the outcome's own colour: green verified, amber for a run that
// could not be called safe (or a correct refusal), red for a definite defect,
// violet for a platform error that says nothing about the change.
function moodOf(state, outcome) {
  if (!TERMINAL.includes(state)) return state === "idle" ? "idle" : "run";
  switch (outcome) {
    case "VERIFIED":
      return "verified";
    case "FAILED":
      return "failed";
    case "ERROR":
      return "error";
    case "UNVERIFIED":
      return "unverified";
    default:
      break;
  }
  if (state === "Done") return "verified";
  if (state === "Aborted") return "failed";
  return "unverified";
}

// A start-job failure arrives as a JSON error envelope. Show its message, not
// the status line and braces.
async function describeFailure(res) {
  const text = await res.text();
  try {
    const body = JSON.parse(text);
    const msg = body.message || (typeof body.detail === "string" ? body.detail : body.detail?.message);
    if (msg) return msg;
  } catch {
    /* not JSON: fall through */
  }
  return `${res.status} ${text}`.slice(0, 300);
}

export default function App() {
  const [jobId, setJobId] = useState(jobIdFromUrl);
  const [health, setHealth] = useState(null);
  const [status, setStatus] = useState(null);
  const [tree, setTree] = useState([]);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState(null);

  const { job, connected, finished } = useJobStream(jobId);
  const terminal = TERMINAL.includes(job.state);
  const hasRun = Boolean(jobId);

  // Keep the address bar in step, and follow Back/Forward. replaceState rather
  // than pushState on start: a new run replaces the view it was launched from,
  // so Back should leave the console rather than step through every job.
  useEffect(() => {
    const url = new URL(window.location.href);
    if (jobId) url.searchParams.set("job", jobId);
    else url.searchParams.delete("job");
    if (url.href !== window.location.href) window.history.replaceState({}, "", url);
  }, [jobId]);

  useEffect(() => {
    const onPop = () => setJobId(jobIdFromUrl());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => {
    fetch("/healthz")
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  // Two things the SSE stream deliberately does not carry: the token budget and
  // the live rate-limit headroom, both of which are cheap to poll and would
  // otherwise flood an append-only log with rows nobody wants to replay.
  //
  // Polling pauses while the tab is hidden: nobody is looking, the stream keeps
  // the record, and a backgrounded console should not keep a server busy. It
  // refreshes the moment the tab is shown again.
  useEffect(() => {
    if (!jobId) return undefined;
    let stop = false;
    let id;
    const tick = async () => {
      if (document.hidden) return;
      try {
        const jobRes = await fetch(`/jobs/${jobId}`);
        // A job id that does not exist is the one case worth surfacing rather
        // than swallowing: it is how a stale or mistyped ?job= link in the
        // address bar would otherwise look identical to "nothing started yet",
        // which is actively misleading to whoever followed that link. It is
        // also permanent, so stop polling a job id that will never resolve.
        if (jobRes.status === 404) {
          if (!stop) setError(`No job found with id ${jobId}.`);
          clearInterval(id);
          return;
        }
        const [s, t] = await Promise.all([
          jobRes.json(),
          fetch(`/jobs/${jobId}/tree`).then((r) => r.json()),
        ]);
        if (!stop) {
          setStatus(s);
          setTree(t.nodes || []);
        }
      } catch {
        /* the stream is the source of truth; polling is supplementary */
      }
    };
    const onVisible = () => {
      if (!document.hidden) tick();
    };
    tick();
    id = setInterval(tick, terminal ? 5000 : 1500);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stop = true;
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [jobId, terminal]);

  const start = useCallback(async (form) => {
    setError(null);
    setSelected(null);
    setStatus(null);
    setTree([]);
    try {
      const res = await fetch("/jobs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(form),
      });
      if (!res.ok) throw new Error(await describeFailure(res));
      const data = await res.json();
      setJobId(data.job_id);
    } catch (e) {
      setError(String(e.message || e));
    }
  }, []);

  // POST /jobs/{id}/cancel has existed on the API since the first version and
  // had no way to reach it from the UI. A long-running job with no stop button
  // is the one interaction a live demo genuinely needs and cannot fake.
  const cancel = useCallback(async () => {
    if (!jobId) return;
    try {
      const res = await fetch(`/jobs/${jobId}/cancel`, { method: "POST" });
      if (!res.ok) throw new Error(await describeFailure(res));
    } catch (e) {
      setError(String(e.message || e));
    }
  }, [jobId]);

  // If the status request never answers, fall back to the stream-derived card
  // after a few seconds instead of showing a placeholder forever.
  const [gaveUp, setGaveUp] = useState(false);
  useEffect(() => {
    setGaveUp(false);
    if (!terminal || status) return undefined;
    const t = setTimeout(() => setGaveUp(true), 4000);
    return () => clearTimeout(t);
  }, [terminal, status, jobId]);

  const running = hasRun && !terminal;
  const verdict = terminal ? status?.outcome : null;
  const mood = moodOf(job.state, verdict?.outcome);
  const checksRelevant =
    Boolean(job.impact || job.strength) || ["Integrating", "Publishing"].includes(job.state);

  // `data-o` is the order a phone reads the panels in. On a wide screen the three
  // columns are independent and it has no effect; on one column the verdict and
  // the pipeline come first, not after a form and two empty lists.
  const outcome = <Outcome job={job} verdict={verdict} pending={terminal && !status && !gaveUp} />;

  return (
    <>
      <div className="ambient" data-mood={mood} aria-hidden="true">
        <i className="a-run" />
        <i className="a-verified" />
        <i className="a-unverified" />
        <i className="a-failed" />
        <i className="a-error" />
      </div>
      <div className="app">
        <Masthead
          health={health}
          provenance={status?.provenance}
          connected={connected}
          finished={finished}
          jobId={jobId}
          running={running}
          onCancel={cancel}
        />

        <Steps jobId={jobId} state={job.state} running={running} />

        <main className="columns">
          {/* ----------------------------------------------------- left -- */}
          <div className="stack">
            <section className="panel" data-accent="job" data-o={hasRun ? 8 : 0}>
              <PanelHeader>Job</PanelHeader>
              <JobForm onStart={start} running={running} />
            </section>

            {error && (
              <section className="panel" data-accent="error" data-o="0">
                <PanelHeader>Error</PanelHeader>
                <div className="body">
                  <p className="empty" role="alert" style={{ color: "var(--red)" }}>{error}</p>
                </div>
              </section>
            )}

            {hasRun && (
              <section className="panel" data-accent="radius" data-o="6">
                <PanelHeader>Blast radius</PanelHeader>
                <RadiusPanel radius={job.radius} />
              </section>
            )}

            {hasRun && (
              <section className="panel" data-accent="tree" data-o="7">
                <PanelHeader>Sandbox fork tree</PanelHeader>
                <ForkTree nodes={tree} />
              </section>
            )}
          </div>

          {/* --------------------------------------------------- centre -- */}
          <div className="stack">
            {terminal && <div data-o="1">{outcome}</div>}

            <section className="panel" data-accent="pipeline" data-o="2">
              <PanelHeader>Pipeline</PanelHeader>
              <div className="body" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                <Pipeline state={job.state} />
                <Stats job={job} status={status} />
                {job.baseline && (
                  <div className="hint">
                    Baseline verified green: {job.baseline.passed ?? "?"} tests passing in{" "}
                    {Math.round((job.baseline.duration_ms ?? 0) / 1000)}s. Every candidate
                    forks from this one image.
                  </div>
                )}
                {job.characterisation && job.characterisation.status !== "skipped" && (
                  <div className="hint">
                    Safety net ({job.characterisation.status}):{" "}
                    {job.characterisation.mutation?.score != null
                      ? `mutation score ${job.characterisation.mutation.score.toFixed(2)}`
                      : "no scoreable mutants"}
                    {job.characterisation.mutants_generated > 0 &&
                      ` across ${job.characterisation.mutants_generated} generated mutants`}
                    . The target had no covering test, so a characterisation suite was
                    generated and measured before any refactor attempt.
                  </div>
                )}
                {job.cleanup?.planned?.length > 0 && (
                  <div className="hint">
                    Cleanup: {job.cleanup.planned.length} file(s) with dead code introduced
                    by this refactor
                    {job.cleanup.skipped
                      ? ` — skipped (${job.cleanup.reason || "refactor diffs did not verify together"})`
                      : job.cleanup.orphansFound != null
                      ? ` — ${job.cleanup.orphansFound} orphan(s) confirmed and removed`
                      : ""}
                    .
                  </div>
                )}
              </div>
            </section>

            {!terminal && <div data-o="1">{outcome}</div>}

            {checksRelevant && (
              <section className="panel" data-accent="impact" data-o="3">
                <PanelHeader>Verification checks</PanelHeader>
                <ChecksPanel impact={job.impact} strength={job.strength} running={running} />
              </section>
            )}

            {hasRun && (
              <section className="panel" data-accent="tasks" data-o="4">
                <PanelHeader>Tasks and candidates</PanelHeader>
                <TaskBoard job={job} onSelect={setSelected} />
              </section>
            )}
          </div>

          {/* ---------------------------------------------------- right -- */}
          <div className="stack">
            {hasRun && (
              <section className="panel" data-accent="evidence" data-o="5">
                <PanelHeader>Evidence</PanelHeader>
                <EvidenceLinks jobId={jobId} ready={terminal} />
              </section>
            )}

            {hasRun && (
              <section className="panel" data-accent="economics" data-o="9">
                <PanelHeader>Run economics</PanelHeader>
                <Economics job={job} status={status} />
              </section>
            )}

            {hasRun && (
              <section className="panel" data-accent="log" data-o="10">
                <PanelHeader>Event log</PanelHeader>
                <EventLog events={job.events} />
              </section>
            )}

            <section className="panel" data-accent="guarantee" data-o="11">
              <PanelHeader>Guarantee</PanelHeader>
              <div className="body">
                <p className="hint">
                  No model sits in the accept path. The decision to keep a patch is a
                  <code> pytest</code> exit code from a real run in a Token Factory
                  Sandbox — never a score from a language model. The architecture
                  contract that forbids <code>gates</code> from importing{" "}
                  <code>models</code> is checked in CI. The badge at the top says
                  whether this run used the real Nebius sandbox and inference; a run on
                  the local stand-in is labelled <em>not live</em> and is not a
                  measurement of the platform.
                </p>
              </div>
            </section>
          </div>
        </main>

        <EvidenceDrawer attempt={selected} onClose={() => setSelected(null)} />
      </div>
    </>
  );
}
