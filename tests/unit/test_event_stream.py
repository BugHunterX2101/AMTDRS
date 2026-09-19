"""Regression coverage for the subscribe/replay race in job_event_stream.

Found while wiring the dashboard's live SSE badges to a real run: a job that
reaches a terminal state fast (a red baseline aborts in well under a second)
could finish *between* the stream replaying its history and subscribing to
new events. EventLog only publishes to subscribers registered at the moment
of the write, so that terminal event was delivered to nobody, the live
EventSource connection just sat there indefinitely, and the dashboard froze
on whatever stage it had last rendered with no error and no way to recover
short of a manual reload. Nothing previously exercised this path at all.
"""

from __future__ import annotations

import asyncio

import pytest

from principal.api.stream import job_event_stream
from principal.config import Settings
from principal.db.events import EventLog
from principal.db.store import Store


def _make_job(store: Store) -> str:
    job = store.create_job(
        repo_url="tests/fixtures/mini_repo", commit_sha="deadbeef", goal="goal",
        target_fqn="src.auth.session.create", token_budget=1000, tunables={},
    )
    return job.id


async def test_terminal_event_written_after_history_is_exhausted_still_arrives(
    store: Store, events: EventLog, settings: Settings
) -> None:
    """The exact race: replay() has already returned its only row and the
    generator has not yet reached `events.subscribe(...)` when the terminal
    event is written. Deterministic (no timing/sleeps needed) because
    `events_since` is an eager `fetchall`, so replay's result set is a fixed
    snapshot the instant the query runs, before the consumer ever asks for
    the first item — the write below cannot land inside it.
    """
    job_id = _make_job(store)
    events.emit(job_id, "job.state", {"state": "Ingesting"})

    stream = job_event_stream(events, job_id, 0, settings)

    first = await asyncio.wait_for(stream.__anext__(), timeout=2)
    assert first.event == "job.state"

    # The generator is suspended at that yield, inside the `for ev in
    # events.replay(...)` loop, one call to __anext__() away from exhausting
    # it and moving on to `events.subscribe(job_id)`. Emitting here lands
    # exactly in the gap the fix closes.
    events.emit(job_id, "job.state", {"state": "Aborted", "reason": "baseline red"})

    second = await asyncio.wait_for(stream.__anext__(), timeout=2)
    assert second.event == "job.state"
    assert '"Aborted"' in second.data

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(stream.__anext__(), timeout=2)


async def test_reconnect_to_an_already_finished_job_does_not_hang(
    store: Store, events: EventLog, settings: Settings
) -> None:
    """A client connecting *after* the job is already done (a page refresh,
    or the first connection racing a very fast job) must get the terminal
    event from replay and have the stream close — not fall through to an
    `await queue.get()` that nothing will ever satisfy."""
    job_id = _make_job(store)
    events.emit(job_id, "job.state", {"state": "Ingesting"})
    events.emit(job_id, "job.state", {"state": "Aborted", "reason": "baseline red"})

    stream = job_event_stream(events, job_id, 0, settings)

    # The timeout is the assertion: before the fix this generator never
    # finished, because replay handed over to an `await queue.get()` that
    # nothing would ever satisfy for an already-finished job.
    seen = []
    async with asyncio.timeout(2):
        async for ev in stream:
            seen.append(ev)

    assert [ev.event for ev in seen] == ["job.state", "job.state"]
    assert '"Aborted"' in seen[-1].data
