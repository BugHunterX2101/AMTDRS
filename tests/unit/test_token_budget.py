"""TokenBudget: reserve is a real reservation, not a read-then-check.

Every task races several candidates concurrently by construction (coder
temperatures 0.0/0.4/0.8, in parallel), so a budget check that only reads
`tokens_spent` and never writes anything until a far-later `spend()` lets
every concurrent caller see the same pre-spend balance and pass — the cap
enforced in isolation, not enforced in aggregate. These tests pin the
reserve/settle/release contract that closes that gap, since nothing else in
this suite exercises `principal.models.budget` at all.
"""

from __future__ import annotations

import pytest

from principal.db.store import Store
from principal.errors import BudgetExhausted
from principal.models.budget import TokenBudget


@pytest.fixture
def job_id(store: Store) -> str:
    job = store.create_job(
        repo_url="x", commit_sha="deadbeef", goal="g", target_fqn=None,
        token_budget=1000, tunables={},
    )
    return job.id


def test_reserve_immediately_counts_against_remaining_budget(store: Store, job_id: str):
    """The whole point: a second concurrent reserve must see the first one's
    reservation, not the balance from before either call started."""
    budget = TokenBudget(store)
    budget.reserve(job_id, 700)
    assert budget.remaining(job_id) == 300

    with pytest.raises(BudgetExhausted):
        budget.reserve(job_id, 400)


def test_two_concurrent_reservations_cannot_both_fit_in_the_gap(store: Store, job_id: str):
    """The exact scenario this fix exists for: two callers each estimate a
    call that individually fits, but not both at once."""
    budget = TokenBudget(store)
    budget.reserve(job_id, 600)
    with pytest.raises(BudgetExhausted):
        budget.reserve(job_id, 600)  # 600 + 600 > 1000, correctly refused
    assert budget.remaining(job_id) == 400


def test_settle_applies_only_the_delta_from_the_estimate(store: Store, job_id: str):
    budget = TokenBudget(store)
    budget.reserve(job_id, 500)
    budget.settle(job_id, estimated=500, actual=620)  # ran over the estimate
    assert budget.remaining(job_id) == 380  # 1000 - 620, not 1000 - 500 - 620

    budget.settle(job_id, estimated=0, actual=0)  # no-op delta is a no-op
    assert budget.remaining(job_id) == 380


def test_settle_refunds_when_actual_usage_is_under_the_estimate(store: Store, job_id: str):
    budget = TokenBudget(store)
    budget.reserve(job_id, 500)
    budget.settle(job_id, estimated=500, actual=200)
    assert budget.remaining(job_id) == 800


def test_release_fully_refunds_a_reservation_that_produced_no_usage(store: Store, job_id: str):
    """A call that times out after exhausting retries, or fails to parse, is
    billed nothing — release must return the estimate exactly, not partially."""
    budget = TokenBudget(store)
    budget.reserve(job_id, 500)
    budget.release(job_id, 500)
    assert budget.remaining(job_id) == 1000


def test_reserve_on_an_unknown_job_is_a_silent_noop(store: Store):
    """Mirrors the pre-existing behaviour for a job id that does not exist —
    callers already treat a missing job as "nothing to enforce", not an error."""
    budget = TokenBudget(store)
    budget.reserve("does-not-exist", 999999)  # must not raise
