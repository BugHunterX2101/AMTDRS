"""Gate 2: syntax. Apply the diff to the local snapshot copy, parse the result
with the tree-sitter grammar for that language, and reject if the tree contains
any ERROR node. This catches truncated generations, which are common enough at
low cost tiers to be worth a dedicated gate, and it costs a millisecond.
"""

from __future__ import annotations

from pathlib import Path

from principal.diffs import ParsedDiff
from principal.errors import DiffUnparseable
from principal.gates.apply import apply_to_text
from principal.gates.pipeline import Verdict, VerdictKind
from principal.graph.languages import lang_of
from principal.graph.parse import parse_source


def check_syntax(diff: ParsedDiff, target_file: str, snapshot: Path) -> tuple[Verdict, str | None]:
    """Returns (verdict, patched_text). patched_text is None unless ok, so the
    caller has exactly the string the sandbox is about to test against."""
    lang = lang_of(target_file)
    if lang is None:
        return Verdict.fail(VerdictKind.SYNTAX, "syntax", f"unsupported file type: {target_file}"), None

    matching = [f for f in diff.patch if target_file in {f.source_file, f.target_file} or
                target_file in {f.source_file.removeprefix("a/"), f.target_file.removeprefix("b/")}]
    if not matching:
        return Verdict.fail(VerdictKind.SYNTAX, "syntax", "diff does not touch the target file"), None
    patched_file = matching[0]

    original_path = snapshot / target_file
    try:
        original = original_path.read_text(encoding="utf-8") if original_path.exists() else ""
    except OSError as exc:
        return Verdict.fail(VerdictKind.SYNTAX, "syntax", f"could not read original file: {exc}"), None

    try:
        result = apply_to_text(original, patched_file)
    except DiffUnparseable as exc:
        return Verdict.fail(VerdictKind.SYNTAX, "syntax", str(exc)), None

    parsed = parse_source(result.new_text.encode("utf-8"), lang, target_file)
    if parsed.has_error:
        return Verdict.fail(VerdictKind.SYNTAX, "syntax", "patched file does not parse cleanly"), None

    return Verdict.ok_(), result.new_text
