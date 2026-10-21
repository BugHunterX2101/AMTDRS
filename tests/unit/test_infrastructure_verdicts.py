"""A platform fault is never a verdict on a patch.

The failure this prevents is quiet and plausible: a sandbox times out, the test
run produces no report, and a pipeline that treats "not green" as "red" discards
a patch that was never actually evaluated — then counts it against the system.
Everything below is a different way of asking whether that can still happen.
"""

from __future__ import annotations

import pytest

from principal.config import Tier
from principal.errors import SandboxForbidden, SandboxOpFailed, SandboxTimeout
from principal.gates.pipeline import VerdictKind
from principal.gates.tests import run_tests_gate
from principal.models.client import Completion
from principal.orchestrator.scheduler import TaskContext, run_task
from principal.sandbox.client import RunResult
from principal.sandbox.runner import _parse
from tests.conftest import diff_block
from tests.integration.test_verification_pipeline import HARMLESS_DIFF

REPORT = (
    '__PRINCIPAL_REPORT__\n{"summary": {"passed": 1, "failed": 0, "collected": 1}, "tests": []}\n'
    "__PRINCIPAL_END__"
)


def _result(exit_code: int, stdout: str = "", *, timed_out: bool = False) -> RunResult:
    return RunResult(image="img", exit_code=exit_code, stdout=stdout, stderr="", duration_ms=5,
                     timed_out=timed_out)


# ---------------------------------------------------------------- parsing ----


@pytest.mark.parametrize("code", [124, 137, 143])
def test_a_run_stopped_from_outside_with_no_report_is_infrastructure(code):
    run = _parse(_result(code, "no sentinel here"))
    assert run.infrastructure
    assert not run.green


def test_a_reported_timeout_is_infrastructure_whatever_the_exit_code():
    assert _parse(_result(1, "", timed_out=True)).infrastructure


def test_a_failing_suite_that_produced_a_report_is_not_infrastructure():
    """The complement. A gate that called everything infrastructure would be
    perfectly safe and would never reject a bad patch."""
    report = REPORT.replace('"failed": 0', '"failed": 2')
    run = _parse(_result(1, report))
    assert not run.infrastructure
    assert run.failed == 2


def test_an_ordinary_non_zero_exit_without_a_report_is_still_a_failure():
    run = _parse(_result(2, "collection blew up"))
    assert not run.infrastructure
    assert run.errors == 1, "no report and a plain failure exit keeps meaning 'the code is broken'"


def test_a_patch_that_does_not_apply_is_not_infrastructure():
    run = _parse(_result(90, "PRINCIPAL_APPLY_FAILED"))
    assert run.apply_failed
    assert not run.infrastructure


# ------------------------------------------------------------------- gate ----


class _Sandbox:
    def __init__(self, result: RunResult | Exception):
        self._result = result

    async def run(self, *args, **kwargs):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


async def test_the_tests_gate_reports_a_timeout_as_infrastructure_not_red():
    verdict, run = await run_tests_gate(
        _Sandbox(_result(124, "", timed_out=True)), object(), "diff", [], timeout_s=1,
    )
    assert verdict.kind is VerdictKind.INFRASTRUCTURE
    assert verdict.kind is not VerdictKind.RED
    assert "not evidence against the patch" in verdict.reason
    assert run.infrastructure


async def test_a_red_suite_is_still_red():
    report = REPORT.replace('"failed": 0', '"failed": 1')
    verdict, _ = await run_tests_gate(_Sandbox(_result(1, report)), object(), "diff", [], timeout_s=1)
    assert verdict.kind is VerdictKind.RED


# -------------------------------------------------------------- scheduling ----


class _Models:
    """Always answers with a diff that is valid for the fixture."""

    model_ids = {Tier.NANO: "nano", Tier.SUPER: "super", Tier.ULTRA: "ultra"}

    def __init__(self):
        self.calls = 0

    async def complete(self, tier, messages, **kwargs):
        self.calls += 1
        return Completion(
            text=diff_block(HARMLESS_DIFF), prompt_tokens=10, completion_tokens=10,
            used_reasoning_fallback=False, protocol=None,
        )


class _Image:
    uuid = "c0"


def _context(store, events, settings, snapshot, graph, sandbox, models):
    job = store.create_job(
        repo_url="r", commit_sha="c", goal="g", target_fqn=None, token_budget=10**6, tunables={},
    )
    task = store.create_task(
        job_id=job.id, seq=0, target_file="src/auth/session.py", instruction="i", acceptance="a",
        depends_on=[],
    )
    ctx = TaskContext(
        job_id=job.id, graph_id=graph.graph_id, snapshot=snapshot, c0_image=_Image(),
        radius_files={"src/auth/session.py"}, store=store, events=events, models=models,
        sandbox=sandbox, convention_index=None, task_timeout_s=60, sandbox_timeout_s=30,
        max_repairs=2, runs_dir=settings.run_dir(job.id),
    )
    return ctx, task


@pytest.mark.parametrize("error", [SandboxTimeout(30), SandboxOpFailed("boom"), SandboxForbidden()])
async def test_a_task_the_platform_could_not_evaluate_is_discarded_as_infrastructure(
    error, store, events, settings, snapshot, graph
):
    models = _Models()
    ctx, task = _context(store, events, settings, snapshot, graph, _Sandbox(error), models)

    result = await run_task(ctx, task, candidates_per_task=3)

    assert result.settled == "discarded"
    assert result.reason.startswith("infrastructure (inconclusive")
    verdicts = {a.verdict for a in store.attempts_of(ctx.job_id)}
    assert verdicts == {"infrastructure"}, "no candidate may be recorded as a red or failed patch"
    assert models.calls == 3, "no repair is spent on a run that never produced a failure to read"

    settled = [e for e in events.replay(ctx.job_id) if e.kind == "task.settled"]
    assert settled[-1].payload["infrastructure"] is True


async def test_a_genuinely_red_task_is_not_labelled_infrastructure(
    store, events, settings, snapshot, graph
):
    report = REPORT.replace('"failed": 0', '"failed": 1')
    models = _Models()
    ctx, task = _context(
        store, events, settings, snapshot, graph, _Sandbox(_result(1, report)), models,
    )

    result = await run_task(ctx, task, candidates_per_task=1)

    assert result.settled == "discarded"
    assert not result.reason.startswith("infrastructure")
    attempts = store.attempts_of(ctx.job_id)
    assert {a.verdict for a in attempts} == {"red"}
    assert len(attempts) == 3, "one candidate plus the two budgeted repairs"
