"""The seven endpoints, plus the debug one that earns its place on camera."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Header, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from principal.api.schemas import (
    CreateJobRequest,
    CreateJobResponse,
    JobBudget,
    JobCounts,
    JobStatusResponse,
    RadiusSummary,
    RateLimitInfo,
)
from principal.api.stream import job_event_stream
from principal.errors import PrincipalError
from principal.graph.radius import blast_radius, find_target
from principal.orchestrator.job import run_job

router = APIRouter()


def _state(request: Request):
    return request.app.state


@router.get("/healthz")
async def healthz(request: Request):
    """Liveness for container hosts, and a one-request answer to "what is this
    process actually configured to talk to" — which is the first thing anyone
    running the hosted demo wants to know."""
    app = _state(request)
    settings = app.settings
    return {
        "status": "ok",
        "sandbox": type(app.sandbox).__name__,
        "inference_configured": bool(settings.nebius_api_key),
        "sandboxes_configured": bool(settings.nebius_api_key and settings.nebius_project_id),
        "publishing_configured": bool(settings.github_token and settings.principal_fork_repo),
        "models": {
            "nano": settings.principal_model_nano,
            "super": settings.principal_model_super,
            "ultra": settings.principal_model_ultra,
        },
    }


@router.post("/jobs", response_model=CreateJobResponse, status_code=202)
async def create_job(body: CreateJobRequest, request: Request):
    app = _state(request)
    settings = app.settings
    job = app.store.create_job(
        repo_url=body.repo_url, commit_sha=body.commit_sha, goal=body.goal,
        target_fqn=body.target_fqn, token_budget=body.token_budget or settings.token_budget_default,
        tunables=settings.recorded_tunables(),
    )
    if body.max_tasks:
        settings = settings.model_copy(update={"max_tasks": body.max_tasks})

    async def _runner() -> None:
        if body.dry_run:
            await _run_plan_only(job.id, app)
        else:
            await run_job(job.id, store=app.store, events=app.events, sandbox=app.sandbox,
                           models=app.models, settings=settings)

    task = asyncio.create_task(_runner())
    app.background_tasks.add(task)
    task.add_done_callback(app.background_tasks.discard)

    return CreateJobResponse(job_id=job.id, stream=f"/jobs/{job.id}/events")


async def _run_plan_only(job_id: str, app) -> None:
    from principal.orchestrator.job import _ingest_and_baseline, _map, _plan  # noqa: PLC0415

    try:
        await _ingest_and_baseline(job_id, app.store, app.events, app.sandbox, app.settings)
        graph_id = await _map(job_id, app.store, app.events, app.settings)
        await _plan(job_id, app.store, app.events, app.models, graph_id, app.settings)
        with app.events.transaction(job_id, "job.state", {"state": "Done"}) as _:
            from principal.db.store import now
            app.store.update_job(job_id, state="Done", stop_reason="dry_run", finished_at=now())
    except PrincipalError as exc:
        from principal.db.store import now
        with app.events.transaction(job_id, "job.state", {"state": "Aborted", "reason": exc.message}):
            app.store.update_job(job_id, state="Aborted", stop_reason=exc.message, finished_at=now())


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str, request: Request):
    app = _state(request)
    job = app.store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")

    tasks = app.store.tasks_of(job_id)
    settled = [t for t in tasks if t.state in {"verified", "discarded"}]
    verified = [t for t in tasks if t.state == "verified"]
    discarded = [t for t in tasks if t.state == "discarded"]

    radius = None
    radius_row = app.store.q1(
        "SELECT payload FROM event WHERE job_id = ? AND kind = 'radius.computed' ORDER BY seq DESC LIMIT 1",
        (job_id,),
    )
    if radius_row:
        p = json.loads(radius_row["payload"])
        radius = RadiusSummary(files=len(p.get("files", [])), tests=len(p.get("tests", [])),
                                unresolved=len(p.get("unresolved", [])))

    return JobStatusResponse(
        job_id=job.id, state=job.state, stop_reason=job.stop_reason, baseline_image=job.baseline_image,
        counts=JobCounts(tasks=len(tasks), settled=len(settled), verified=len(verified),
                          discarded=len(discarded)),
        budget=JobBudget(limit=job.token_budget, spent=job.tokens_spent,
                          refusals=app.models.budget_refusals),
        rate_limit=RateLimitInfo(remaining_requests=app.models.rate_headroom.remaining_requests,
                                  remaining_tokens=app.models.rate_headroom.remaining_tokens),
        radius=radius, pr_url=job.pr_url,
    )


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request, last_event_id: str = Header(default="0", alias="Last-Event-ID")):
    app = _state(request)
    if app.store.get_job(job_id) is None:
        raise HTTPException(404, "job not found")
    try:
        after = int(last_event_id or 0)
    except ValueError:
        after = 0
    return EventSourceResponse(job_event_stream(app.events, job_id, after, app.settings))


@router.get("/jobs/{job_id}/tree")
async def job_tree(job_id: str, request: Request):
    """Walks attempt.parent_image and attempt.result_image. No extra bookkeeping
    is needed because the sandbox state graph is content-addressed and those two
    columns already describe it."""
    app = _state(request)
    if app.store.get_job(job_id) is None:
        raise HTTPException(404, "job not found")
    attempts = app.store.attempts_of(job_id)
    nodes = [
        {"attempt_id": a.id, "task_id": a.task_id, "parent_image": a.parent_image,
         "result_image": a.result_image, "verdict": a.verdict, "kind": a.kind, "n": a.n}
        for a in attempts
    ]
    return {"job_id": job_id, "nodes": nodes}


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str, request: Request):
    app = _state(request)
    art = app.store.get_artifact(artifact_id)
    if art is None:
        raise HTTPException(404, "artifact not found")
    path = app.settings.run_dir(art["job_id"]) / art["path"]
    if not path.exists():
        raise HTTPException(404, "artifact file missing on disk")
    return {
        "id": art["id"], "job_id": art["job_id"], "kind": art["kind"], "sha256": art["sha256"],
        "content": path.read_text(encoding="utf-8", errors="replace"),
    }


@router.post("/jobs/{job_id}/cancel", status_code=202)
async def cancel_job(job_id: str, request: Request):
    app = _state(request)
    if app.store.get_job(job_id) is None:
        raise HTTPException(404, "job not found")
    from principal.db.store import now

    live = app.store.live_operations(job_id)
    for _attempt_id, op_id in live:
        try:
            await app.sandbox.cancel(op_id)
        except Exception:  # noqa: BLE001, S110
            pass
    with app.events.transaction(job_id, "job.stopped", {"reason": "cancelled"}):
        app.store.update_job(job_id, state="NoPR", stop_reason="cancelled", finished_at=now())
    return {"status": "cancelling"}


@router.get("/debug/blast-radius")
async def debug_blast_radius(repo_url: str, commit_sha: str, target_fqn: str, request: Request):
    """No job required. Being able to show the blast radius of a symbol in two
    seconds is the cheapest way to make the static-analysis claim concrete on
    camera."""
    app = _state(request)
    from principal.graph.build import build as build_graph
    from principal.sandbox.baseline import ensure_snapshot

    # Both find_target (TARGET_NOT_FOUND) and blast_radius (RADIUS_TOO_LARGE) can
    # raise PrincipalError. Neither is caught here: the app-level exception
    # handler turns each into the documented error envelope with the right status
    # code, which is one implementation instead of two ad hoc translations.
    snapshot = await ensure_snapshot(repo_url, commit_sha, app.settings.principal_snapshots_dir)
    stats = build_graph(app.store, snapshot, repo_url, commit_sha)
    target = find_target(app.store, stats.graph_id, target_fqn)
    radius = blast_radius(app.store, stats.graph_id, target, max_depth=app.settings.radius_max_depth,
                           cap=app.settings.radius_file_cap)
    return radius.to_payload()
