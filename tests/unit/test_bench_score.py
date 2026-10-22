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


# ------------------------------------------------- outcome-aware scoring --

from bench.score import provenance_line, render  # noqa: E402


def test_tests_green_with_open_caveats_is_not_a_win_and_not_a_failure():
    outcomes = [
        TaskOutcome("t1", "C", verified=False, outcome="UNVERIFIED", cause="caveats"),
        TaskOutcome("t2", "C", verified=True, outcome="VERIFIED"),
    ]
    s = score(outcomes)["C"]
    assert (s.verified, s.unverified, s.failed_on_merit) == (1, 1, 0)
    assert s.verified_rate == 0.5, "an unverified pass stays in the denominator"


def test_no_candidate_verified_is_a_miss_even_though_the_outcome_says_unverified():
    outcomes = [TaskOutcome("t1", "C", verified=False, outcome="UNVERIFIED", cause="no_candidate")]
    s = score(outcomes)["C"]
    assert (s.unverified, s.failed_on_merit) == (0, 1)


def test_an_error_outcome_is_infrastructure_even_without_an_error_code():
    outcomes = [TaskOutcome("t1", "C", verified=False, outcome="ERROR", cause="infrastructure")]
    s = score(outcomes)["C"]
    assert s.excluded_infrastructure == 1 and s.scored == 0


def test_time_to_verified_uses_only_verified_runs():
    outcomes = [
        TaskOutcome("t1", "C", verified=True, duration_s=100.0),
        TaskOutcome("t2", "C", verified=True, duration_s=300.0),
        TaskOutcome("t3", "C", verified=False, error_code="GATE_BEHAVIOUR", duration_s=9999.0),
    ]
    row = score(outcomes)["C"].to_row()
    assert row["median_time_to_verified_s"] == 200.0


def test_failed_attempts_per_verified_counts_the_work_thrown_away():
    outcomes = [
        TaskOutcome("t1", "C", verified=True, failed_attempts=3),
        TaskOutcome("t2", "C", verified=True, failed_attempts=1),
    ]
    assert score(outcomes)["C"].to_row()["failed_attempts_per_verified"] == 2.0


def test_unreported_sandbox_cost_is_a_dash_not_zero():
    row = score([TaskOutcome("t1", "C", verified=True)])["C"].to_row()
    assert row["sandbox_cost_usd"] == "—"
    row = score([TaskOutcome("t1", "C", verified=True, sandbox_cost=0.5)])["C"].to_row()
    assert row["sandbox_cost_usd"] == 0.5


def test_nothing_verified_has_no_time_or_token_figures():
    row = score([TaskOutcome("t1", "C", verified=False, error_code="GATE_TESTS_RED")])["C"].to_row()
    assert row["median_time_to_verified_s"] == "—" and row["tokens_per_verified"] == "—"


def test_the_table_says_what_it_was_measured_on():
    live = render(score([TaskOutcome("t1", "C", verified=True)]), {"live": True})
    local = render(score([TaskOutcome("t1", "C", verified=True)]), {"live": False, "sandbox": "local"})
    assert "LIVE — real Nebius" in live
    assert "NOT LIVE" in local and "not a measurement of the platform" in local
    assert provenance_line(None).startswith("Executed on: unknown")


def test_a_verified_claim_the_oracle_rejects_is_a_false_accept():
    outcomes = [
        TaskOutcome("t1", "A", verified=True, oracle=False),
        TaskOutcome("t2", "A", verified=True, oracle=True),
    ]
    s = score(outcomes)["A"]
    assert (s.false_accepts, s.oracle_checked) == (1, 2)
    assert s.to_row()["false_accepts"] == "1/2"


def test_a_claim_nobody_checked_is_neither_a_false_accept_nor_a_clean_one():
    s = score([TaskOutcome("t1", "C", verified=True, oracle=None)])["C"]
    assert (s.false_accepts, s.oracle_checked) == (0, 0)
    assert s.to_row()["false_accepts"] == "—"


def test_an_oracle_verdict_on_a_run_that_claimed_nothing_is_not_counted():
    s = score([TaskOutcome("t1", "B", verified=False, error_code="GATE_TESTS_RED", oracle=False)])["B"]
    assert (s.false_accepts, s.oracle_checked) == (0, 0)


def test_the_table_has_a_false_accepts_column():
    table = render(score([TaskOutcome("t1", "A", verified=True, oracle=False)]))
    assert "false_accepts" in table and "1/1" in table
