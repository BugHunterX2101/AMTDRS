"""The three-bucket scorer. Getting this wrong would misrepresent the one
number the whole benchmark exists to produce."""

from __future__ import annotations

from bench.score import TaskOutcome, score


def test_a_sandbox_timeout_is_excluded_not_counted_as_a_loss():
    outcomes = [TaskOutcome("t1", "C", verified=False, error_code="SANDBOX_TIMEOUT")]
    s = score(outcomes)["C"]
    assert s.failed_on_merit == 0
    assert s.excluded_infrastructure == 1
    assert s.scored == 0, "the denominator must not include infrastructure exclusions"


def test_a_red_suite_counts_as_failed_on_merit_not_excluded():
    outcomes = [TaskOutcome("t1", "B", verified=False, error_code="GATE_TESTS_RED")]
    s = score(outcomes)["B"]
    assert s.failed_on_merit == 1
    assert s.excluded_infrastructure == 0


def test_verified_rate_excludes_infrastructure_from_the_denominator():
    """One verified, one genuine failure, one infrastructure exclusion. The
    rate must be 1/2, not 1/3 — folding the exclusion in would understate the
    system exactly the way the design doc warns against."""
    outcomes = [
        TaskOutcome("t1", "C", verified=True),
        TaskOutcome("t2", "C", verified=False, error_code="GATE_BEHAVIOUR"),
        TaskOutcome("t3", "C", verified=False, error_code="SANDBOX_OP_FAILED"),
    ]
    s = score(outcomes)["C"]
    assert s.verified == 1
    assert s.failed_on_merit == 1
    assert s.excluded_infrastructure == 1
    assert s.verified_rate == 0.5


def test_every_exclusion_is_individually_listed():
    """The exclusion has to be auditable, not just a count someone has to take
    on trust."""
    outcomes = [
        TaskOutcome("t1", "A", verified=False, error_code="MODEL_RATE_LIMITED", note="429 after 3 retries"),
    ]
    s = score(outcomes)["A"]
    assert s.excluded_detail == [{"task_id": "t1", "code": "MODEL_RATE_LIMITED", "note": "429 after 3 retries"}]


def test_unknown_error_code_does_not_crash_and_counts_as_merit_failure():
    """A code the taxonomy does not recognise should not silently vanish into
    either bucket by accident — it is treated as a merit failure, the more
    conservative of the two, rather than swallowed."""
    outcomes = [TaskOutcome("t1", "B", verified=False, error_code="SOMETHING_NEW")]
    s = score(outcomes)["B"]
    assert s.failed_on_merit == 1
    assert s.excluded_infrastructure == 0


def test_verified_with_no_error_code_is_unambiguous():
    outcomes = [TaskOutcome("t1", "C", verified=True, error_code=None)]
    o = outcomes[0]
    assert o.infrastructure is False


def test_empty_outcomes_produce_no_arms():
    assert score([]) == {}


def test_arms_are_scored_independently():
    outcomes = [
        TaskOutcome("t1", "A", verified=False, error_code="GATE_TESTS_RED"),
        TaskOutcome("t1", "C", verified=True),
    ]
    s = score(outcomes)
    assert s["A"].verified == 0
    assert s["C"].verified == 1


def test_tokens_per_verified_is_none_when_nothing_verified():
    outcomes = [TaskOutcome("t1", "A", verified=False, error_code="GATE_TESTS_RED", tokens=500)]
    assert score(outcomes)["A"].tokens_per_verified is None
