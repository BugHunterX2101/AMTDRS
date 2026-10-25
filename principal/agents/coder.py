"""Coder: one task -> one unified diff. Nano, escalating to Super on repair.

File-scoped and cannot widen its own scope: it receives exactly one file to
modify plus read-only context, and its diff is rejected by a deterministic check
(gate 1) if it touches any other path. Test bodies are excluded from its context
on purpose — a coder that has read the assertions is a coder that can satisfy
them specifically, which would defeat the whole point of an independent oracle.
"""

from __future__ import annotations

from pathlib import Path

from principal.agents.spotlight import NOTICE, spotlight
from principal.config import Tier
from principal.diffs import ParsedDiff, canonicalise, parse_diff
from principal.graph.languages import lang_of
from principal.graph.parse import CallSite
from principal.models.client import Completion, ModelClient

PROMPT = (Path(__file__).parent / "prompts" / "coder.md").read_text(encoding="utf-8")

EXT_OF = {"python": "python", "typescript": "typescript"}

# How much of the target file the model is shown. It was 20000 characters, which cut
# requests' utils.py (36061) before prepend_scheme_if_needed (at 32182): the model was
# asked to change a function it could not see, and every hunk it wrote failed the
# context check. Live, the whole of that file made an 11093-token coder prompt, and
# Nano and Super both took it; the cap itself has not been sent to a model. Past it
# the view is still cut, and that fails closed: `canonicalise`
# matches hunks against the real file, so a hunk for unseen lines cannot apply.
MAX_FILE_CHARS = 120_000


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

    # The fence around the file moves here from the template so the delimiters
    # can sit outside it: a file that itself contains a ``` line cannot close a
    # fence that is inside the spotlighted block.
    fenced = f"```{EXT_OF.get(lang, 'text')}\n{file_content[:MAX_FILE_CHARS]}\n```"
    return PROMPT.format(
        instruction=instruction, acceptance=acceptance, target_file=target_file,
        file_content=spotlight("file to change", fenced),
        call_sites_block=spotlight("call sites", calls_block),
        convention_block=spotlight("convention examples", convention_block),
        test_names_block=spotlight("test names", tests_block),
        untrusted_notice=NOTICE,
    )


async def generate_candidate(
    client: ModelClient, *, job_id: str, context_pack: str, temperature: float, tier: Tier = Tier.NANO,
    target_file: str | None = None, original: str | None = None,
) -> tuple[ParsedDiff, Completion]:
    messages = [{"role": "user", "content": context_pack}]
    completion = await client.complete(
        tier, messages, temperature=temperature, job_id=job_id, max_tokens=max_tokens_for(tier),
        role="coder",
    )
    diff = parse_diff(completion.text)
    if original is not None and target_file is not None:
        diff = canonicalise(diff, target_file, original)
    return diff, completion


def max_tokens_for(tier: Tier) -> int:
    # Every tier reasons before it answers, Nano included, and the thinking is
    # counted against this budget. A hit on it leaves no diff block. Live samples of
    # one task (src/pricing.py, indicative and not a rate): 4 of 6 Nano calls were cut
    # off at 3000, 4 of 12 at 6000 and 1 of 12 at 10000; uncut calls used up to 6788.
    return 10000
