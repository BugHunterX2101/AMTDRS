"""The model-call ledger: every token the budget charged can be attributed.

A run's `tokens_spent` is one job-level counter. Before the ledger, only the
coder attempts that survived diff parsing left a per-call record, so a live run
that spent 26,597 tokens could account for 7,309 of them, and nobody could say
which model had answered the planner. These tests pin the three properties that
close that gap: one `model.call` event per call, the ledger reconciling to the
budget, and the evidence bundle saying so rather than implying it.

The completions here are synthetic, standing in for the provider's response so
the accounting can be exercised without spending anything. They say nothing
about the platform.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from principal.config import Protocol, Tier
from principal.db.events import EventLog
from principal.db.store import Store
from principal.errors import Code, PrincipalError
from principal.models.client import Completion, ModelClient
from principal.orchestrator.evidence import _model_calls

MODELS = {Tier.NANO: "nano-id", Tier.SUPER: "super-id", Tier.ULTRA: "ultra-id"}
MESSAGES = [{"role": "user", "content": "x"}]


@pytest.fixture
def job_id(store: Store) -> str:
    return store.create_job(
        repo_url="x", commit_sha="deadbeef", goal="g", target_fqn=None,
        token_budget=1_000_000, tunables={},
    ).id


def _client(store: Store, events: EventLog | None, *, prompt=100, completion=40, abandoned=(0, 0)):
    client = ModelClient(
        store=store, base_url="http://invalid.test/v1/", api_key="k", model_ids=MODELS,
        capability={}, events=events,
    )

    async def fake_call(model, messages, chosen, schema, temperature, max_tokens):
        done = Completion(
            text="ok", prompt_tokens=prompt, completion_tokens=completion,
            used_reasoning_fallback=False, protocol=Protocol.TEXT,
        )
        return done, None, abandoned

    client._call_with_retries = fake_call  # type: ignore[method-assign]
    return client


def _calls(store: Store, job_id: str) -> list[dict]:
    rows = store.q("SELECT payload FROM event WHERE job_id = ? AND kind = 'model.call' ORDER BY seq", (job_id,))
    return [json.loads(r["payload"]) for r in rows]


async def test_each_call_leaves_one_record_naming_who_answered(store, events, job_id):
    client = _client(store, events)

    await client.complete(Tier.ULTRA, MESSAGES, job_id=job_id, role="planner")
    await client.complete(Tier.NANO, MESSAGES, job_id=job_id, role="coder")

    planner, coder = _calls(store, job_id)
    assert (planner["role"], planner["tier"], planner["model"]) == ("planner", "ultra", "ultra-id")
    assert (coder["role"], coder["tier"], coder["model"]) == ("coder", "nano", "nano-id")
    assert planner["billed_tokens"] == coder["billed_tokens"] == 140


async def test_the_ledger_reconciles_to_what_the_budget_charged(store, events, job_id):
    client = _client(store, events, abandoned=(10, 5))

    for _ in range(3):
        await client.complete(Tier.NANO, MESSAGES, job_id=job_id, role="coder")

    billed = sum(c["billed_tokens"] for c in _calls(store, job_id))
    assert billed == 3 * (100 + 40 + 10 + 5), "abandoned retries are billed, so the ledger must include them"
    assert store.get_job(job_id).tokens_spent == billed


async def test_a_completion_the_caller_later_rejects_is_still_in_the_ledger(store, events, job_id):
    """The case that left 3 of 6 attempts with no token count: the model answered
    and was billed, then the diff would not parse. The ledger is written at the
    call, before any caller can discard the result."""
    client = _client(store, events)

    await client.complete(Tier.NANO, MESSAGES, job_id=job_id, role="coder")
    # ...the caller now fails to parse the diff and records nothing on the attempt.

    assert len(_calls(store, job_id)) == 1
    assert store.get_job(job_id).tokens_spent == 140


async def test_a_call_that_fails_costs_nothing_and_records_nothing(store, events, job_id):
    client = _client(store, events)

    async def boom(*args, **kwargs):
        raise PrincipalError(Code.MODEL_EMPTY_RESPONSE, "the provider returned nothing")

    client._call_with_retries = boom  # type: ignore[method-assign]

    with pytest.raises(PrincipalError):
        await client.complete(Tier.NANO, MESSAGES, job_id=job_id, role="coder")

    assert _calls(store, job_id) == []
    assert store.get_job(job_id).tokens_spent == 0, "the reservation must be refunded"


async def test_blank_replies_that_were_billed_are_charged_when_the_call_finally_fails(
    store, events, job_id, monkeypatch,
):
    """A reply with no text was still generated and billed. When every retry comes
    back blank the call fails, and refunding the whole reservation then hid every
    token those replies cost."""
    from types import SimpleNamespace

    client = ModelClient(store=store, base_url="http://invalid.test/v1/", api_key="k",
                         model_ids=MODELS, capability={}, events=events)

    async def no_wait(*args, **kwargs):
        return None

    monkeypatch.setattr(client, "_backoff", no_wait)
    blank = SimpleNamespace(
        headers={},
        parse=lambda: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="", reasoning_content=None,
                                                             tool_calls=None))],
            usage=SimpleNamespace(prompt_tokens=50, completion_tokens=7),
        ),
    )

    async def create(**kwargs):
        return blank

    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        with_raw_response=SimpleNamespace(create=create))))

    with pytest.raises(PrincipalError) as err:
        await client.complete(Tier.NANO, MESSAGES, job_id=job_id, role="coder")

    assert err.value.code is Code.MODEL_EMPTY_RESPONSE
    [call] = _calls(store, job_id)
    assert call["role"] == "coder" and call["billed_tokens"] == 4 * 57
    assert store.get_job(job_id).tokens_spent == 4 * 57


async def test_budget_refusals_are_counted_per_job(store, events):
    small = store.create_job(repo_url="x", commit_sha="deadbeef", goal="g", target_fqn=None,
                             token_budget=10, tunables={}).id
    other = store.create_job(repo_url="x", commit_sha="deadbeef", goal="g", target_fqn=None,
                             token_budget=1_000_000, tunables={}).id
    client = _client(store, events)

    with pytest.raises(PrincipalError):
        await client.complete(Tier.NANO, MESSAGES, job_id=small, role="coder")

    assert client.refusals_for(small) == 1
    assert client.refusals_for(other) == 0


async def test_a_call_with_no_job_is_not_recorded(store, events):
    client = _client(store, events)
    await client.complete(Tier.NANO, MESSAGES, role="probe")
    assert store.q("SELECT 1 FROM event WHERE kind = 'model.call'") == []


async def test_the_record_is_written_even_when_no_event_log_is_wired(store, job_id):
    """The evidence reads the database, so attribution must not depend on every
    construction site remembering to pass an EventLog."""
    client = _client(store, None)
    await client.complete(Tier.SUPER, MESSAGES, job_id=job_id, role="repairer")
    assert [c["role"] for c in _calls(store, job_id)] == ["repairer"]


# ----------------------------------------------------------------- the evidence --


async def test_the_evidence_breaks_usage_down_and_reconciles(store, events, job_id):
    client = _client(store, events)
    await client.complete(Tier.ULTRA, MESSAGES, job_id=job_id, role="planner")
    for _ in range(2):
        await client.complete(Tier.NANO, MESSAGES, job_id=job_id, role="coder")

    calls = _model_calls(store, job_id, store.get_job(job_id).tokens_spent)

    assert calls["calls"] == 3
    by = {(r["role"], r["model"]): r for r in calls["by_role"]}
    assert by[("planner", "ultra-id")]["calls"] == 1
    assert by[("coder", "nano-id")]["calls"] == 2
    assert by[("coder", "nano-id")]["billed_tokens"] == 280
    assert calls["attributed_tokens"] == 420
    assert calls["unattributed_tokens"] == 0


async def test_a_cancelled_call_gives_its_reservation_back(store, events, job_id):
    """The leak the first uncached live run exposed: 26,626 tokens charged, 22,761
    in the ledger. The scheduler cancels the losing candidates once one goes green,
    `CancelledError` is not an `Exception`, and the reservation made on entry was
    neither settled nor released, so it sat in `tokens_spent` with no record."""
    client = _client(store, events)

    async def cancelled(model, messages, chosen, schema, temperature, max_tokens):
        raise asyncio.CancelledError

    client._call_with_retries = cancelled  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await client.complete(Tier.NANO, MESSAGES, job_id=job_id, role="coder")

    assert store.get_job(job_id).tokens_spent == 0
    assert _calls(store, job_id) == []


async def test_the_characterise_step_does_not_charge_its_model_call_twice(
    store, events, job_id, settings, tmp_path, monkeypatch,
):
    """`ModelClient.complete` settles the call against `job_id`; `_characterise`
    used to add the same prompt+completion again, so the ledger summed to half of
    what the job reported for that call. Nothing past the model call runs here."""
    from types import SimpleNamespace

    from principal.agents import characteriser
    from principal.orchestrator import job as job_module

    class Stop(Exception):
        pass

    store.update_job(job_id, baseline_image="c0")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    target = SimpleNamespace(
        id=1, fqn="m.f", path="src/m.py", signature="()", line_start=1, line_end=2,
    )

    async def snapshot(*args, **kwargs):
        return tmp_path

    async def from_uuid(image):
        return image

    async def stop_after_the_model_call(*args, **kwargs):
        raise Stop

    monkeypatch.setattr(job_module, "ensure_snapshot", snapshot)
    monkeypatch.setattr(job_module, "establish_safety_net", stop_after_the_model_call)
    monkeypatch.setattr(characteriser, "extract_module", lambda text: text)

    with pytest.raises(Stop):
        await job_module._characterise(
            job_id, store, events, SimpleNamespace(from_uuid=from_uuid), _client(store, events),
            "no-such-graph", target, settings,
        )

    billed = sum(c["billed_tokens"] for c in _calls(store, job_id))
    assert billed == 140
    assert store.get_job(job_id).tokens_spent == billed


async def test_unattributed_tokens_are_reported_not_hidden(store, events, job_id):
    client = _client(store, events)
    await client.complete(Tier.NANO, MESSAGES, job_id=job_id, role="coder")
    store.add_tokens(job_id, 500)  # spend that bypassed the client

    calls = _model_calls(store, job_id, store.get_job(job_id).tokens_spent)

    assert calls["unattributed_tokens"] == 500


def test_a_job_recorded_before_the_ledger_gets_no_breakdown(store, job_id):
    """Nothing was recorded, so nothing is claimed. A missing ledger is not 'zero'."""
    store.add_tokens(job_id, 26_597)
    assert _model_calls(store, job_id, 26_597) is None


async def test_a_missing_key_is_refused_before_any_request_and_costs_nothing(store, events, job_id):
    """The hosted demo has no credentials. The call used to go out with a placeholder
    key and come back as the provider's 401, which a user then read as a platform fault."""
    client = ModelClient(store=store, base_url="http://invalid.test/v1/", api_key="",
                         model_ids=MODELS, capability={}, events=events)

    with pytest.raises(PrincipalError) as raised:
        await client.complete(Tier.NANO, MESSAGES, job_id=job_id, role="planner")

    assert raised.value.code is Code.CONFIG_MISSING
    assert "NEBIUS_API_KEY" in raised.value.message
    assert _calls(store, job_id) == []
    assert store.get_job(job_id).tokens_spent == 0
