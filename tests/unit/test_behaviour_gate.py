"""The three checks that sit beyond "the tests pass".

Every one of these exists because a patch can make a suite green while
destroying something. If this file goes red, "verified" has quietly stopped
meaning what the README says it means.
"""

from __future__ import annotations

import json

from principal.gates.behaviour import (
    check_coverage_floor,
    check_test_count,
    evaluate_behaviour,
)


def _cov(**files: float) -> str:
    return json.dumps(
        {"files": {p: {"summary": {"percent_covered": v}} for p, v in files.items()}}
    )


# ------------------------------------------------------------- test count --

def test_removing_a_test_is_caught_even_though_the_suite_is_green():
    """The failure mode this exists for: delete the failing test, and every
    remaining test passes. The exit code looks perfect."""
    assert check_test_count(14, 13) is False


def test_adding_a_test_is_also_caught():
    """A refactor is not the place to add tests. A changed count in either
    direction means the suite is not the one that was verified green."""
    assert check_test_count(14, 15) is False


def test_unchanged_count_passes():
    assert check_test_count(14, 14) is True


def test_unmeasured_count_does_not_fail_the_gate():
    """This check cannot assert anything about a number that was never
    collected, and inventing a failure there would be its own dishonesty."""
    assert check_test_count(None, 13) is True
    assert check_test_count(14, None) is True


# ---------------------------------------------------------------- coverage --

def test_coverage_drop_on_a_touched_file_fails():
    ok, drops = check_coverage_floor(
        _cov(**{"src/auth/session.py": 92.0}),
        _cov(**{"src/auth/session.py": 71.0}),
        {"src/auth/session.py"},
    )
    assert not ok
    assert drops[0]["before"] == 92.0 and drops[0]["after"] == 71.0


def test_coverage_rise_passes():
    ok, drops = check_coverage_floor(
        _cov(**{"src/auth/session.py": 71.0}),
        _cov(**{"src/auth/session.py": 92.0}),
        {"src/auth/session.py"},
    )
    assert ok and drops == []


def test_coverage_drop_on_an_untouched_file_is_ignored():
    """Noise from an unrelated file would make this gate fire on changes the
    patch did not cause, and a gate that cries wolf gets switched off."""
    ok, _ = check_coverage_floor(
        _cov(**{"src/other.py": 90.0}), _cov(**{"src/other.py": 10.0}), {"src/auth/session.py"}
    )
    assert ok


def test_sandbox_work_prefix_is_normalised():
    """The sandbox reports paths under /work; the graph stores them
    repo-relative. Without normalisation the two never match and this check
    silently passes on everything."""
    ok, drops = check_coverage_floor(
        json.dumps({"files": {"/work/src/auth/session.py": {"summary": {"percent_covered": 92.0}}}}),
        json.dumps({"files": {"work/src/auth/session.py": {"summary": {"percent_covered": 50.0}}}}),
        {"src/auth/session.py"},
    )
    assert not ok and drops


def test_malformed_coverage_json_does_not_crash_the_gate():
    ok, drops = check_coverage_floor("not json at all", "{}", {"src/x.py"})
    assert ok and drops == []


# ------------------------------------------------------------- composition --

class _FakeStore:
    def __init__(self, exports: set[str]):
        self._exports = exports

    def exported_symbols(self, graph_id, paths=None):
        return self._exports


def test_undeclared_removal_of_a_public_symbol_fails():
    result = evaluate_behaviour(
        _FakeStore({"src/auth/session.py::create", "src/auth/session.py::revoke"}),
        "g", {"src/auth/session.py"}, declared_removals=set(),
        patched_exports={"src/auth/session.py": {"create"}},
        baseline_coverage_json="{}", integration_coverage_json="{}",
        baseline_collected=14, integration_collected=14,
    )
    assert not result.verdict.ok
    assert "src/auth/session.py::revoke" in result.removed_undeclared


def test_declared_removal_is_allowed():
    """Removing a symbol is legitimate when the plan said so. The check is
    against what was *declared*, not against change itself."""
    result = evaluate_behaviour(
        _FakeStore({"src/auth/session.py::create", "src/auth/session.py::revoke"}),
        "g", {"src/auth/session.py"}, declared_removals={"revoke"},
        patched_exports={"src/auth/session.py": {"create"}},
        baseline_coverage_json="{}", integration_coverage_json="{}",
        baseline_collected=14, integration_collected=14,
    )
    assert result.verdict.ok


def test_api_delta_is_reported_before_coverage():
    """When several checks fail at once the message must name the most serious
    one. A vanished public symbol outranks a coverage dip."""
    result = evaluate_behaviour(
        _FakeStore({"src/a.py::gone"}), "g", {"src/a.py"}, declared_removals=set(),
        patched_exports={"src/a.py": set()},
        baseline_coverage_json=_cov(**{"src/a.py": 90.0}),
        integration_coverage_json=_cov(**{"src/a.py": 10.0}),
        baseline_collected=14, integration_collected=9,
    )
    assert "exported symbol disappeared" in result.verdict.reason


def test_fqn_exports_from_the_integration_tree_match_the_stored_baseline():
    """The store holds `module.parent.name`; the re-extraction must speak the same
    dialect or every symbol of every touched file looks removed. Live run 9 failed
    this way on a type-hint-only change."""
    result = evaluate_behaviour(
        _FakeStore({"src/pricing.py::src.pricing.calc_total", "src/pricing.py::src.pricing.TAX_RATE"}),
        "g", {"src/pricing.py"}, declared_removals=set(),
        patched_exports={"src/pricing.py": {"src.pricing.calc_total", "src.pricing.TAX_RATE"}},
        baseline_coverage_json="{}", integration_coverage_json="{}",
        baseline_collected=3, integration_collected=3,
    )
    assert result.verdict.ok


def test_declared_removal_may_name_the_bare_symbol_or_the_fqn():
    for declared in ({"old_fn"}, {"src.mod.old_fn"}):
        result = evaluate_behaviour(
            _FakeStore({"src/mod.py::src.mod.old_fn"}), "g", {"src/mod.py"},
            declared_removals=declared, patched_exports={"src/mod.py": set()},
            baseline_coverage_json="{}", integration_coverage_json="{}",
            baseline_collected=1, integration_collected=1,
        )
        assert result.verdict.ok, declared
