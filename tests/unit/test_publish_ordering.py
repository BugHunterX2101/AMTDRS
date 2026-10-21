"""The pull request link must reach the stream before the stream ends.

The SSE stream closes at the terminal `job.state` Done, and a replay of a
finished run stops there too. `pr.opened` was written after Done, so no client,
live or reopening the run later, ever received it and the console never showed
the link.
"""

from __future__ import annotations

from principal.api.stream import job_event_stream
from principal.orchestrator.job import _finish_done


async def _kinds(events, job_id, settings):
    return [ev.event async for ev in job_event_stream(events, job_id, 0, settings)]


async def test_a_replayed_finished_run_delivers_its_pr_link(store, events, settings):
    job = store.create_job(repo_url="r", commit_sha="c", goal="g", target_fqn=None,
                           token_budget=1, tunables={})

    _finish_done(store, events, job.id, "https://example.test/pr/7")

    kinds = await _kinds(events, job.id, settings)
    assert kinds == ["pr.opened", "job.state"]
    assert store.get_job(job.id).pr_url == "https://example.test/pr/7"


async def test_no_pr_link_event_without_a_pr(store, events, settings):
    job = store.create_job(repo_url="r", commit_sha="c", goal="g", target_fqn=None,
                           token_budget=1, tunables={})

    _finish_done(store, events, job.id, None)

    assert await _kinds(events, job.id, settings) == ["job.state"]
