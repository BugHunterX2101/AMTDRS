"""The evidence bundle reports what the record supports and nothing more."""

from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from principal.api.routes import router
from principal.orchestrator.evidence import build_evidence, build_zip, provenance_of, render_markdown
from principal.orchestrator.signals import execution_provenance

LIVE = {"sandbox": "live", "inference": "live", "live": True}
LOCAL = {"sandbox": "local", "inference": "not_configured", "live": False}


def _job(store, provenance=None):
    tunables = {"execution_provenance": provenance} if provenance else {}
    return store.create_job(
        repo_url="https://example.test/r", commit_sha="a" * 40, goal="add type hints",
        target_fqn="src.pricing.calc_total", token_budget=1000, tunables=tunables,
    )


def _finish(store, job, state="Done", stop_reason=None):
    store.update_job(job.id, state=state, stop_reason=stop_reason)


def test_unknown_provenance_is_never_reported_as_live(store):
    job = _job(store)
    prov = provenance_of(store, job.id)
    assert prov["live"] is False and prov["sandbox"] == "unknown"


def test_recorded_provenance_is_carried_through(store):
    assert provenance_of(store, _job(store, LIVE).id)["live"] is True
    assert provenance_of(store, _job(store, LOCAL).id)["live"] is False


def test_local_sandbox_is_labelled_local_not_live(settings):
    from principal.sandbox.fake import FakeSandbox

    prov = execution_provenance(FakeSandbox(), SimpleNamespace(execution_mode="live"), settings)
    assert prov["sandbox"] == "local" and prov["live"] is False


def test_unfinished_run_has_no_outcome(store, settings):
    job = _job(store, LIVE)
    ev = build_evidence(store, settings, job.id)
    assert ev["outcome"] is None
    assert "has not finished" in render_markdown(ev)


def test_unknown_job_has_no_evidence(store, settings):
    assert build_evidence(store, settings, "nope") is None


def test_finished_run_carries_verdict_gates_and_usage(store, settings):
    job = _job(store, LIVE)
    task = store.create_task(job_id=job.id, seq=1, target_file="src/pricing.py",
                             instruction="annotate", acceptance="tests pass", depends_on=[])
    store.update_task(task.id, state="verified")
    attempt = store.create_attempt(task_id=task.id, job_id=job.id, n=1, model="nano",
                                   temperature=0.2, parent_image="img")
    store.update_attempt(attempt.id, verdict="ok", prompt_tokens=100, completion_tokens=40, cost=0.002)
    store.append_event(job.id, "strength.result", {
        "status": "measured", "floor": 0.6, "generated": 1, "detected": 0, "survived": 1,
        "score": 0.0, "below_floor": True, "disclaimer": "injected faults, not proof",
    })
    _finish(store, job)

    ev = build_evidence(store, settings, job.id)

    assert ev["outcome"]["outcome"] == "UNVERIFIED"
    assert ev["provenance"]["live"] is True
    assert ev["gates"]["strength"]["score"] == 0.0
    assert ev["gates"]["impact"] is None
    assert ev["usage"]["sandbox_cost_usd"] == pytest.approx(0.002)
    assert ev["usage"]["attempts_by_verdict"] == {"ok": 1}
    md = render_markdown(ev)
    assert "**LIVE**" in md and "UNVERIFIED" in md and "injected faults, not proof" in md
    assert "did not run" in md


def test_cost_is_none_not_zero_when_nothing_reported_it(store, settings):
    job = _job(store, LIVE)
    task = store.create_task(job_id=job.id, seq=1, target_file="a.py", instruction="x",
                             acceptance="y", depends_on=[])
    attempt = store.create_attempt(task_id=task.id, job_id=job.id, n=1, model="nano",
                                   temperature=0.2, parent_image="img")
    # The sandbox client reports 0.0 when the platform says nothing about cost.
    store.update_attempt(attempt.id, cost=0.0)
    ev = build_evidence(store, settings, job.id)
    assert ev["usage"]["sandbox_cost_usd"] is None
    assert "not reported" in render_markdown(ev)


def test_non_live_run_is_labelled_in_the_markdown(store, settings):
    ev = build_evidence(store, settings, _job(store, LOCAL).id)
    assert "NOT LIVE" in render_markdown(ev)


def test_zip_ships_artifacts_with_a_hash_manifest_and_flags_tampering(store, settings):
    job = _job(store, LIVE)
    run_dir = settings.run_dir(job.id)
    store.put_artifact(job_id=job.id, kind="diff", rel_path="attempts/a.diff",
                       content="--- a\n+++ b\n", base_dir=run_dir)
    store.put_artifact(job_id=job.id, kind="diff", rel_path="attempts/b.diff",
                       content="original\n", base_dir=run_dir)
    (run_dir / "attempts" / "b.diff").write_text("tampered\n", encoding="utf-8")
    _finish(store, job)

    ev = build_evidence(store, settings, job.id)
    with zipfile.ZipFile(io.BytesIO(build_zip(store, settings, ev))) as z:
        names = set(z.namelist())
        manifest = {m["path"]: m["intact"] for m in json.loads(z.read("manifest.json"))}

    assert {"evidence.json", "evidence.md", "manifest.json", "verification.md"} <= names
    assert "artifacts/attempts/a.diff" in names
    assert manifest == {"attempts/a.diff": True, "attempts/b.diff": False}


def test_the_characterisation_score_is_reported_not_just_that_it_ran(store, settings):
    """The result nests the score under `mutation`. Reading a top-level `score`
    made every characterised run's report say only "ran"."""
    job = _job(store, LIVE)
    store.append_event(job.id, "characterise.result", {
        "status": "ok", "reason": "", "test_path": "tests/x.py", "mutants_generated": 5,
        "mutation": {"score": 0.8},
    })
    md = render_markdown(build_evidence(store, settings, job.id))
    assert "**characterise:** ok, mutation score 0.80 across 5 mutants" in md


@pytest.fixture
def client(store, settings):
    from principal.db.events import EventLog

    app = FastAPI()
    app.include_router(router)
    app.state.store = store
    app.state.settings = settings
    app.state.events = EventLog(store, settings.principal_runs_dir)
    app.state.background_tasks = {}

    async def cancel(op_id):
        return None

    app.state.sandbox = SimpleNamespace(cancel=cancel)
    app.state.models = SimpleNamespace(
        refusals_for=lambda _job_id: 0,
        rate_headroom=SimpleNamespace(remaining_requests=None, remaining_tokens=None),
    )
    c = TestClient(app)
    c.app_state = app.state
    return c


def test_cancelling_a_finished_job_does_not_rewrite_its_result(client, store):
    job = _job(store, LIVE)
    store.update_job(job.id, state="Done", pr_url="https://example.test/pr/1")

    resp = client.post(f"/jobs/{job.id}/cancel")

    assert resp.status_code == 409
    after = store.get_job(job.id)
    assert after.state == "Done" and after.stop_reason is None


def test_cancelling_a_running_job_stops_its_task_too(client, store):
    """Marking the row NoPR is not enough: the coroutine kept running, and its
    later Done or Aborted overwrote the cancellation."""
    job = _job(store, LIVE)
    store.update_job(job.id, state="Executing")
    task = SimpleNamespace(cancelled=False, done=lambda: False)
    task.cancel = lambda: setattr(task, "cancelled", True)
    client.app_state.background_tasks[job.id] = task

    resp = client.post(f"/jobs/{job.id}/cancel")

    assert resp.status_code == 202
    assert task.cancelled is True
    after = store.get_job(job.id)
    assert (after.state, after.stop_reason) == ("NoPR", "cancelled")


def test_job_status_exposes_outcome_and_provenance(client, store):
    job = _job(store, LIVE)
    _finish(store, job, state="NoSafetyNet", stop_reason="no tests")
    body = client.get(f"/jobs/{job.id}").json()
    assert body["outcome"]["outcome"] == "UNVERIFIED"
    assert body["provenance"]["live"] is True


def test_evidence_routes(client, store):
    job = _job(store, LOCAL)
    _finish(store, job)

    as_json = client.get(f"/jobs/{job.id}/evidence")
    assert as_json.status_code == 200 and as_json.json()["job"]["id"] == job.id

    md = client.get(f"/jobs/{job.id}/evidence.md")
    assert md.status_code == 200 and "NOT LIVE" in md.text

    archive = client.get(f"/jobs/{job.id}/evidence.zip")
    assert archive.status_code == 200 and archive.headers["content-type"] == "application/zip"
    assert zipfile.ZipFile(io.BytesIO(archive.content)).testzip() is None


@pytest.mark.parametrize("suffix", ["evidence", "evidence.md", "evidence.zip"])
def test_evidence_routes_404_for_unknown_job(client, suffix):
    assert client.get(f"/jobs/missing/{suffix}").status_code == 404
