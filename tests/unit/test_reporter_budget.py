"""The report writer needs room to finish.

Nano reasons before it answers. In the live runs on 2026-10-20/21, two of three
reporter calls stopped at exactly the old 2,000-token cap, so the JSON was cut
off, failed to parse, and the template fallback shipped instead of the model's
report while the 2,000 tokens were still charged.
"""

from __future__ import annotations

import json

from principal.agents.reporter import compose_report
from principal.config import Protocol, Tier
from principal.models.client import Completion

_OBSERVED_LIVE_USAGE = 2000  # completion tokens of a truncated live reporter reply


class _Client:
    def __init__(self):
        self.max_tokens = None

    async def complete(self, tier, messages, **kw):
        self.max_tokens = kw["max_tokens"]
        body = {"title": "t", "body": "model report", "risks": []}
        return Completion(text=json.dumps(body), prompt_tokens=1, completion_tokens=1,
                          used_reasoning_fallback=False, protocol=Protocol.SCHEMA)


async def test_the_report_writer_has_headroom_over_what_nano_actually_used():
    client = _Client()

    report = await compose_report(client, job_id="j", goal="g", verified=[], discarded=[],
                                  uncovered=[], integration={}, tier=Tier.NANO)

    assert report.body == "model report"
    assert client.max_tokens >= 2 * _OBSERVED_LIVE_USAGE
