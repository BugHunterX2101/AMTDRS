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

import difflib
import re
from dataclasses import dataclass

import unidiff

from principal.errors import DiffUnparseable

OPEN = "<<<DIFF"
CLOSE = "DIFF>>>"
BLOCK = re.compile(re.escape(OPEN) + r"\r?\n(.*?)" + re.escape(CLOSE), re.DOTALL)
# Some replies end the block with a bare `>>>`. No diff line can be exactly
# `>>>` (every one starts with space, + or -), so it can only be the end.
BARE_BLOCK = re.compile(re.escape(OPEN) + r"\r?\n(.*?)\r?\n>>>[ \t]*(?:\r?\n|$)", re.DOTALL)


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
    indented_closer = bool(blocks)
    if not blocks:
        blocks = BARE_BLOCK.findall(raw_text)
    if not blocks:
        raise DiffUnparseable("no <<<DIFF ... DIFF>>> block found in the model output")
    if len(blocks) > 1:
        raise DiffUnparseable(f"expected exactly one diff block, found {len(blocks)}")
    body = blocks[0]
    # ` DIFF>>>` indented by the model leaves a whitespace-only last line, which
    # would read as an empty context line. A real blank context line is followed
    # by a newline before the marker, so it does not end up here.
    last = body.rsplit("\n", 1)[-1]
    if indented_closer and last and not last.strip():
        body = body[: -len(last)]
    return body.strip("\n")


def parse_diff(raw_text: str) -> ParsedDiff:
    text = extract_diff_block(raw_text)
    if not text.strip():
        raise DiffUnparseable("diff block was empty")

    text = _normalise_hunks(text)
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


_HUNK_HEADER = re.compile(r"^@@\s*-(\d+)(?:,\d+)?\s+\+\d+(?:,\d+)?\s*@@")
_NO_NEWLINE = "\\ No newline at end of file"


def _normalise_hunks(text: str) -> str:
    """Fill in hunk headers that carry no line numbers.

    Models copy the prompt's `@@ ...` placeholder, and `unidiff` then sees no
    hunks at all. The counts are redundant with the body, so derive them. A start
    line is only ever a hint for `canonicalise`, which locates hunks by content.
    """
    out: list[str] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.startswith("@@"):
            out.append(line)
            i += 1
            continue
        m = _HUNK_HEADER.match(line)
        start = int(m.group(1)) if m else 1
        i += 1
        body: list[str] = []
        while i < len(lines) and not lines[i].startswith("@@") and not (
            lines[i].startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ ")
        ):
            body.append(lines[i])
            i += 1
        while body and body[-1] == "":
            body.pop()
        fixed = [b if b[:1] in {" ", "+", "-", "\\"} else " " + b for b in body]
        old = sum(1 for b in fixed if b[0] in " -")
        new = sum(1 for b in fixed if b[0] in " +")
        # A numeric header makes a claim about the body, and a body that falls
        # short of it is how a truncated model response looks. Keep such a header
        # as written so `unidiff` still rejects the mismatch; only a placeholder
        # header with no numbers at all is filled in.
        out.append(line if m else f"@@ -{start},{old} +{start},{new} @@")
        out.extend(fixed)
    return "\n".join(out)


def canonicalise(parsed: ParsedDiff, target_file: str, original: str) -> ParsedDiff:
    """Re-derive a correct unified diff from what the hunks mean.

    Hunks are located by their content, not by the line numbers the model wrote,
    which are routinely off. The patched text is then diffed against the original
    to produce headers `git apply` accepts. Deterministic, and no model is
    involved: a hunk whose context cannot be found in the file is rejected.
    """
    files = list(parsed.patch)
    if len(files) != 1 or files[0].is_added_file or files[0].is_removed_file:
        return parsed

    src = original.splitlines(keepends=True)
    eol = "\r\n" if src and src[0].endswith("\r\n") else "\n"
    out: list[str] = []
    cursor = 0
    for hunk in files[0]:
        old = [ln.value.rstrip("\r\n") for ln in hunk if ln.is_context or ln.is_removed]
        new = [ln.value.rstrip("\r\n") for ln in hunk if ln.is_context or ln.is_added]
        hint = max(hunk.source_start - 1, 0)
        starts = [
            s for s in range(cursor, len(src) - len(old) + 1)
            if all(src[s + k].rstrip("\r\n") == old[k] for k in range(len(old)))
        ]
        if old and not starts:
            raise DiffUnparseable("context mismatch: a hunk's context is not found in the file")
        at = min(starts, key=lambda s: abs(s - hint)) if old else min(max(hint, cursor), len(src))
        out.extend(src[cursor:at])
        out.extend(n + eol for n in new)
        cursor = at + len(old)
    out.extend(src[cursor:])

    if out == src:
        raise DiffUnparseable("diff block contains no hunks, so it would change nothing")

    body: list[str] = []
    for ln in list(difflib.unified_diff(src, out, n=3))[2:]:
        body.append(ln if ln.endswith("\n") else ln + "\n" + _NO_NEWLINE + "\n")
    text = f"--- a/{target_file}\n+++ b/{target_file}\n" + "".join(body)
    return parse_diff(f"{OPEN}\n{text}{CLOSE}")


def path_traversal(paths: set[str]) -> bool:
    return any(".." in p.split("/") or p.startswith("/") for p in paths)
