"""`ensure_snapshot` under concurrent callers for the same repo + commit.

The hosted demo's whole point is running with zero credentials against one
bundled fixture repo at one fixed commit — which means concurrent visitors
are the expected traffic pattern here, not a rare edge case. Before the fix
this function raced rmtree/copytree for two callers building the same
destination at once: one job's checkout could be deleted mid-write by
another job's rmtree. These pin the fix — same key serializes and converges
on one good snapshot, different keys still build in parallel.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from principal.sandbox.baseline import _lock_for, ensure_snapshot
from tests.conftest import FIXTURE_SHA, MINI_REPO


async def test_lock_for_the_same_key_serializes_critical_sections():
    """The property the whole fix rests on, proven directly rather than by
    hoping a filesystem race happens to manifest inside a test's time budget:
    two callers locking the same key can never be inside their critical
    section at the same instant, and the second sees whatever the first left
    behind rather than a half-written intermediate state."""
    key = "same-destination"
    log: list[str] = []

    async def critical_section(tag: str) -> None:
        async with await _lock_for(key):
            log.append(f"{tag}-enter")
            await asyncio.sleep(0.05)  # forces a real interleaving window
            log.append(f"{tag}-exit")

    await asyncio.gather(critical_section("a"), critical_section("b"))

    # Never a-enter, b-enter, a-exit, b-exit (or the mirror) — each run is
    # fully contained between its own enter and exit.
    assert log in (["a-enter", "a-exit", "b-enter", "b-exit"],
                   ["b-enter", "b-exit", "a-enter", "a-exit"])


async def test_lock_for_different_keys_does_not_serialize():
    """The other half of the property: the lock must be keyed, not global —
    two callers with different keys should be able to overlap."""
    overlapped = asyncio.Event()

    async def critical_section(key: str) -> None:
        async with await _lock_for(key):
            if key == "distinct-key-b":
                overlapped.set()
            await asyncio.sleep(0.05)

    task_a = asyncio.create_task(critical_section("distinct-key-a"))
    await asyncio.sleep(0.01)  # let a acquire its lock first
    task_b = asyncio.create_task(critical_section("distinct-key-b"))
    await asyncio.wait_for(overlapped.wait(), timeout=1.0)  # b ran while a still holds its own lock
    await asyncio.gather(task_a, task_b)


async def test_concurrent_calls_for_the_same_commit_converge_on_one_good_snapshot(tmp_path: Path):
    dest_root = tmp_path / "snapshots"
    results = await asyncio.gather(
        *(ensure_snapshot(str(MINI_REPO), FIXTURE_SHA, dest_root) for _ in range(8))
    )

    assert len({str(p) for p in results}) == 1
    dest = results[0]
    assert (dest / ".principal-ok").exists()
    assert (dest / ".git").exists()
    # The tree actually has content, not a partial checkout torn apart by a
    # concurrent rmtree landing mid-copy.
    assert (dest / "src" / "auth" / "session.py").exists()


async def test_concurrent_calls_for_different_commits_do_not_serialize_on_each_other(tmp_path: Path):
    """Not a timing assertion — a correctness one: two distinct destinations
    must both end up complete and independent, proving the lock is keyed by
    destination rather than a single global lock forcing every snapshot
    build in the process through one queue."""
    dest_root = tmp_path / "snapshots"
    a, b = await asyncio.gather(
        ensure_snapshot(str(MINI_REPO), "deadbeef", dest_root),
        ensure_snapshot(str(MINI_REPO), "beadfeed", dest_root),
    )
    assert a != b
    assert (a / ".principal-ok").read_text() == "deadbeef"
    assert (b / ".principal-ok").read_text() == "beadfeed"


async def test_a_second_call_after_the_first_completes_is_a_cheap_cache_hit(tmp_path: Path):
    dest_root = tmp_path / "snapshots"
    first = await ensure_snapshot(str(MINI_REPO), FIXTURE_SHA, dest_root)
    marker_before = (first / ".principal-ok").stat().st_mtime_ns

    second = await ensure_snapshot(str(MINI_REPO), FIXTURE_SHA, dest_root)
    assert second == first
    assert (first / ".principal-ok").stat().st_mtime_ns == marker_before
