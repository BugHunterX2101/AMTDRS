"""Verification strength: would the tests that just passed notice a mistake?

A green run says the suite found nothing wrong. It does not say the suite could
have. After a refactor passes, this module asks the second question about the
only lines that matter: the ones the refactor changed. It injects small,
deliberate faults — a flipped comparison, an off-by-one constant, a dropped
negation — one at a time into those lines and reruns the covering tests. A fault
the tests catch is *killed*; one they miss *survives*, and a surviving mutant is a
concrete, located statement that the suite is blind to that kind of change here.

This is a measurement, not a proof, and it is reported as one:

  A high score means the tests detect these injected faults. It does not mean the
  refactor is behaviourally equivalent to the original.
  A survivor is a gap to inspect. Some survivors are equivalent mutants — a change
  that cannot alter behaviour — and no tool can tell those apart in general.
  A run that could not be measured says so. It never defaults to a flattering
  number.

The generator and the classification are the ones the characterisation gate
already uses, so a score here means the same thing as a score there.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from principal.gates.characterise import DEFAULT_MUTATION_FLOOR
from principal.gates.impact import changed_lines
from principal.gates.mutation import Mutant, classify, generate_mutants
from principal.sandbox.scripts import (
    SENTINEL_MUTATION,
    audit_mutation_script,
    extract,
    parse_mutation_results,
)

logger = logging.getLogger("principal.gates.strength")

DEFAULT_AUDIT_CAP = 20

DISCLAIMER = (
    "These are injected faults, not a proof of equivalence. Passing tests plus a high score mean the"
    " suite detects these kinds of change in the changed lines; they do not mean the refactor"
    " preserves behaviour everywhere. A surviving mutant is a gap to inspect, and some survivors are"
    " equivalent mutants that cannot change behaviour."
)


@dataclass(frozen=True, slots=True)
class PlannedMutant:
    mutant_id: str
    path: str
    mutant: Mutant


@dataclass(slots=True)
class StrengthResult:
    status: str                       # measured | no_mutants | unavailable | skipped
    reason: str = ""
    floor: float = DEFAULT_MUTATION_FLOOR
    generated: int = 0
    killed: int = 0
    survived: int = 0
    incompetent: int = 0
    survivors: list[dict[str, Any]] = field(default_factory=list)
    per_file: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def score(self) -> float | None:
        scored = self.killed + self.survived
        return (self.killed / scored) if scored else None

    @property
    def below_floor(self) -> bool:
        """Only a measured score can be below the floor. Not measuring is a
        different statement and is reported as one."""
        return self.status == "measured" and self.score is not None and self.score < self.floor

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status, "reason": self.reason, "floor": self.floor,
            "generated": self.generated, "detected": self.killed, "survived": self.survived,
            "incompetent": self.incompetent, "score": self.score, "below_floor": self.below_floor,
            "survivors": self.survivors[:20], "per_file": self.per_file, "disclaimer": DISCLAIMER,
        }


def _ranges(lines: set[int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for line in sorted(lines):
        if out and line <= out[-1][1] + 1:
            out[-1] = (out[-1][0], line)
        else:
            out.append((line, line))
    return out


def plan_mutants(
    baseline_tree: dict[str, str], patched_tree: dict[str, str], touched: set[str], *,
    cap: int = DEFAULT_AUDIT_CAP,
) -> list[PlannedMutant]:
    """Mutants confined to the lines the refactor changed, spread across files.

    The budget is shared out per file before it is spent, so a large change in one
    file cannot use the whole cap and leave the others unmeasured.
    """
    files = [p for p in sorted(touched) if p.endswith(".py") and p in patched_tree]
    if not files or cap < 1:
        return []
    # More files than budget: sample files evenly rather than taking the first few,
    # so the alphabet does not decide which files get measured.
    if len(files) > cap:
        files = _spread(files, cap)
    quotas = _quotas(cap, len(files))

    planned: list[PlannedMutant] = []
    for fi, path in enumerate(files):
        source = patched_tree[path]
        _old, new = changed_lines(baseline_tree.get(path, ""), source)
        ranges = _ranges(new)
        if not ranges:
            continue
        per_range = max(1, quotas[fi] // len(ranges))
        found: list[PlannedMutant] = []
        seen: set[tuple[int, str]] = set()
        for ri, (lo, hi) in enumerate(ranges):
            for m in generate_mutants(source, line_start=lo, line_end=hi, cap=per_range):
                key = (m.line, m.description)
                if key in seen:
                    continue
                seen.add(key)
                found.append(PlannedMutant(f"f{fi}r{ri}_{m.mutant_id}", path, m))
        # `per_range` is floored at one so no range goes unmeasured, which means a
        # file with many small ranges can exceed its share. Cut it back here, by
        # even sampling, so that the share is a limit and not a suggestion.
        planned.extend(_spread(found, quotas[fi]))
    return planned


def _quotas(cap: int, n: int) -> list[int]:
    """Split `cap` into `n` shares that sum to it, earlier files taking the remainder."""
    base, extra = divmod(cap, n)
    return [base + (1 if i < extra else 0) for i in range(n)]


def _spread(items: list, k: int) -> list:
    """`k` items taken evenly across `items`, in order. All of them if it fits."""
    if k >= len(items):
        return list(items)
    if k <= 0:
        return []
    if k == 1:
        return [items[0]]
    return [items[round(i * (len(items) - 1) / (k - 1))] for i in range(k)]


def _summarise(
    planned: list[PlannedMutant], results: dict[str, int], floor: float,
) -> StrengthResult:
    outcome = classify(results)
    by_id = {p.mutant_id: p for p in planned}
    survivors = [
        {"file": by_id[i].path, "line": by_id[i].mutant.line, "operator": by_id[i].mutant.operator,
         "description": by_id[i].mutant.description}
        for i in sorted(outcome.survived) if i in by_id
    ]
    per_file: dict[str, dict[str, int]] = {}
    for bucket, ids in (("detected", outcome.killed), ("survived", outcome.survived),
                        ("incompetent", outcome.incompetent)):
        for i in ids:
            if i in by_id:
                counts = per_file.setdefault(by_id[i].path, {"detected": 0, "survived": 0, "incompetent": 0})
                counts[bucket] += 1
    measured = bool(outcome.killed or outcome.survived)
    return StrengthResult(
        status="measured" if measured else "no_mutants",
        reason="" if measured
        else "every injected fault broke the module rather than its behaviour, so nothing was scoreable",
        floor=floor, generated=len(planned), killed=len(outcome.killed), survived=len(outcome.survived),
        incompetent=len(outcome.incompetent), survivors=survivors, per_file=per_file,
    )


async def run_strength_audit(
    sandbox, image, planned: list[PlannedMutant], test_nodeids: list[str], *,
    floor: float = DEFAULT_MUTATION_FLOOR, timeout_s: int = 1200, on_operation=None,
) -> StrengthResult:
    """One sandbox operation for the whole audit, on a disposable fork of the
    patched image. Never raises: an audit that could not run reports that, because
    the audit informs the outcome and must not be able to take the job down."""
    if not planned:
        return StrengthResult(
            status="no_mutants", floor=floor,
            reason="the changed lines contain nothing this mutator can alter (comments, imports,"
                   " moved definitions), so the suite's sensitivity to them was not measured",
        )
    script = audit_mutation_script(
        [(p.mutant_id, p.path, p.mutant.source) for p in planned], test_nodeids,
    )
    try:
        run = await sandbox.run(image, script, disposable=True, timeout_s=timeout_s,
                                on_operation=on_operation)
    except Exception as exc:  # noqa: BLE001
        logger.warning("mutation audit could not run: %s", exc)
        return StrengthResult(status="unavailable", floor=floor, generated=len(planned),
                              reason=f"the sandbox operation failed: {exc}")

    results = parse_mutation_results(extract(run.stdout, SENTINEL_MUTATION))
    if not results:
        return StrengthResult(
            status="unavailable", floor=floor, generated=len(planned),
            reason=f"the audit produced no results (exit {run.exit_code}); it is inconclusive and is"
                   " not counted as either detected or missed",
        )
    return _summarise(planned, results, floor)
