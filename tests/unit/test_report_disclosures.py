"""The PR body must disclose what the live dashboard already knew.

Three facts were previously computed — cleanup-vs-requested provenance, a
generated characterisation test standing in for real coverage, a relocated
symbol reconciling a declared removal — and published only as SSE events for
whoever happened to be watching the console live. None of them reached the
one artifact a PR reviewer actually reads. `compose_report`'s model path and
its fallback, and `render_fallback_body`'s last-resort fallback below that,
each have to carry all three independently, since either one can be what
ships depending on whether the model call succeeded.
"""

from __future__ import annotations

from principal.agents.reporter import fallback_report
from principal.db.store import Task
from principal.publish.prbody import render_fallback_body


def _task(target_file: str, kind: str = "refactor", **overrides) -> Task:
    base = dict(
        id="t-1", job_id="j-1", seq=1, target_file=target_file,
        instruction="do the thing", acceptance="tests pass",
        declared_removals=[], depends_on=[], state="verified",
        attempts=1, winning_attempt_id="a-1", discard_reason=None, kind=kind,
    )
    base.update(overrides)
    return Task(**base)


# ---------------------------------------------------------- fallback_report --


def test_cleanup_tasks_are_separated_from_the_requested_change():
    verified = [
        {"target_file": "src/a.py", "instruction": "make ttl kwonly", "kind": "refactor"},
        {"target_file": "src/b.py", "instruction": "drop unused import", "kind": "cleanup"},
    ]
    report = fallback_report(verified, [], [], "make ttl keyword-only")
    assert "## Verified changes" in report.body
    assert "src/a.py" in report.body.split("## Cleanup")[0]
    assert "## Cleanup (dead code this refactor orphaned, not part of the requested change)" in report.body
    assert "src/b.py" in report.body.split("## Cleanup")[1]


def test_characterisation_only_coverage_is_disclosed_as_its_own_risk_with_score():
    report = fallback_report(
        verified=[{"target_file": "src/legacy.py", "instruction": "x", "kind": "refactor"}],
        discarded=[], uncovered=[], goal="g",
        characterisation_only=["src/legacy.py"],
        characterisation_result={"mutation": {"score": 0.67}},
    )
    assert "## Risk: verified only by generated tests, not pre-existing ones" in report.body
    assert "src/legacy.py" in report.body
    assert "0.67" in report.body
    assert any(r.kind == "characterisation_only" and r.detail == "src/legacy.py" for r in report.risks)


def test_characterisation_only_section_omitted_when_nothing_qualifies():
    report = fallback_report([], [], [], "g", characterisation_only=[])
    assert "generated tests" not in report.body


def test_relocated_symbols_are_disclosed_so_a_deletion_reads_as_a_move():
    report = fallback_report(
        verified=[{"target_file": "src/new.py", "instruction": "x", "kind": "refactor"}],
        discarded=[], uncovered=[], goal="g", relocated_symbols=["old_name"],
    )
    assert "## Relocated symbols" in report.body
    assert "old_name" in report.body


def test_unresolved_call_sites_are_listed_individually_not_just_counted():
    """The dashboard's Blast radius panel tells the operator these are
    "listed in the pull request for a human to check" — that claim is only
    true if every one actually appears, not a summary count."""
    sites = [
        {"file": "src/registry.py", "line": 14, "name": "create",
         "reason": "unresolved call matching the target name"},
        {"file": "src/admin/tools.py", "line": 6, "name": "login",
         "reason": "dynamic dispatch, target cannot be proved statically"},
    ]
    report = fallback_report([], [], [], "g", unresolved_call_sites=sites)
    assert "## Call sites the graph could not resolve statically" in report.body
    assert "src/registry.py:14" in report.body
    assert "src/admin/tools.py:6" in report.body
    assert sum(1 for r in report.risks if r.kind == "unresolved_call_site") == 2


def test_uncovered_and_characterisation_only_are_distinct_sections():
    """A file with zero coverage and a file covered only by a generated test
    are different claims and must not collapse into one risk category."""
    report = fallback_report(
        verified=[
            {"target_file": "src/naked.py", "instruction": "x", "kind": "refactor"},
            {"target_file": "src/generated.py", "instruction": "x", "kind": "refactor"},
        ],
        discarded=[], uncovered=["src/naked.py"], goal="g",
        characterisation_only=["src/generated.py"],
    )
    assert "src/naked.py" in report.body.split("## Risk: modified with no test coverage")[1].split("##")[0]
    assert "src/generated.py" not in report.body.split("## Risk: modified with no test coverage")[1].split("##")[0]


# ------------------------------------------------------- render_fallback_body --


def test_prbody_fallback_separates_cleanup_from_requested_work():
    body = render_fallback_body(
        "goal text",
        verified=[_task("src/a.py", kind="refactor"), _task("src/b.py", kind="cleanup")],
        discarded=[], uncovered=[],
    )
    assert "## Cleanup (dead code this refactor orphaned, not part of the requested change)" in body
    assert "src/a.py" in body.split("## Cleanup")[0]
    assert "src/b.py" in body.split("## Cleanup")[1]


def test_prbody_fallback_discloses_characterisation_only_files_with_score():
    body = render_fallback_body(
        "goal", verified=[_task("src/legacy.py")], discarded=[], uncovered=[],
        characterisation_only=["src/legacy.py"],
        characterisation_result={"mutation": {"score": 0.81}},
    )
    assert "## Risk: verified only by a generated test, not a pre-existing one" in body
    assert "0.81" in body


def test_prbody_fallback_discloses_relocated_symbols():
    body = render_fallback_body(
        "goal", verified=[_task("src/new.py")], discarded=[], uncovered=[],
        relocated_symbols=["moved_fn"],
    )
    assert "## Relocated symbols" in body
    assert "moved_fn" in body


def test_prbody_fallback_discloses_unresolved_call_sites():
    body = render_fallback_body(
        "goal", verified=[_task("src/a.py")], discarded=[], uncovered=[],
        unresolved_call_sites=[
            {"file": "src/registry.py", "line": 14, "name": "create", "reason": "dynamic dispatch"},
        ],
    )
    assert "## Call sites the graph could not resolve statically" in body
    assert "src/registry.py:14" in body
    assert "create" in body


def test_prbody_fallback_says_nothing_extra_when_there_is_nothing_to_disclose():
    body = render_fallback_body("goal", verified=[_task("src/a.py")], discarded=[], uncovered=[])
    assert "Cleanup" not in body
    assert "generated test" not in body
    assert "Relocated" not in body
    assert "unresolved" not in body.lower()
