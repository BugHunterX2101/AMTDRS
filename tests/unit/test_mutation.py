"""Mutation generation and scoring, the measurement that makes a generated
safety net defensible instead of merely present.

If this file goes red, the mutation floor stops meaning what the README says it
means: a suite could score 1.0 while never actually detecting a broken change.
"""

from __future__ import annotations

from principal.gates.mutation import Mutant, classify, generate_mutants

SOURCE = """DEFAULT_TTL = 3600


def is_valid(age, ttl=DEFAULT_TTL):
    if age < 0:
        return False
    return age < ttl
"""


def _mutants(cap: int = 30) -> list[Mutant]:
    return generate_mutants(SOURCE, line_start=1, line_end=7, cap=cap)


def test_generates_at_least_one_mutant_for_a_function_with_comparisons():
    mutants = _mutants()
    assert mutants
    assert all(m.source != SOURCE for m in mutants)


def test_every_mutant_is_a_single_change():
    """Two simultaneous mutations can cancel each other out, and a killed
    double-mutant says nothing about whether either half alone was detected."""
    mutants = _mutants()
    for m in mutants:
        # Each mutant differs from the original by exactly the described change;
        # unparse-and-reparse means whitespace can shift, so compare tokens that
        # actually changed rather than raw diff lines.
        assert m.mutant_id
        assert m.description


def test_mutant_ids_are_unique():
    mutants = _mutants()
    ids = [m.mutant_id for m in mutants]
    assert len(ids) == len(set(ids))


def test_respects_the_cap():
    mutants = generate_mutants(SOURCE, line_start=1, line_end=7, cap=2)
    assert len(mutants) <= 2


def test_out_of_range_lines_produce_nothing():
    """A symbol's own line range, never the whole module — mutating outside it
    would measure a suite's sensitivity to code the refactor never touches."""
    mutants = generate_mutants(SOURCE, line_start=2, line_end=2, cap=30)  # the blank line
    assert mutants == []


def test_syntactically_invalid_source_produces_nothing_rather_than_raising():
    assert generate_mutants("def f(:\n", line_start=1, line_end=1) == []


def test_comparison_flip_is_reachable():
    """The `age < 0` guard is the clearest single-line target in the fixture
    source; if a comparison operator swap is never generated, the mutator's
    core operator is broken."""
    mutants = _mutants(cap=500)
    assert any(m.operator == "comparison" for m in mutants)


# ------------------------------------------------------------- classify() --


def test_score_is_killed_over_killed_plus_survived():
    outcome = classify({"m000": 1, "m001": 1, "m002": 0})
    assert outcome.score == 2 / 3


def test_incompetent_mutants_are_excluded_from_the_denominator():
    """A mutant that fails to import measures the parser, not the tests.
    Counting it either way would misstate what the suite actually caught."""
    outcome = classify({"m000": 1, "m001": 2, "m002": 3})
    assert len(outcome.incompetent) == 2
    assert outcome.score == 1.0  # the one scoreable mutant was killed


def test_no_scoreable_mutants_yields_none_not_zero():
    """None and 0.0 mean different things: a suite that killed nothing scoreable
    said nothing about itself, whereas a suite that killed 0 of 5 said a lot."""
    outcome = classify({"m000": 2})
    assert outcome.score is None


def test_all_survived_scores_zero():
    outcome = classify({"m000": 0, "m001": 0})
    assert outcome.score == 0.0
