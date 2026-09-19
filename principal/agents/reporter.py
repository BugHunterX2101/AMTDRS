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


def _render(
    verified: list[dict], discarded: list[dict], uncovered: list[str], integration: dict,
) -> str:
    verified_block = "\n".join(
        f"- **{t['target_file']}**: {t['instruction']} ({t['acceptance']})" for t in verified
    ) or "(none)"
    discarded_block = "\n".join(
        f"- **{t['target_file']}**: {t.get('discard_reason', 'unknown')}" for t in discarded
    ) or "(none — every planned task was verified)"
    uncovered_block = "\n".join(f"- {f}" for f in uncovered) or "(none)"
    integration_block = json.dumps(integration, indent=2)

    return PROMPT.format(
        verified_block=verified_block, discarded_block=discarded_block,
        uncovered_block=uncovered_block, integration_block=integration_block,
        schema=json.dumps(ReportOutput.model_json_schema()),
    )


def fallback_report(
    verified: list[dict], discarded: list[dict], uncovered: list[str], goal: str,
) -> ReportOutput:
    """Formatting, not reasoning — a templated body if the model call fails."""
    lines = [f"Automated migration: {goal}", "", "## Verified changes"]
    for t in verified:
        lines.append(f"- `{t['target_file']}`: {t['instruction']}")
    if discarded:
        lines += ["", "## Not migrated"]
        for t in discarded:
            lines.append(f"- `{t['target_file']}`: {t.get('discard_reason', 'unknown')}")
    if uncovered:
        lines += ["", "## Risk: modified with no test coverage"]
        for f in uncovered:
            lines.append(f"- `{f}`")
    risks = [{"kind": "uncovered", "detail": f} for f in uncovered]
    risks += [{"kind": "discarded", "detail": t["target_file"]} for t in discarded]
    return ReportOutput(
        title=f"Principal: {goal[:60]}", body="\n".join(lines),
        risks=[{"kind": r["kind"], "detail": r["detail"]} for r in risks],  # type: ignore[arg-type]
    )


async def compose_report(
    client: ModelClient, *, job_id: str, goal: str, verified: list[dict], discarded: list[dict],
    uncovered: list[str], integration: dict, tier: Tier = Tier.NANO,
) -> ReportOutput:
    prompt = _render(verified, discarded, uncovered, integration)
    messages = [{"role": "user", "content": prompt}]
    try:
        completion = await client.complete(
            tier, messages, schema=ReportOutput, temperature=0.3, job_id=job_id, max_tokens=2000,
        )
        return ReportOutput.model_validate_json(_strip_fences(completion.text))
    except Exception:  # noqa: BLE001
        return fallback_report(verified, discarded, uncovered, goal)


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
    return t.strip()
