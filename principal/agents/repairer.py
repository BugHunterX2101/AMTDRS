"""Repairer: failing diff + stack trace -> revised diff. Super.

Receives the failing diff, the stack trace and the failing test names. It does
not receive the original goal — the goal string is not in scope at the call
site, so it cannot leak in by accident.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from principal.agents.coder import max_tokens_for
from principal.agents.contracts import RepairKind
from principal.agents.spotlight import NOTICE, references_section, spotlight
from principal.config import Tier
from principal.diffs import ParsedDiff, canonicalise, parse_diff
from principal.graph.languages import lang_of
from principal.grounding.tavily import Reference
from principal.models.client import Completion, ModelClient

PROMPT = (Path(__file__).parent / "prompts" / "repairer.md").read_text(encoding="utf-8")
EXT_OF = {"python": "python", "typescript": "typescript"}
KIND_LINE = re.compile(r"KIND:\s*(\w+)", re.IGNORECASE)


def render_repair_prompt(
    *, target_file: str, current_content: str, failing_diff: str, failing_tests: list[str],
    stack_trace: str, references: Sequence[Reference] = (),
) -> str:
    lang = lang_of(target_file) or "python"
    # Fences are built here, inside the delimiters, for the reason given in coder.py.
    # The stack trace and test names are repository text too: an assertion message
    # or a parametrised test id is whatever the test author wrote.
    return references_section(references) + PROMPT.format(
        target_file=target_file,
        current_content=spotlight("current file", f"```{EXT_OF.get(lang, 'text')}\n{current_content[:20000]}\n```"),
        failing_diff=spotlight("failing diff", f"```diff\n{failing_diff[:4000]}\n```"),
        failing_tests=spotlight("failing test names", ", ".join(failing_tests[:10]) or "(none named)"),
        stack_trace=spotlight("test output", f"```\n{stack_trace[:4000]}\n```"),
        untrusted_notice=NOTICE,
    )


async def repair(
    client: ModelClient, *, job_id: str, prompt: str, temperature: float = 0.2, tier: Tier = Tier.SUPER,
    target_file: str | None = None, original: str | None = None,
) -> tuple[ParsedDiff, RepairKind, Completion]:
    messages = [{"role": "user", "content": prompt}]
    completion = await client.complete(
        tier, messages, temperature=temperature, job_id=job_id, max_tokens=max_tokens_for(tier),
        role="repairer",
    )
    diff = parse_diff(completion.text)
    if original is not None and target_file is not None:
        diff = canonicalise(diff, target_file, original)
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
