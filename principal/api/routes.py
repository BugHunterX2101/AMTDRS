"""The seven endpoints, plus the debug one that earns its place on camera."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
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
from principal.graph.radius import blast_radius, find_target
from principal.orchestrator.evidence import build_evidence, build_zip, provenance_of, render_markdown
from principal.orchestrator.job import run_job, run_plan_only
from principal.orchestrator.signals import execution_provenance, gather_signals
from principal.outcome import classify, is_terminal

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
    backends = getattr(app, "backends", {})
    inference_verified = bool(backends.get("inference_verified"))
    sandbox_verified = bool(backends.get("sandbox_verified"))
    return {
        "status": "ok",
        "sandbox": type(app.sandbox).__name__,
        "inference_configured": bool(settings.nebius_api_key),
        "sandboxes_configured": bool(settings.nebius_api_key and settings.nebius_project_id),
        # "Configured" only says a credential is present. "Verified" says a call
        # to Token Factory / Sandboxes actually succeeded at startup.
        "inference_verified": inference_verified,
        "sandbox_verified": sandbox_verified,
        "live": inference_verified and sandbox_verified,
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
    tunables = settings.recorded_tunables()
    tunables["execution_provenance"] = execution_provenance(
        app.sandbox, app.models, settings, verified=getattr(app, "backends", None),
    )
    job = app.store.create_job(
        repo_url=body.repo_url, commit_sha=body.commit_sha, goal=body.goal,
        target_fqn=body.target_fqn, token_budget=body.token_budget or settings.token_budget_default,
        tunables=tunables, routine=body.routine,
    )
    if body.max_tasks:
        settings = settings.model_copy(update={"max_tasks": body.max_tasks})

    async def _runner() -> None:
        if body.dry_run:
            await _run_plan_only(job.id, app, settings)
        else:
            await run_job(job.id, store=app.store, events=app.events, sandbox=app.sandbox,
                           models=app.models, settings=settings)

    # Keyed by job, so cancel can stop the coroutine and not only relabel the row.
    task = asyncio.create_task(_runner())
    app.background_tasks[job.id] = task
    task.add_done_callback(lambda _t, key=job.id: app.background_tasks.pop(key, None))

    return CreateJobResponse(job_id=job.id, stream=f"/jobs/{job.id}/events")


async def _run_plan_only(job_id: str, app, settings) -> None:
    # `settings` is the per-job copy, so a request's `max_tasks` reaches the planner.
    await run_plan_only(job_id, store=app.store, events=app.events, sandbox=app.sandbox,
                        models=app.models, settings=settings)


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
                          refusals=app.models.refusals_for(job_id)),
        rate_limit=RateLimitInfo(remaining_requests=app.models.rate_headroom.remaining_requests,
                                  remaining_tokens=app.models.rate_headroom.remaining_tokens),
        radius=radius, pr_url=job.pr_url,
        outcome=(verdict.to_payload() if (verdict := classify(gather_signals(app.store, job_id))) else None),
        provenance=provenance_of(app.store, job_id),
    )


@router.get("/jobs/{job_id}/evidence")
async def job_evidence(job_id: str, request: Request):
    """Everything needed to judge the run, as JSON, built from the stored record."""
    app = _state(request)
    evidence = build_evidence(app.store, app.settings, job_id)
    if evidence is None:
        raise HTTPException(404, "job not found")
    return evidence


@router.get("/jobs/{job_id}/evidence.md", response_class=PlainTextResponse)
async def job_evidence_markdown(job_id: str, request: Request):
    app = _state(request)
    evidence = build_evidence(app.store, app.settings, job_id)
    if evidence is None:
        raise HTTPException(404, "job not found")
    return PlainTextResponse(render_markdown(evidence), media_type="text/markdown; charset=utf-8")


@router.get("/jobs/{job_id}/evidence.zip")
async def job_evidence_zip(job_id: str, request: Request):
    app = _state(request)
    evidence = build_evidence(app.store, app.settings, job_id)
    if evidence is None:
        raise HTTPException(404, "job not found")
    return Response(
        build_zip(app.store, app.settings, evidence), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="evidence-{job_id}.zip"'},
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
    job = app.store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    # A finished run's result is the record. Cancelling it would relabel a Done
    # run as NoPR "cancelled" after the fact.
    if is_terminal(job.state):
        return JSONResponse(status_code=409, content={"error": {
            "code": "JOB_FINISHED", "message": f"job already finished in state {job.state}",
            "retryable": False, "job_id": job_id,
        }})
    from principal.db.store import now

    # Stop the coroutine first. Left running, it went on to write Done or Aborted
    # over the cancellation below.
    task = app.background_tasks.get(job_id)
    if task is not None and not task.done():
        task.cancel()

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
