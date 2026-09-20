"""Characterisation: establishing an oracle where the repository has none.

The problem this solves is real and is currently a hard blocker. Principal aborts
when the baseline suite is absent, and test selection falls back to running
everything when no covering test exists — which on a genuinely untested module
gives a green result that means nothing, because the suite passes without ever
exercising the code being changed. On exactly the legacy codebases this product
targets, Principal today either refuses to run or runs with a hollow oracle.

The obvious fix destroys the product. If an agent writes the tests and those
tests authorise the change, the system proves that model-written code satisfies
model-written expectations: both artifacts carry the same misunderstandings, and
the failure modes correlate precisely where they need to be independent. Every
claim about not being able to cheat would have to be withdrawn.

Four rules make the safe version safe, and three of them are enforced by code
that already existed:

  1. The Characteriser never sees the refactoring goal. A test writer that knows
     the intended change writes tests that accommodate it.
  2. Generated tests must pass against unmodified C0, or they are discarded.
     They describe what the code *does*, not what it should do.
  3. They are frozen before any refactoring starts and no agent can touch them —
     gate 1 already rejects every diff touching a test path, and these land in
     one.
  4. They are never the only oracle: they join whatever real tests exist, and a
     change verified only by generated tests is reported as such rather than
     presented as verified.

What makes it defensible rather than merely careful is that the suite's quality
is measured instead of asserted. Below the floor, Principal reports that the
target cannot be safely refactored — a genuinely useful answer, and a far better
one than a confident PR backed by vacuous tests.
"""

from __future__ import annotations

from dataclasses import dataclass

from principal.gates.mutation import (
    DEFAULT_CAP,
    MutationOutcome,
    classify,
    generate_mutants,
)
from principal.gates.pipeline import Verdict, VerdictKind
from principal.sandbox.scripts import (
    CHARACTERISATION_DIR,
    SENTINEL_MUTATION,
    SENTINEL_REPORT,
    characterisation_script,
    extract,
    mutation_script,
    parse_mutation_results,
)

# Calibrated on the fixture repository by writing deliberately weak and
# deliberately good suites and seeing where the scores land. It is a threshold,
# not a law of nature, and it is recorded in the run metadata so a published
# number can be read against the value that produced it.
DEFAULT_MUTATION_FLOOR = 0.60


@dataclass(slots=True)
class CharacterisationResult:
    status: str                      # "ok" | "discarded" | "insufficient" | "skipped"
    reason: str = ""
    test_path: str | None = None
    test_source: str | None = None
    mutation: MutationOutcome | None = None
    mutants_generated: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def score(self) -> float | None:
        return self.mutation.score if self.mutation else None

    @property
    def verdict(self) -> Verdict:
        if self.ok:
            return Verdict.ok_()
        if self.status == "skipped":
            return Verdict.ok_()
        return Verdict.fail(VerdictKind.BEHAVIOUR, "characterise", self.reason)

    def to_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "test_path": self.test_path,
            "mutants_generated": self.mutants_generated,
            **({"mutation": self.mutation.to_payload()} if self.mutation else {}),
        }

    @staticmethod
    def discarded(reason: str) -> CharacterisationResult:
        return CharacterisationResult(status="discarded", reason=reason)

    @staticmethod
    def insufficient(score: float | None, floor: float, outcome: MutationOutcome) -> CharacterisationResult:
        measured = "no scoreable mutants" if score is None else f"mutation score {score:.2f}"
        generated = len(outcome.killed) + len(outcome.survived) + len(outcome.incompetent)
        return CharacterisationResult(
            status="insufficient",
            reason=f"{measured} is below the floor of {floor:.2f}: the generated"
                   " tests do not detect real changes to this symbol",
            mutation=outcome, mutants_generated=generated,
        )


def characterisation_path(target_fqn: str) -> str:
    """One file per target, named from the fqn so a rerun overwrites rather than
    accumulates. It lands under a test directory, which is what freezes it."""
    slug = "".join(c if c.isalnum() else "_" for c in target_fqn).strip("_").lower()
    return f"{CHARACTERISATION_DIR}/test_characterise_{slug}.py"


def needs_characterisation(covering_test_count: int, *, threshold: int = 1) -> bool:
    """Run this phase only when the target is actually uncovered.

    On a well-tested repository it never fires and costs nothing, which is what
    keeps the phase from being a tax on the common case.
    """
    return covering_test_count < threshold


async def establish_safety_net(
    sandbox, c0_image, *, target_path: str, target_source: str, line_start: int, line_end: int,
    test_source: str, test_path: str, floor: float = DEFAULT_MUTATION_FLOOR,
    cap: int = DEFAULT_CAP, timeout_s: int = 900, on_operation=None,
) -> CharacterisationResult:
    """Two sandbox operations, total: validate, then measure.

    No model is reached from here. The Characteriser produced `test_source`
    upstream; this gate only decides whether what it produced is worth trusting,
    which is why the accept path stays free of model judgement.
    """
    validation = await sandbox.run(
        c0_image, characterisation_script(test_path, test_source),
        disposable=False, timeout_s=timeout_s, on_operation=on_operation,
    )
    if validation.exit_code != 0:
        return CharacterisationResult.discarded(
            "generated tests do not pass against the unmodified baseline, so they"
            " are wrong about current behaviour"
        )

    report = extract(validation.stdout, SENTINEL_REPORT)
    if not _collected_anything(report):
        return CharacterisationResult.discarded("generated suite collected no tests")

    mutants = generate_mutants(target_source, line_start=line_start, line_end=line_end, cap=cap)
    if not mutants:
        return CharacterisationResult.discarded(
            "no mutants could be generated for this symbol, so the suite's"
            " sensitivity cannot be measured"
        )

    # The image with the characterisation tests written into it, so the mutation
    # loop runs against exactly the suite that was just validated.
    mutation_image = await sandbox.from_uuid(validation.image) if validation.image else c0_image

    run = await sandbox.run(
        mutation_image,
        mutation_script(target_path, [(m.mutant_id, m.source) for m in mutants], [test_path]),
        disposable=True, timeout_s=timeout_s, on_operation=on_operation,
    )
    outcome = classify(parse_mutation_results(extract(run.stdout, SENTINEL_MUTATION)))
    score = outcome.score

    if score is None or score < floor:
        return CharacterisationResult.insufficient(score, floor, outcome)

    return CharacterisationResult(
        status="ok", test_path=test_path, test_source=test_source, mutation=outcome,
        mutants_generated=len(mutants),
    )


def _collected_anything(report_json: str) -> bool:
    import json

    try:
        data = json.loads(report_json or "{}")
    except json.JSONDecodeError:
        return False
    summary = data.get("summary", {}) or {}
    return int(summary.get("passed", 0) or 0) > 0
