"""Characteriser: one uncovered symbol -> a small suite that pins its behaviour.

The only new agent in this design, and it is the only one because it is the only
addition doing something a deterministic tool cannot. Security scanning is rules,
cleanup detection is graph queries plus reference counting, and rubric grading is
advisory output rather than a decision. Writing a test that captures what a
function currently does is genuinely generative, so it gets a model.

Two properties of this module are load-bearing and neither is obvious from its
size.

It never receives the goal. `characterise()` takes a symbol and its call sites
and there is no parameter through which the refactoring goal could arrive, which
is a stronger guarantee than instructing a model not to use it — exactly as the
Repairer's context is narrowed to the failure rather than the intent.

It cannot decide anything. It returns test source. Whether that source is worth
trusting is settled by `gates/characterise.py` against a baseline run and a
mutation score, neither of which involves a model.

Tier is Super rather than Nano by default. Test writing is quality-sensitive in a
way patch generation is not — a patch that is wrong fails a gate, whereas a test
that is subtly wrong is discarded at best and misleading at worst — and the
mutation score is exactly the yardstick for deciding whether that spend is
justified, so the choice is measurable rather than assumed.
"""

from __future__ import annotations

import re
from pathlib import Path

from principal.config import MAX_GENERATED_TESTS, Tier
from principal.graph.parse import CallSite
from principal.models.client import Completion, ModelClient

PROMPT = (Path(__file__).parent / "prompts" / "characteriser.md").read_text(encoding="utf-8")

_FENCE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def render_characterisation_prompt(
    *, target_fqn: str, target_path: str, signature: str, source: str,
    call_sites: list[CallSite], convention_examples: list[tuple[str, str]],
    max_tests: int = MAX_GENERATED_TESTS,
) -> str:
    """Note what is absent from this signature: there is no `goal` parameter."""
    calls_block = "\n".join(
        f"- `{c.enclosing or '<module>'}` at line {c.line} calls `{c.callee_name}(...)`"
        for c in call_sites[:40]
    ) or "(no call sites recorded)"
    convention_block = "\n\n".join(
        f"### {path}\n```python\n{excerpt}\n```" for path, excerpt in convention_examples[:2]
    ) or "(none found)"

    return PROMPT.format(
        target_fqn=target_fqn, target_path=target_path,
        signature=signature or "(no signature captured)", source=source[:12000],
        call_sites_block=calls_block, convention_block=convention_block, max_tests=max_tests,
    )


def extract_module(text: str) -> str:
    """Pull the test module out of the response.

    Returning the raw text when no fence is present is deliberate: a model that
    answered with bare code should not be discarded for formatting, and a model
    that answered with prose produces a module that fails to collect, which the
    baseline run rejects a moment later. Either way the decision is made by
    running it, not by parsing it.
    """
    matches = _FENCE.findall(text or "")
    if matches:
        return max(matches, key=len).strip() + "\n"
    return (text or "").strip() + "\n"


async def characterise(
    client: ModelClient, *, job_id: str, target_fqn: str, target_path: str, signature: str,
    source: str, call_sites: list[CallSite], convention_examples: list[tuple[str, str]] | None = None,
    tier: Tier = Tier.SUPER, temperature: float = 0.0,
) -> tuple[str, Completion]:
    prompt = render_characterisation_prompt(
        target_fqn=target_fqn, target_path=target_path, signature=signature, source=source,
        call_sites=call_sites, convention_examples=convention_examples or [],
    )
    completion = await client.complete(
        tier, [{"role": "user", "content": prompt}], temperature=temperature, job_id=job_id,
        max_tokens=3000,
    )
    return extract_module(completion.text), completion
