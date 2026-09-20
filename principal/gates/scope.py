"""Gate 1: scope. Local, no sandbox, the guardrail the whole safety argument
rests on. Parse the diff with a real patch parser rather than a regex — a hand-
rolled header match is precisely the kind of thing a malformed diff slips past.
"""

from __future__ import annotations

from principal.diffs import ParsedDiff, path_traversal
from principal.gates.pipeline import Verdict, VerdictKind
from principal.graph.parse import is_test_path
from principal.routines.registry import Routine, default_routine

BLOCKED_MANIFESTS = {
    "pyproject.toml", "setup.py", "setup.cfg", "poetry.lock", "Pipfile",
    "Pipfile.lock", "package.json", "package-lock.json", "yarn.lock",
    "pnpm-lock.yaml", "requirements.txt", "requirements-dev.txt", "test-requirements.txt",
    "Cargo.toml", "Cargo.lock", "go.mod", "go.sum",
}


def check_scope(
    diff: ParsedDiff, target_file: str, radius_files: set[str], *, routine: Routine | None = None,
) -> Verdict:
    """The one enforcement point for what a diff may touch.

    `routine` is read, never branched on ad hoc: today both registered routines
    leave `allows_new_files` false, so the check below has the same effect it
    always had. What changes is where that policy is declared. Relocation moves
    a symbol between two files that are both already inside the blast radius —
    the source loses it, the destination gains it, two single-file tasks, same
    as any other task — so it needs no widening here at all. The hook exists for
    the routine that will: extraction has to create a new shared module, and
    when that is built, `allows_new_files` is read from here rather than the
    gate growing a special case for one routine.
    """
    routine = routine or default_routine()
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

    outside = paths - radius_files
    if outside and not routine.allows_new_files:
        return Verdict.fail(
            VerdictKind.SCOPE, "scope", "diff touches a file outside the job's blast radius",
            paths=sorted(outside),
        )

    return Verdict.ok_()
