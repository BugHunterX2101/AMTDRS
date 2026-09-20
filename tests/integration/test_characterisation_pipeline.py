"""Characterisation, against the real sandbox scripts and real pytest.

This is the highest-risk untested surface in the characterisation design: the
shell scripts in `sandbox/scripts.py` that write a generated test file and run
thirty mutants in one operation. A bug here would not show up as a wrong
answer — it would show up as `establish_safety_net` silently returning a score
of `None` or 0.0 for a perfectly good suite, which is exactly the failure mode
that would make `NoSafetyNet` fire on jobs that should have proceeded.

No model is involved. The Characteriser's output is stood in for by hand-written
test modules, which is the correct boundary: this file proves the gate's
sandbox mechanics, not the model's writing ability.
"""

from __future__ import annotations

from principal.gates.characterise import establish_safety_net
from principal.sandbox.baseline import build_baseline
from tests.conftest import FIXTURE_SHA, MINI_REPO

TARGET_PATH = "src/auth/session.py"
LINE_START = 17
LINE_END = 22

TARGET_SOURCE = (MINI_REPO / "src" / "auth" / "session.py").read_text(encoding="utf-8")

GOOD_SUITE = '''
import pytest

from src.auth.session import create


def test_returns_a_session_with_the_given_user():
    s = create("alice")
    assert s.user == "alice"


def test_default_ttl_is_used_when_not_given():
    s = create("alice")
    assert s.ttl == 3600


def test_ttl_is_coerced_to_int():
    s = create("alice", "120")
    assert s.ttl == 120


def test_empty_user_raises_value_error():
    with pytest.raises(ValueError):
        create("")
'''

# Calls the function and asserts nothing about its result. A suite like this
# should survive nearly every mutant of the body it "covers".
WEAK_SUITE = '''
from src.auth.session import create


def test_create_does_not_crash():
    create("alice")
'''

# Fails against the untouched baseline outright — asserts behaviour the
# function does not have. Rule 2 exists precisely to catch this.
WRONG_SUITE = '''
from src.auth.session import create


def test_wrongly_expects_a_dict():
    assert create("alice") == {"user": "alice"}
'''


async def test_a_good_suite_clears_the_floor(sandbox, snapshot):
    baseline = await build_baseline(
        sandbox, base_image="python:3.11-slim", repo_url=str(snapshot),
        commit_sha=FIXTURE_SHA, timeout_s=300,
    )
    c0 = await sandbox.from_uuid(baseline.image)

    result = await establish_safety_net(
        sandbox, c0, target_path=TARGET_PATH, target_source=TARGET_SOURCE,
        line_start=LINE_START, line_end=LINE_END, test_source=GOOD_SUITE,
        test_path="tests/principal_characterisation/test_characterise_create.py",
        floor=0.60, cap=20, timeout_s=300,
    )

    assert result.ok, result.reason
    assert result.mutation is not None
    assert result.mutation.score is not None
    assert result.mutation.score >= 0.60
    assert result.mutants_generated > 0

    # The image the orchestrator re-baselines the job onto. Without a real
    # checkpoint here, gate 3 would run every subsequent candidate against an
    # image that never heard of the test that just proved itself trustworthy.
    assert result.image
    characterised = await sandbox.from_uuid(result.image)
    check = await sandbox.run(
        characterised, "cd /work && test -f tests/principal_characterisation/test_characterise_create.py",
        disposable=True, timeout_s=60,
    )
    assert check.exit_code == 0, "the characterisation test must be physically present on the checkpoint"


async def test_a_weak_suite_is_reported_as_insufficient_not_silently_accepted(sandbox, snapshot):
    baseline = await build_baseline(
        sandbox, base_image="python:3.11-slim", repo_url=str(snapshot),
        commit_sha=FIXTURE_SHA, timeout_s=300,
    )
    c0 = await sandbox.from_uuid(baseline.image)

    result = await establish_safety_net(
        sandbox, c0, target_path=TARGET_PATH, target_source=TARGET_SOURCE,
        line_start=LINE_START, line_end=LINE_END, test_source=WEAK_SUITE,
        test_path="tests/principal_characterisation/test_characterise_create.py",
        floor=0.60, cap=20, timeout_s=300,
    )

    assert not result.ok
    assert result.status == "insufficient"
    assert result.mutation is not None
    assert (result.mutation.score or 0.0) < 0.60


async def test_a_suite_that_fails_on_baseline_is_discarded_before_any_mutation_run(sandbox, snapshot):
    """Rule 2: a test that fails against unmodified C0 is wrong about current
    behaviour, and is discarded without spending a single mutation operation."""
    baseline = await build_baseline(
        sandbox, base_image="python:3.11-slim", repo_url=str(snapshot),
        commit_sha=FIXTURE_SHA, timeout_s=300,
    )
    c0 = await sandbox.from_uuid(baseline.image)

    result = await establish_safety_net(
        sandbox, c0, target_path=TARGET_PATH, target_source=TARGET_SOURCE,
        line_start=LINE_START, line_end=LINE_END, test_source=WRONG_SUITE,
        test_path="tests/principal_characterisation/test_characterise_create.py",
        floor=0.60, cap=20, timeout_s=300,
    )

    assert not result.ok
    assert result.status == "discarded"
    assert result.mutation is None  # never reached the mutation phase


async def test_characterisation_tests_land_under_a_frozen_test_path(sandbox, snapshot):
    """From the moment the file is written it must be under a path gate 1's
    `is_test_path` recognises, which is the entire freeze mechanism — no new
    scope-gate code was written for this, so a regression here is silent."""
    from principal.graph.parse import is_test_path

    assert is_test_path("tests/principal_characterisation/test_characterise_create.py")
