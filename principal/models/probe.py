"""The 3x3 capability probe.

For each configured model, fire one tiny request under each output protocol and
cache the outcome. The whole probe costs a few hundred tokens once per process
and turns the project's biggest unknown into a boot-time fact instead of a week-
five discovery. Include a check for whether `content` came back empty with text
in `reasoning_content`, and whether `completion_tokens` accounts for it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from openai import AsyncOpenAI
from pydantic import BaseModel

from principal.config import Protocol
from principal.models.protocols import build_request, extract_structured, text_of


class _ProbeSchema(BaseModel):
    ok: bool
    word: str


@dataclass(slots=True)
class ProbeResult:
    protocol: Protocol
    worked: bool
    used_reasoning_fallback: bool
    completion_tokens_covers_reasoning: bool | None
    error: str | None = None


@dataclass(slots=True)
class ModelCapability:
    model: str
    best_protocol: Protocol
    results: dict[Protocol, ProbeResult] = field(default_factory=dict)

    def as_row(self) -> dict[str, object]:
        return {
            "model": self.model,
            "best_protocol": self.best_protocol.value,
            "results": {
                p.value: {
                    "worked": r.worked,
                    "used_reasoning_fallback": r.used_reasoning_fallback,
                    "error": r.error,
                }
                for p, r in self.results.items()
            },
        }


PROBE_PROMPT = (
    "Reply with a JSON object matching the schema. Set ok to true and word to "
    "the single word 'principal'."
)


async def _try_protocol(
    client: AsyncOpenAI, model: str, protocol: Protocol
) -> ProbeResult:
    shape = build_request(protocol, _ProbeSchema)
    messages = [{"role": "user", "content": PROBE_PROMPT}]
    kwargs: dict[str, object] = {"model": model, "messages": messages, "temperature": 0.0, "max_tokens": 200}
    if shape.response_format is not None:
        kwargs["response_format"] = shape.response_format
    if shape.tools is not None:
        kwargs["tools"] = shape.tools
        kwargs["tool_choice"] = shape.tool_choice

    try:
        resp = await client.chat.completions.create(**kwargs)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(protocol, worked=False, used_reasoning_fallback=False,
                            completion_tokens_covers_reasoning=None, error=str(exc)[:300])

    message = resp.choices[0].message
    text, used_fallback = text_of(message)
    raw = extract_structured(message, protocol, _ProbeSchema)

    try:
        parsed = _ProbeSchema.model_validate_json(raw)
        worked = parsed.ok
    except Exception:  # noqa: BLE001
        worked = False

    covers_reasoning: bool | None = None
    usage = getattr(resp, "usage", None)
    if usage is not None and used_fallback:
        reasoning_tokens = getattr(
            getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None
        )
        completion_tokens = getattr(usage, "completion_tokens", None)
        if reasoning_tokens is not None and completion_tokens is not None:
            covers_reasoning = completion_tokens >= reasoning_tokens > 0

    return ProbeResult(
        protocol=protocol, worked=worked, used_reasoning_fallback=used_fallback,
        completion_tokens_covers_reasoning=covers_reasoning,
        error=None if worked else f"parsed but ok!=true, raw={raw[:200]}",
    )


async def probe_model(client: AsyncOpenAI, model: str) -> ModelCapability:
    results: dict[Protocol, ProbeResult] = {}
    for protocol in (Protocol.SCHEMA, Protocol.TOOLS, Protocol.TEXT):
        results[protocol] = await _try_protocol(client, model, protocol)

    for protocol in (Protocol.SCHEMA, Protocol.TOOLS, Protocol.TEXT):
        if results[protocol].worked:
            return ModelCapability(model=model, best_protocol=protocol, results=results)
    # Every protocol technically "worked" through the text path in the sense that
    # a diff parser only ever needs raw text, so TEXT is always the floor.
    return ModelCapability(model=model, best_protocol=Protocol.TEXT, results=results)


async def probe_all(client: AsyncOpenAI, models: list[str]) -> dict[str, ModelCapability]:
    out: dict[str, ModelCapability] = {}
    for model in models:
        out[model] = await probe_model(client, model)
    return out


def summary_table(caps: dict[str, ModelCapability]) -> str:
    """3x3 table for the run log and the tooling feedback."""
    lines = [f"{'model':<45} {'schema':<8} {'tools':<8} {'text':<8} best"]
    for model, cap in caps.items():
        row = [model]
        for p in (Protocol.SCHEMA, Protocol.TOOLS, Protocol.TEXT):
            r = cap.results.get(p)
            mark = "ok" if r and r.worked else ("fallback" if r and r.used_reasoning_fallback else "fail")
            row.append(mark)
        row.append(cap.best_protocol.value)
        lines.append(f"{row[0]:<45} {row[1]:<8} {row[2]:<8} {row[3]:<8} {row[4]}")
    return "\n".join(lines)


def dump_json(caps: dict[str, ModelCapability]) -> str:
    return json.dumps({m: c.as_row() for m, c in caps.items()}, indent=2)
