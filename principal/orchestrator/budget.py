"""Job-level budget decisions that sit above a single model call.

Token-level reserve/refuse lives in principal.models.budget, next to the client
that spends tokens. This module owns the job-wide fail-closed rule: no PR when
more than max_discard_ratio of planned tasks were discarded.
"""

from __future__ import annotations

from principal.db.store import Task


def discard_ratio(tasks: list[Task]) -> float:
    if not tasks:
        return 0.0
    settled = [t for t in tasks if t.state in {"verified", "discarded"}]
    if not settled:
        return 0.0
    discarded = [t for t in settled if t.state == "discarded"]
    return len(discarded) / len(tasks)


def exceeds_discard_cap(tasks: list[Task], max_ratio: float) -> bool:
    return discard_ratio(tasks) > max_ratio
