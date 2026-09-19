"""Apply a unified diff to file text in memory, no sandbox and no git.

Gate 1 and gate 2 both need the patched content before deciding whether the
patch is worth a sandbox operation. Doing this in Python rather than shelling out
to `git apply` keeps the snapshot on disk untouched, which matters because
several tasks read it concurrently.
"""

from __future__ import annotations

from dataclasses import dataclass

import unidiff

from principal.errors import DiffUnparseable


@dataclass(slots=True)
class ApplyResult:
    new_text: str
    hunks_applied: int


def apply_to_text(original: str, patched_file: unidiff.PatchedFile) -> ApplyResult:
    if patched_file.is_added_file:
        lines = []
        for hunk in patched_file:
            for line in hunk:
                if line.is_added or line.is_context:
                    lines.append(line.value)
        return ApplyResult("".join(lines), len(list(patched_file)))

    src_lines = original.splitlines(keepends=True)
    out: list[str] = []
    cursor = 0  # 0-indexed position in src_lines already emitted

    for hunk in patched_file:
        start = hunk.source_start - 1  # unidiff is 1-indexed
        if start < cursor:
            raise DiffUnparseable("hunks overlap or are out of order")
        out.extend(src_lines[cursor:start])
        pos = start
        for line in hunk:
            if line.is_context:
                if pos >= len(src_lines) or _norm(src_lines[pos]) != _norm(line.value):
                    raise DiffUnparseable(
                        f"context mismatch at line {pos + 1}: patch does not apply cleanly"
                    )
                out.append(src_lines[pos])
                pos += 1
            elif line.is_removed:
                if pos >= len(src_lines) or _norm(src_lines[pos]) != _norm(line.value):
                    raise DiffUnparseable(
                        f"removed-line mismatch at line {pos + 1}: patch does not apply cleanly"
                    )
                pos += 1
            elif line.is_added:
                out.append(line.value)
        cursor = pos

    out.extend(src_lines[cursor:])
    return ApplyResult("".join(out), len(list(patched_file)))


def _norm(line: str) -> str:
    return line.rstrip("\r\n")
