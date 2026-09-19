import { useCallback, useEffect, useState } from "react";
import { useJobStream } from "./useJobStream";
import { Backdrop } from "./Backdrop";
import {
  Economics,
  EvidenceDrawer,
  EventLog,
  ForkTree,
  JobForm,
  Masthead,
  Outcome,
  PanelHeader,
  Pipeline,
  RadiusPanel,
  Stats,
  TaskBoard,
} from "./components";

export default function App() {
  const [jobId, setJobId] = useState(null);
  const [health, setHealth] = useState(null);
  const [status, setStatus] = useState(null);
  const [tree, setTree] = useState([]);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState(null);

  const { job, connected } = useJobStream(jobId);
  const terminal = ["Done", "NoPR", "Aborted"].includes(job.state);

  useEffect(() => {
    fetch("/healthz")
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  // Two things the SSE stream deliberately does not carry: the token budget and
  // the live rate-limit headroom, both of which are cheap to poll and would
  // otherwise flood an append-only log with rows nobody wants to replay.
  useEffect(() => {
    if (!jobId) return undefined;
    let stop = false;
    const tick = async () => {
      try {
        const [s, t] = await Promise.all([
          fetch(`/jobs/${jobId}`).then((r) => r.json()),
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
    tick();
    const id = setInterval(tick, terminal ? 5000 : 1500);
    return () => {
      stop = true;
      clearInterval(id);
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
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
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
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    } catch (e) {
      setError(String(e.message || e));
    }
  }, [jobId]);

  const running = Boolean(jobId) && !terminal;

  // The room takes the colour of the verdict: violet→green while work is being
  // verified, amber for the no-PR outcome (a correct result, not an error, so
  // it must not be red), red only for a genuine abort.
  const mood =
    job.state === "NoPR"
      ? { from: "#d29922", to: "#a371f7" }
      : job.state === "Aborted"
      ? { from: "#f85149", to: "#d29922" }
      : job.state === "Done"
      ? { from: "#3fb950", to: "#76b900" }
      : { from: "#a371f7", to: "#3fb950" };

  return (
    <>
      <Backdrop from={mood.from} to={mood.to} alpha={0.42} />
      <div className="app">
      <Masthead
        health={health}
        connected={connected}
        jobId={jobId}
        running={running}
        onCancel={cancel}
      />

      <div className="columns">
        {/* ------------------------------------------------------- left -- */}
        <div className="stack">
          <section className="panel" data-accent="job">
            <PanelHeader>Job</PanelHeader>
            <JobForm onStart={start} running={running} />
          </section>

          {error && (
            <section className="panel" data-accent="error">
              <PanelHeader>Error</PanelHeader>
              <div className="body">
                <p className="empty" role="alert" style={{ color: "var(--red)" }}>{error}</p>
              </div>
            </section>
          )}

          <section className="panel" data-accent="radius">
            <PanelHeader>Blast radius</PanelHeader>
            <RadiusPanel radius={job.radius} />
          </section>

          <section className="panel" data-accent="tree">
            <PanelHeader>Sandbox fork tree</PanelHeader>
            <ForkTree nodes={tree} />
          </section>
        </div>

        {/* ----------------------------------------------------- centre -- */}
        <div className="stack">
          <section className="panel" data-accent="pipeline">
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
            </div>
          </section>

          <Outcome job={job} />

          <section className="panel" data-accent="tasks">
            <PanelHeader>Tasks and candidates</PanelHeader>
            <TaskBoard job={job} onSelect={setSelected} />
          </section>
        </div>

        {/* ------------------------------------------------------ right -- */}
        <div className="stack">
          <section className="panel" data-accent="economics">
            <PanelHeader>Run economics</PanelHeader>
            <Economics job={job} status={status} />
          </section>

          <section className="panel" data-accent="log">
            <PanelHeader>Event log</PanelHeader>
            <EventLog events={job.events} />
          </section>

          <section className="panel" data-accent="guarantee">
            <PanelHeader>Guarantee</PanelHeader>
            <div className="body">
              <p className="hint">
                No model sits in the accept path. The decision to keep a patch is a
                <code> pytest</code> exit code from a real run in a Token Factory
                Sandbox — never a score from a language model. The architecture
                contract that forbids <code>gates</code> from importing{" "}
                <code>models</code> is checked in CI.
              </p>
            </div>
          </section>
        </div>
      </div>

      <EvidenceDrawer attempt={selected} onClose={() => setSelected(null)} />
      </div>
    </>
  );
}
