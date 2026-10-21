"""The mutation audit against a real tree, with real `pytest` doing the judging.

The unit tests prove the accounting. This proves the part they cannot: that an
injected fault really lands in the file, really reaches the tests, and is really
taken out again — and that a weakness in the suite shows up as a located
survivor rather than a clean number.
"""

from __future__ import annotations

import difflib
from pathlib import Path

import pytest

from principal.gates.impact import changed_lines
from principal.gates.strength import plan_mutants, run_strength_audit
from principal.sandbox.baseline import build_baseline
from principal.sandbox.runner import run_attempt
from tests.conftest import FIXTURE_SHA

TARGET = "src/auth/session.py"

# Adds a clamp no test reaches. Of the faults injected into these lines, the ones
# that change the condition's behaviour for ordinary inputs are caught; the ones
# that only move the boundary, or change what an unexecuted branch returns, are
# not — which is the realistic shape of a weak suite.
CLAMP = "    if ttl > 100000:\n        ttl = 100000\n"


def _patch(original: str) -> tuple[str, str]:
    marker = '    if not user:\n        raise ValueError("user is required")\n'
    assert marker in original
    patched = original.replace(marker, CLAMP + marker)
    diff = "".join(difflib.unified_diff(
        original.splitlines(keepends=True), patched.splitlines(keepends=True),
        fromfile=f"a/{TARGET}", tofile=f"b/{TARGET}",
    ))
    return patched, diff


def _read_in_image(image) -> str:
    """The Windows fake keeps the tree under <image>/work; elsewhere it is /work."""
    local = image.workdir / "work" / TARGET
    path = local if local.exists() else Path("/work") / TARGET
    return path.read_text(encoding="utf-8")


@pytest.fixture
async def patched_image(sandbox, snapshot):
    baseline = await build_baseline(
        sandbox, base_image="python:3.11-slim", repo_url=str(snapshot), commit_sha=FIXTURE_SHA,
        timeout_s=300,
    )
    original = (snapshot / TARGET).read_text(encoding="utf-8")
    patched, diff = _patch(original)
    c0 = await sandbox.from_uuid(baseline.image)
    run = await run_attempt(sandbox, c0, diff, [], timeout_s=180)
    assert run.green, "the clamp is behaviour-preserving for the suite; it must stay green"
    return original, patched, await sandbox.from_uuid(run.image)


async def test_the_audit_finds_both_what_the_tests_catch_and_what_they_miss(sandbox, patched_image):
    original, patched, image = patched_image
    planned = plan_mutants({TARGET: original}, {TARGET: patched}, {TARGET})
    assert len(planned) >= 3

    result = await run_strength_audit(sandbox, image, planned, [], timeout_s=300)

    assert result.status == "measured"
    assert result.killed >= 1, "a flipped condition on the ordinary path must be caught"
    assert result.survived >= 1, "a boundary the suite never reaches must survive"
    assert result.score is not None and result.score < 1.0
    assert all(s["file"] == TARGET for s in result.survivors)
    _old, changed = changed_lines(original, patched)
    assert changed == {20, 21}, "the clamp is the only change, and it is these two lines"
    assert {s["line"] for s in result.survivors} <= changed, "survivors point at the changed lines"


async def test_the_audited_image_is_left_exactly_as_it_was(sandbox, patched_image):
    """The audit runs on a disposable fork. If it leaked, the image the approval
    step publishes from would carry an injected fault."""
    original, patched, image = patched_image
    planned = plan_mutants({TARGET: original}, {TARGET: patched}, {TARGET})

    await run_strength_audit(sandbox, image, planned, [], timeout_s=300)

    assert _read_in_image(image) == patched
