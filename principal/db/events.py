"""The event log. Append the row and its event in one transaction, or neither.

Everything the dashboard renders, every trace file published with the submission
and every claim in the PR body reads from here. Live subscribers are fed from the
same write, so a reconnecting client resumes from its last seq rather than
replaying the job.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from principal.db.store import Store


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
        for q in list(self._subscribers.get(job_id, ())):
            self._offer(q, ev)

    def _offer(self, q: asyncio.Queue[Event], ev: Event) -> None:
        loop = self._loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None:
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(ev)
        elif loop is not None and loop.is_running():
            loop.call_soon_threadsafe(lambda: q.put_nowait(ev))

    def _append_trace(self, job_id: str, seq: int, kind: str, payload: dict[str, Any]) -> None:
        """One JSONL file per job. Publishing traces is most of the credibility
        and it costs one function."""
        d = self.runs_dir / job_id
        d.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"seq": seq, "kind": kind, "payload": payload}, default=str)
        with (d / "trace.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    # ----------------------------------------------------------- reading ----

    def replay(self, job_id: str, after_seq: int = 0) -> Iterator[Event]:
        for r in self.store.events_since(job_id, after_seq, limit=100_000):
            yield Event(
                seq=r["seq"], job_id=r["job_id"], kind=r["kind"], payload=json.loads(r["payload"])
            )

    def subscribe(self, job_id: str) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=4096)
        self._subscribers.setdefault(job_id, set()).add(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue[Event]) -> None:
        subs = self._subscribers.get(job_id)
        if subs:
            subs.discard(q)
            if not subs:
                self._subscribers.pop(job_id, None)
