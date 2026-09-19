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

    case "integration.result":
      return { ...s, integration: p };

    case "pr.opened":
      return { ...s, prUrl: p.url };

    default:
      return s;
  }
}

export function useJobStream(jobId) {
  const [job, setJob] = useState(EMPTY);
  const [connected, setConnected] = useState(false);
  const sourceRef = useRef(null);

  const reset = useCallback(() => setJob(EMPTY), []);

  useEffect(() => {
    if (!jobId) return undefined;
    setJob(EMPTY);

    const es = new EventSource(`/jobs/${jobId}/events`);
    sourceRef.current = es;
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);

    // Named SSE events do not reach onmessage, and the kinds are open-ended, so
    // every listener goes through one handler registered per kind we know about
    // plus a catch-all for anything the server adds later.
    const handle = (kind) => (e) => {
      let payload = {};
      try {
        payload = JSON.parse(e.data);
      } catch {
        payload = { raw: e.data };
      }
      const ev = { seq: Number(e.lastEventId || 0), kind, payload };
      setJob((s) => {
        const next = reduce(s, ev);
        // Bounded: a long run emits thousands of events and the raw log is a
        // debugging aid, not a data store.
        const events = [ev, ...next.events].slice(0, 400);
        return { ...next, events };
      });
    };

    const kinds = [
      "job.state", "job.stopped", "baseline.operation", "baseline.ready",
      "graph.built", "radius.computed", "plan.ready", "attempt.started",
      "attempt.verdict", "repair.classified", "repair.test_expectation",
      "task.settled", "integration.operation", "integration.conflict",
      "integration.result", "pr.opened", "pr.publish_failed",
    ];
    for (const k of kinds) es.addEventListener(k, handle(k));

    return () => {
      es.close();
      sourceRef.current = null;
      setConnected(false);
    };
  }, [jobId]);

  return { job, connected, reset };
}
