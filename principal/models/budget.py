"""Reserve and refuse. Budget is enforced at the call site, not a reporting layer.

The model client refuses a call that would exceed the remaining budget and
raises; the task fails closed and the job moves to NoPR rather than truncating
silently. This is the code that turns "ran out of credits mid-benchmark" into a
clean stop instead of a confusing one.

`reserve` has to actually reserve, not just check. Every task races several
candidates concurrently by construction (three temperatures, in parallel), so a
version that only reads `tokens_spent` and compares — without writing anything
until the far-later `spend()`, after a model round-trip — lets every concurrent
call see the same pre-spend balance and pass. The budget cap is then a check
that nothing subtracts against, and total spend can clear it by however many
calls happened to be in flight at once. Reserving the estimate immediately,
inside the same transaction as the check, closes that: the second concurrent
call sees the first call's reservation already counted.
"""

from __future__ import annotations

from principal.db.store import Store
from principal.errors import BudgetExhausted


def estimate_tokens(text: str) -> int:
    """Rough estimate for the pre-call check. Real counts come from the response
    and are what actually get written to the ledger; this only gates the call."""
    return max(1, len(text) // 3)


class TokenBudget:
    def __init__(self, store: Store):
        self.store = store

    def remaining(self, job_id: str) -> int:
        job = self.store.get_job(job_id)
        if job is None:
            return 0
        return max(0, job.token_budget - job.tokens_spent)

    def reserve(self, job_id: str, estimated: int) -> None:
        """Check and provisionally spend `estimated`, atomically.

        `settle` (below) corrects the ledger to the real usage once it is
        known; `release` refunds the whole reservation if the call never
        produced any billable usage at all. Every caller of `reserve` must
        eventually call exactly one of the two, or the reservation leaks.
        """
        with self.store.tx() as conn:
            row = conn.execute(
                "SELECT token_budget, tokens_spent FROM job WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return
            budget, spent = row["token_budget"], row["tokens_spent"]
            if spent + estimated > budget:
                raise BudgetExhausted(spent=spent, limit=budget, requested=estimated)
            conn.execute(
                "UPDATE job SET tokens_spent = tokens_spent + ? WHERE id = ?", (estimated, job_id)
            )

    def settle(self, job_id: str, estimated: int, actual: int) -> None:
        """Replace a reservation with the real usage, once the call returns.

        `actual` commonly differs from `estimated` in both directions — the
        estimate is a character-count heuristic, not a token count — so this
        applies the difference rather than re-adding the full actual amount,
        which would double-count the portion already reserved.
        """
        delta = actual - estimated
        if delta:
            self.store.add_tokens(job_id, delta)

    def release(self, job_id: str, estimated: int) -> None:
        """Refund a reservation for a call that produced no billable usage —
        it never reached the provider, or was served entirely from cache."""
        self.store.add_tokens(job_id, -estimated)

    def spend(self, job_id: str, prompt_tokens: int, completion_tokens: int) -> None:
        """Unreserved spend, for accounting not gated by `reserve` (e.g. a
        cache hit charged without ever having reserved against it)."""
        self.store.add_tokens(job_id, prompt_tokens + completion_tokens)
