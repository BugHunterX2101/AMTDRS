"""Integration must never go green over an empty diff set.

A run of the baseline tests with nothing applied passes by construction, so if
every verified task lost its diff (an unreadable artifact, or the lone task
dropped on conflict) the pipeline would otherwise carry on to the behaviour gate
and publishing with nothing to publish, and report an unrelated reason.
"""

from __future__ import annotations

import pytest

from principal.orchestrator import job as job_module
from principal.orchestrator.job import _integrate_and_publish, _Stopped
from principal.sandbox.runner import TestRun as Run


class _Sandbox:
    async def from_uuid(self, uuid):
        return uuid


def _verified_job(store, *, tasks: int = 1):
    job = store.create_job(
        repo_url="r", commit_sha="c", goal="g", target_fqn=None, token_budget=1, tunables={},
    )
    store.update_job(job.id, baseline_image="img")
    for seq in range(tasks):
        task = store.create_task(
            job_id=job.id, seq=seq, target_file=f"f{seq}.py", instruction="i", acceptance="a",
            depends_on=[],
        )
        store.update_task(task.id, state="verified")
    return job


async def _run(job, store, events, settings):
    with pytest.raises(_Stopped):
        await _integrate_and_publish(
            job.id, store, events, _Sandbox(), None, "g", None, settings,
        )
    return store.get_job(job.id)


async def test_verified_tasks_without_diff_artifacts_stop_honestly(
    monkeypatch, store, events, settings
):
    calls = []

    async def fake_integration(*args, **kwargs):
        calls.append(args)
        return Run(exit_code=0)

    monkeypatch.setattr(job_module, "run_integration", fake_integration)
    job = _verified_job(store)

    done = await _run(job, store, events, settings)

    assert done.state == "NoPR"
    assert done.stop_reason == "no verified task has a readable diff"
    assert calls == []


async def test_dropping_the_only_conflicting_task_stops_honestly(
    monkeypatch, store, events, settings
):
    calls = []

    async def fake_integration(sandbox, c0, diffs, **kwargs):
        calls.append(list(diffs))
        return Run(exit_code=91, conflict_index=0) if diffs else Run(exit_code=0)

    monkeypatch.setattr(job_module, "run_integration", fake_integration)
    monkeypatch.setattr(
        job_module, "_load_winning_diffs",
        lambda store, settings, job_id, verified: {t.id: "diff" for t in verified},
    )
    job = _verified_job(store)

    done = await _run(job, store, events, settings)

    assert done.state == "NoPR"
    assert done.stop_reason == "the only verified diff conflicted at integration"
    assert calls == [["diff"]]
