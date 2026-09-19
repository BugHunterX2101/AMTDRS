"""Schema, tools and text adapters, and the reasoning_content trap.

Token Factory documents both JSON schema output and forced function calling, and
notes that structured-output support is per-model. Against that, a Nebius API
issue reports that the Nemotron models served there are reasoning models that
return empty `content` with the text in `reasoning_content`, and that tool calls
fail with 400 through the OpenAI-compatible wrapper. Do not pick a protocol at
build time — probe it and cache the outcome (see probe.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from principal.config import Protocol


def text_of(message: Any) -> tuple[str, bool]:
    """Read a completion's text. Returns (text, used_reasoning_fallback).

    This is four lines and it is the difference between a working system and a
    day lost to a debugger. If the fallback fires constantly, token accounting is
    undercounting reasoning tokens and the budget model is wrong — log the flag.
    """
    content = (getattr(message, "content", None) or "").strip()
    if content:
        return content, False
    reasoning = (getattr(message, "reasoning_content", None) or "").strip()
    return reasoning, bool(reasoning)


@dataclass(slots=True)
class RequestShape:
    """What to send, chosen by protocol. `messages` may be extended by the caller."""

    extra_body: dict[str, Any]
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    response_format: dict[str, Any] | None = None


def build_request(protocol: Protocol, schema: type[BaseModel] | None) -> RequestShape:
    if protocol is Protocol.SCHEMA and schema is not None:
        return RequestShape(
            extra_body={},
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": True,
                },
            },
        )
    if protocol is Protocol.TOOLS and schema is not None:
        return RequestShape(
            extra_body={},
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": f"emit_{schema.__name__.lower()}",
                        "description": f"Emit a validated {schema.__name__}.",
                        "parameters": schema.model_json_schema(),
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": f"emit_{schema.__name__.lower()}"}},
        )
    return RequestShape(extra_body={})


def extract_structured(message: Any, protocol: Protocol, schema: type[BaseModel] | None) -> str:
    """Pull the JSON text out of wherever the protocol put it."""
    if protocol is Protocol.TOOLS and schema is not None:
        calls = getattr(message, "tool_calls", None) or []
        if calls:
            return calls[0].function.arguments
        # Fall through: some deployments return schema-shaped text even when
        # tools were requested, which is exactly the kind of drift the probe
        # exists to catch rather than assume away.
    text, _ = text_of(message)
    return text
