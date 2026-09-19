"""The job state machine. From a goal to a PR, or to a reported reason it stopped.

Four layers, one rule: nothing moves from left to right without passing a test.
NoPR is a success state, not a failure state: it means the system did its job and
correctly declined to ship.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from principal.agents.planner import plan as run_planner
from principal.agents.reporter import compose_report
from principal.config import Settings
from principal.db.events import EventLog
from principal.db.store import Store, Task, now
from principal.errors import Code, PrincipalError
from principal.gates.apply import apply_to_text
from principal.gates.behaviour import evaluate_behaviour
from principal.graph.build import build as build_graph
from principal.graph.build import ingest_coverage
from principal.graph.embed import ConventionIndex
from principal.graph.languages import lang_of
from principal.graph.parse import parse_source
from principal.graph.radius import BlastRadius, blast_radius, find_target
from principal.models.client import ModelClient
from principal.orchestrator.budget import exceeds_discard_cap
from principal.orchestrator.ordering import order_waves
from principal.orchestrator.scheduler import TaskContext, WaveRunner
from principal.publish.github import open_pr
from principal.publish.prbody import render_fallback_body
from principal.sandbox.baseline import build_baseline, ensure_snapshot
from principal.sandbox.client import SandboxClient
from principal.sandbox.runner import run_integration

logger = logging.getLogger("principal.orchestrator.job")


class _Stopped(Exception):
    """Internal signal: the job already wrote its terminal state and event."""


async def run_job(
    job_id: str, *, store: Store, events: EventLog, sandbox: SandboxClient, models: ModelClient,
    settings: Settings,
) -> None:
    job = store.get_job(job_id)
    if job is None:
        return

    try:
        await _ingest_and_baseline(job_id, store, events, sandbox, settings)
        graph_id = await _map(job_id, store, events, settings)
        radius = await _plan(job_id, store, events, models, graph_id, settings)
        await _execute(job_id, store, events, sandbox, models, graph_id, radius, settings)
        await _integrate_and_publish(job_id, store, events, sandbox, models, graph_id, radius, settings)
    except _Stopped:
        pass
    except PrincipalError as exc:
        logger.warning("job %s aborted: %s", job_id, exc)
        with events.transaction(job_id, "job.state", {"state": "Aborted", "reason": exc.message,
                                                        "code": exc.code.value}):
            store.update_job(job_id, state="Aborted", stop_reason=exc.message, finished_at=now())
    except Exception as exc:  # noqa: BLE001
        logger.exception("job %s crashed", job_id)
        with events.transaction(job_id, "job.state", {"state": "Aborted", "reason": str(exc)}):
            store.update_job(job_id, state="Aborted", stop_reason=str(exc), finished_at=now())


def _stop_no_pr(store: Store, events: EventLog, job_id: str, reason: str) -> None:
    with events.transaction(job_id, "job.stopped", {"reason": reason}):
        store.update_job(job_id, state="NoPR", stop_reason=reason, finished_at=now())
    raise _Stopped(reason)


async def _ingest_and_baseline(job_id: str, store: Store, events: EventLog, sandbox: SandboxClient,
                                settings: Settings) -> None:
    job = store.get_job(job_id)
    assert job is not None
    with events.transaction(job_id, "job.state", {"state": "Ingesting"}):
        store.update_job(job_id, state="Ingesting")

    await ensure_snapshot(job.repo_url, job.commit_sha, settings.principal_snapshots_dir)

    with events.transaction(job_id, "job.state", {"state": "Baselining"}):
        store.update_job(job_id, state="Baselining")

    def on_op(op_id: str) -> None:
        events.emit(job_id, "baseline.operation", {"operation_id": op_id})

    baseline = await build_baseline(
        sandbox, base_image=settings.principal_base_image, repo_url=job.repo_url,
        commit_sha=job.commit_sha, timeout_s=max(900, settings.sandbox_timeout_s * 2),
        on_operation=on_op,
    )

    with events.transaction(job_id, "baseline.ready", {
        "image": baseline.image, "tag": baseline.tag, "duration_ms": baseline.duration_ms,
        "collected": baseline.collected, "passed": baseline.passed,
    }):
        store.update_job(job_id, baseline_image=baseline.image)

    store.put_artifact(
        job_id=job_id, kind="coverage", rel_path="baseline_coverage.json",
        content=baseline.coverage_json or "{}", base_dir=settings.run_dir(job_id),
    )
    store.put_artifact(
        job_id=job_id, kind="test_log", rel_path="baseline_report.json",
        content=baseline.report_json or "{}", base_dir=settings.run_dir(job_id),
    )


async def _map(job_id: str, store: Store, events: EventLog, settings: Settings) -> str:
    job = store.get_job(job_id)
    assert job is not None
    with events.transaction(job_id, "job.state", {"state": "Mapping"}):
        store.update_job(job_id, state="Mapping")

    snapshot = await ensure_snapshot(job.repo_url, job.commit_sha, settings.principal_snapshots_dir)
    stats = build_graph(store, snapshot, job.repo_url, job.commit_sha)

    baseline_cov = store.q1(
        "SELECT * FROM artifact WHERE job_id = ? AND kind = 'coverage' ORDER BY rowid LIMIT 1", (job_id,)
    )
    if baseline_cov is not None:
        cov_text = (settings.run_dir(job_id) / baseline_cov["path"]).read_text(encoding="utf-8")
        ingest_coverage(store, stats.graph_id, cov_text)

    with events.transaction(job_id, "graph.built", {
        "files": stats.files, "symbols": stats.symbols, "call_edges": stats.call_edges,
        "heuristic_edges": stats.heuristic_edges, "test_edges": stats.test_edges,
    }):
        store.update_job(job_id, graph_id=stats.graph_id)

    return stats.graph_id


async def _plan(job_id: str, store: Store, events: EventLog, models: ModelClient, graph_id: str,
                 settings: Settings) -> BlastRadius:
    job = store.get_job(job_id)
    assert job is not None
    if not job.target_fqn:
        raise PrincipalError(Code.TARGET_NOT_FOUND, "no target_fqn was given")

    target = find_target(store, graph_id, job.target_fqn)
    radius = blast_radius(
        store, graph_id, target, max_depth=settings.radius_max_depth, cap=settings.radius_file_cap,
    )

    with events.transaction(job_id, "radius.computed", radius.to_payload()):
        pass

    with events.transaction(job_id, "job.state", {"state": "Planning"}):
        store.update_job(job_id, state="Planning")

    plan_result = await run_planner(
        models, job_id=job_id, goal=job.goal, target_fqn=job.target_fqn, radius=radius,
        max_depth=settings.radius_max_depth, max_tasks=settings.max_tasks,
        tier=settings.principal_planner_tier,
    )

    for t in plan_result.tasks:
        store.create_task(
            job_id=job_id, seq=t.seq, target_file=t.target_file, instruction=t.instruction,
            acceptance=t.acceptance, depends_on=t.depends_on, declared_removals=t.declared_removals,
        )

    with events.transaction(job_id, "plan.ready", {
        "tasks": [t.model_dump() for t in plan_result.tasks], "unhandled": plan_result.unhandled,
    }):
        pass

    return radius


async def _execute(job_id: str, store: Store, events: EventLog, sandbox: SandboxClient,
                    models: ModelClient, graph_id: str, radius: BlastRadius, settings: Settings) -> None:
    job = store.get_job(job_id)
    assert job is not None
    with events.transaction(job_id, "job.state", {"state": "Executing"}):
        store.update_job(job_id, state="Executing")

    snapshot = await ensure_snapshot(job.repo_url, job.commit_sha, settings.principal_snapshots_dir)
    assert job.baseline_image is not None
    c0_image = await sandbox.from_uuid(job.baseline_image)

    convention_paths = sorted(radius.files)
    convention_index = ConventionIndex(snapshot, convention_paths) if convention_paths else None

    ctx = TaskContext(
        job_id=job_id, graph_id=graph_id, snapshot=snapshot, c0_image=c0_image,
        radius_files=set(radius.files), store=store, events=events, models=models, sandbox=sandbox,
        convention_index=convention_index, task_timeout_s=settings.task_timeout_s,
        sandbox_timeout_s=settings.sandbox_timeout_s, max_repairs=settings.max_repairs,
        bench_accept_without_gates=settings.bench_accept_without_gates,
    )

    tasks = store.tasks_of(job_id)
    waves = order_waves(tasks)
    runner = WaveRunner(ctx=ctx, max_parallel=settings.max_parallel_tasks,
                         candidates_per_task=settings.candidates_per_task)
    await runner.run_waves(waves)

    tasks = store.tasks_of(job_id)
    if exceeds_discard_cap(tasks, settings.max_discard_ratio):
        discarded = [t for t in tasks if t.state == "discarded"]
        _stop_no_pr(
            store, events, job_id,
            f"{len(discarded)}/{len(tasks)} tasks discarded, exceeds cap of "
            f"{settings.max_discard_ratio:.0%}",
        )


async def _integrate_and_publish(job_id: str, store: Store, events: EventLog, sandbox: SandboxClient,
                                  models: ModelClient, graph_id: str, radius: BlastRadius,
                                  settings: Settings) -> None:
    job = store.get_job(job_id)
    assert job is not None
    tasks = store.tasks_of(job_id)
    verified = sorted((t for t in tasks if t.state == "verified"), key=lambda t: t.seq)
    discarded = [t for t in tasks if t.state == "discarded"]

    if not verified:
        _stop_no_pr(store, events, job_id, "no task was verified")

    with events.transaction(job_id, "job.state", {"state": "Integrating"}):
        store.update_job(job_id, state="Integrating")

    diff_by_file = _load_winning_diffs(store, settings, job_id, verified)
    diffs = [diff_by_file[t.target_file] for t in verified if t.target_file in diff_by_file]

    assert job.baseline_image is not None
    c0_image = await sandbox.from_uuid(job.baseline_image)

    def on_op(op_id: str) -> None:
        events.emit(job_id, "integration.operation", {"operation_id": op_id})

    run = await run_integration(
        sandbox, c0_image, diffs, timeout_s=max(900, settings.sandbox_timeout_s * 2), on_operation=on_op,
    )

    if run.conflict_index is not None:
        events.emit(job_id, "integration.conflict", {"dropped_index": run.conflict_index})
        # Bounded: retry exactly once, dropping the later task by seq. A second
        # failure ends the job in NoPR rather than looping.
        if 0 <= run.conflict_index < len(verified):
            dropped = verified.pop(run.conflict_index)
            store.update_task(dropped.id, state="discarded",
                               discard_reason="dropped at integration: conflicted with another patch")
            diffs = [diff_by_file[t.target_file] for t in verified if t.target_file in diff_by_file]
            run = await run_integration(
                sandbox, c0_image, diffs, timeout_s=max(900, settings.sandbox_timeout_s * 2),
                on_operation=on_op,
            )

    if not run.green:
        with events.transaction(job_id, "integration.result", {
            "pass": False, "failing": run.failing[:20], "exit_code": run.exit_code,
        }):
            store.update_job(job_id, state="NoPR", stop_reason="integration run did not pass",
                              finished_at=now())
        raise _Stopped("integration red")

    snapshot = await ensure_snapshot(job.repo_url, job.commit_sha, settings.principal_snapshots_dir)
    touched_files = {t.target_file for t in verified}
    declared_removals = {r for t in verified for r in t.declared_removals}
    patched_exports = _reextract_exports(snapshot, verified, diff_by_file)

    baseline_cov = _read_artifact_text(store, settings, job_id, kind="coverage", path=None, first=True)
    baseline_collected = _baseline_collected(store, settings, job_id)

    behaviour = evaluate_behaviour(
        store, graph_id, touched_files, declared_removals, patched_exports, baseline_cov or "{}",
        run.coverage_json, baseline_collected, run.collected,
    )

    store.put_artifact(
        job_id=job_id, kind="coverage", rel_path="integration_coverage.json",
        content=run.coverage_json or "{}", base_dir=settings.run_dir(job_id),
    )

    with events.transaction(job_id, "integration.result", {
        "pass": behaviour.verdict.ok, "api_delta_ok": behaviour.api_delta_ok,
        "removed_undeclared": behaviour.removed_undeclared, "coverage_ok": behaviour.coverage_ok,
        "coverage_drops": behaviour.coverage_drops, "test_count_ok": behaviour.test_count_ok,
        "baseline_collected": behaviour.baseline_collected,
        "integration_collected": behaviour.integration_collected,
    }):
        pass

    if not behaviour.verdict.ok:
        with events.transaction(job_id, "job.stopped", {"reason": behaviour.verdict.reason}):
            store.update_job(job_id, state="NoPR", stop_reason=behaviour.verdict.reason, finished_at=now())
        raise _Stopped(behaviour.verdict.reason)

    with events.transaction(job_id, "job.state", {"state": "Publishing"}):
        store.update_job(job_id, state="Publishing")

    uncovered = _uncovered_files(store, graph_id, touched_files)
    report = await compose_report(
        models, job_id=job_id, goal=job.goal,
        verified=[{"target_file": t.target_file, "instruction": t.instruction, "acceptance": t.acceptance}
                  for t in verified],
        discarded=[{"target_file": t.target_file, "discard_reason": t.discard_reason} for t in discarded],
        uncovered=uncovered,
        integration={
            "api_delta_ok": behaviour.api_delta_ok, "coverage_ok": behaviour.coverage_ok,
            "test_count_ok": behaviour.test_count_ok,
            "baseline_collected": behaviour.baseline_collected,
            "integration_collected": behaviour.integration_collected,
        },
    )

    pr_url = None
    if settings.bench_accept_without_gates:
        # Arm A accepts patches without gating on the tests. Such a patch has
        # not been verified by anything, so it must never reach a pull request
        # regardless of how the run is invoked. The benchmark measures; it does
        # not publish.
        events.emit(job_id, "pr.publish_failed",
                    {"reason": "benchmark arm runs ungated; publishing is disabled"})
        with events.transaction(job_id, "job.state", {"state": "Done"}):
            store.update_job(job_id, state="Done", finished_at=now())
        return
    try:
        pr_url = await open_pr(
            job=job, title=report.title,
            body=report.body or render_fallback_body(job.goal, verified, discarded, uncovered),
            diffs=diffs, settings=settings,
        )
    except PrincipalError as exc:
        events.emit(job_id, "pr.publish_failed", {"reason": exc.message})

    with events.transaction(job_id, "job.state", {"state": "Done"}):
        store.update_job(job_id, state="Done", pr_url=pr_url, finished_at=now())
    if pr_url:
        events.emit(job_id, "pr.opened", {"url": pr_url})


# ------------------------------------------------------------------ helpers ---


def _load_winning_diffs(store: Store, settings: Settings, job_id: str, verified: list[Task]) -> dict[str, str]:
    out: dict[str, str] = {}
    for t in verified:
        art = store.q1(
            "SELECT * FROM artifact WHERE attempt_id = ? AND kind = 'diff' ORDER BY rowid DESC LIMIT 1",
            (t.winning_attempt_id,),
        )
        if art is None:
            continue
        out[t.target_file] = (settings.run_dir(job_id) / art["path"]).read_text(encoding="utf-8")
    return out


def _read_artifact_text(store: Store, settings: Settings, job_id: str, *, kind: str,
                         path: str | None, first: bool = False) -> str | None:
    if path is not None:
        row = store.q1(
            "SELECT * FROM artifact WHERE job_id = ? AND kind = ? AND path = ?", (job_id, kind, path)
        )
    else:
        order = "ORDER BY rowid" if first else "ORDER BY rowid DESC"
        row = store.q1(f"SELECT * FROM artifact WHERE job_id = ? AND kind = ? {order} LIMIT 1",
                        (job_id, kind))
    if row is None:
        return None
    return (settings.run_dir(job_id) / row["path"]).read_text(encoding="utf-8")


def _baseline_collected(store: Store, settings: Settings, job_id: str) -> int | None:
    text = _read_artifact_text(store, settings, job_id, kind="test_log", path="baseline_report.json")
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data.get("summary", {}).get("collected")


def _reextract_exports(
    snapshot: Path, verified: list[Task], diff_by_file: dict[str, str],
) -> dict[str, set[str]]:
    """Apply each verified task's winning diff to the pristine snapshot text and
    re-parse the result with tree-sitter to recover the post-patch export set.

    This is what the public API delta check compares against baseline — done
    from artifacts already on disk, without a second sandbox trip.
    """
    import unidiff

    out: dict[str, set[str]] = {}
    for t in verified:
        diff_text = diff_by_file.get(t.target_file)
        lang = lang_of(t.target_file)
        if diff_text is None or lang is None:
            out[t.target_file] = set()
            continue
        try:
            patch = unidiff.PatchSet(diff_text)
        except unidiff.UnidiffParseError:
            out[t.target_file] = set()
            continue
        matching = list(patch)
        if not matching:
            out[t.target_file] = set()
            continue
        original_path = snapshot / t.target_file
        try:
            original = original_path.read_text(encoding="utf-8") if original_path.exists() else ""
            result = apply_to_text(original, matching[0])
            parsed = parse_source(result.new_text.encode("utf-8"), lang, t.target_file)
            out[t.target_file] = {d.name for d in parsed.definitions if d.exported}
        except Exception:  # noqa: BLE001
            out[t.target_file] = set()
    return out


def _uncovered_files(store: Store, graph_id: str, files: set[str]) -> list[str]:
    out = []
    for f in sorted(files):
        symbols = store.symbols_in_files(graph_id, {f})
        if not symbols:
            out.append(f)
            continue
        covered = store.tests_covering(graph_id, {s.id for s in symbols})
        if not covered:
            out.append(f)
    return out
