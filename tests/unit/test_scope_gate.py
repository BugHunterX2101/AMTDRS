"""Gate 1 is the guardrail the entire safety argument rests on.

Each test here corresponds to a way a generated patch could do damage, so a
failure in this file means a specific safety claim in the README has stopped
being true.
"""

from __future__ import annotations

import pytest

from principal.diffs import DiffUnparseable, parse_diff, path_traversal
from principal.gates.pipeline import VerdictKind
from principal.gates.scope import check_scope
from tests.conftest import diff_block

TARGET = "src/api/login.py"
RADIUS = {"src/api/login.py", "src/api/refresh.py", "src/auth/session.py"}


def _diff(path: str, *, old: str = "old_line", new: str = "new_line") -> object:
    return parse_diff(
        diff_block(
            f"""--- a/{path}
+++ b/{path}
@@ -1,3 +1,3 @@
 context
-{old}
+{new}
 trailing"""
        )
    )


def test_in_scope_patch_passes():
    assert check_scope(_diff(TARGET), TARGET, RADIUS).ok


def test_rejects_file_outside_the_task_even_if_inside_the_radius():
    """The radius is what the *job* may touch; the task is what this *attempt*
    may touch. Checking only the looser bound would let one candidate edit a
    file that a concurrently running task owns."""
    verdict = check_scope(_diff("src/api/refresh.py"), TARGET, RADIUS)
    assert not verdict.ok
    assert verdict.kind is VerdictKind.SCOPE
    assert "scoped to" in verdict.reason


def test_rejects_file_outside_the_radius():
    verdict = check_scope(_diff("src/unrelated/thing.py"), "src/unrelated/thing.py", RADIUS)
    assert not verdict.ok
    assert verdict.kind is VerdictKind.SCOPE


@pytest.mark.parametrize(
    "path", ["tests/test_login.py", "src/tests/test_x.py", "test_login.py"]
)
def test_rejects_edits_to_tests(path):
    """Without this, the cheapest way to make a failing patch pass is to delete
    the test that fails. The gate must make that impossible, not unlikely."""
    verdict = check_scope(_diff(path), path, RADIUS | {path})
    assert not verdict.ok
    assert "test file" in verdict.reason


@pytest.mark.parametrize(
    "path", ["pyproject.toml", "requirements.txt", "package.json", "go.mod", "Cargo.lock"]
)
def test_rejects_dependency_manifests(path):
    """A patch that edits a manifest can make the suite pass by changing what is
    installed, which is a green test that proves nothing about the refactor."""
    verdict = check_scope(_diff(path), path, RADIUS | {path})
    assert not verdict.ok
    assert "manifest" in verdict.reason


@pytest.mark.parametrize(
    "path", ["../../../etc/passwd", "/etc/passwd", "src/../../secrets.py"]
)
def test_rejects_path_traversal(path):
    assert path_traversal({path})


def test_empty_diff_is_rejected_not_accepted():
    """A diff naming no files touches nothing, so every set-containment check
    passes vacuously. It must be rejected explicitly."""
    with pytest.raises(DiffUnparseable):
        parse_diff(diff_block("--- a/x.py\n+++ b/x.py"))


def test_unfenced_output_is_unparseable():
    with pytest.raises(DiffUnparseable):
        parse_diff("here is your patch:\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b")


def test_two_diff_blocks_are_rejected():
    """Two blocks means the model produced alternatives. Silently taking the
    first is a guess, and a guess in the accept path is exactly what this system
    is built to avoid."""
    one = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b"
    with pytest.raises(DiffUnparseable):
        parse_diff(diff_block(one) + "\n" + diff_block(one))
