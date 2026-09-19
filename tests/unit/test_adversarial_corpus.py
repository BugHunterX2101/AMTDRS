"""The adversarial diff corpus: the regression suite for every safety claim.

Each fixture in tests/fixtures/diffs/ is a specific way a generated patch could
do damage, and each row below is the verdict that must come back. If a row here
goes green when it should be red, a named guarantee in the README has stopped
being true — so these assertions are deliberately exact about *which* gate
catches each case, not merely that something did.

The last row matters as much as the others. A gate suite that only proves things
get rejected is satisfied by a gate that rejects everything.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from principal.diffs import DiffUnparseable, parse_diff
from principal.gates.pipeline import VerdictKind
from principal.gates.scope import check_scope
from principal.gates.syntax import check_syntax

CORPUS = Path(__file__).resolve().parent.parent / "fixtures" / "diffs"

# The blast radius of src.auth.session.create in the mini_repo fixture.
RADIUS = {
    "src/auth/session.py", "src/api/login.py", "src/api/refresh.py",
    "src/admin/tools.py", "src/workers/cleanup.py", "src/legacy/compat.py",
    "src/registry.py",
}

UNPARSEABLE = "DIFF_UNPARSEABLE"

# fixture -> (task's target_file, required outcome)
CASES = [
    # --- gate 1: scope ---------------------------------------------------
    ("edits_test_file.diff", "src/auth/session.py", VerdictKind.SCOPE,
     "an agent that can edit the tests can pass any test"),
    ("edits_two_files.diff", "src/api/login.py", VerdictKind.SCOPE,
     "both edits are correct; it is still not this task's file to touch"),
    ("edits_manifest.diff", "pyproject.toml", VerdictKind.SCOPE,
     "changing what is installed makes the suite pass without proving anything"),
    ("path_traversal.diff", "../../etc/passwd", VerdictKind.SCOPE,
     "a diff header is a string before it is a path"),
    ("injected_instruction.diff", "src/auth/session.py", VerdictKind.SCOPE,
     "repo content is data; an instruction in a docstring earns no privilege"),

    # --- gate 2: syntax --------------------------------------------------
    ("truncated.diff", "src/auth/session.py", VerdictKind.SYNTAX,
     "applies cleanly and leaves the file unparseable"),

    # --- the parser, before anything is treated as a diff ----------------
    ("truncated_midhunk.diff", "src/auth/session.py", UNPARSEABLE,
     "the hunk header lies about its own line count"),
    ("two_blocks.txt", "src/auth/session.py", UNPARSEABLE,
     "two blocks means alternatives; taking the first would be a guess"),
    ("unfenced.txt", "src/auth/session.py", UNPARSEABLE,
     "no sentinel block at all"),

    # --- the one that must pass ------------------------------------------
    ("correct.diff", "src/auth/session.py", VerdictKind.OK,
     "the actual intended migration"),
]


def _load(name: str) -> str:
    """A .diff holds the bare patch; a .txt holds raw model output, sentinels
    and prose included."""
    raw = (CORPUS / name).read_text(encoding="utf-8")
    return raw if name.endswith(".txt") else f"<<<DIFF\n{raw.rstrip()}\nDIFF>>>"


@pytest.mark.parametrize(
    ("name", "target_file", "expected", "why"),
    CASES,
    ids=[c[0] for c in CASES],
)
async def test_adversarial_fixture(name, target_file, expected, why, snapshot):
    body = _load(name)

    try:
        diff = parse_diff(body)
    except DiffUnparseable:
        assert expected == UNPARSEABLE, f"{name} died at the parser but should have reached a gate: {why}"
        return

    assert expected != UNPARSEABLE, f"{name} parsed but should not have: {why}"

    verdict = check_scope(diff, target_file, RADIUS)
    if not verdict.ok:
        assert verdict.kind == expected, f"{name}: {why}"
        return

    verdict, _ = check_syntax(diff, target_file, snapshot)
    assert verdict.kind == expected, f"{name}: {why} (got {verdict.kind}: {verdict.reason})"


def test_every_fixture_on_disk_is_covered():
    """A fixture nobody asserts on is a fixture that silently stopped working.
    This fails when someone adds a .diff without adding its required verdict."""
    on_disk = {p.name for p in CORPUS.iterdir() if p.suffix in {".diff", ".txt"}}
    asserted = {c[0] for c in CASES}
    assert on_disk == asserted, f"uncovered fixtures: {on_disk - asserted}"


def test_the_corpus_contains_a_passing_case():
    """Guards the guard: a suite of only-rejections is satisfied by a gate that
    rejects everything, which would be perfectly safe and perfectly useless."""
    assert any(expected is VerdictKind.OK for _, _, expected, _ in CASES)
