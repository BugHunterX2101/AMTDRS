"""The only place a token is spent.

No other module imports an HTTP client for the inference API, which makes the
budget, the accounting and the cache exactly one implementation each and makes
the token counters trustworthy.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any

import httpx
from openai import APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel

from principal.config import Protocol, Tier
from principal.db.store import Store
from principal.errors import Code, ModelEmptyResponse, PrincipalError
from principal.models.budget import TokenBudget, estimate_tokens
from principal.models.cache import CachedCompletion, DiskCache, cache_key
from principal.models.protocols import build_request, extract_structured, text_of

logger = logging.getLogger("principal.models")


@dataclass(slots=True)
class Completion:
    text: str
    prompt_tokens: int
    completion_tokens: int
    used_reasoning_fallback: bool
    protocol: Protocol
    from_cache: bool = False


@dataclass(slots=True)
class RateHeadroom:
    remaining_requests: int | None = None
    remaining_tokens: int | None = None


class ModelClient:
    """Budget check, protocol application, reasoning_content extraction,
    retries and accounting. One method, so no caller has to remember any of it.
    """

    def __init__(
        self,
        *,
        store: Store,
        base_url: str,
        api_key: str,
        model_ids: dict[Tier, str],
        capability: dict[str, Protocol],
        cache: DiskCache | None = None,
        forced_protocol: Protocol | None = None,
    ):
        self.store = store
        self.budget = TokenBudget(store)
        self.model_ids = model_ids
        self.capability = dict(capability)
        self.forced_protocol = forced_protocol
        self.cache = cache
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key or "unset")
        self.reasoning_fallback_count = 0
        self.protocol_downgrades = 0
        self.rate_limit_backoffs = 0
        self.budget_refusals = 0
        self.rate_headroom = RateHeadroom()

    def protocol_for(self, model: str) -> Protocol:
        if self.forced_protocol is not None:
            return self.forced_protocol
        return self.capability.get(model, Protocol.TEXT)

    async def complete(
        self,
        tier: Tier,
        messages: list[dict[str, Any]],
        *,
        protocol: Protocol | None = None,
        schema: type[BaseModel] | None = None,
        temperature: float = 0.0,
        job_id: str | None = None,
        max_tokens: int = 4000,
    ) -> Completion:
        model = self.model_ids[tier]
        chosen = protocol or self.protocol_for(model)

        estimate = sum(estimate_tokens(m.get("content", "")) for m in messages) + max_tokens
        if job_id is not None:
            try:
                self.budget.reserve(job_id, estimate)
            except PrincipalError:
                self.budget_refusals += 1
                raise

        key = cache_key(model, messages, temperature, chosen.value)
        if self.cache is not None:
            cached = self.cache.get(key)
            if cached is not None:
                if job_id is not None:
                    self.budget.spend(job_id, cached.prompt_tokens, cached.completion_tokens)
                return Completion(
                    text=cached.text, prompt_tokens=cached.prompt_tokens,
                    completion_tokens=cached.completion_tokens,
                    used_reasoning_fallback=cached.used_reasoning_fallback,
                    protocol=Protocol(cached.protocol), from_cache=True,
                )

        completion, downgraded_to = await self._call_with_retries(
            model, messages, chosen, schema, temperature, max_tokens
        )
        if downgraded_to is not None:
            self.capability[model] = downgraded_to
            self.protocol_downgrades += 1

        if job_id is not None:
            self.budget.spend(job_id, completion.prompt_tokens, completion.completion_tokens)

        if self.cache is not None:
            self.cache.put(
                key,
                CachedCompletion(
                    text=completion.text, prompt_tokens=completion.prompt_tokens,
                    completion_tokens=completion.completion_tokens,
                    used_reasoning_fallback=completion.used_reasoning_fallback,
                    protocol=completion.protocol.value,
                ),
            )
        return completion

    async def _call_with_retries(
        self, model: str, messages: list[dict[str, Any]], protocol: Protocol,
        schema: type[BaseModel] | None, temperature: float, max_tokens: int,
    ) -> tuple[Completion, Protocol | None]:
        downgraded_to: Protocol | None = None
        current = protocol
        attempts = 0
        while True:
            attempts += 1
            shape = build_request(current, schema)
            kwargs: dict[str, Any] = {
                "model": model, "messages": messages, "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if shape.response_format is not None:
                kwargs["response_format"] = shape.response_format
            if shape.tools is not None:
                kwargs["tools"] = shape.tools
                kwargs["tool_choice"] = shape.tool_choice

            try:
                resp = await self._client.chat.completions.with_raw_response.create(**kwargs)
                self._record_rate_headroom(resp.headers)
                parsed = resp.parse()
            except APIStatusError as exc:
                if exc.status_code == 400 and current is not Protocol.TEXT:
                    # A protocol downgrade is a permanent fact for the process
                    # lifetime, not a per-call accident. Fall back one level and
                    # retry once, unchanged otherwise.
                    logger.warning("model=%s protocol=%s rejected with 400, downgrading", model, current)
                    current = Protocol.TOOLS if current is Protocol.SCHEMA else Protocol.TEXT
                    downgraded_to = current
                    continue
                if exc.status_code == 429:
                    self.rate_limit_backoffs += 1
                    if attempts <= 3:
                        await self._backoff(attempts)
                        continue
                    raise PrincipalError(Code.MODEL_RATE_LIMITED, str(exc)) from exc
                if exc.status_code >= 500 and attempts <= 3:
                    await self._backoff(attempts)
                    continue
                raise PrincipalError(Code.MODEL_EMPTY_RESPONSE, f"HTTP {exc.status_code}: {exc}") from exc
            except RateLimitError as exc:
                self.rate_limit_backoffs += 1
                if attempts <= 3:
                    await self._backoff(attempts)
                    continue
                raise PrincipalError(Code.MODEL_RATE_LIMITED, str(exc)) from exc
            except (APITimeoutError, httpx.TimeoutException) as exc:
                if attempts <= 3:
                    await self._backoff(attempts)
                    continue
                raise PrincipalError(Code.MODEL_EMPTY_RESPONSE, f"timeout: {exc}") from exc

            message = parsed.choices[0].message
            raw = extract_structured(message, current, schema)
            _, used_fallback = text_of(message)
            if used_fallback:
                self.reasoning_fallback_count += 1

            if not raw.strip():
                if attempts <= 3:
                    await self._backoff(attempts, base=0.5)
                    continue
                raise ModelEmptyResponse(model)

            usage = parsed.usage
            completion = Completion(
                text=raw, prompt_tokens=int(usage.prompt_tokens) if usage else 0,
                completion_tokens=int(usage.completion_tokens) if usage else estimate_tokens(raw),
                used_reasoning_fallback=used_fallback, protocol=current,
            )
            return completion, downgraded_to

    async def _backoff(self, attempt: int, base: float = 1.0) -> None:
        delay = base * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
        await asyncio.sleep(min(delay, 20.0))

    def _record_rate_headroom(self, headers: Any) -> None:
        try:
            rr = headers.get("x-ratelimit-remaining-requests")
            rt = headers.get("x-ratelimit-remaining-tokens")
        except AttributeError:
            return
        if rr is not None:
            self.rate_headroom.remaining_requests = int(rr)
        if rt is not None:
            self.rate_headroom.remaining_tokens = int(rt)

    def counters(self) -> dict[str, int]:
        return {
            "reasoning_fallback_count": self.reasoning_fallback_count,
            "protocol_downgrades": self.protocol_downgrades,
            "rate_limit_backoffs": self.rate_limit_backoffs,
            "budget_refusals": self.budget_refusals,
        }
