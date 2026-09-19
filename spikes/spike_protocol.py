"""Spike: does each configured model answer in the protocol we expect, and does
`reasoning_content` show up empty-`content` behaviour (Nebius API issue #211)?

This is the script that answers the question before `principal serve` has to.
Run it standalone, read the table, and if a model's `content` comes back empty
while `reasoning_content` does not, that is issue #211 and the boot-time
capability probe (`principal.models.probe`) is why the rest of the system never
sees it.

    python -m spikes.spike_protocol
    python -m spikes.spike_protocol --model nvidia/nemotron-3-super-120b-a12b
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from principal.config import get_settings
from principal.models.probe import dump_json, probe_all, summary_table


async def _raw_reasoning_check(client, model: str) -> None:
    """Bypass the probe's own parsing and print the raw response shape, so
    "empty content, non-empty reasoning_content" is something you can see with
    your own eyes rather than take on the probe's word."""
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with exactly the word: ping"}],
            max_tokens=200,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  raw call failed: {exc}")
        return

    msg = resp.choices[0].message
    content = getattr(msg, "content", None) or ""
    reasoning = getattr(msg, "reasoning_content", None) or ""
    print(f"  content:           {content[:80]!r}")
    print(f"  reasoning_content: {reasoning[:80]!r}")
    if not content and reasoning:
        print("  >>> issue #211 reproduced: content is empty, reasoning_content is not.")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", dest="models", default=None,
                        help="probe one model (repeatable); default: all three configured tiers")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.nebius_api_key:
        print("NEBIUS_API_KEY is not set. This spike needs real inference access.", file=sys.stderr)
        return 1

    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=settings.principal_inference_base, api_key=settings.nebius_api_key)
    models = args.models or [
        settings.principal_model_nano, settings.principal_model_super, settings.principal_model_ultra,
    ]

    print(f"probing against {settings.principal_inference_base}")
    print(f"models: {', '.join(models)}\n")

    caps = await probe_all(client, models)
    print(summary_table(caps))
    print()
    print(dump_json(caps))

    print("\nraw reasoning_content check (one call per model):")
    for model in models:
        print(f"\n{model}")
        await _raw_reasoning_check(client, model)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
