"""Gate 1: scope. Local, no sandbox, the guardrail the whole safety argument
rests on. Parse the diff with a real patch parser rather than a regex — a hand-
rolled header match is precisely the kind of thing a malformed diff slips past.
"""

from __future__ import annotations

from principal.diffs import ParsedDiff, path_traversal
from principal.gates.pipeline import Verdict, VerdictKind
from principal.graph.parse import is_test_path

BLOCKED_MANIFESTS = {
    "pyproject.toml", "setup.py", "setup.cfg", "poetry.lock", "Pipfile",
    "Pipfile.lock", "package.json", "package-lock.json", "yarn.lock",
    "pnpm-lock.yaml", "requirements.txt", "requirements-dev.txt", "test-requirements.txt",
    "Cargo.toml", "Cargo.lock", "go.mod", "go.sum",
}


def check_scope(diff: ParsedDiff, target_file: str, radius_files: set[str]) -> Verdict:
    paths = diff.touched_paths

    if path_traversal(paths):
        return Verdict.fail(VerdictKind.SCOPE, "scope", "path traversal in a diff header", paths=sorted(paths))

    # Checked against the TASK's target file, not merely the blast radius. The
    # radius is what the job may touch; the task is what this attempt may touch.
    # Checking only the looser bound would let a coder edit a file assigned to a
    # different task running concurrently.
    if paths - {target_file}:
        return Verdict.fail(
            VerdictKind.SCOPE, "scope",
            f"diff touches {sorted(paths - {target_file})}, task is scoped to {target_file}",
            paths=sorted(paths),
        )
    if not paths:
        return Verdict.fail(VerdictKind.SCOPE, "scope", "diff names no files")

    if any(is_test_path(p) for p in paths):
        return Verdict.fail(VerdictKind.SCOPE, "scope", "diff touches a test file", paths=sorted(paths))

    manifest_hits = [p for p in paths if p.rsplit("/", 1)[-1] in BLOCKED_MANIFESTS]
    if manifest_hits:
        return Verdict.fail(
            VerdictKind.SCOPE, "scope", "diff touches a dependency manifest", paths=manifest_hits
        )

    if not paths <= radius_files:
        return Verdict.fail(
            VerdictKind.SCOPE, "scope", "diff touches a file outside the job's blast radius",
            paths=sorted(paths - radius_files),
        )

    return Verdict.ok_()
