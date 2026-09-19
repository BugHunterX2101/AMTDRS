"""Reserve and refuse. Budget is enforced at the call site, not a reporting layer.

The model client refuses a call that would exceed the remaining budget and
raises; the task fails closed and the job moves to NoPR rather than truncating
silently. This is the code that turns "ran out of credits mid-benchmark" into a
clean stop instead of a confusing one.
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
        job = self.store.get_job(job_id)
        if job is None:
            return
        remaining = job.token_budget - job.tokens_spent
        if estimated > remaining:
            raise BudgetExhausted(spent=job.tokens_spent, limit=job.token_budget, requested=estimated)

    def spend(self, job_id: str, prompt_tokens: int, completion_tokens: int) -> None:
        self.store.add_tokens(job_id, prompt_tokens + completion_tokens)
