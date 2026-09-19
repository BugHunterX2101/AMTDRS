"""Coder: one task -> one unified diff. Nano, escalating to Super on repair.

File-scoped and cannot widen its own scope: it receives exactly one file to
modify plus read-only context, and its diff is rejected by a deterministic check
(gate 1) if it touches any other path. Test bodies are excluded from its context
on purpose — a coder that has read the assertions is a coder that can satisfy
them specifically, which would defeat the whole point of an independent oracle.
"""

from __future__ import annotations

from pathlib import Path

from principal.config import Tier
from principal.diffs import ParsedDiff, parse_diff
from principal.graph.languages import lang_of
from principal.graph.parse import CallSite
from principal.models.client import Completion, ModelClient

PROMPT = (Path(__file__).parent / "prompts" / "coder.md").read_text(encoding="utf-8")

EXT_OF = {"python": "python", "typescript": "typescript"}


def render_context_pack(
    *, instruction: str, acceptance: str, target_file: str, file_content: str,
    call_sites: list[CallSite], convention_examples: list[tuple[str, str]],
    test_names: list[str],
) -> str:
    lang = lang_of(target_file) or "python"
    calls_block = "\n".join(f"- line {c.line}: `{c.callee_name}(...)`" for c in call_sites[:60]) or "(none in this file)"
    convention_block = "\n\n".join(
        f"### {path}\n```{EXT_OF.get(lang, 'text')}\n{excerpt}\n```" for path, excerpt in convention_examples
    ) or "(none found)"
    tests_block = "\n".join(f"- {n}" for n in test_names[:40]) or "(no covering tests found — see risk notes)"

    return PROMPT.format(
        instruction=instruction, acceptance=acceptance, target_file=target_file,
        ext=EXT_OF.get(lang, "text"), file_content=file_content[:20000],
        call_sites_block=calls_block, convention_block=convention_block, test_names_block=tests_block,
    )


async def generate_candidate(
    client: ModelClient, *, job_id: str, context_pack: str, temperature: float, tier: Tier = Tier.NANO,
) -> tuple[ParsedDiff, Completion]:
    messages = [{"role": "user", "content": context_pack}]
    completion = await client.complete(
        tier, messages, temperature=temperature, job_id=job_id, max_tokens=3000,
    )
    return parse_diff(completion.text), completion
