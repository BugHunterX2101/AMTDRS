"""The event log. Append the row and its event in one transaction, or neither.

Everything the dashboard renders, every trace file published with the submission
and every claim in the PR body reads from here. Live subscribers are fed from the
same write, so a reconnecting client resumes from its last seq rather than
replaying the job.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from principal.db.store import Store

# Open trace handles held at once. Only a handful of jobs are ever active
# together; the cap exists so the dict cannot grow without bound.
_MAX_OPEN_TRACES = 8

# Rows per replay page. Large enough that an ordinary job replays in one
# query, small enough that a long one does not materialise in a single list.
_REPLAY_PAGE = 2000


@dataclass(slots=True)
class Event:
    seq: int
    job_id: str
    kind: str
    payload: dict[str, Any]


class EventLog:
    def __init__(self, store: Store, runs_dir: Path, slow_mo_ms: int = 0):
        self.store = store
        self.runs_dir = runs_dir
        self.slow_mo_ms = slow_mo_ms
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        # `_after_write` can run on a worker thread (see `_offer`), while
        # subscribe/unsubscribe run on the event loop. Without this, iterating
        # the subscriber set can race a mutation of it.
        self._guard = threading.Lock()
        self._lagged: set[asyncio.Queue[Event]] = set()
        self._trace_files: dict[str, TextIO] = {}

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop or asyncio.get_running_loop()

    # ----------------------------------------------------------- writing ----

    def emit(self, job_id: str, kind: str, payload: dict[str, Any] | None = None, **extra: Any) -> int:
        """Write one event. Callers that also mutate a row use `transaction` below."""
        body = {**(payload or {}), **extra}
        with self.store.tx():
            seq = self.store.append_event(job_id, kind, body)
        self._after_write(job_id, seq, kind, body)
        return seq

    def transaction(self, job_id: str, kind: str, payload: dict[str, Any] | None = None):
        """Context manager: row writes inside, event committed with them.

        This is the mechanism behind the rule that `job`, `task` and `attempt`
        never move without a matching event. If those ever disagree, the trace
        stops being evidence.
        """
        body = dict(payload or {})
        log = self

        class _Tx:
            def __enter__(self) -> dict[str, Any]:
                self._cm = log.store.tx()
                self._cm.__enter__()
                return body

            def __exit__(self, exc_type, exc, tb) -> bool:
                if exc_type is None:
                    self.seq = log.store.append_event(job_id, kind, body)
                self._cm.__exit__(exc_type, exc, tb)
                if exc_type is None:
                    log._after_write(job_id, self.seq, kind, body)
                return False

        return _Tx()

    def _after_write(self, job_id: str, seq: int, kind: str, payload: dict[str, Any]) -> None:
        self._append_trace(job_id, seq, kind, payload)
        ev = Event(seq=seq, job_id=job_id, kind=kind, payload=payload)
        with self._guard:
            targets = list(self._subscribers.get(job_id, ()))
        for q in targets:
            self._offer(q, ev)

    def _offer(self, q: asyncio.Queue[Event], ev: Event) -> None:
        loop = self._loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None:
            self._put(q, ev)
        elif loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._put, q, ev)

    def _put(self, q: asyncio.Queue[Event], ev: Event) -> None:
        """Deliver, or record that this subscriber has fallen behind.

        Dropping an event silently is not acceptable here: the trace is the
        product's evidence, and a client cannot detect a hole in a stream it
        never received. Marking the subscriber lagged instead lets the stream
        close the connection, which sends the client back through replay from
        its last seq — and replay reads the table, which never lost anything.
        """
        try:
            q.put_nowait(ev)
        except asyncio.QueueFull:
            with self._guard:
                self._lagged.add(q)

    def has_lagged(self, q: asyncio.Queue[Event]) -> bool:
        with self._guard:
            return q in self._lagged

    def _append_trace(self, job_id: str, seq: int, kind: str, payload: dict[str, Any]) -> None:
        """One JSONL file per job. Publishing traces is most of the credibility
        and it costs one function.

        The handle is kept open and flushed per line rather than reopened per
        event: this runs on every state change in the system, and mkdir + open
        + close per event is three syscalls to write one line. Flushing keeps
        the file complete for anyone reading it concurrently.
        """
        line = json.dumps({"seq": seq, "kind": kind, "payload": payload}, default=str)
        with self._guard:
            fh = self._trace_files.get(job_id)
            if fh is None:
                d = self.runs_dir / job_id
                d.mkdir(parents=True, exist_ok=True)
                fh = (d / "trace.jsonl").open("a", encoding="utf-8")
                # Bounded, so a long-lived server running many jobs does not
                # accumulate one descriptor per job it has ever seen. Reopening
                # is append-mode, so an evicted job that writes again is fine.
                while len(self._trace_files) >= _MAX_OPEN_TRACES:
                    oldest = next(iter(self._trace_files))
                    evicted = self._trace_files.pop(oldest)
                    try:
                        evicted.close()
                    except OSError:
                        pass
                self._trace_files[job_id] = fh
            fh.write(line + "\n")
            fh.flush()

    def close(self) -> None:
        with self._guard:
            for fh in self._trace_files.values():
                try:
                    fh.close()
                except OSError:
                    pass
            self._trace_files.clear()

    # ----------------------------------------------------------- reading ----

    def replay(self, job_id: str, after_seq: int = 0) -> Iterator[Event]:
        """Every event after `after_seq`, in pages.

        Paged rather than one big LIMIT because replay is the resume guarantee:
        a single capped query silently stops at the cap, and a client that
        reconnects into a truncated replay has a hole it cannot see. Paging
        ends only when the table is genuinely exhausted.
        """
        cursor = after_seq
        while True:
            rows = self.store.events_since(job_id, cursor, limit=_REPLAY_PAGE)
            if not rows:
                return
            for r in rows:
                cursor = max(cursor, int(r["seq"]))
                yield Event(
                    seq=r["seq"], job_id=r["job_id"], kind=r["kind"],
                    payload=json.loads(r["payload"]),
                )
            if len(rows) < _REPLAY_PAGE:
                return

    def subscribe(self, job_id: str) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=4096)
        with self._guard:
            self._subscribers.setdefault(job_id, set()).add(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue[Event]) -> None:
        with self._guard:
            self._lagged.discard(q)
            subs = self._subscribers.get(job_id)
            if subs:
                subs.discard(q)
                if not subs:
                    self._subscribers.pop(job_id, None)
