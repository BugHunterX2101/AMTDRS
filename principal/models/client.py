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
from typing import TYPE_CHECKING, Any

import httpx
from openai import APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel

from principal.config import Protocol, Tier
from principal.db.store import Store
from principal.errors import Code, ModelEmptyResponse, PrincipalError
from principal.models.budget import TokenBudget, estimate_tokens
from principal.models.cache import CachedCompletion, DiskCache, cache_key
from principal.models.protocols import build_request, extract_structured, text_of

if TYPE_CHECKING:
    from principal.db.events import EventLog

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

    # Provenance label. A subclass that does not call Token Factory overrides
    # this, so a result can never be presented as live when it was not.
    execution_mode = "live"

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
        events: EventLog | None = None,
    ):
        self.store = store
        # Where each call's `model.call` record goes. Optional so a client built
        # without one still writes the record to the database, which is what the
        # evidence reads; the log only adds the live stream and the trace file.
        self.events = events
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
        # The status endpoint reports refusals per job; the total above is process-wide.
        self._refusals_by_job: dict[str, int] = {}
        self.rate_headroom = RateHeadroom()

    def refusals_for(self, job_id: str) -> int:
        return self._refusals_by_job.get(job_id, 0)

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
        role: str | None = None,
    ) -> Completion:
        model = self.model_ids[tier]
        chosen = protocol or self.protocol_for(model)

        estimate = sum(estimate_tokens(m.get("content", "")) for m in messages) + max_tokens
        reserved = False
        if job_id is not None:
            try:
                self.budget.reserve(job_id, estimate)
                reserved = True
            except PrincipalError:
                self.budget_refusals += 1
                self._refusals_by_job[job_id] = self.refusals_for(job_id) + 1
                raise

        key = cache_key(model, messages, temperature, chosen.value)
        if self.cache is not None:
            cached = self.cache.get(key)
            if cached is not None:
                hit = Completion(
                    text=cached.text, prompt_tokens=cached.prompt_tokens,
                    completion_tokens=cached.completion_tokens,
                    used_reasoning_fallback=cached.used_reasoning_fallback,
                    protocol=Protocol(cached.protocol), from_cache=True,
                )
                if job_id is not None and reserved:
                    billed = cached.prompt_tokens + cached.completion_tokens
                    self.budget.settle(job_id, estimate, billed)
                    self._record_call(job_id, role, tier, model, hit, billed)
                return hit

        try:
            completion, downgraded_to, abandoned = await self._call_with_retries(
                model, messages, chosen, schema, temperature, max_tokens
            )
        except (Exception, asyncio.CancelledError) as exc:
            # The reservation was provisional spend against a call that never
            # produced anything billable — refund it, or every failed call
            # (a timeout, a 5xx that exhausts retries, a malformed diff) would
            # permanently burn its estimate out of the job's real budget.
            # A cancelled call is the same: the scheduler cancels the losing
            # candidates once one goes green, and `CancelledError` is not an
            # `Exception`, so it has to be named.
            # The exception is blank replies the provider generated and billed
            # before the call gave up: those tokens were spent, so they are
            # charged and recorded rather than refunded.
            if job_id is not None and reserved:
                prompt_spent, completion_spent = getattr(exc, "billed_usage", (0, 0))
                if prompt_spent + completion_spent:
                    self.budget.settle(job_id, estimate, prompt_spent + completion_spent)
                    self._record_call(job_id, role, tier, model, Completion(
                        text="", prompt_tokens=prompt_spent, completion_tokens=completion_spent,
                        used_reasoning_fallback=False, protocol=chosen,
                    ), prompt_spent + completion_spent)
                else:
                    self.budget.release(job_id, estimate)
            raise

        if downgraded_to is not None:
            self.capability[model] = downgraded_to
            self.protocol_downgrades += 1

        if job_id is not None and reserved:
            actual = (
                completion.prompt_tokens + abandoned[0] + completion.completion_tokens + abandoned[1]
            )
            self.budget.settle(job_id, estimate, actual)
            self._record_call(job_id, role, tier, model, completion, actual)

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

    def _record_call(
        self, job_id: str, role: str | None, tier: Tier, model: str, completion: Completion, billed: int,
    ) -> None:
        """One `model.call` record per billed call, written here and not by the
        caller, so a completion that a caller goes on to reject (a diff that will
        not parse) is still attributed. `billed` is what the budget was charged,
        abandoned retries included, so the records sum to `tokens_spent`."""
        payload = {
            "role": role or "unlabelled", "tier": tier.value, "model": model,
            "prompt_tokens": completion.prompt_tokens, "completion_tokens": completion.completion_tokens,
            "billed_tokens": billed, "from_cache": completion.from_cache,
            "protocol": completion.protocol.value,
            "reasoning_fallback": completion.used_reasoning_fallback,
        }
        if self.events is not None:
            self.events.emit(job_id, "model.call", payload)
        else:
            with self.store.tx():
                self.store.append_event(job_id, "model.call", payload)

    async def _call_with_retries(
        self, model: str, messages: list[dict[str, Any]], protocol: Protocol,
        schema: type[BaseModel] | None, temperature: float, max_tokens: int,
    ) -> tuple[Completion, Protocol | None, tuple[int, int]]:
        downgraded_to: Protocol | None = None
        current = protocol
        attempts = 0
        # Usage from attempts whose output was thrown away. Billed by the
        # provider, so charged to the budget even though no caller sees it.
        abandoned_prompt = 0
        abandoned_completion = 0

        def failed(err: PrincipalError) -> PrincipalError:
            # Carries the abandoned usage out, so `complete` charges it.
            err.billed_usage = (abandoned_prompt, abandoned_completion)  # type: ignore[attr-defined]
            return err

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
                        await self._backoff(attempts, retry_after=_retry_after(exc))
                        continue
                    raise failed(PrincipalError(Code.MODEL_RATE_LIMITED, str(exc))) from exc
                if exc.status_code >= 500 and attempts <= 3:
                    await self._backoff(attempts, retry_after=_retry_after(exc))
                    continue
                raise failed(PrincipalError(Code.MODEL_EMPTY_RESPONSE, f"HTTP {exc.status_code}: {exc}")) from exc
            except RateLimitError as exc:
                self.rate_limit_backoffs += 1
                if attempts <= 3:
                    await self._backoff(attempts, retry_after=_retry_after(exc))
                    continue
                raise failed(PrincipalError(Code.MODEL_RATE_LIMITED, str(exc))) from exc
            except (APITimeoutError, httpx.TimeoutException) as exc:
                if attempts <= 3:
                    await self._backoff(attempts)
                    continue
                raise failed(PrincipalError(Code.MODEL_EMPTY_RESPONSE, f"timeout: {exc}")) from exc

            message = parsed.choices[0].message
            raw = extract_structured(message, current, schema)
            _, used_fallback = text_of(message)
            if used_fallback:
                self.reasoning_fallback_count += 1

            usage = parsed.usage
            if not raw.strip():
                # A blank response was still generated and still billed. Carry
                # its usage forward so the budget reflects what was actually
                # spent rather than only what was eventually returned — a
                # counter that undercounts is worse than no counter, because
                # the whole point of the cap is that it cannot be overrun
                # quietly.
                if usage:
                    abandoned_prompt += int(usage.prompt_tokens or 0)
                    abandoned_completion += int(usage.completion_tokens or 0)
                if attempts <= 3:
                    await self._backoff(attempts, base=0.5)
                    continue
                raise failed(ModelEmptyResponse(model))

            completion = Completion(
                text=raw, prompt_tokens=int(usage.prompt_tokens) if usage else 0,
                completion_tokens=int(usage.completion_tokens) if usage else estimate_tokens(raw),
                used_reasoning_fallback=used_fallback, protocol=current,
            )
            return completion, downgraded_to, (abandoned_prompt, abandoned_completion)

    def _record_rate_headroom(self, headers: Any) -> None:
        # Widened past AttributeError deliberately: a provider that sends a
        # non-integer value here would otherwise turn a completed, already-paid-
        # for call into an exception on the success path. Headroom is telemetry;
        # it is never worth failing a good response over.
        try:
            rr = headers.get("x-ratelimit-remaining-requests")
            rt = headers.get("x-ratelimit-remaining-tokens")
            if rr is not None:
                self.rate_headroom.remaining_requests = int(rr)
            if rt is not None:
                self.rate_headroom.remaining_tokens = int(rt)
        except (AttributeError, TypeError, ValueError):
            return

    def counters(self) -> dict[str, int]:
        return {
            "reasoning_fallback_count": self.reasoning_fallback_count,
            "protocol_downgrades": self.protocol_downgrades,
            "rate_limit_backoffs": self.rate_limit_backoffs,
            "budget_refusals": self.budget_refusals,
        }

    async def _backoff(self, attempt: int, base: float = 1.0, retry_after: float | None = None) -> None:
        """Exponential with jitter, unless the server said how long to wait.

        Guessing when `Retry-After` is present is wrong in both directions: too
        short earns another 429 and burns a retry, too long adds dead time to
        every rate-limited call in the run.
        """
        if retry_after is not None and retry_after > 0:
            await asyncio.sleep(min(retry_after, 60.0))
            return
        delay = base * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
        await asyncio.sleep(min(delay, 20.0))


def _retry_after(exc: Exception) -> float | None:
    """Seconds from a `Retry-After` header, if the response carried one."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after")
        return float(raw) if raw is not None else None
    except (AttributeError, TypeError, ValueError):
        return None
