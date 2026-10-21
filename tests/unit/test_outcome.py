"""The four outcomes, and the lines between them.

Each case is a way the result could be mislabelled in a direction that misleads a
reviewer: a platform failure shown as a bad patch, a caveat swallowed into a clean
pass, a definite defect softened into "could not check". The boundaries are
asserted from both sides.
"""

from __future__ import annotations

import pytest

from principal.outcome import (
    INFRA_DISCARD_PREFIX,
    Outcome,
    Signals,
    classify,
    is_terminal,
    open_caveats,
)


def _finding(kind: str, severity: str, detail: str = "detail") -> dict:
    return {"kind": kind, "severity": severity, "detail": detail}


def _impact(*findings: dict) -> dict:
    return {"findings": list(findings), "blocked": any(f["severity"] == "block" for f in findings)}


def _done(**over) -> Signals:
    base = dict(state="Done", verified_tasks=3)
    base.update(over)
    return Signals(**base)


# ------------------------------------------------------------------ still running --


@pytest.mark.parametrize("state", ["Ingesting", "Baselining", "Mapping", "Planning", "Executing",
                                   "Integrating", "Publishing", "CleanupExecuting"])
def test_a_run_in_progress_has_no_outcome_yet(state):
    assert classify(Signals(state=state)) is None
    assert not is_terminal(state)


# --------------------------------------------------------------------------- Done --


def test_a_clean_run_is_verified():
    report = classify(_done(pr_url="https://example.test/pr/1"))
    assert report.outcome is Outcome.VERIFIED
    assert report.reasons == []
    assert report.pr_offered and report.repository_changed


def test_verified_without_a_pr_does_not_claim_the_repository_changed():
    """No GitHub token: the run is still verified, but nothing was published."""
    report = classify(_done(pr_url=None))
    assert report.outcome is Outcome.VERIFIED
    assert not report.pr_offered and not report.repository_changed


def test_informational_findings_never_downgrade_a_clean_run():
    report = classify(_done(impact=_impact(_finding("call_sites_unchanged", "info"))))
    assert report.outcome is Outcome.VERIFIED


def test_a_warning_means_unverified_even_though_the_tests_passed():
    report = classify(_done(
        pr_url="https://example.test/pr/1",
        impact=_impact(_finding("stale_reference", "warn", "src/r.py:9 still names 'old'")),
    ))
    assert report.outcome is Outcome.UNVERIFIED
    assert report.cause == "caveats"
    assert "src/r.py:9 still names 'old'" in report.reasons
    assert report.pr_offered, "tests are green: the draft is still offered, flagged for a human"


def test_a_suite_below_the_strength_floor_is_a_caveat_with_the_numbers_in_it():
    report = classify(_done(strength={"below_floor": True, "detected": 1, "survived": 3,
                                      "score": 0.25, "floor": 0.5}))
    assert report.outcome is Outcome.UNVERIFIED
    assert "1 of 4 injected faults" in report.reasons[0]
    assert "25%" in report.reasons[0] and "50%" in report.reasons[0]


def test_an_unmeasured_strength_audit_is_not_a_caveat_and_not_a_pass():
    """'Could not measure' is recorded in the evidence; it must neither flatter the
    run nor penalise it."""
    report = classify(_done(strength={"status": "unavailable", "below_floor": False}))
    assert report.outcome is Outcome.VERIFIED


def test_a_measured_suite_at_or_above_the_floor_is_not_a_caveat():
    report = classify(_done(strength={"below_floor": False, "detected": 4, "survived": 0,
                                      "score": 1.0, "floor": 0.5}))
    assert report.outcome is Outcome.VERIFIED


def test_a_partial_migration_is_unverified():
    report = classify(_done(discarded_tasks=2))
    assert report.outcome is Outcome.UNVERIFIED
    assert any("partial" in r for r in report.reasons)


def test_infrastructure_discards_are_named_as_the_platform_not_the_patch():
    caveats = open_caveats(_done(discarded_tasks=2, infrastructure_discards=2))
    assert len(caveats) == 1 and "platform" in caveats[0]
    assert "not verified" not in caveats[0]


def test_real_and_infrastructure_discards_are_reported_separately():
    caveats = open_caveats(_done(discarded_tasks=3, infrastructure_discards=1))
    assert any("2 planned task(s) were not verified" in c for c in caveats)
    assert any("1 planned task(s) could not be evaluated because the platform" in c for c in caveats)


def test_uncovered_and_generated_only_files_are_caveats():
    caveats = open_caveats(_done(uncovered=["a.py"], characterisation_only=["b.py"]))
    assert caveats == ["a.py was modified and no test covers it",
                       "b.py is verified only by a generated test, not a pre-existing one"]


def test_the_headline_stays_short_but_reasons_are_complete():
    report = classify(_done(uncovered=[f"f{i}.py" for i in range(8)]))
    assert len(report.reasons) == 8
    assert "(+5 more)" in report.headline


# ------------------------------------------------------------------------- NoPR --


def _no_pr(**over) -> Signals:
    base = dict(state="NoPR", stop_reason="something stopped it")
    base.update(over)
    return Signals(**base)


def test_cancelled_is_unverified_not_failed():
    report = classify(_no_pr(stop_reason="cancelled"))
    assert report.outcome is Outcome.UNVERIFIED and report.cause == "cancelled"


def test_nothing_verified_is_unverified_and_leaves_the_repository_alone():
    report = classify(_no_pr(stop_reason="no task was verified", discarded_tasks=3))
    assert report.outcome is Outcome.UNVERIFIED
    assert report.cause == "no_candidate"
    assert not report.repository_changed and not report.pr_offered


def test_exceeding_the_discard_cap_is_unverified_incomplete():
    report = classify(_no_pr(stop_reason="4/10 tasks discarded", verified_tasks=6, discarded_tasks=4))
    assert report.outcome is Outcome.UNVERIFIED and report.cause == "incomplete"


def test_a_definite_impact_defect_is_failed():
    report = classify(_no_pr(impact=_impact(
        _finding("incompatible_call", "block", "src/admin.py:5 passes 2 positional argument(s)"),
    )))
    assert report.outcome is Outcome.FAILED and report.cause == "impact"
    assert report.reasons == ["src/admin.py:5 passes 2 positional argument(s)"]


def test_a_dangling_call_is_a_defect():
    report = classify(_no_pr(impact=_impact(_finding("dangling_call", "block", "still calls create"))))
    assert report.outcome is Outcome.FAILED


def test_an_uncheckable_block_is_unverified_not_failed():
    """The patch is not known to be wrong. It could not be checked, and saying
    'failed' would claim a defect nobody found."""
    report = classify(_no_pr(impact=_impact(_finding("uncheckable", "block", "x.py no longer parses"))))
    assert report.outcome is Outcome.UNVERIFIED and report.cause == "uncheckable"


def test_a_defect_outranks_an_uncheckable_finding_in_the_same_run():
    report = classify(_no_pr(impact=_impact(
        _finding("uncheckable", "block", "x.py no longer parses"),
        _finding("incompatible_call", "block", "y.py:3 missing required argument 'ttl'"),
    )))
    assert report.outcome is Outcome.FAILED
    assert report.reasons == ["y.py:3 missing required argument 'ttl'"]


def test_a_red_integration_run_is_failed_and_names_the_failing_tests():
    report = classify(_no_pr(integration={"pass": False, "failing": ["tests/test_a.py::test_x"]}))
    assert report.outcome is Outcome.FAILED and report.cause == "integration"
    assert "tests/test_a.py::test_x" in report.reasons[0]


def test_a_behaviour_failure_after_a_green_run_is_failed():
    report = classify(_no_pr(integration={"pass": False, "removed_undeclared": ["src/a.py::gone"]}))
    assert report.outcome is Outcome.FAILED
    assert "src/a.py::gone" in report.reasons[0]


def test_introduced_security_findings_are_failed():
    report = classify(_no_pr(security={"introduced": [
        {"rule": "hardcoded-secret", "message": "token literal", "path": "a.py", "line": 4},
    ]}))
    assert report.outcome is Outcome.FAILED and report.cause == "security"


def test_when_only_the_platform_failed_the_outcome_is_error_not_failed():
    """The reason this module exists. Three tasks, all discarded because the sandbox
    timed out: no patch was ever judged."""
    report = classify(_no_pr(stop_reason="3/3 tasks discarded", discarded_tasks=3,
                             infrastructure_discards=3))
    assert report.outcome is Outcome.ERROR and report.cause == "infrastructure"
    assert "not evidence" in report.headline


def test_a_mix_of_real_and_infrastructure_discards_is_not_an_error():
    """One patch genuinely failed, so the run says something about the code."""
    report = classify(_no_pr(discarded_tasks=3, infrastructure_discards=2))
    assert report.outcome is Outcome.UNVERIFIED


def test_infrastructure_discards_with_a_verified_task_are_not_an_error():
    report = classify(_no_pr(verified_tasks=1, discarded_tasks=2, infrastructure_discards=2))
    assert report.outcome is Outcome.UNVERIFIED


# ------------------------------------------------------------- other terminal states --


def test_aborted_is_error():
    report = classify(Signals(state="Aborted", stop_reason="sandbox refused (403)"))
    assert report.outcome is Outcome.ERROR
    assert report.reasons == ["sandbox refused (403)"]
    assert not report.repository_changed


def test_no_safety_net_is_unverified_with_its_own_cause():
    report = classify(Signals(state="NoSafetyNet", stop_reason="mutation score 0.20 below floor 0.50"))
    assert report.outcome is Outcome.UNVERIFIED and report.cause == "no_safety_net"


def test_a_plan_only_run_is_never_verified():
    """A dry run ends in Done after planning. Nothing was generated or tested, so
    "every check that ran came back clean" is vacuously true and must not read as
    a pass."""
    report = classify(Signals(state="Done", stop_reason="dry_run"))
    assert report.outcome is Outcome.UNVERIFIED and report.cause == "plan_only"
    assert not report.pr_offered and not report.repository_changed


def test_done_with_no_verified_task_is_not_verified():
    assert classify(Signals(state="Done", verified_tasks=0)).outcome is Outcome.UNVERIFIED


# ------------------------------------------------------------------- the contract --


def test_the_prefix_the_scheduler_writes_is_the_one_this_module_reads():
    """The two live in different layers and are joined only by this string."""
    from principal.orchestrator import scheduler

    assert scheduler.INFRA_DISCARD_PREFIX == INFRA_DISCARD_PREFIX
