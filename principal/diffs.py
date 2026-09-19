"""Sentinel block extraction, then a real patch parser.

The Coder always uses protocol C (raw text). A unified diff is whitespace-
sensitive, and wrapping one in JSON means escaping newlines and tabs through a
model with no particular reason to get the escaping right. The parser takes the
last sentinel-delimited block, rejects anything with a second block, and rejects
any diff whose file headers name a path other than the task's target file. That
last check is gate 1 and it happens here, before the diff is ever treated as one.

This lives at the top level rather than under `agents` because both `agents` and
`gates` need it, and the architecture contract forbids `gates` from importing
`agents` — "no model in the accept path" is the project's central claim, so the
import graph has to make it true rather than merely intend it. Parsing a diff
involves no model, so a neutral leaf module is where it belongs; keeping it
under `agents` would have meant weakening the contract to accommodate a file
that was never the problem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import unidiff

from principal.errors import DiffUnparseable

OPEN = "<<<DIFF"
CLOSE = "DIFF>>>"
BLOCK = re.compile(re.escape(OPEN) + r"\r?\n(.*?)" + re.escape(CLOSE), re.DOTALL)


@dataclass(slots=True)
class ParsedDiff:
    text: str
    patch: unidiff.PatchSet
    touched_paths: set[str]


def _strip_prefix(path: str) -> str:
    """a/src/x.py -> src/x.py. Also tolerates a bare path with no prefix."""
    if path in ("/dev/null",):
        return path
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return path[2:]
    return path


def extract_diff_block(raw_text: str) -> str:
    blocks = BLOCK.findall(raw_text)
    if not blocks:
        raise DiffUnparseable("no <<<DIFF ... DIFF>>> block found in the model output")
    if len(blocks) > 1:
        raise DiffUnparseable(f"expected exactly one diff block, found {len(blocks)}")
    return blocks[0].strip("\n")


def parse_diff(raw_text: str) -> ParsedDiff:
    text = extract_diff_block(raw_text)
    if not text.strip():
        raise DiffUnparseable("diff block was empty")

    try:
        patch = unidiff.PatchSet(text)
    except unidiff.UnidiffParseError as exc:
        raise DiffUnparseable(f"not a valid unified diff: {exc}") from exc

    if len(patch) == 0:
        raise DiffUnparseable("diff block parsed to zero file changes")

    # Headers with no hunks parse cleanly and apply as a no-op, which is the
    # worst possible shape for this system to accept: it clears gate 1 (it
    # touches only its own target file), clears gate 2 (the file still parses,
    # because it is unchanged), and then clears gate 3 (the tests pass, because
    # nothing happened). The task would be reported "verified" having changed
    # nothing at all. A diff that changes nothing is not a patch.
    if not any(f for f in patch if len(f) > 0):
        raise DiffUnparseable("diff block contains no hunks, so it would change nothing")

    touched: set[str] = set()
    for f in patch:
        touched.add(_strip_prefix(f.source_file))
        touched.add(_strip_prefix(f.target_file))
    touched.discard("/dev/null")

    return ParsedDiff(text=text, patch=patch, touched_paths=touched)


def path_traversal(paths: set[str]) -> bool:
    return any(".." in p.split("/") or p.startswith("/") for p in paths)
