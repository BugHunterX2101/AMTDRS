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
    # Subscribe *before* replaying history, not after. EventLog._after_write
    # publishes only to subscribers registered at that exact instant and never
    # redelivers a missed event to a connection that subscribes late — only a
    # fresh reconnect with a later Last-Event-ID would ever see it, and
    # EventSource has no reason to reconnect a connection that is merely idle
    # rather than dropped. A job that finishes between "replay returned its
    # last historical row" and "subscribe registered the queue" — which a
    # fast-failing job like a red baseline can do in well under a second —
    # would otherwise lose its terminal event forever: the dashboard sits on
    # whatever stage it last saw, "streaming" stays lit, and nothing ever
    # arrives to correct it. Subscribing first means the live queue and the
    # replay can now overlap, so duplicates from that overlap are dropped by
    # sequence number below rather than resent.
    queue = events.subscribe(job_id)
    try:
        max_seq = last_event_id
        for ev in events.replay(job_id, after_seq=last_event_id):
            max_seq = max(max_seq, ev.seq)
            if settings.principal_slow_mo_ms:
                await asyncio.sleep(settings.principal_slow_mo_ms / 1000)
            yield ServerSentEvent(id=str(ev.seq), event=ev.kind, data=_dumps(ev.payload))
            if _terminal(ev.kind, ev.payload):
                # Already over before this client ever connected (a page
                # refresh after the job finished, say) — closing here instead
                # of falling through to an unresolvable `await queue.get()`
                # is what stops the connection hanging open forever.
                return

        while True:
            ev = await queue.get()
            if ev.seq <= max_seq:
                continue  # already delivered by the replay above
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
