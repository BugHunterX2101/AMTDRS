"""The facts about a run that decide its outcome, read from the record it left.

`outcome.classify` is a pure function of plain data. This module is the other
half: it builds that data from the store, so a run that just finished and a run
opened from a link a month later are classified from exactly the same evidence.
Nothing here is cached or remembered between calls; the event log is the record
and this reads it.

It also holds the coverage-risk helpers the publisher and the classifier both
need, and the execution-provenance record that says whether a run touched the
real platform or a local stand-in. Provenance is recorded on the job when it is
created so no later reader has to guess.
"""

from __future__ import annotations

import json
from typing import Any

from principal.config import Settings
from principal.db.store import Store
from principal.outcome import INFRA_DISCARD_PREFIX, Signals


def last_event(store: Store, job_id: str, kind: str) -> dict[str, Any] | None:
    row = store.q1(
        "SELECT payload FROM event WHERE job_id = ? AND kind = ? ORDER BY seq DESC LIMIT 1",
        (job_id, kind),
    )
    if row is None:
        return None
    try:
        return json.loads(row["payload"])
    except json.JSONDecodeError:
        return None


# ------------------------------------------------------------- coverage risk --


def uncovered_files(store: Store, graph_id: str, files: set[str]) -> list[str]:
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


def characterisation_only_files(store: Store, graph_id: str, files: set[str]) -> list[str]:
    """Files whose only test coverage is a generated characterisation test.

    `uncovered_files` cannot tell this apart from real coverage: a
    characterisation edge satisfies "has some covering test" exactly like a
    hand-written one, by construction (gate 3 has to treat them identically or
    the whole feature is pointless). But characterise.py's own rule 4 says a
    change verified only by generated tests must be *reported* as such, not
    presented as ordinarily covered — and nothing upstream of the PR body ever
    made that distinction, so it silently never happened. This is what closes
    that gap: the one place that still has `source` on every test_edge row.
    """
    out: list[str] = []
    for f in sorted(files):
        symbols = store.symbols_in_files(graph_id, {f})
        if not symbols:
            continue  # already reported as uncovered
        covered = store.tests_covering(graph_id, {s.id for s in symbols})
        if not covered:
            continue  # already reported as uncovered
        if {row["source"] for row in covered} == {"characterisation"}:
            out.append(f)
    return out


def last_characterisation_result(store: Store, job_id: str) -> dict | None:
    """The mutation score behind a characterisation-only file, if the job ran
    that phase. Read from the event log rather than a dedicated row: the
    result was already published there for the live console, and duplicating
    it into another table would just be a second place for the two to drift
    apart."""
    return last_event(store, job_id, "characterise.result")


# ------------------------------------------------------------------- signals --


def gather_signals(
    store: Store, job_id: str, *, state: str | None = None, stop_reason: str | None = None,
    pr_url: str | None = None,
) -> Signals:
    """The inputs to `classify`, for this job as it stands in the store.

    `state`, `stop_reason` and `pr_url` override what the job row says. The
    publisher needs that: it classifies the run a moment *before* it writes
    `Done`, so that the verdict can go into the pull request body.
    """
    job = store.get_job(job_id)
    if job is None:
        raise KeyError(job_id)

    tasks = store.tasks_of(job_id)
    verified = [t for t in tasks if t.state == "verified"]
    discarded = [t for t in tasks if t.state == "discarded"]
    infra = [t for t in discarded if (t.discard_reason or "").startswith(INFRA_DISCARD_PREFIX)]

    effective_state = state or job.state
    uncovered: list[str] = []
    generated_only: list[str] = []
    if effective_state == "Done" and job.graph_id and verified:
        touched = {t.target_file for t in verified}
        uncovered = uncovered_files(store, job.graph_id, touched)
        generated_only = characterisation_only_files(store, job.graph_id, touched)

    return Signals(
        state=effective_state,
        stop_reason=stop_reason if stop_reason is not None else job.stop_reason,
        pr_url=pr_url if pr_url is not None else job.pr_url,
        verified_tasks=len(verified), discarded_tasks=len(discarded),
        infrastructure_discards=len(infra),
        impact=last_event(store, job_id, "impact.result"),
        strength=last_event(store, job_id, "strength.result"),
        security=last_event(store, job_id, "security.result"),
        integration=last_event(store, job_id, "integration.result"),
        uncovered=uncovered, characterisation_only=generated_only,
    )


# ---------------------------------------------------------------- provenance --


def execution_provenance(
    sandbox: object, models: object, settings: Settings, *, verified: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Which parts of a run touched the real platform, recorded on the job.

    `live` is true only when both inference and the sandbox are real. A run on
    the in-process stand-in is labelled `local`, never `live`, so no number it
    produces can be mistaken for a measurement of the platform. When the caller
    knows whether the credentials were actually verified at startup, a configured
    but unverified backend does not count as live.
    """
    sandbox_mode = getattr(sandbox, "execution_mode", "unknown")
    inference_configured = bool(settings.nebius_api_key) and getattr(models, "execution_mode", "live") == "live"
    if verified is not None:
        inference_configured = inference_configured and bool(verified.get("inference_verified"))
        if sandbox_mode == "live" and not verified.get("sandbox_verified"):
            sandbox_mode = "unverified"
    inference_mode = "live" if inference_configured else "not_configured"
    return {
        "sandbox": sandbox_mode,
        "sandbox_class": type(sandbox).__name__,
        "inference": inference_mode,
        "live": sandbox_mode == "live" and inference_mode == "live",
        "models": {
            "nano": settings.principal_model_nano,
            "super": settings.principal_model_super,
            "ultra": settings.principal_model_ultra,
        },
    }
