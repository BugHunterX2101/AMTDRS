"""Scoring, and the reason it has three buckets instead of two.

A run either verified a change, failed to produce a correct one, or never got
the chance because something outside the system broke. Collapsing the third into
the second is the obvious simplification and it is dishonest in both directions
at once: it understates the verified-refactor rate, which makes the system look
worse than it is, and it hides a platform problem inside a quality number, which
is exactly the signal that belongs in the tooling feedback.

So: verified / failed on merit / excluded as infrastructure. Every excluded
result is listed individually with its error code, so the exclusion is auditable
rather than a number to be taken on trust.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from principal.errors import INFRASTRUCTURE, Code


@dataclass(slots=True)
class TaskOutcome:
    task_id: str
    arm: str
    verified: bool
    error_code: str | None = None
    duration_s: float = 0.0
    tokens: int = 0
    sandbox_runs: int = 0
    candidates: int = 0
    repairs: int = 0
    pr_opened: bool = False
    note: str = ""

    @property
    def infrastructure(self) -> bool:
        if self.verified or self.error_code is None:
            return False
        try:
            return Code(self.error_code) in INFRASTRUCTURE
        except ValueError:
            return False


@dataclass(slots=True)
class ArmScore:
    arm: str
    attempted: int = 0
    verified: int = 0
    failed_on_merit: int = 0
    excluded_infrastructure: int = 0
    excluded_detail: list[dict[str, str]] = field(default_factory=list)
    total_tokens: int = 0
    total_sandbox_runs: int = 0
    total_duration_s: float = 0.0

    @property
    def scored(self) -> int:
        """The denominator. Infrastructure failures are excluded from it, not
        counted as losses — that is the entire point of the third bucket."""
        return self.verified + self.failed_on_merit

    @property
    def verified_rate(self) -> float | None:
        return (self.verified / self.scored) if self.scored else None

    @property
    def tokens_per_verified(self) -> float | None:
        return (self.total_tokens / self.verified) if self.verified else None

    def to_row(self) -> dict[str, object]:
        return {
            "arm": self.arm,
            "attempted": self.attempted,
            "verified": self.verified,
            "failed_on_merit": self.failed_on_merit,
            "excluded_infrastructure": self.excluded_infrastructure,
            "verified_rate": _round(self.verified_rate),
            "tokens_per_verified": _round(self.tokens_per_verified, 0),
            "sandbox_runs": self.total_sandbox_runs,
            "wall_clock_s": _round(self.total_duration_s, 1),
        }


def score(outcomes: list[TaskOutcome]) -> dict[str, ArmScore]:
    arms: dict[str, ArmScore] = {}
    for o in outcomes:
        s = arms.setdefault(o.arm, ArmScore(arm=o.arm))
        s.attempted += 1
        s.total_tokens += o.tokens
        s.total_sandbox_runs += o.sandbox_runs
        s.total_duration_s += o.duration_s

        if o.verified:
            s.verified += 1
        elif o.infrastructure:
            s.excluded_infrastructure += 1
            s.excluded_detail.append(
                {"task_id": o.task_id, "code": o.error_code or "", "note": o.note}
            )
        else:
            s.failed_on_merit += 1
    return arms


def render(arms: dict[str, ArmScore]) -> str:
    """A table, then every exclusion by name. The exclusions are printed in full
    rather than summarised, because a reader has to be able to disagree with
    them."""
    order = ["A", "B", "C"]
    rows = [arms[a].to_row() for a in order if a in arms]
    rows += [s.to_row() for k, s in sorted(arms.items()) if k not in order]

    headers = ["arm", "attempted", "verified", "failed_on_merit",
               "excluded_infrastructure", "verified_rate", "tokens_per_verified",
               "sandbox_runs", "wall_clock_s"]
    widths = {h: max(len(h), *(len(str(r[h])) for r in rows)) if rows else len(h) for h in headers}

    out = [
        "  ".join(h.ljust(widths[h]) for h in headers),
        "  ".join("-" * widths[h] for h in headers),
    ]
    out += ["  ".join(str(r[h]).ljust(widths[h]) for h in headers) for r in rows]

    out.append("")
    out.append(f"{ARM_LEGEND}")

    excluded = [(a, d) for a, s in sorted(arms.items()) for d in s.excluded_detail]
    if excluded:
        out.append("")
        out.append("Excluded as infrastructure (not counted against any arm):")
        for arm, d in excluded:
            out.append(f"  arm {arm}  {d['task_id']:<28} {d['code']}  {d['note']}")
    else:
        out.append("")
        out.append("No results excluded as infrastructure.")
    return "\n".join(out)


ARM_LEGEND = (
    "A = single shot, no gates      (what one model call alone achieves)\n"
    "B = one candidate, gates on    (isolates the value of verification)\n"
    "C = full swarm                 (isolates the value of parallel search)"
)


def write_report(arms: dict[str, ArmScore], outcomes: list[TaskOutcome], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "summary": {a: s.to_row() for a, s in sorted(arms.items())},
                "excluded": {
                    a: s.excluded_detail for a, s in sorted(arms.items()) if s.excluded_detail
                },
                "outcomes": [asdict(o) for o in outcomes],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _round(value: float | None, places: int = 3) -> float | str:
    if value is None:
        return "—"
    return round(value, places) if places else int(round(value))
