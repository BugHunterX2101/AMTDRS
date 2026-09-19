"""Runs the task set across the requested arms and prints the honest scorecard.

Each (task, arm) pair gets its own database, run directory and cache, so the
arms cannot see each other's work and a crashed run cannot corrupt another
arm's numbers. The pipeline itself is real `run_job` — arm A is not a
special-cased shortcut, it is the same orchestrator with
`bench_accept_without_gates` set, which is what makes the comparison honest:
every arm pays for the same baseline build, runs in the same kind of sandbox,
and is measured by the same scorer.

Requires Nebius credentials. There is no offline mode for this script on
purpose — the numbers this produces are only meaningful against the real
platform, and a benchmark that silently ran against a stub would be exactly
the kind of dishonesty this whole project is built to avoid.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import sys
import time
from pathlib import Path

from bench.arms import Arm
from bench.arms import parse as parse_arms
from bench.score import TaskOutcome, render, score, write_report
from principal.config import Settings, Tier, get_settings
from principal.db.events import EventLog
from principal.db.store import Store
from principal.errors import PrincipalError
from principal.models.cache import DiskCache
from principal.models.client import ModelClient

logger = logging.getLogger("bench")

ROOT = Path(__file__).resolve().parent.parent


def load_tasks(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    tasks = data["tasks"]
    if not tasks:
        raise SystemExit(f"{path} defines no tasks")
    return tasks


async def run_one(task: dict, arm: Arm, base_settings: Settings, work_dir: Path) -> TaskOutcome:
    run_id = f"{task['id']}__{arm.name}"
    work_dir.mkdir(parents=True, exist_ok=True)
    settings = arm.settings(base_settings).model_copy(
        update={
            "principal_db": work_dir / f"{run_id}.db",
            "principal_runs_dir": work_dir / run_id / "runs",
            "principal_snapshots_dir": work_dir / "snapshots",  # shared: content-addressed by commit
            "principal_cache_dir": work_dir / run_id / "cache",
        }
    )

    store = Store(settings.principal_db)
    events = EventLog(store, settings.principal_runs_dir)
    events.bind_loop()

    # Without this, a reasoning model's empty `content` (see docs/FEEDBACK.md,
    # item 2) would read as "the model produced nothing" and silently deflate
    # every arm's score for a reason that has nothing to do with the arm being
    # measured.
    capability = await _probe_once(settings)

    models = ModelClient(
        store=store, base_url=settings.principal_inference_base, api_key=settings.nebius_api_key,
        model_ids={t: settings.model_id(t) for t in Tier}, capability=capability,
        cache=DiskCache(settings.principal_cache_dir, enabled=settings.principal_model_cache),
    )

    sandbox = _make_sandbox(settings)

    job = store.create_job(
        repo_url=task["repo_url"], commit_sha=task["commit_sha"], goal=task["goal"],
        target_fqn=task.get("target_fqn"), token_budget=settings.token_budget_default,
        tunables={**settings.recorded_tunables(), "arm": arm.name, "task_id": task["id"]},
    )

    started = time.monotonic()
    error_code: str | None = None
    note = ""
    try:
        from principal.orchestrator.job import run_job

        await run_job(job.id, store=store, events=events, sandbox=sandbox, models=models,
                      settings=settings)
    except PrincipalError as exc:
        error_code = exc.code.value
        note = exc.message
    except Exception as exc:  # noqa: BLE001
        error_code = "SANDBOX_OP_FAILED"
        note = f"unhandled: {exc}"
    duration = time.monotonic() - started

    final = store.get_job(job.id)
    attempts = store.attempts_of(job.id)
    tasks_ = store.tasks_of(job.id)

    verified = _independently_verify(final, tasks_, attempts, bench_ungated=arm.name == "A")

    outcome = TaskOutcome(
        task_id=task["id"], arm=arm.name, verified=verified, error_code=error_code,
        duration_s=round(duration, 2), tokens=final.tokens_spent if final else 0,
        sandbox_runs=len(attempts), candidates=arm.candidates,
        repairs=sum(1 for a in attempts if a.kind == "repairer"),
        pr_opened=bool(final and final.pr_url), note=note or (final.stop_reason if final else ""),
    )
    store.close()
    return outcome


def _independently_verify(final, tasks_, attempts, *, bench_ungated: bool) -> bool:
    """The number that actually gets scored, computed the same way regardless
    of arm. For a gated arm this is just "did the job finish Done" — the
    orchestrator already refused to say so otherwise. For the ungated arm it is
    recomputed from the attempts' *true* exit codes, because the job accepted
    the winning candidate without checking and its own verdict cannot be
    trusted for scoring."""
    if final is None or final.state != "Done":
        return False
    if not bench_ungated:
        return True

    winners = {t.winning_attempt_id for t in tasks_ if t.winning_attempt_id}
    if not winners:
        return False
    by_id = {a.id: a for a in attempts}
    return all((by_id[w].exit_code == 0) for w in winners if w in by_id)


_capability_cache: dict | None = None


async def _probe_once(settings: Settings) -> dict:
    """Run once per harness invocation and reused across every (task, arm) run
    — the three model ids do not change between arms, and re-probing per run
    would be nine avoidable round trips for a nine-cell A×B×C grid."""
    global _capability_cache
    if _capability_cache is not None:
        return _capability_cache
    try:
        from openai import AsyncOpenAI

        from principal.models.probe import probe_all

        client = AsyncOpenAI(base_url=settings.principal_inference_base, api_key=settings.nebius_api_key)
        wanted = [settings.principal_model_nano, settings.principal_model_super, settings.principal_model_ultra]
        caps = await probe_all(client, wanted)
        _capability_cache = {m: c.best_protocol for m, c in caps.items()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("capability probe failed, defaulting to text protocol: %s", exc)
        _capability_cache = {}
    return _capability_cache


def _make_sandbox(settings: Settings):
    if settings.nebius_api_key and settings.nebius_project_id:
        from principal.sandbox.client import ContreeSandbox

        return ContreeSandbox(
            api_key=settings.nebius_api_key, project_id=settings.nebius_project_id,
            base_url=settings.principal_sandbox_base, max_inflight=settings.max_inflight_ops,
        )
    raise SystemExit(
        "bench requires real Nebius credentials (NEBIUS_API_KEY and NEBIUS_PROJECT_ID). "
        "Running the benchmark against FakeSandbox would produce numbers about this laptop, "
        "not about the platform, and this script refuses to report those as if they were the same thing."
    )


async def main_async(args: argparse.Namespace) -> int:
    tasks = load_tasks(Path(args.tasks))
    arms = parse_arms(args.arms)
    base_settings = get_settings()
    work_dir = Path(args.out)
    if args.clean and work_dir.exists():
        shutil.rmtree(work_dir)

    outcomes = []
    for task in tasks:
        for arm in arms:
            print(f"[{task['id']} / arm {arm.name}] {arm.description}", file=sys.stderr)
            outcome = await run_one(task, arm, base_settings, work_dir)
            status = "VERIFIED" if outcome.verified else ("infra" if outcome.infrastructure else "failed")
            print(f"  -> {status}  {outcome.duration_s}s  {outcome.tokens} tokens", file=sys.stderr)
            outcomes.append(outcome)

    arm_scores = score(outcomes)
    report_path = work_dir / "report.json"
    write_report(arm_scores, outcomes, report_path)

    print()
    print(render(arm_scores))
    print()
    print(f"full report: {report_path}")
    return 0


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description="Run the Principal benchmark arms.")
    parser.add_argument("--tasks", default=str(ROOT / "bench" / "tasks.json"))
    parser.add_argument("--arms", default="A,B,C", help="comma-separated: any of A,B,C")
    parser.add_argument("--out", default=str(ROOT / "runs" / "bench"))
    parser.add_argument("--clean", action="store_true", help="wipe --out before running")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
