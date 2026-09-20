"""The job state machine. From a goal to a PR, or to a reported reason it stopped.

Four layers, one rule: nothing moves from left to right without passing a test.
NoPR is a success state, not a failure state: it means the system did its job and
correctly declined to ship.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from principal.agents.characteriser import characterise as run_characteriser
from principal.agents.planner import plan as run_planner
from principal.agents.reporter import compose_report
from principal.config import Settings
from principal.db.events import EventLog
from principal.db.store import Store, SymbolRow, Task, now
from principal.errors import Code, DiffUnparseable, NoSafetyNet, PrincipalError
from principal.gates.apply import apply_to_text
from principal.gates.behaviour import evaluate_behaviour
from principal.gates.characterise import (
    DEFAULT_MUTATION_FLOOR,
    characterisation_path,
    establish_safety_net,
    needs_characterisation,
)
from principal.gates.security import check_security
from principal.graph.build import build as build_graph
from principal.graph.build import ingest_coverage
from principal.graph.embed import ConventionIndex
from principal.graph.languages import lang_of
from principal.graph.parse import CallSite, parse_source
from principal.graph.radius import BlastRadius, find_target
from principal.models.client import ModelClient
from principal.orchestrator.budget import exceeds_discard_cap
from principal.orchestrator.cleanup import plan_cleanup
from principal.orchestrator.ordering import order_waves
from principal.orchestrator.scheduler import TaskContext, WaveRunner
from principal.publish.github import open_pr
from principal.publish.prbody import render_fallback_body
from principal.routines.registry import routine_for
from principal.routines.relocation import moved_symbols
from principal.sandbox.baseline import build_baseline, clone_source, ensure_snapshot
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
        target = _resolve_target(store, job_id, graph_id)
        await _characterise(job_id, store, events, sandbox, models, graph_id, target, settings)
        radius = await _plan(job_id, store, events, models, graph_id, target, settings)
        await _execute(job_id, store, events, sandbox, models, graph_id, radius, settings)
        await _cleanup(job_id, store, events, sandbox, models, graph_id, settings)
        await _integrate_and_publish(job_id, store, events, sandbox, models, graph_id, radius, settings)
    except _Stopped:
        pass
    except NoSafetyNet as exc:
        logger.info("job %s stopped: %s", job_id, exc)
        with events.transaction(job_id, "job.state", {"state": "NoSafetyNet", "reason": exc.message}):
            store.update_job(job_id, state="NoSafetyNet", stop_reason=exc.message, finished_at=now())
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

    snapshot = await ensure_snapshot(job.repo_url, job.commit_sha, settings.principal_snapshots_dir)

    with events.transaction(job_id, "job.state", {"state": "Baselining"}):
        store.update_job(job_id, state="Baselining")

    def on_op(op_id: str) -> None:
        events.emit(job_id, "baseline.operation", {"operation_id": op_id})

    baseline = await build_baseline(
        sandbox, base_image=settings.principal_base_image, repo_url=job.repo_url,
        clone_url=clone_source(job.repo_url, snapshot),
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


def _resolve_target(store: Store, job_id: str, graph_id: str) -> SymbolRow:
    job = store.get_job(job_id)
    assert job is not None
    if not job.target_fqn:
        raise PrincipalError(Code.TARGET_NOT_FOUND, "no target_fqn was given")
    return find_target(store, graph_id, job.target_fqn)


async def _characterise(
    job_id: str, store: Store, events: EventLog, sandbox: SandboxClient, models: ModelClient,
    graph_id: str, target: SymbolRow, settings: Settings,
) -> None:
    """Establish an oracle where the repository has none.

    Fires only when the target is genuinely uncovered — on a well-tested
    repository this reads zero rows and returns immediately, so the phase costs
    nothing in the common case. A discarded or insufficient result raises
    `NoSafetyNet`, which `run_job` treats as its own terminal state: correct
    refusal, not a system failure.
    """
    job = store.get_job(job_id)
    assert job is not None
    covering = store.tests_covering(graph_id, {target.id})
    if not needs_characterisation(len(covering)):
        return

    with events.transaction(job_id, "job.state", {"state": "Characterising"}):
        store.update_job(job_id, state="Characterising")

    snapshot = await ensure_snapshot(job.repo_url, job.commit_sha, settings.principal_snapshots_dir)
    source = (snapshot / target.path).read_text(encoding="utf-8")
    call_sites = [
        CallSite(callee_name=r["callee_name"], receiver=None, line=r["line"], enclosing=None)
        for r in store.q(
            "SELECT line, callee_name FROM call_edge WHERE graph_id = ? AND callee_symbol_id = ?"
            " ORDER BY line", (graph_id, target.id),
        )
    ]

    test_source, completion = await run_characteriser(
        models, job_id=job_id, target_fqn=target.fqn, target_path=target.path,
        signature=target.signature or "", source=source, call_sites=call_sites,
    )
    store.add_tokens(job_id, completion.prompt_tokens + completion.completion_tokens)

    test_path = characterisation_path(target.fqn)
    assert job.baseline_image is not None
    c0_image = await sandbox.from_uuid(job.baseline_image)

    def on_op(op_id: str) -> None:
        events.emit(job_id, "characterise.operation", {"operation_id": op_id})

    result = await establish_safety_net(
        sandbox, c0_image, target_path=target.path, target_source=source,
        line_start=target.line_start, line_end=target.line_end, test_source=test_source,
        test_path=test_path, floor=DEFAULT_MUTATION_FLOOR, timeout_s=max(900, settings.sandbox_timeout_s * 2),
        on_operation=on_op,
    )

    with events.transaction(job_id, "characterise.result", result.to_payload()):
        pass

    if not result.ok:
        raise NoSafetyNet(target.fqn, result.score, DEFAULT_MUTATION_FLOOR)

    store.put_artifact(
        job_id=job_id, kind="characterisation", rel_path=test_path.replace("/", "_") + ".py",
        content=result.test_source or "", base_dir=settings.run_dir(job_id),
    )
    _register_characterisation(store, graph_id, target, test_path, result.test_source or "")


def _register_characterisation(
    store: Store, graph_id: str, target: SymbolRow, test_path: str, test_source: str,
) -> None:
    """Insert the generated suite into test_edge as a `characterisation` source.

    This is the entire integration with gate 3: `select_tests` already prefers
    coverage-derived edges and falls back to import-derived ones for any symbol
    that has rows in `test_edge`, so once these rows exist the generated tests
    are picked up by code that has not changed at all.
    """
    file_id = store.q1("SELECT id FROM file WHERE graph_id = ? AND path = ?", (graph_id, test_path))
    if file_id is None:
        sha = hashlib.sha256(test_source.encode()).hexdigest()
        cur = store.exec(
            "INSERT INTO file (graph_id, path, lang, sha256, is_test) VALUES (?,?,?,?,1)",
            (graph_id, test_path, "python", sha),
        )
        fid = cur.lastrowid
    else:
        fid = file_id["id"]

    for name in sorted(set(re.findall(r"^def (test_\w+)", test_source, re.MULTILINE))):
        nodeid = f"{test_path}::{name}"
        store.exec(
            "INSERT INTO test_edge (graph_id, test_file_id, symbol_id, nodeid, source)"
            " VALUES (?,?,?,?,'characterisation')",
            (graph_id, fid, target.id, nodeid),
        )


async def _plan(job_id: str, store: Store, events: EventLog, models: ModelClient, graph_id: str,
                 target: SymbolRow, settings: Settings) -> BlastRadius:
    job = store.get_job(job_id)
    assert job is not None
    if not job.target_fqn:
        raise PrincipalError(Code.TARGET_NOT_FOUND, "no target_fqn was given")

    routine = routine_for(job.routine)
    radius = routine.seed_radius(
        store, graph_id, target, max_depth=settings.radius_max_depth, cap=settings.radius_file_cap,
    )

    with events.transaction(job_id, "radius.computed", {**radius.to_payload(), "routine": routine.kind.value}):
        pass

    with events.transaction(job_id, "job.state", {"state": "Planning"}):
        store.update_job(job_id, state="Planning")

    plan_result = await run_planner(
        models, job_id=job_id, goal=job.goal, target_fqn=job.target_fqn, radius=radius,
        max_depth=settings.radius_max_depth, max_tasks=settings.max_tasks,
        tier=settings.principal_planner_tier, prompt_template=routine.prompt_text,
    )

    for t in plan_result.tasks:
        store.create_task(
            job_id=job_id, seq=t.seq, target_file=t.target_file, instruction=t.instruction,
            acceptance=t.acceptance, depends_on=t.depends_on, declared_removals=t.declared_removals,
            kind="refactor",
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


_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".mypy_cache", ".pytest_cache", ".ruff_cache"}


def _build_patched_tree(
    snapshot: Path, touched: set[str], verified: list[Task], diffs_by_task: dict[str, str],
) -> dict[str, str]:
    """Every Python file's text, patched where the refactor touched it.

    `orphaned_symbols` has to see the whole tree, not only the touched files: a
    reference to a name Principal is considering deleting can live anywhere, and
    reading only the files that changed would make everything outside them
    invisible — precisely the direction that deletes something live.
    """
    by_file: dict[str, list[Task]] = {}
    for t in verified:
        if t.target_file in touched:
            by_file.setdefault(t.target_file, []).append(t)

    tree: dict[str, str] = {}
    for path in snapshot.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        rel = path.relative_to(snapshot).as_posix()
        try:
            base_text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if rel in touched:
            base_text, _ = _apply_task_diffs(base_text, by_file.get(rel, []), diffs_by_task)
        tree[rel] = base_text
    return tree


def _materialize_patched_snapshot(
    settings: Settings, job_id: str, snapshot: Path, touched: set[str], tree: dict[str, str],
) -> Path:
    """A throwaway copy of the snapshot with the touched files overwritten by
    their post-refactor content, so the cleanup Coder's context pack shows the
    tree it will actually be integrated into rather than the pristine baseline.
    """
    import shutil

    dest = settings.run_dir(job_id) / "cleanup_snapshot"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(snapshot, dest, ignore=shutil.ignore_patterns(*_SKIP_DIRS))
    for rel in touched:
        text = tree.get(rel)
        if text is None:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return dest


async def _cleanup(job_id: str, store: Store, events: EventLog, sandbox: SandboxClient,
                    models: ModelClient, graph_id: str, settings: Settings) -> None:
    """The second planning pass. Deterministic detection, then the same Coder,
    the same gates, the same candidate race as any other task.

    Nothing here decides what is dead — `plan_cleanup` does, from a reparse and
    a reference count, before any sandbox is touched. This function's only job
    is to run the tasks that pass returned and get their diffs onto the same
    record `_integrate_and_publish` already reads.
    """
    job = store.get_job(job_id)
    assert job is not None
    tasks = store.tasks_of(job_id)
    verified = sorted((t for t in tasks if t.state == "verified"), key=lambda t: t.seq)
    if not verified:
        return  # nothing was verified; _integrate_and_publish reports that outcome

    diffs_by_task = _load_winning_diffs(store, settings, job_id, verified)
    touched = {t.target_file for t in verified if t.id in diffs_by_task}
    if not touched:
        return

    snapshot = await ensure_snapshot(job.repo_url, job.commit_sha, settings.principal_snapshots_dir)
    tree = _build_patched_tree(snapshot, touched, verified, diffs_by_task)

    cleanup_tasks = plan_cleanup(store, graph_id, tree, touched)
    if not cleanup_tasks:
        events.emit(job_id, "cleanup.result", {"orphans_found": 0})
        return

    with events.transaction(job_id, "job.state", {"state": "CleanupPlanning"}):
        store.update_job(job_id, state="CleanupPlanning")
    events.emit(job_id, "cleanup.planned", {
        "tasks": [
            {"target_file": ct.target_file, "unused_imports": list(ct.unused_imports),
             "orphaned_symbols": list(ct.orphaned_symbols)}
            for ct in cleanup_tasks
        ],
    })

    with events.transaction(job_id, "job.state", {"state": "CleanupExecuting"}):
        store.update_job(job_id, state="CleanupExecuting")

    assert job.baseline_image is not None
    c0 = await sandbox.from_uuid(job.baseline_image)

    def on_op(op_id: str) -> None:
        events.emit(job_id, "cleanup.operation", {"operation_id": op_id})

    # A probe integration, not the final one: it exists only to produce an image
    # the cleanup Coder can fork from and test against, one that already has the
    # refactor applied. `_integrate_and_publish` still runs its own integration
    # from pristine C0 at the end with the full diff set, refactor and cleanup
    # together — that run, not this one, is the number that gets published.
    ordered_diffs = [diffs_by_task[t.id] for t in verified if t.id in diffs_by_task]
    combined = await run_integration(
        sandbox, c0, ordered_diffs, timeout_s=max(900, settings.sandbox_timeout_s * 2), on_operation=on_op,
    )
    if not combined.green:
        events.emit(job_id, "cleanup.skipped", {
            "reason": "the refactor diffs did not pass together, so there is nothing safe to clean up against",
        })
        return

    combined_image = await sandbox.from_uuid(combined.image) if combined.image else c0
    patched_snapshot = _materialize_patched_snapshot(settings, job_id, snapshot, touched, tree)
    convention_index = ConventionIndex(patched_snapshot, sorted(touched)) if touched else None

    ctx = TaskContext(
        job_id=job_id, graph_id=graph_id, snapshot=patched_snapshot, c0_image=combined_image,
        radius_files=set(touched), store=store, events=events, models=models, sandbox=sandbox,
        convention_index=convention_index, task_timeout_s=settings.task_timeout_s,
        sandbox_timeout_s=settings.sandbox_timeout_s, max_repairs=settings.max_repairs,
        bench_accept_without_gates=settings.bench_accept_without_gates,
    )

    seq_base = max((t.seq for t in tasks), default=0) + 1
    created: list[Task] = []
    for i, ct in enumerate(cleanup_tasks):
        row = store.create_task(
            job_id=job_id, seq=seq_base + i, target_file=ct.target_file, instruction=ct.instruction,
            acceptance=ct.acceptance, depends_on=[], declared_removals=list(ct.names), kind="cleanup",
        )
        created.append(row)

    runner = WaveRunner(ctx=ctx, max_parallel=settings.max_parallel_tasks,
                         candidates_per_task=settings.candidates_per_task)
    await runner.run_waves([created])


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

    diffs_by_task = _load_winning_diffs(store, settings, job_id, verified)
    # `applied` is kept strictly parallel to `diffs`. A verified task whose diff
    # artifact could not be read is skipped from both, so the conflict index the
    # sandbox reports — which indexes the diffs it was handed — still names the
    # right task. Deriving one list from `verified` and indexing into the other
    # would drop an unrelated task the moment those two lengths diverge. Order
    # matters here beyond bookkeeping: `verified` is sorted by seq, and a cleanup
    # task's diff is only valid applied after the refactor diff on the same file
    # that precedes it in seq order.
    applied = [t for t in verified if t.id in diffs_by_task]
    diffs = [diffs_by_task[t.id] for t in applied]

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
        if 0 <= run.conflict_index < len(applied):
            dropped = applied.pop(run.conflict_index)
            verified = [t for t in verified if t.id != dropped.id]
            store.update_task(dropped.id, state="discarded",
                               discard_reason="dropped at integration: conflicted with another patch")
            diffs = [diffs_by_task[t.id] for t in applied]
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
    patched_exports = _reextract_exports(snapshot, verified, diffs_by_task)

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

    # Observability only, never a gate: for a relocation job, a declared removal
    # that reappears as an export somewhere else in the touched set is a move
    # that behaved the way a move should. One that does not reappear is not
    # necessarily wrong — the destination may not export it — but it is worth a
    # reviewer's attention, which is what the risk section is for.
    relocated = sorted(moved_symbols(sorted(declared_removals), patched_exports)) if job.routine == "relocation" else []

    with events.transaction(job_id, "integration.result", {
        "pass": behaviour.verdict.ok, "api_delta_ok": behaviour.api_delta_ok,
        "removed_undeclared": behaviour.removed_undeclared, "coverage_ok": behaviour.coverage_ok,
        "coverage_drops": behaviour.coverage_drops, "test_count_ok": behaviour.test_count_ok,
        "baseline_collected": behaviour.baseline_collected,
        "integration_collected": behaviour.integration_collected,
        "relocated_symbols": relocated,
    }):
        pass

    if not behaviour.verdict.ok:
        with events.transaction(job_id, "job.stopped", {"reason": behaviour.verdict.reason}):
            store.update_job(job_id, state="NoPR", stop_reason=behaviour.verdict.reason, finished_at=now())
        raise _Stopped(behaviour.verdict.reason)

    baseline_texts, patched_texts = _security_scan_texts(snapshot, touched_files, verified, diffs_by_task)
    security = check_security(baseline_texts, patched_texts)
    with events.transaction(job_id, "security.result", security.to_payload()):
        pass
    if security.introduced:
        with events.transaction(job_id, "job.stopped", {"reason": security.verdict.reason}):
            store.update_job(job_id, state="NoPR", stop_reason=security.verdict.reason, finished_at=now())
        raise _Stopped(security.verdict.reason)

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
    """Task id -> winning diff text.

    Keyed by task, not by target file: a cleanup task legitimately targets the
    same file as the refactor task that orphaned something in it, and a
    file-keyed dict would silently drop one of the two diffs. `verified` is
    expected sorted by seq, and seq order is what makes applying both diffs
    against pristine C0, in that order, produce the right final file — a
    cleanup task's diff was generated against the tree *after* the refactor
    diff, so it is only valid applied after it.
    """
    out: dict[str, str] = {}
    for t in verified:
        art = store.q1(
            "SELECT * FROM artifact WHERE attempt_id = ? AND kind = 'diff' ORDER BY rowid DESC LIMIT 1",
            (t.winning_attempt_id,),
        )
        if art is None:
            continue
        out[t.id] = (settings.run_dir(job_id) / art["path"]).read_text(encoding="utf-8")
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


def _apply_task_diffs(base_text: str, tasks_for_file: list[Task], diffs_by_task: dict[str, str]) -> tuple[str, bool]:
    """Apply every task's diff for one file, in the order given, onto `base_text`.

    Shared by every host-side reconstruction of "what does this file look like
    now": the API delta re-extraction, the security scan's before/after pair,
    and the cleanup pass's view of the tree. A file can carry more than one
    diff — a cleanup task frequently targets the same file as the refactor task
    that orphaned something in it — and each one after the first is only valid
    applied on top of the one before it, which is why order is the caller's
    responsibility (pass tasks already sorted by seq) rather than this
    function's.
    """
    import unidiff

    text = base_text
    applied_any = False
    for t in tasks_for_file:
        diff_text = diffs_by_task.get(t.id)
        if diff_text is None:
            continue
        try:
            matching = list(unidiff.PatchSet(diff_text))
            if not matching:
                continue
            text = apply_to_text(text, matching[0]).new_text
        except (unidiff.UnidiffParseError, DiffUnparseable):
            logger.warning("task %s: winning diff for %s could not be re-applied host-side", t.id, t.target_file)
            continue
        applied_any = True
    return text, applied_any


def _reextract_exports(
    snapshot: Path, verified: list[Task], diffs_by_task: dict[str, str],
) -> dict[str, set[str]]:
    """Apply every verified task's winning diff to the pristine snapshot text, IN
    SEQ ORDER, and re-parse the result with tree-sitter to recover the post-patch
    export set.

    This is what the public API delta check compares against baseline — done
    from artifacts already on disk, without a second sandbox trip.
    """
    by_file: dict[str, list[Task]] = {}
    for t in verified:
        by_file.setdefault(t.target_file, []).append(t)

    out: dict[str, set[str]] = {}
    for target_file, task_group in by_file.items():
        lang = lang_of(target_file)
        original_path = snapshot / target_file
        try:
            base_text = original_path.read_text(encoding="utf-8") if original_path.exists() else ""
        except OSError:
            base_text = ""

        text, applied_any = _apply_task_diffs(base_text, task_group, diffs_by_task)
        if not applied_any or lang is None:
            out[target_file] = set()
            continue
        try:
            parsed = parse_source(text.encode("utf-8"), lang, target_file)
            out[target_file] = {d.name for d in parsed.definitions if d.exported}
        except Exception:  # noqa: BLE001
            out[target_file] = set()
    return out


def _security_scan_texts(
    snapshot: Path, touched_files: set[str], verified: list[Task], diffs_by_task: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Before and after text for gate 5, restricted to the files this job
    touched. Scanning the whole repository would answer "is this repository
    secure", which is not this gate's job and would drown a real finding under
    every pre-existing one in a legacy codebase; scanning only what changed
    answers "did this change make anything worse", which is the differential
    claim gate 5 actually makes.
    """
    by_file: dict[str, list[Task]] = {}
    for t in verified:
        if t.target_file in touched_files:
            by_file.setdefault(t.target_file, []).append(t)

    before: dict[str, str] = {}
    after: dict[str, str] = {}
    for path in sorted(touched_files):
        original_path = snapshot / path
        try:
            original = original_path.read_text(encoding="utf-8") if original_path.exists() else ""
        except OSError:
            original = ""
        before[path] = original
        after[path], _ = _apply_task_diffs(original, by_file.get(path, []), diffs_by_task)
    return before, after


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
