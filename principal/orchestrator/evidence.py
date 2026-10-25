"""The evidence bundle: one document that answers "what did this run do, and why
should I believe it".

Everything here is read back from the record a run left (rows, event log, artifact
files). Nothing is recomputed or remembered, so a bundle fetched while a run is
live, or a month later, is built from the same evidence the verdict was.

The bundle never claims more than the record supports. `provenance.live` is false
unless both inference and the sandbox were real, and a stored file is flagged
`intact: false` if its bytes no longer match the hash recorded for it.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from typing import Any

from principal.config import Settings
from principal.db.store import Store
from principal.orchestrator.signals import gather_signals, last_event
from principal.outcome import classify
from principal.publish.prbody import render_verification_block, safe_link

SCHEMA = 1

# Gate results, in the order the pipeline runs them.
_GATE_EVENTS = (
    ("characterise", "characterise.result"),
    ("integration", "integration.result"),
    ("security", "security.result"),
    ("impact", "impact.result"),
    ("strength", "strength.result"),
)

# Artifact kinds whose content is small enough to ship inside the zip as-is.
_BUNDLED_KINDS = frozenset({"diff", "coverage", "test_log"})


def provenance_of(store: Store, job_id: str) -> dict[str, Any]:
    """Whether this job touched the real platform, as recorded when it was created.
    A job created before provenance was recorded is `unknown`, never `live`."""
    job = store.get_job(job_id)
    recorded = (job.tunables if job else {}).get("execution_provenance")
    if not isinstance(recorded, dict):
        return {"sandbox": "unknown", "inference": "unknown", "live": False,
                "note": "no execution provenance was recorded for this job"}
    return recorded


def _attempt_rows(store: Store, job_id: str) -> list[dict[str, Any]]:
    return [
        {"attempt_id": a.id, "task_id": a.task_id, "n": a.n, "kind": a.kind, "model": a.model,
         "verdict": a.verdict, "gate": a.gate, "duration_ms": a.duration_ms,
         "prompt_tokens": a.prompt_tokens, "completion_tokens": a.completion_tokens,
         "cost": a.cost, "diff_sha256": a.diff_sha256}
        for a in store.attempts_of(job_id)
    ]


def _usage(attempts: list[dict[str, Any]], tokens_spent: int, token_budget: int) -> dict[str, Any]:
    sandbox_cost = sum(a["cost"] or 0.0 for a in attempts)
    return {
        "tokens_spent": tokens_spent,
        "token_budget": token_budget,
        "prompt_tokens": sum(a["prompt_tokens"] or 0 for a in attempts),
        "completion_tokens": sum(a["completion_tokens"] or 0 for a in attempts),
        # What the sandbox platform reported charging. It defaults to 0.0 when the
        # platform reports nothing, so zero means "not reported", never "free".
        # Model tokens are counted above and deliberately not priced: no price
        # table is recorded with the run.
        "sandbox_cost_usd": round(sandbox_cost, 6) if sandbox_cost > 0 else None,
        "attempts": len(attempts),
        "attempts_by_verdict": _count(a["verdict"] or "unfinished" for a in attempts),
    }


def _model_calls(store: Store, job_id: str, tokens_spent: int) -> dict[str, Any] | None:
    """Per-call attribution of the job's tokens, from the `model.call` records.

    `None` for a job recorded before the ledger existed: nothing was written, so
    nothing is claimed, and "no breakdown" is not the same statement as "zero".
    `unattributed_tokens` is whatever the budget charged that no record explains,
    reported as a number so a gap is visible rather than assumed away.
    """
    rows = store.q("SELECT payload FROM event WHERE job_id = ? AND kind = 'model.call' ORDER BY seq", (job_id,))
    if not rows:
        return None
    by_role: dict[tuple[str, str, str], dict[str, Any]] = {}
    attributed = 0
    for row in rows:
        c = json.loads(row["payload"])
        key = (c["role"], c["tier"], c["model"])
        slot = by_role.setdefault(key, {
            "role": c["role"], "tier": c["tier"], "model": c["model"],
            "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "billed_tokens": 0, "cached": 0,
        })
        slot["calls"] += 1
        slot["prompt_tokens"] += c["prompt_tokens"]
        slot["completion_tokens"] += c["completion_tokens"]
        slot["billed_tokens"] += c["billed_tokens"]
        slot["cached"] += 1 if c["from_cache"] else 0
        attributed += c["billed_tokens"]
    return {
        "calls": len(rows),
        "by_role": sorted(by_role.values(), key=lambda r: -r["billed_tokens"]),
        "attributed_tokens": attributed,
        "unattributed_tokens": tokens_spent - attributed,
    }


def grounding_record(store: Store, job_id: str) -> dict[str, Any] | None:
    """Every web search the run made, and every one it decided not to make, as
    recorded. `None` when the run recorded neither (it predates grounding)."""
    rows = store.q(
        "SELECT kind, payload FROM event WHERE job_id = ? AND kind IN ('grounding.searched', 'grounding.skipped')"
        " ORDER BY seq", (job_id,),
    )
    if not rows:
        return None
    searched = [json.loads(r["payload"]) for r in rows if r["kind"] == "grounding.searched"]
    return {
        "searches": searched,
        "skipped": [json.loads(r["payload"]) for r in rows if r["kind"] == "grounding.skipped"],
        # Cache hits repeat an earlier search's results; they cost nothing.
        "provider_requests": sum(1 for s in searched if not s.get("cached")),
    }


def _count(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))


def _artifacts(store: Store, settings: Settings, job_id: str) -> list[dict[str, Any]]:
    out = []
    for row in store.artifacts_of(job_id):
        path = settings.run_dir(job_id) / row["path"]
        out.append({
            "id": row["id"], "kind": row["kind"], "path": row["path"], "sha256": row["sha256"],
            "attempt_id": row["attempt_id"], "present": path.exists(),
        })
    return out


def build_evidence(store: Store, settings: Settings, job_id: str) -> dict[str, Any] | None:
    job = store.get_job(job_id)
    if job is None:
        return None

    signals = gather_signals(store, job_id)
    verdict = classify(signals)
    tasks = store.tasks_of(job_id)
    attempts = _attempt_rows(store, job_id)
    gates = {name: last_event(store, job_id, kind) for name, kind in _GATE_EVENTS}

    return {
        "schema": SCHEMA,
        "job": {
            "id": job.id, "repo_url": job.repo_url, "commit_sha": job.commit_sha, "goal": job.goal,
            "target_fqn": job.target_fqn, "routine": job.routine, "state": job.state,
            "stop_reason": job.stop_reason, "pr_url": job.pr_url, "created_at": job.created_at,
            "finished_at": job.finished_at, "baseline_image": job.baseline_image,
        },
        "provenance": provenance_of(store, job_id),
        "outcome": verdict.to_payload() if verdict else None,
        "tasks": [
            {"seq": t.seq, "kind": t.kind, "target_file": t.target_file, "instruction": t.instruction,
             "acceptance": t.acceptance, "state": t.state, "attempts": t.attempts,
             "winning_attempt_id": t.winning_attempt_id, "discard_reason": t.discard_reason,
             "declared_removals": t.declared_removals}
            for t in tasks
        ],
        "gates": gates,
        "usage": {**_usage(attempts, job.tokens_spent, job.token_budget),
                  "model_calls": _model_calls(store, job_id, job.tokens_spent)},
        "attempts": attempts,
        "grounding": grounding_record(store, job_id),
        "artifacts": _artifacts(store, settings, job_id),
        "tunables": job.tunables,
        "reproduce": {
            "repo_url": job.repo_url, "commit_sha": job.commit_sha, "goal": job.goal,
            "target_fqn": job.target_fqn,
            "command": (
                f"principal run --repo {job.repo_url} --commit {job.commit_sha}"
                f" --goal {json.dumps(job.goal)} --target {job.target_fqn}"
            ),
        },
    }


# ------------------------------------------------------------------ markdown --


def render_markdown(ev: dict[str, Any]) -> str:
    job, prov, outcome = ev["job"], ev["provenance"], ev["outcome"]
    lines = [f"# Verification evidence for job {job['id']}", ""]
    lines += [
        f"- **Repository:** {job['repo_url']} @ `{job['commit_sha'][:12]}`",
        f"- **Goal:** {job['goal']}",
        f"- **Target:** `{job['target_fqn']}`",
        f"- **Run state:** {job['state']}" + (f" ({job['stop_reason']})" if job["stop_reason"] else ""),
        f"- **Executed on:** {_provenance_label(prov)}",
        "",
    ]

    if outcome is None:
        lines += ["## Outcome", "", "The run has not finished, so it has no outcome yet.", ""]
    else:
        lines += ["## Outcome", "", f"**{outcome['outcome']}** — {outcome['headline']}", ""]
        lines += [f"- {r}" for r in outcome["reasons"]] + ([""] if outcome["reasons"] else [])

    lines += ["## Tasks", ""]
    for t in ev["tasks"]:
        tail = f" — {t['discard_reason']}" if t["discard_reason"] else ""
        lines.append(f"{t['seq']}. `{t['target_file']}` [{t['state']}] {t['instruction']}{tail}")
    lines.append("")

    lines += ["## Gates", ""]
    for name, payload in ev["gates"].items():
        lines.append(f"- **{name}:** " + (_gate_line(name, payload) if payload else "did not run"))
    lines.append("")

    u = ev["usage"]
    cost = f"${u['sandbox_cost_usd']:.4f}" if u["sandbox_cost_usd"] is not None else "not reported"
    verdicts = ", ".join(f"{k}: {v}" for k, v in u["attempts_by_verdict"].items())
    lines += [
        "## Usage", "",
        f"- {u['attempts']} attempts ({verdicts})",
        f"- tokens {u['tokens_spent']:,} of {u['token_budget']:,}; sandbox cost {cost} (model tokens are not priced)",
    ]
    mc = u.get("model_calls")
    if mc is None:
        lines.append("- model calls: not itemised (this run was recorded before the per-call ledger)")
    else:
        lines += [f"- {mc['calls']} model calls, by role:"]
        lines += [
            f"  - **{r['role']}** — `{r['model']}` ({r['tier']}): {r['calls']} call(s), "
            f"{r['billed_tokens']:,} tokens ({r['prompt_tokens']:,} prompt, {r['completion_tokens']:,} completion)"
            + (f", {r['cached']} from cache" if r["cached"] else "")
            for r in mc["by_role"]
        ]
        gap = mc["unattributed_tokens"]
        lines.append(
            "  - reconciles: every charged token is attributed" if gap == 0
            else f"  - **{gap:,} charged tokens are not attributed to any recorded call**"
        )
    lines.append("")
    lines += _grounding_lines(ev.get("grounding"))
    lines += ["## Reproduce", "", "```", ev["reproduce"]["command"], "```", ""]
    strength = ev["gates"].get("strength")
    if strength and strength.get("disclaimer"):
        lines += ["> " + strength["disclaimer"], ""]
    return "\n".join(lines)


def _grounding_lines(g: dict[str, Any] | None) -> list[str]:
    if g is None:
        return []
    lines = ["## Web grounding", "",
             "Library documentation fetched for the planner and repairer prompts. No gate reads it.", ""]
    for s in g["searches"]:
        where = f"{s['stage']}" + (f", task {s['task_id']}" if s.get("task_id") else "")
        rid = f"request `{s['request_id']}`" if s.get("request_id") else "no request id"
        lines.append(f"- **{where}:** \"{s['query']}\" ({'cached, ' if s.get('cached') else ''}{rid})")
        lines += [f"  - {safe_link(r['title'], r['url'])}" for r in s.get("results") or []]
    for s in g["skipped"]:
        lines.append(f"- **{s['stage']}:** not searched — {s['reason']}")
    lines.append("")
    return lines


def _provenance_label(prov: dict[str, Any]) -> str:
    if prov.get("live"):
        return "**LIVE** — real Nebius sandbox and real inference"
    return (f"**NOT LIVE** — sandbox: {prov.get('sandbox', 'unknown')}, "
            f"inference: {prov.get('inference', 'unknown')}. Numbers from this run are not "
            "measurements of the platform.")


def _gate_line(name: str, p: dict[str, Any]) -> str:
    if name == "integration":
        return "pass" if p.get("pass") else f"fail ({'; '.join(p.get('removed_undeclared') or []) or 'see record'})"
    if name == "security":
        return f"{len(p.get('introduced') or [])} introduced finding(s)"
    if name == "impact":
        return (f"{len(p.get('unexpected_files') or [])} unexpected file(s), "
                f"{p.get('call_sites_changed', 0)} of {p.get('expected_call_sites', 0)} call sites changed, "
                f"{len(p.get('findings') or [])} finding(s)")
    if name == "strength":
        if p.get("status") != "measured":
            return f"not measured ({p.get('reason') or p.get('status')})"
        return (f"{p.get('detected')} of {p.get('detected', 0) + p.get('survived', 0)} injected faults "
                f"detected, score {p.get('score'):.0%} (floor {p.get('floor'):.0%})")
    if name == "characterise":
        score = (p.get("mutation") or {}).get("score")
        measured = f"mutation score {score:.2f}" if isinstance(score, (int, float)) else "no mutation score"
        return f"{p.get('status', 'ran')}, {measured} across {p.get('mutants_generated', 0)} mutants"
    return "ran"


# ----------------------------------------------------------------------- zip --


def build_zip(store: Store, settings: Settings, ev: dict[str, Any]) -> bytes:
    """The evidence document plus the files it cites, with a manifest of hashes.
    A file whose bytes no longer match the recorded hash is listed as not intact
    rather than silently shipped as if it were the original."""
    job_id = ev["job"]["id"]
    buf = io.BytesIO()
    manifest: list[dict[str, Any]] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("evidence.json", json.dumps(ev, indent=2, sort_keys=True))
        z.writestr("evidence.md", render_markdown(ev))
        signals = gather_signals(store, job_id)
        verdict = classify(signals)
        if verdict is not None:
            z.writestr("verification.md", render_verification_block(verdict, signals.impact, signals.strength))

        for art in ev["artifacts"]:
            if art["kind"] not in _BUNDLED_KINDS or not art["present"]:
                continue
            data = (settings.run_dir(job_id) / art["path"]).read_bytes()
            intact = hashlib.sha256(data).hexdigest() == art["sha256"]
            manifest.append({"path": art["path"], "sha256": art["sha256"], "intact": intact})
            z.writestr(f"artifacts/{art['path']}", data)
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
    return buf.getvalue()
