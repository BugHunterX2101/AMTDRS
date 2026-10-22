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
import statistics
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
    # The run's outcome (VERIFIED / UNVERIFIED / FAILED / ERROR) as the product
    # itself classifies it. Empty for the ungated arm, which skips the checks the
    # classification reads.
    outcome: str = ""
    # Why: UNVERIFIED covers both "tests passed, caveats open" (cause "caveats")
    # and "nothing verified, nothing offered" (any other cause). Only the first is
    # a bucket of its own; the second is a miss.
    cause: str = ""
    # Candidate or repair attempts that did not pass, i.e. the work thrown away
    # on the way to the result. A swarm that verifies after 11 failures and one
    # that verifies after 0 are not the same cost.
    failed_attempts: int = 0
    # What the sandbox platform reported charging. 0.0 means "not reported".
    sandbox_cost: float = 0.0
    # Whether the independent oracle (bench/oracle.py) accepted the patch this run
    # claimed was verified. None: the task has no oracle, the run claimed nothing,
    # or the patched tree could not be rebuilt. Only False is a false accept.
    oracle: bool | None = None

    @property
    def unverified(self) -> bool:
        """Tests passed, but open caveats mean Principal could not call it safe."""
        return (not self.verified and self.error_code is None
                and self.outcome == "UNVERIFIED" and self.cause == "caveats")

    @property
    def infrastructure(self) -> bool:
        if self.verified:
            return False
        if self.outcome == "ERROR":
            return True
        if self.error_code is None:
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
    # Tests passed but caveats stayed open, so Principal would not call it safe.
    # Not a win, so not in `verified`; not a defect either, so not a failure.
    unverified: int = 0
    failed_on_merit: int = 0
    excluded_infrastructure: int = 0
    excluded_detail: list[dict[str, str]] = field(default_factory=list)
    total_tokens: int = 0
    total_sandbox_runs: int = 0
    total_duration_s: float = 0.0
    total_failed_attempts: int = 0
    total_sandbox_cost: float = 0.0
    # Wall clock of each run that ended VERIFIED, for time-to-verified.
    verified_durations_s: list[float] = field(default_factory=list)
    # Of the verified claims, how many an independent oracle checked, and how many
    # of those it rejected. A claim with no oracle is not counted either way.
    oracle_checked: int = 0
    false_accepts: int = 0

    @property
    def scored(self) -> int:
        """The denominator. Infrastructure failures are excluded from it, not
        counted as losses — that is the entire point of the third bucket."""
        return self.verified + self.unverified + self.failed_on_merit

    @property
    def median_time_to_verified_s(self) -> float | None:
        return statistics.median(self.verified_durations_s) if self.verified_durations_s else None

    @property
    def failed_attempts_per_verified(self) -> float | None:
        return (self.total_failed_attempts / self.verified) if self.verified else None

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
            "unverified": self.unverified,
            "failed_on_merit": self.failed_on_merit,
            "excluded_infrastructure": self.excluded_infrastructure,
            "verified_rate": _round(self.verified_rate),
            # "n/m": n claims rejected by the oracle of m it checked. "—" when none was checked.
            "false_accepts": f"{self.false_accepts}/{self.oracle_checked}" if self.oracle_checked else "—",
            "tokens_per_verified": _round(self.tokens_per_verified, 0),
            "failed_attempts_per_verified": _round(self.failed_attempts_per_verified, 1),
            "median_time_to_verified_s": _round(self.median_time_to_verified_s, 1),
            "sandbox_runs": self.total_sandbox_runs,
            # "—" when the platform reported no charge, never 0: zero reads as free.
            "sandbox_cost_usd": _round(self.total_sandbox_cost, 4) if self.total_sandbox_cost > 0 else "—",
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
        s.total_failed_attempts += o.failed_attempts
        s.total_sandbox_cost += o.sandbox_cost

        if o.verified:
            s.verified += 1
            s.verified_durations_s.append(o.duration_s)
            if o.oracle is not None:
                s.oracle_checked += 1
                s.false_accepts += 0 if o.oracle else 1
        elif o.unverified:
            s.unverified += 1
        elif o.infrastructure:
            s.excluded_infrastructure += 1
            s.excluded_detail.append(
                {"task_id": o.task_id, "code": o.error_code or "", "note": o.note}
            )
        else:
            s.failed_on_merit += 1
    return arms


def provenance_line(provenance: dict[str, object] | None) -> str:
    """One line saying what these numbers were measured on. A number without it
    can be read as a measurement of the platform when it was not."""
    if not provenance:
        return "Executed on: unknown (no provenance recorded)"
    if provenance.get("live"):
        return "Executed on: LIVE — real Nebius sandbox and real inference"
    return (f"Executed on: NOT LIVE — sandbox {provenance.get('sandbox', 'unknown')}, "
            f"inference {provenance.get('inference', 'unknown')}; not a measurement of the platform")


def render(arms: dict[str, ArmScore], provenance: dict[str, object] | None = None) -> str:
    """A table, then every exclusion by name. The exclusions are printed in full
    rather than summarised, because a reader has to be able to disagree with
    them."""
    order = ["A", "B", "C"]
    rows = [arms[a].to_row() for a in order if a in arms]
    rows += [s.to_row() for k, s in sorted(arms.items()) if k not in order]

    headers = ["arm", "attempted", "verified", "unverified", "failed_on_merit",
               "excluded_infrastructure", "verified_rate", "false_accepts", "tokens_per_verified",
               "failed_attempts_per_verified", "median_time_to_verified_s",
               "sandbox_runs", "sandbox_cost_usd", "wall_clock_s"]
    widths = {h: max(len(h), *(len(str(r[h])) for r in rows)) if rows else len(h) for h in headers}

    out = [
        provenance_line(provenance),
        "",
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


def write_report(
    arms: dict[str, ArmScore], outcomes: list[TaskOutcome], path: Path,
    provenance: dict[str, object] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "provenance": provenance,
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
