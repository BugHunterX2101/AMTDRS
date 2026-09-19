"""SSE from the event table. Every message carries event.seq as its id, so a
client that drops resumes from Last-Event-ID with a query rather than a replay.

This matters more than it sounds: a demo laptop that sleeps for ten seconds
mid-run should reconnect into a live view, not an empty one.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from sse_starlette.event import ServerSentEvent

from principal.config import Settings
from principal.db.events import EventLog


async def job_event_stream(
    events: EventLog, job_id: str, last_event_id: int, settings: Settings
) -> AsyncIterator[ServerSentEvent]:
    for ev in events.replay(job_id, after_seq=last_event_id):
        if settings.principal_slow_mo_ms:
            await asyncio.sleep(settings.principal_slow_mo_ms / 1000)
        yield ServerSentEvent(id=str(ev.seq), event=ev.kind, data=_dumps(ev.payload))

    queue = events.subscribe(job_id)
    try:
        while True:
            ev = await queue.get()
            if settings.principal_slow_mo_ms:
                await asyncio.sleep(settings.principal_slow_mo_ms / 1000)
            yield ServerSentEvent(id=str(ev.seq), event=ev.kind, data=_dumps(ev.payload))
            if _terminal(ev.kind, ev.payload):
                break
    finally:
        events.unsubscribe(job_id, queue)


def _terminal(kind: str, payload: dict) -> bool:
    if kind == "job.stopped":
        return True
    return kind == "job.state" and payload.get("state") in {"Done", "Aborted"}


def _dumps(payload: dict) -> str:
    import json

    return json.dumps(payload, default=str)
