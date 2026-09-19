"""Repairer: failing diff + stack trace -> revised diff. Super.

Receives the failing diff, the stack trace and the failing test names. It does
not receive the original goal — the goal string is not in scope at the call
site, so it cannot leak in by accident.
"""

from __future__ import annotations

import re
from pathlib import Path

from principal.agents.contracts import RepairKind
from principal.config import Tier
from principal.diffs import ParsedDiff, parse_diff
from principal.graph.languages import lang_of
from principal.models.client import Completion, ModelClient

PROMPT = (Path(__file__).parent / "prompts" / "repairer.md").read_text(encoding="utf-8")
EXT_OF = {"python": "python", "typescript": "typescript"}
KIND_LINE = re.compile(r"KIND:\s*(\w+)", re.IGNORECASE)


def render_repair_prompt(
    *, target_file: str, current_content: str, failing_diff: str, failing_tests: list[str],
    stack_trace: str,
) -> str:
    lang = lang_of(target_file) or "python"
    return PROMPT.format(
        target_file=target_file, ext=EXT_OF.get(lang, "text"), current_content=current_content[:20000],
        failing_diff=failing_diff[:4000], failing_tests=", ".join(failing_tests[:10]) or "(none named)",
        stack_trace=stack_trace[:4000],
    )


async def repair(
    client: ModelClient, *, job_id: str, prompt: str, temperature: float = 0.2, tier: Tier = Tier.SUPER,
) -> tuple[ParsedDiff, RepairKind, Completion]:
    messages = [{"role": "user", "content": prompt}]
    completion = await client.complete(
        tier, messages, temperature=temperature, job_id=job_id, max_tokens=3000,
    )
    diff = parse_diff(completion.text)
    kind = _extract_kind(completion.text)
    return diff, kind, completion


def _extract_kind(text: str) -> RepairKind:
    m = KIND_LINE.search(text)
    if not m:
        return RepairKind.OTHER
    try:
        return RepairKind(m.group(1).lower())
    except ValueError:
        return RepairKind.OTHER
