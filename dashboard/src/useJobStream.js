import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Folds the job's SSE event stream into render state.
 *
 * The reducer here is deliberately the *only* place event semantics live. The
 * server's event log is append-only and every message carries its `seq` as the
 * SSE id, so a dropped connection resumes with Last-Event-ID rather than
 * replaying from zero — which matters more than it sounds, because a demo
 * laptop that sleeps for ten seconds should reconnect into a live view.
 *
 * EventSource sets Last-Event-ID automatically on reconnect from the last id it
 * saw, so resume needs no code here beyond not tearing the stream down.
 */

const EMPTY = {
  state: "idle",
  stopReason: null,
  baseline: null,
  graph: null,
  radius: null,
  plan: [],
  tasks: {},     // task_id -> { id, file, title, state, attempts: {} }
  attempts: {},  // attempt_id -> { id, taskId, n, model, verdict, gate, reason, ... }
  characterisation: null,
  cleanup: null,
  security: null,
  impact: null,    // blast-radius check of the integrated patch set
  strength: null,  // injected-fault audit of the tests over the changed lines
  integration: null,
  prUrl: null,
  events: [],
};

function reduce(s, ev) {
  const p = ev.payload || {};
  switch (ev.kind) {
    case "job.state":
      return { ...s, state: p.state, stopReason: p.reason ?? s.stopReason };

    case "job.stopped":
      return { ...s, state: "NoPR", stopReason: p.reason };

    case "baseline.ready":
      return { ...s, baseline: p };

    case "graph.built":
      return { ...s, graph: p };

    case "radius.computed":
      return { ...s, radius: p };

    case "plan.ready": {
      const tasks = { ...s.tasks };
      for (const t of p.tasks || []) {
        tasks[t.id] = {
          id: t.id,
          file: t.target_file || t.file,
          title: t.title || t.goal,
          wave: t.wave ?? 0,
          state: "pending",
          attempts: {},
        };
      }
      return { ...s, plan: p.tasks || [], tasks };
    }

    case "attempt.started": {
      const attempts = {
        ...s.attempts,
        [p.attempt_id]: {
          id: p.attempt_id,
          taskId: p.task_id,
          n: p.n,
          model: p.model,
          temperature: p.temperature,
          kind: p.kind || "coder",
          verdict: "running",
        },
      };
      const tasks = { ...s.tasks };
      const t = tasks[p.task_id];
      if (t) {
        tasks[p.task_id] = {
          ...t,
          state: t.state === "pending" ? "running" : t.state,
          attempts: { ...t.attempts, [p.attempt_id]: true },
        };
      }
      return { ...s, attempts, tasks };
    }

    case "attempt.verdict": {
      const prev = s.attempts[p.attempt_id] || { id: p.attempt_id, taskId: p.task_id };
      return {
        ...s,
        attempts: {
          ...s.attempts,
          [p.attempt_id]: {
            ...prev,
            verdict: p.verdict,
            gate: p.gate,
            reason: p.reason,
            artifactId: p.artifact_id,
            diffArtifactId: p.diff_artifact_id,
            operationId: p.operation_id,
            failing: p.failing || [],
          },
        },
      };
    }

    case "task.settled": {
      const t = s.tasks[p.task_id];
      if (!t) return s;
      return {
        ...s,
        tasks: {
          ...s.tasks,
          [p.task_id]: {
            ...t,
            state: p.winning_attempt_id ? "verified" : "discarded",
            winner: p.winning_attempt_id,
            discardReason: p.discard_reason,
          },
        },
      };
    }

    case "characterise.result":
      return { ...s, characterisation: p };

    case "cleanup.planned":
      return { ...s, cleanup: { ...s.cleanup, planned: p.tasks || [] } };

    case "cleanup.tasks_created": {
      // Cleanup tasks are created after `cleanup.planned` fires, so this is
      // the first point their real task ids are known — merge them into the
      // same `tasks` map plan.ready populates, so the task board renders
      // both kinds of work through one code path instead of two.
      const tasks = { ...s.tasks };
      for (const t of p.tasks || []) {
        tasks[t.id] = {
          id: t.id, file: t.target_file, title: "cleanup: remove dead code",
          wave: 0, state: "pending", attempts: {}, kind: "cleanup",
        };
      }
      return { ...s, tasks };
    }

    case "cleanup.result":
      return { ...s, cleanup: { ...s.cleanup, orphansFound: p.orphans_found, removed: p.removed } };

    case "cleanup.skipped":
      return { ...s, cleanup: { ...s.cleanup, skipped: true, reason: p.reason } };

    case "security.result":
      return { ...s, security: p };

    case "impact.result":
      return { ...s, impact: p };

    case "strength.result":
      return { ...s, strength: p };

    case "integration.result":
      return { ...s, integration: p };

    case "pr.opened":
      return { ...s, prUrl: p.url };

    default:
      return s;
  }
}

const TERMINAL = new Set(["Done", "NoPR", "Aborted", "NoSafetyNet"]);

const isTerminalEvent = (ev) =>
  ev.kind === "job.stopped" || (ev.kind === "job.state" && TERMINAL.has(ev.payload?.state));

// How long events are held before they are applied. A job that replays its log
// on connect, or a wave of candidates finishing together, delivers dozens of
// events inside a few milliseconds; applying each one as its own state update
// re-rendered the entire console per event. One flush per ~60ms is below what a
// person can perceive as delay and collapses a burst into a single render.
const FLUSH_MS = 60;

// After a terminal state, keep the stream open briefly so trailing events
// (a PR link, the last log rows) still land, then close it. Left open, the
// browser reconnects every few seconds to a run that will never speak again.
const DRAIN_MS = 1500;

export function useJobStream(jobId) {
  const [job, setJob] = useState(EMPTY);
  const [connected, setConnected] = useState(false);
  const [finished, setFinished] = useState(false);
  const sourceRef = useRef(null);

  const reset = useCallback(() => setJob(EMPTY), []);

  useEffect(() => {
    if (!jobId) return undefined;
    setJob(EMPTY);
    setFinished(false);

    const es = new EventSource(`/jobs/${jobId}/events`);
    sourceRef.current = es;
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    const buffer = [];
    let flushTimer = 0;
    let closeTimer = 0;

    const flush = () => {
      flushTimer = 0;
      const batch = buffer.splice(0);
      if (batch.length === 0) return;
      setJob((s) => {
        let next = s;
        for (const ev of batch) next = reduce(next, ev);
        // Bounded: a long run emits thousands of events and the raw log is a
        // debugging aid, not a data store. Newest first.
        const events = [...batch.slice().reverse(), ...next.events].slice(0, 400);
        return { ...next, events };
      });
      if (!closeTimer && batch.some(isTerminalEvent)) {
        closeTimer = setTimeout(() => {
          es.close();
          setConnected(false);
          setFinished(true);
        }, DRAIN_MS);
      }
    };

    // Named SSE events do not reach onmessage, and EventSource has no catch-all
    // for named events, so a kind the server adds must be added to this list
    // too. `model.call` is left out on purpose: one per model call would flood
    // the log, and the evidence bundle itemises them.
    const handle = (kind) => (e) => {
      let payload = {};
      try {
        payload = JSON.parse(e.data);
      } catch {
        payload = { raw: e.data };
      }
      buffer.push({ seq: Number(e.lastEventId || 0), kind, payload });
      if (!flushTimer) flushTimer = setTimeout(flush, FLUSH_MS);
    };

    const kinds = [
      "job.state", "job.stopped", "baseline.operation", "baseline.ready",
      "graph.built", "radius.computed", "characterise.operation",
      "characterise.result", "characterise.rebaselined", "plan.ready", "attempt.started",
      "attempt.verdict", "repair.classified", "repair.test_expectation", "test_selection.fallback",
      "task.settled", "cleanup.planned", "cleanup.tasks_created",
      "cleanup.operation", "cleanup.result", "cleanup.skipped",
      "integration.operation", "integration.conflict", "integration.result",
      "security.result", "impact.result", "strength.operation", "strength.result", "pr.opened", "pr.publish_failed", "stream.resync",
    ];
    for (const k of kinds) es.addEventListener(k, handle(k));

    return () => {
      clearTimeout(flushTimer);
      clearTimeout(closeTimer);
      es.close();
      sourceRef.current = null;
      setConnected(false);
    };
  }, [jobId]);

  return { job, connected, finished, reset };
}
