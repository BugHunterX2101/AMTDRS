"""A task is verified on the baseline plus the verified diffs it depends on.

Every candidate used to be tested on C0 alone. A task that depends on another
one — the definition of a breaking interface change, landing after its callers
were updated — was then tested against callers that were never updated, failed,
and was discarded although the integrated result would have been green.
"""

from __future__ import annotations

import base64
import re

from principal.config import Tier
from principal.models.client import Completion
from principal.orchestrator.ordering import order_waves
from principal.orchestrator.scheduler import TaskContext, WaveRunner
from principal.sandbox.client import RunResult
from tests.conftest import diff_block
from tests.integration.test_verification_pipeline import HARMLESS_DIFF

LOGIN_DIFF = """--- a/src/api/login.py
+++ b/src/api/login.py
@@ -1,5 +1,6 @@
 from src.auth.session import create

+# touched by the dependency-patch test

 def login(user):
     return create(user, 3600)
"""

LOGIN_MARK = "# touched by the dependency-patch test"
SESSION_MARK = "# touched by the verification pipeline test"

REPORT = (
    '__PRINCIPAL_REPORT__\n{"summary": {"passed": 1, "failed": 0, "collected": 1}, "tests": []}\n'
    "__PRINCIPAL_END__"
)


class _Models:
    model_ids = {Tier.NANO: "nano", Tier.SUPER: "super", Tier.ULTRA: "ultra"}

    async def complete(self, tier, messages, **kwargs):
        prompt = messages[-1]["content"]
        body = LOGIN_DIFF if "src/api/login.py" in prompt else HARMLESS_DIFF
        return Completion(
            text=diff_block(body), prompt_tokens=10, completion_tokens=10,
            used_reasoning_fallback=False, protocol=None,
        )


class _Sandbox:
    """Green every time; keeps the patch each run applied."""

    def __init__(self):
        self.patches: list[str] = []

    async def run(self, image, script, **kwargs):
        encoded = re.search(r"echo (\S+) \| base64 -d > /tmp/p\.diff", script).group(1)
        self.patches.append(base64.b64decode(encoded).decode())
        return RunResult(image="img", exit_code=0, stdout=REPORT, stderr="", duration_ms=5)


class _Image:
    uuid = "c0"


async def _run(store, events, settings, snapshot, graph, depends_on):
    job = store.create_job(
        repo_url="r", commit_sha="c", goal="g", target_fqn=None, token_budget=10**6, tunables={},
    )
    store.create_task(
        job_id=job.id, seq=0, target_file="src/api/login.py", instruction="i", acceptance="a",
        depends_on=[],
    )
    store.create_task(
        job_id=job.id, seq=1, target_file="src/auth/session.py", instruction="i", acceptance="a",
        depends_on=depends_on,
    )
    sandbox = _Sandbox()
    ctx = TaskContext(
        job_id=job.id, graph_id=graph.graph_id, snapshot=snapshot, c0_image=_Image(),
        radius_files={"src/api/login.py", "src/auth/session.py"}, store=store, events=events,
        models=_Models(), sandbox=sandbox, convention_index=None, task_timeout_s=60,
        sandbox_timeout_s=30, max_repairs=0, runs_dir=settings.run_dir(job.id),
    )
    runner = WaveRunner(ctx=ctx, max_parallel=1, candidates_per_task=1)
    results = await runner.run_waves(order_waves(store.tasks_of(job.id)))
    return results, sandbox.patches


async def test_a_dependent_task_is_tested_with_the_diff_it_depends_on(
    store, events, settings, snapshot, graph
):
    results, patches = await _run(store, events, settings, snapshot, graph, depends_on=[0])

    assert [r.settled for r in results] == ["verified", "verified"]
    assert LOGIN_MARK in patches[0] and SESSION_MARK not in patches[0], (
        "a task with no dependencies is tested on C0 alone"
    )
    assert patches[1].index(LOGIN_MARK) < patches[1].index(SESSION_MARK)


async def test_the_winning_diff_stays_the_tasks_own(store, events, settings, snapshot, graph):
    """Integration applies each task's winning diff once; folding the dependency
    into it would apply that change twice."""
    results, _ = await _run(store, events, settings, snapshot, graph, depends_on=[0])

    assert SESSION_MARK in results[1].diff_text
    assert LOGIN_MARK not in results[1].diff_text


async def test_independent_tasks_are_still_tested_on_the_baseline_alone(
    store, events, settings, snapshot, graph
):
    _, patches = await _run(store, events, settings, snapshot, graph, depends_on=[])

    assert sorted((LOGIN_MARK in p, SESSION_MARK in p) for p in patches) == [(False, True), (True, False)]
