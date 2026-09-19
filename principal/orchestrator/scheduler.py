"""Scheduling and the candidate race.

First green wins rather than best of three: there is no scoring function that can
rank two patches that both pass the same tests, and inventing one would put a
model back in the accept path, which the design rules out.

Cancelling a losing candidate has to cancel its sandbox operation too, or the job
holds operation slots it is no longer using. This works only because
sandbox.run registers the operation id before awaiting.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from principal.agents.coder import generate_candidate, render_context_pack
from principal.agents.contracts import RepairKind
from principal.agents.repairer import render_repair_prompt
from principal.agents.repairer import repair as repair_agent
from principal.config import Tier
from principal.db.events import EventLog
from principal.db.store import Store, Task
from principal.diffs import ParsedDiff
from principal.errors import DiffUnparseable, PrincipalError
from principal.gates.pipeline import Verdict, VerdictKind
from principal.gates.scope import check_scope
from principal.gates.syntax import check_syntax
from principal.gates.tests import run_tests_gate, select_tests
from principal.graph.embed import ConventionIndex
from principal.graph.parse import CallSite
from principal.models.client import ModelClient
from principal.sandbox.client import SandboxClient
from principal.sandbox.runner import stack_trace as extract_stack_trace

logger = logging.getLogger("principal.orchestrator.scheduler")

TEMPERATURES = (0.0, 0.4, 0.8)


@dataclass(slots=True)
class TaskContext:
    job_id: str
    graph_id: str
    snapshot: Path
    c0_image: object
    radius_files: set[str]
    store: Store
    events: EventLog
    models: ModelClient
    sandbox: SandboxClient
    convention_index: ConventionIndex | None
    task_timeout_s: int
    sandbox_timeout_s: int
    max_repairs: int
    # Benchmark arm A only. Scope and syntax stay enforced — they are structural
    # safety, not a verification opinion — but the tests verdict is forced to
    # ok so a single ungated candidate settles immediately. This measures what
    # one unsupervised model call produces; it is never set outside bench/, and
    # job.py refuses to open a pull request while it is on regardless of how
    # this got set.
    bench_accept_without_gates: bool = False


@dataclass(slots=True)
class TaskResult:
    task: Task
    settled: str  # "verified" | "discarded"
    winning_attempt_id: str | None = None
    diff_text: str | None = None
    result_image: str | None = None
    reason: str | None = None


def _read_file(snapshot: Path, rel: str) -> str:
    try:
        return (snapshot / rel).read_text(encoding="utf-8")
    except OSError:
        return ""


def _call_sites_in_file(store: Store, graph_id: str, target_file: str) -> list[CallSite]:
    rows = store.q(
        "SELECT c.line, c.callee_name FROM call_edge c JOIN file f ON f.id = c.file_id"
        " WHERE c.graph_id = ? AND f.path = ? AND c.confidence = 'static' ORDER BY c.line",
        (graph_id, target_file),
    )
    return [CallSite(callee_name=r["callee_name"], receiver=None, line=r["line"], enclosing=None) for r in rows]


async def _build_context_pack(ctx: TaskContext, task: Task) -> str:
    content = _read_file(ctx.snapshot, task.target_file)
    call_sites = _call_sites_in_file(ctx.store, ctx.graph_id, task.target_file)
    nodeids, _ = select_tests(ctx.store, ctx.graph_id, task.target_file)

    examples: list[tuple[str, str]] = []
    if ctx.convention_index is not None:
        try:
            found = await ctx.convention_index.query(task.instruction, k=2, exclude={task.target_file})
            examples = [(e.path, e.excerpt) for e in found]
        except Exception:  # noqa: BLE001
            examples = []

    test_names = sorted({n.split("::", 1)[-1] for n in nodeids}) if nodeids else []

    return render_context_pack(
        instruction=task.instruction, acceptance=task.acceptance, target_file=task.target_file,
        file_content=content, call_sites=call_sites, convention_examples=examples,
        test_names=test_names,
    )


async def _apply_local_gates(ctx: TaskContext, task: Task, diff: ParsedDiff) -> tuple[Verdict, str | None]:
    scope_verdict = check_scope(diff, task.target_file, ctx.radius_files)
    if not scope_verdict.ok:
        return scope_verdict, None
    return check_syntax(diff, task.target_file, ctx.snapshot)


async def _run_sandbox_gate(
    ctx: TaskContext, task: Task, diff: ParsedDiff, attempt_id: str,
) -> tuple[Verdict, object]:
    nodeids, fallback_all = select_tests(ctx.store, ctx.graph_id, task.target_file)
    if fallback_all:
        ctx.events.emit(
            ctx.job_id, "test_selection.fallback",
            {"task_id": task.id, "file": task.target_file, "reason": "no covering tests found"},
        )

    def on_op(op_id: str) -> None:
        # Written BEFORE the await returns, which is what makes crash recovery
        # and cancellation possible.
        ctx.store.update_attempt(attempt_id, operation_id=op_id)

    verdict, run = await run_tests_gate(
        ctx.sandbox, ctx.c0_image, diff.text, nodeids, timeout_s=ctx.sandbox_timeout_s, on_operation=on_op,
    )
    if ctx.bench_accept_without_gates and not verdict.ok and verdict.kind not in (
        VerdictKind.SCOPE, VerdictKind.SYNTAX,
    ):
        # The true result is still on `run` (exit code, failing tests) for the
        # benchmark's own independent scoring — only the *decision to proceed*
        # is overridden, and only for the tests verdict specifically.
        verdict = Verdict.ok_()
    return verdict, run


async def _one_candidate(
    ctx: TaskContext, task: Task, context_pack: str, n: int, temperature: float, tier: Tier,
) -> tuple[str, Verdict, object | None]:
    """Returns (attempt_id, verdict, TestRun|None)."""
    attempt = ctx.store.create_attempt(
        task_id=task.id, job_id=ctx.job_id, n=n, model=ctx.models.model_ids[tier],
        temperature=temperature, parent_image=str(getattr(ctx.c0_image, "uuid", "")), kind="coder",
    )
    ctx.events.emit(ctx.job_id, "attempt.started", {
        "task_id": task.id, "attempt_id": attempt.id, "n": n, "model": ctx.models.model_ids[tier],
        "temperature": temperature,
    })

    try:
        diff, completion = await generate_candidate(
            ctx.models, job_id=ctx.job_id, context_pack=context_pack, temperature=temperature, tier=tier,
        )
    except DiffUnparseable as exc:
        ctx.store.update_attempt(attempt.id, verdict="error", gate="diffparse")
        ctx.events.emit(ctx.job_id, "attempt.verdict", {
            "task_id": task.id, "attempt_id": attempt.id, "verdict": "error", "gate": "diffparse",
            "reason": str(exc),
        })
        return attempt.id, Verdict.fail(VerdictKind.ERROR, "diffparse", str(exc)), None
    except PrincipalError as exc:
        ctx.store.update_attempt(attempt.id, verdict="error", gate="model")
        ctx.events.emit(ctx.job_id, "attempt.verdict", {
            "task_id": task.id, "attempt_id": attempt.id, "verdict": "error", "gate": "model",
            "reason": exc.message,
        })
        return attempt.id, Verdict.fail(VerdictKind.ERROR, "model", exc.message), None

    ctx.store.update_attempt(
        attempt.id, diff_sha256=hashlib.sha256(diff.text.encode()).hexdigest(),
        prompt_tokens=completion.prompt_tokens, completion_tokens=completion.completion_tokens,
    )
    # Saved unconditionally, not only on a sandbox pass, so a repair always has
    # something to start from even when every candidate died at a local gate.
    ctx.store.put_artifact(
        job_id=ctx.job_id, kind="diff", rel_path=f"attempts/{attempt.id}.diff",
        content=diff.text, base_dir=Path("runs") / ctx.job_id, attempt_id=attempt.id,
    )

    local_verdict, patched_text = await _apply_local_gates(ctx, task, diff)
    del patched_text
    if not local_verdict.ok:
        ctx.store.update_attempt(attempt.id, verdict=local_verdict.kind.value, gate=local_verdict.gate)
        ctx.events.emit(ctx.job_id, "attempt.verdict", {
            "task_id": task.id, "attempt_id": attempt.id, "verdict": local_verdict.kind.value,
            "gate": local_verdict.gate, "reason": local_verdict.reason,
        })
        return attempt.id, local_verdict, None

    verdict, run = await _run_sandbox_gate(ctx, task, diff, attempt.id)
    ctx.store.update_attempt(
        attempt.id, verdict=verdict.kind.value, gate=verdict.gate or "tests",
        result_image=getattr(run, "image", None), exit_code=getattr(run, "exit_code", None),
        duration_ms=getattr(run, "duration_ms", None),
    )

    art_id = None
    if run is not None:
        art_id = ctx.store.put_artifact(
            job_id=ctx.job_id, kind="test_log", rel_path=f"attempts/{attempt.id}.log",
            content=getattr(run, "stdout", ""), base_dir=Path("runs") / ctx.job_id,
            attempt_id=attempt.id,
        )

    ctx.events.emit(ctx.job_id, "attempt.verdict", {
        "task_id": task.id, "attempt_id": attempt.id, "verdict": verdict.kind.value,
        "gate": verdict.gate, "reason": verdict.reason, "artifact_id": art_id,
        "operation_id": getattr(run, "operation_id", None),
        "failing": getattr(run, "failing", [])[:10] if run else [],
    })
    return attempt.id, verdict, run


async def _first_green(coros: list) -> tuple[str, Verdict, object | None] | None:
    pending = {asyncio.create_task(c) for c in coros}
    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for d in done:
                try:
                    attempt_id, verdict, run = d.result()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("candidate coroutine raised: %s", exc)
                    continue
                if verdict.ok:
                    return attempt_id, verdict, run
        return None
    finally:
        for p in pending:
            p.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def _repair_once(
    ctx: TaskContext, task: Task, failing_diff: str, run: object, attempt_n: int,
) -> tuple[str, Verdict, object | None]:
    content = _read_file(ctx.snapshot, task.target_file)
    trace = extract_stack_trace(run) if run is not None else "(no test output captured)"
    prompt = render_repair_prompt(
        target_file=task.target_file, current_content=content, failing_diff=failing_diff,
        failing_tests=getattr(run, "failing", []) if run else [], stack_trace=trace,
    )

    attempt = ctx.store.create_attempt(
        task_id=task.id, job_id=ctx.job_id, n=attempt_n, model=ctx.models.model_ids[Tier.SUPER],
        temperature=0.2, parent_image=str(getattr(ctx.c0_image, "uuid", "")), kind="repair",
    )
    ctx.events.emit(ctx.job_id, "attempt.started", {
        "task_id": task.id, "attempt_id": attempt.id, "n": attempt_n,
        "model": ctx.models.model_ids[Tier.SUPER], "temperature": 0.2, "kind": "repair",
    })

    try:
        diff, kind, completion = await repair_agent(ctx.models, job_id=ctx.job_id, prompt=prompt)
    except (DiffUnparseable, PrincipalError) as exc:
        reason = exc.message if isinstance(exc, PrincipalError) else str(exc)
        ctx.store.update_attempt(attempt.id, verdict="error", gate="diffparse")
        ctx.events.emit(ctx.job_id, "attempt.verdict", {
            "task_id": task.id, "attempt_id": attempt.id, "verdict": "error", "reason": reason,
        })
        return attempt.id, Verdict.fail(VerdictKind.ERROR, "diffparse", reason), None

    ctx.store.update_attempt(
        attempt.id, prompt_tokens=completion.prompt_tokens, completion_tokens=completion.completion_tokens,
    )
    ctx.store.put_artifact(
        job_id=ctx.job_id, kind="diff", rel_path=f"attempts/{attempt.id}.diff",
        content=diff.text, base_dir=Path("runs") / ctx.job_id, attempt_id=attempt.id,
    )
    ctx.events.emit(ctx.job_id, "repair.classified", {
        "task_id": task.id, "attempt_id": attempt.id, "kind": kind.value,
    })
    if kind is RepairKind.TEST_EXPECTATION:
        ctx.events.emit(ctx.job_id, "repair.test_expectation", {
            "task_id": task.id, "attempt_id": attempt.id,
            "note": "the repairer believes the failing test asserts the old behaviour;"
                    " it cannot edit the test, so this will likely exhaust repairs",
        })

    local_verdict, _ = await _apply_local_gates(ctx, task, diff)
    if not local_verdict.ok:
        ctx.store.update_attempt(attempt.id, verdict=local_verdict.kind.value, gate=local_verdict.gate)
        ctx.events.emit(ctx.job_id, "attempt.verdict", {
            "task_id": task.id, "attempt_id": attempt.id, "verdict": local_verdict.kind.value,
            "gate": local_verdict.gate, "reason": local_verdict.reason,
        })
        return attempt.id, local_verdict, None

    verdict, new_run = await _run_sandbox_gate(ctx, task, diff, attempt.id)
    ctx.store.update_attempt(
        attempt.id, verdict=verdict.kind.value, gate=verdict.gate or "tests",
        result_image=getattr(new_run, "image", None), exit_code=getattr(new_run, "exit_code", None),
        duration_ms=getattr(new_run, "duration_ms", None),
    )
    ctx.events.emit(ctx.job_id, "attempt.verdict", {
        "task_id": task.id, "attempt_id": attempt.id, "verdict": verdict.kind.value,
        "gate": verdict.gate, "reason": verdict.reason,
        "failing": getattr(new_run, "failing", [])[:10] if new_run else [],
    })
    return attempt.id, verdict, new_run


async def run_task(ctx: TaskContext, task: Task, candidates_per_task: int = 3) -> TaskResult:
    """Retries are bounded at 3 attempts per task: the original fanout counts as
    one collective attempt round, then 2 stack-trace repairs. Each retry gets the
    failure output and nothing else — no accumulated conversation history."""
    ctx.store.update_task(task.id, state="running")

    try:
        context_pack = await asyncio.wait_for(_build_context_pack(ctx, task), timeout=60)
    except TimeoutError:
        return _discard(ctx, task, "context assembly timed out")

    temps = TEMPERATURES[:candidates_per_task] or (0.0,)
    coros = [
        _one_candidate(ctx, task, context_pack, n=i + 1, temperature=t, tier=Tier.NANO)
        for i, t in enumerate(temps)
    ]

    try:
        winner = await asyncio.wait_for(_first_green(coros), timeout=ctx.task_timeout_s)
    except TimeoutError:
        return _discard(ctx, task, f"exceeded task timeout of {ctx.task_timeout_s}s")

    if winner is not None:
        attempt_id, _, run = winner
        return _verify_and_settle(ctx, task, attempt_id, run)

    # No first-attempt candidate went green. Repair with the most informative
    # failure: prefer a real test failure over a scope/syntax rejection.
    last_diff, last_run = await _best_failure(ctx, task, coros_results=None)
    n = candidates_per_task + 1
    for _ in range(ctx.max_repairs):
        try:
            attempt_id, verdict, run = await asyncio.wait_for(
                _repair_once(ctx, task, last_diff or "", last_run, n), timeout=ctx.task_timeout_s,
            )
        except TimeoutError:
            return _discard(ctx, task, f"repair exceeded task timeout of {ctx.task_timeout_s}s")
        if verdict.ok:
            return _verify_and_settle(ctx, task, attempt_id, run)
        last_run = run
        n += 1

    return _discard(ctx, task, "exhausted: no candidate or repair passed local tests")


async def _best_failure(ctx: TaskContext, task: Task, coros_results) -> tuple[str | None, object | None]:
    """After _first_green exhausts every candidate, recover the most useful diff
    and test run to seed the repair loop. Reads the last attempt's artifact."""
    del coros_results
    rows = ctx.store.q(
        "SELECT id FROM attempt WHERE task_id = ? ORDER BY rowid DESC LIMIT 1", (task.id,)
    )
    if not rows:
        return None, None
    attempt_id = rows[0]["id"]
    art = ctx.store.q1(
        "SELECT * FROM artifact WHERE attempt_id = ? AND kind = 'diff' ORDER BY rowid DESC LIMIT 1",
        (attempt_id,),
    )
    diff_text = None
    if art is not None:
        try:
            diff_text = (Path("runs") / ctx.job_id / art["path"]).read_text(encoding="utf-8")
        except OSError:
            diff_text = None
    return diff_text, None


def _verify_and_settle(ctx: TaskContext, task: Task, attempt_id: str, run: object) -> TaskResult:
    del run
    art = ctx.store.q1(
        "SELECT * FROM artifact WHERE attempt_id = ? AND kind = 'diff' ORDER BY rowid DESC LIMIT 1",
        (attempt_id,),
    )
    diff_text = ""
    if art is not None:
        try:
            diff_text = (Path("runs") / ctx.job_id / art["path"]).read_text(encoding="utf-8")
        except OSError:
            pass
    attempt = ctx.store.get_attempt(attempt_id)
    ctx.store.update_task(task.id, state="verified", winning_attempt_id=attempt_id)
    ctx.events.emit(ctx.job_id, "task.settled", {"task_id": task.id, "winning_attempt_id": attempt_id})
    return TaskResult(
        task=task, settled="verified", winning_attempt_id=attempt_id, diff_text=diff_text,
        result_image=attempt.result_image if attempt else None,
    )


def _discard(ctx: TaskContext, task: Task, reason: str) -> TaskResult:
    ctx.store.update_task(task.id, state="discarded", discard_reason=reason)
    ctx.events.emit(ctx.job_id, "task.settled", {"task_id": task.id, "discard_reason": reason})
    return TaskResult(task=task, settled="discarded", reason=reason)


@dataclass(slots=True)
class WaveRunner:
    ctx: TaskContext
    max_parallel: int
    candidates_per_task: int

    async def run_waves(self, waves: list[list[Task]]) -> list[TaskResult]:
        sem = asyncio.Semaphore(self.max_parallel)
        results: list[TaskResult] = []
        for wave in waves:
            async def bound(t: Task) -> TaskResult:
                async with sem:
                    return await run_task(self.ctx, t, self.candidates_per_task)
            wave_results = await asyncio.gather(*(bound(t) for t in wave))
            results.extend(wave_results)
        return results
