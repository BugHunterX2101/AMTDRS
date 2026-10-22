"""What the candidate race leaves on the record.

Two gaps found in a live run's database: the candidates cancelled once another
went green kept no verdict at all, so the console showed them "running" after
the job had finished; and each repair after the first was handed the seed's diff
next to a later run's failures, a pair that never existed.

The model and sandbox are replaced by stand-ins here; these tests are about the
scheduler's bookkeeping, not about the platform.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from principal.config import Tier
from principal.gates.pipeline import Verdict, VerdictKind
from principal.orchestrator import scheduler
from principal.orchestrator.scheduler import TaskContext


@pytest.fixture
def ctx_and_task(store, events, tmp_path: Path):
    job = store.create_job(repo_url="x", commit_sha="deadbeef", goal="g", target_fqn="m.f",
                           token_budget=1_000_000, tunables={})
    task = store.create_task(job_id=job.id, seq=1, target_file="m.py", instruction="i",
                             acceptance="a", depends_on=[])
    ctx = TaskContext(
        job_id=job.id, graph_id="g", snapshot=tmp_path, c0_image=SimpleNamespace(uuid="c0"),
        radius_files={"m.py"}, store=store, events=events,
        models=SimpleNamespace(model_ids={Tier.NANO: "nano", Tier.SUPER: "super"}),
        sandbox=None, convention_index=None, task_timeout_s=30, sandbox_timeout_s=30,
        max_repairs=2, runs_dir=tmp_path,
    )
    return ctx, task


def _verdict_events(store, job_id):
    rows = store.q("SELECT payload FROM event WHERE job_id = ? AND kind = 'attempt.verdict'", (job_id,))
    return [json.loads(r["payload"]) for r in rows]


def _stub_first_round(monkeypatch, failures):
    async def pack(*args):
        return "pack"

    async def first_round(coros):
        for c in coros:
            c.close()
        return None, failures

    monkeypatch.setattr(scheduler, "_build_context_pack", pack)
    monkeypatch.setattr(scheduler, "_first_green", first_round)


async def test_a_cancelled_candidate_is_recorded_as_cancelled(ctx_and_task, store, monkeypatch):
    ctx, task = ctx_and_task

    async def never_answers(*args, **kwargs):
        await asyncio.sleep(3600)

    monkeypatch.setattr(scheduler, "generate_candidate", never_answers)
    running = asyncio.create_task(scheduler._one_candidate(ctx, task, "pack", 1, 0.0, Tier.NANO))
    await asyncio.sleep(0)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    [attempt] = store.attempts_of(ctx.job_id)
    assert attempt.verdict == "cancelled"
    assert _verdict_events(store, ctx.job_id)[-1]["verdict"] == "cancelled"


async def test_each_repair_is_given_the_diff_that_produced_the_failure_it_reads(ctx_and_task, monkeypatch):
    ctx, task = ctx_and_task
    red = Verdict.fail(VerdictKind.RED, "tests", "red")
    seed_run, repair_run = SimpleNamespace(failing=["t1"]), SimpleNamespace(failing=["t2"])
    diffs = {"seed": "SEED DIFF", "r4": "REPAIR 1 DIFF"}
    seen: list[tuple[str, object]] = []

    async def repair(_ctx, _task, failing_diff, run, n):
        seen.append((failing_diff, run))
        return f"r{n}", red, repair_run

    _stub_first_round(monkeypatch, [("seed", red, seed_run)])
    monkeypatch.setattr(scheduler, "_repair_once", repair)
    monkeypatch.setattr(scheduler, "_diff_of_attempt", lambda _ctx, aid: diffs.get(aid))

    await scheduler.run_task(ctx, task)

    assert seen == [("SEED DIFF", seed_run), ("REPAIR 1 DIFF", repair_run)]


async def test_a_repair_that_never_reached_the_sandbox_does_not_replace_the_seed(ctx_and_task, monkeypatch):
    """A repair that died at a local gate has no run to read. Pairing its diff with
    "no test output" would hand the next repair less than it already had."""
    ctx, task = ctx_and_task
    red = Verdict.fail(VerdictKind.RED, "tests", "red")
    syntax = Verdict.fail(VerdictKind.SYNTAX, "syntax", "bad")
    seed_run = SimpleNamespace(failing=["t1"])
    seen: list[tuple[str, object]] = []

    async def repair(_ctx, _task, failing_diff, run, n):
        seen.append((failing_diff, run))
        return f"r{n}", syntax, None

    _stub_first_round(monkeypatch, [("seed", red, seed_run)])
    monkeypatch.setattr(scheduler, "_repair_once", repair)
    monkeypatch.setattr(scheduler, "_diff_of_attempt", lambda _ctx, aid: {"seed": "SEED DIFF"}.get(aid, "X"))

    await scheduler.run_task(ctx, task)

    assert seen == [("SEED DIFF", seed_run), ("SEED DIFF", seed_run)]


async def test_with_no_diff_to_repair_super_takes_the_task_as_a_coder(ctx_and_task, monkeypatch):
    """The Repairer is not told the goal. When every first-round candidate died before
    it parsed there is no diff to show it, so it would be asked to repair nothing."""
    ctx, task = ctx_and_task
    unparseable = Verdict.fail(VerdictKind.ERROR, "diffparse", "no block")
    red = Verdict.fail(VerdictKind.RED, "tests", "red")
    run = SimpleNamespace(failing=["t1"])
    coded: list[tuple[Tier, str]] = []
    repaired: list[str] = []

    async def as_coder(_ctx, _task, pack, n, temperature, tier):
        coded.append((tier, pack))
        return f"c{n}", red, run

    async def repair(_ctx, _task, failing_diff, _run, n):
        repaired.append(failing_diff)
        return f"r{n}", red, run

    _stub_first_round(monkeypatch, [("a1", unparseable, None), ("a2", unparseable, None)])
    monkeypatch.setattr(scheduler, "_one_candidate", as_coder)
    monkeypatch.setattr(scheduler, "_repair_once", repair)
    monkeypatch.setattr(scheduler, "_diff_of_attempt", lambda _ctx, aid: {"c4": "CODER DIFF"}.get(aid))

    await scheduler.run_task(ctx, task)

    assert coded == [(Tier.SUPER, "pack")]
    assert repaired == ["CODER DIFF"]
