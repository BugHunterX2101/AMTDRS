"""The cleanup pass reports what it removed, not only that it found nothing.

`cleanup.result` was emitted only when no dead code was found. A run whose
cleanup tasks did remove something left the console's "N removed" line with no
number to show. The platform calls are stubbed here; this is about the record.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from principal.orchestrator import job as job_module
from principal.sandbox.runner import TestRun as Run


class _Sandbox:
    async def from_uuid(self, uuid):
        return uuid


async def test_removed_dead_code_is_counted_in_the_result(monkeypatch, store, events, settings, tmp_path):
    job = store.create_job(repo_url="r", commit_sha="c", goal="g", target_fqn=None,
                           token_budget=1, tunables={})
    store.update_job(job.id, baseline_image="img")
    t = store.create_task(job_id=job.id, seq=1, target_file="a.py", instruction="i", acceptance="a",
                          depends_on=[])
    store.update_task(t.id, state="verified")

    snap = tmp_path / "snapshot"  # not tmp_path itself: the runs dir lives there
    snap.mkdir()

    async def snapshot(*args):
        return snap

    async def integration(*args, **kwargs):
        return Run(exit_code=0)

    class Runner:
        def __init__(self, **kwargs):
            pass

        async def run_waves(self, waves):
            for task in waves[0]:
                store.update_task(task.id, state="verified")

    planned = SimpleNamespace(target_file="a.py", unused_imports=["os"], orphaned_symbols=["helper"],
                              instruction="remove", acceptance="tests pass", names=["os", "helper"])
    monkeypatch.setattr(job_module, "_load_winning_diffs", lambda *a: {t.id: "diff"})
    monkeypatch.setattr(job_module, "ensure_snapshot", snapshot)
    monkeypatch.setattr(job_module, "plan_cleanup", lambda *a: [planned])
    monkeypatch.setattr(job_module, "run_integration", integration)
    monkeypatch.setattr(job_module, "ConventionIndex", lambda *a: None)
    monkeypatch.setattr(job_module, "WaveRunner", Runner)

    await job_module._cleanup(job.id, store, events, _Sandbox(), None, "g", settings)

    rows = store.q("SELECT payload FROM event WHERE job_id = ? AND kind = 'cleanup.result'", (job.id,))
    assert [json.loads(r["payload"]) for r in rows] == [{"orphans_found": 2, "removed": 2}]
