"""Reporter: verification log + merged patch set -> PR title, body, risk notes.

Reads artifacts, never the job's intent. It has no access to the goal string,
only to diffs, verdicts and logs, so it cannot describe a change that did not
happen. Falls back to a templated body if the model call fails — the PR should
never be blocked on prose.
"""

from __future__ import annotations

import json
from pathlib import Path

from principal.agents.contracts import ReportOutput
from principal.config import Tier
from principal.models.client import ModelClient

PROMPT = (Path(__file__).parent / "prompts" / "reporter.md").read_text(encoding="utf-8")


def _task_line(t: dict) -> str:
    tag = " _(cleanup — not part of the requested change)_" if t.get("kind") == "cleanup" else ""
    if "acceptance" in t:
        return f"- **{t['target_file']}**: {t['instruction']} ({t['acceptance']}){tag}"
    return f"- **{t['target_file']}**: {t.get('discard_reason', 'unknown')}{tag}"


def _render(
    verified: list[dict], discarded: list[dict], uncovered: list[str], integration: dict,
    characterisation_only: list[str] | None = None, characterisation_result: dict | None = None,
    relocated_symbols: list[str] | None = None, unresolved_call_sites: list[dict] | None = None,
) -> str:
    verified_block = "\n".join(_task_line(t) for t in verified) or "(none)"
    discarded_block = "\n".join(_task_line(t) for t in discarded) or "(none — every planned task was verified)"
    uncovered_block = "\n".join(f"- {f}" for f in uncovered) or "(none)"
    integration_block = json.dumps(integration, indent=2)
    characterisation_block = _characterisation_block(characterisation_only, characterisation_result)
    relocated_block = "\n".join(f"- {s}" for s in (relocated_symbols or [])) or "(none, or not a relocation)"
    unresolved_block = _unresolved_block(unresolved_call_sites)

    return PROMPT.format(
        verified_block=verified_block, discarded_block=discarded_block,
        uncovered_block=uncovered_block, integration_block=integration_block,
        characterisation_block=characterisation_block, relocated_block=relocated_block,
        unresolved_block=unresolved_block,
        schema=json.dumps(ReportOutput.model_json_schema()),
    )


def _unresolved_block(sites: list[dict] | None) -> str:
    if not sites:
        return "(none — every call site the graph found resolves statically)"
    return "\n".join(f"- `{s['file']}:{s['line']}` — {s['name']} ({s['reason']})" for s in sites)


def _characterisation_block(only: list[str] | None, result: dict | None) -> str:
    if not only:
        return "(none — every touched file had real, pre-existing test coverage)"
    score = None
    if result:
        mutation = result.get("mutation") or {}
        score = mutation.get("score")
    scored = f", mutation score {score:.2f}" if isinstance(score, (int, float)) else ""
    lines = [f"- {f}" for f in only]
    lines.append(
        f"(the generated suite passed against the unmodified baseline and cleared its"
        f" mutation-score floor{scored}, but it is model-written, not pre-existing — say so)"
    )
    return "\n".join(lines)


def fallback_report(
    verified: list[dict], discarded: list[dict], uncovered: list[str], goal: str,
    characterisation_only: list[str] | None = None, characterisation_result: dict | None = None,
    relocated_symbols: list[str] | None = None, unresolved_call_sites: list[dict] | None = None,
) -> ReportOutput:
    """Formatting, not reasoning — a templated body if the model call fails.

    Every disclosure here is deterministic and does not depend on a model
    call succeeding, which matters more for these than for the prose: a
    reviewer must be able to trust "verified only by generated tests" and
    "this is cleanup, not what was asked for" even on the one path that
    exists specifically because the model call failed.
    """
    asked = [t for t in verified if t.get("kind") != "cleanup"]
    cleanup = [t for t in verified if t.get("kind") == "cleanup"]

    lines = [f"Automated migration: {goal}", "", "## Verified changes"]
    for t in asked:
        lines.append(f"- `{t['target_file']}`: {t['instruction']}")
    if cleanup:
        lines += ["", "## Cleanup (dead code this refactor orphaned, not part of the requested change)"]
        for t in cleanup:
            lines.append(f"- `{t['target_file']}`: {t['instruction']}")
    if discarded:
        lines += ["", "## Not migrated"]
        for t in discarded:
            lines.append(f"- `{t['target_file']}`: {t.get('discard_reason', 'unknown')}")
    if uncovered:
        lines += ["", "## Risk: modified with no test coverage"]
        for f in uncovered:
            lines.append(f"- `{f}`")
    if characterisation_only:
        lines += ["", "## Risk: verified only by generated tests, not pre-existing ones"]
        score = ((characterisation_result or {}).get("mutation") or {}).get("score")
        note = f" (mutation score {score:.2f})" if isinstance(score, (int, float)) else ""
        for f in characterisation_only:
            lines.append(f"- `{f}`{note}")
    if relocated_symbols:
        lines += ["", "## Relocated symbols (moved, not removed — check the new import path)"]
        for s in relocated_symbols:
            lines.append(f"- `{s}`")
    if unresolved_call_sites:
        lines += ["", "## Call sites the graph could not resolve statically"]
        for s in unresolved_call_sites:
            lines.append(f"- `{s['file']}:{s['line']}` — {s['name']} ({s['reason']})")

    risks = [{"kind": "uncovered", "detail": f} for f in uncovered]
    risks += [{"kind": "discarded", "detail": t["target_file"]} for t in discarded]
    risks += [{"kind": "characterisation_only", "detail": f} for f in (characterisation_only or [])]
    risks += [
        {"kind": "unresolved_call_site", "detail": f"{s['file']}:{s['line']} — {s['name']}"}
        for s in (unresolved_call_sites or [])
    ]
    return ReportOutput(
        title=f"Principal: {goal[:60]}", body="\n".join(lines),
        risks=[{"kind": r["kind"], "detail": r["detail"]} for r in risks],  # type: ignore[arg-type]
    )


async def compose_report(
    client: ModelClient, *, job_id: str, goal: str, verified: list[dict], discarded: list[dict],
    uncovered: list[str], integration: dict, characterisation_only: list[str] | None = None,
    characterisation_result: dict | None = None, relocated_symbols: list[str] | None = None,
    unresolved_call_sites: list[dict] | None = None, tier: Tier = Tier.NANO,
) -> ReportOutput:
    prompt = _render(
        verified, discarded, uncovered, integration,
        characterisation_only=characterisation_only, characterisation_result=characterisation_result,
        relocated_symbols=relocated_symbols, unresolved_call_sites=unresolved_call_sites,
    )
    messages = [{"role": "user", "content": prompt}]
    try:
        completion = await client.complete(
            tier, messages, schema=ReportOutput, temperature=0.3, job_id=job_id, max_tokens=2000,
        )
        return ReportOutput.model_validate_json(_strip_fences(completion.text))
    except Exception:  # noqa: BLE001
        return fallback_report(
            verified, discarded, uncovered, goal,
            characterisation_only=characterisation_only, characterisation_result=characterisation_result,
            relocated_symbols=relocated_symbols, unresolved_call_sites=unresolved_call_sites,
        )


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
    return t.strip()
