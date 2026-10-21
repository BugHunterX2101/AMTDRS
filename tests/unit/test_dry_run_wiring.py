"""A dry run ingests, maps and plans, then stops in a terminal state.

The API and the CLI both run it through `job.run_plan_only`. The CLI's own copy
never wrote a terminal state, so a dry run sat in Planning with no outcome, and
a planned-only run must never be classified as verified.
"""

from __future__ import annotations

from principal.api.routes import _run_plan_only
from principal.errors import Code, PrincipalError
from principal.orchestrator import job as job_module
from principal.orchestrator.signals import gather_signals
from principal.outcome import Outcome, classify


def _stub_pipeline(monkeypatch, plan_error: Exception | None = None):
    calls: list[tuple] = []

    async def ingest(job_id, store, events, sandbox, settings):
        calls.append(("ingest",))

    async def map_(job_id, store, events, settings):
        calls.append(("map",))
        return "graph-1"

    def resolve(store, job_id, graph_id):
        calls.append(("resolve", graph_id))
        return "target-1"

    async def plan(job_id, store, events, models, graph_id, target, settings):
        calls.append(("plan", graph_id, target))
        if plan_error is not None:
            raise plan_error

    monkeypatch.setattr(job_module, "_ingest_and_baseline", ingest)
    monkeypatch.setattr(job_module, "_map", map_)
    monkeypatch.setattr(job_module, "_resolve_target", resolve)
    monkeypatch.setattr(job_module, "_plan", plan)
    return calls


def _new_job(store):
    return store.create_job(repo_url="r", commit_sha="c", goal="g", target_fqn=None,
                            token_budget=1, tunables={})


async def _dry_run(store, events, settings, job_id):
    await job_module.run_plan_only(job_id, store=store, events=events, sandbox=None, models=None,
                                   settings=settings)


async def test_a_dry_run_ends_done_and_unverified(monkeypatch, store, events, settings):
    calls = _stub_pipeline(monkeypatch)
    job = _new_job(store)

    await _dry_run(store, events, settings, job.id)

    assert calls == [("ingest",), ("map",), ("resolve", "graph-1"), ("plan", "graph-1", "target-1")]
    row = store.get_job(job.id)
    assert (row.state, row.stop_reason) == ("Done", "dry_run")
    report = classify(gather_signals(store, job.id))
    assert (report.outcome, report.cause) == (Outcome.UNVERIFIED, "plan_only")


async def test_a_plan_that_fails_aborts_the_dry_run_with_its_code(monkeypatch, store, events,
                                                                  settings):
    _stub_pipeline(monkeypatch, PrincipalError(Code.PLAN_INVALID, "no tasks"))
    job = _new_job(store)

    await _dry_run(store, events, settings, job.id)

    row = store.get_job(job.id)
    assert (row.state, row.stop_reason) == ("Aborted", "no tasks")
    last = list(events.replay(job.id))[-1]
    assert (last.kind, last.payload) == (
        "job.state", {"state": "Aborted", "reason": "no tasks", "code": "PLAN_INVALID"},
    )


async def test_a_crash_aborts_the_dry_run_instead_of_leaving_it_running(monkeypatch, store, events,
                                                                        settings):
    _stub_pipeline(monkeypatch, RuntimeError("boom"))
    job = _new_job(store)

    await _dry_run(store, events, settings, job.id)

    row = store.get_job(job.id)
    assert (row.state, row.stop_reason) == ("Aborted", "boom")


async def test_the_api_dry_run_goes_through_the_same_function(monkeypatch, store, events, settings):
    seen = []

    async def fake(job_id, **kw):
        seen.append((job_id, kw["settings"]))

    monkeypatch.setattr("principal.api.routes.run_plan_only", fake)

    class App:
        pass

    app = App()
    app.store, app.events, app.sandbox, app.models = store, events, None, None
    await _run_plan_only("j1", app, settings)

    assert seen == [("j1", settings)]
