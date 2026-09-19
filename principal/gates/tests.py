"""Gate 3: local tests. Test selection, then one sandbox run.

Test selection comes from test_edge for the symbols defined in the changed file,
preferring coverage-derived edges and falling back to import-derived ones. If
selection returns nothing, the full suite runs rather than declaring success —
a file with no covering tests must never pass a gate by default.
"""

from __future__ import annotations

from principal.db.store import Store
from principal.gates.pipeline import Verdict, VerdictKind
from principal.sandbox.runner import TestRun, run_attempt

ALL_TESTS_SENTINEL: list[str] = []  # empty selection means "run everything"


def select_tests(store: Store, graph_id: str, changed_file: str) -> tuple[list[str], bool]:
    """Returns (nodeids, used_full_suite_fallback)."""
    symbols = store.symbols_in_files(graph_id, {changed_file})
    if not symbols:
        return ALL_TESTS_SENTINEL, True

    edges = store.tests_covering(graph_id, {s.id for s in symbols})
    coverage = [e for e in edges if e["source"] == "coverage"]
    chosen = coverage or [e for e in edges if e["source"] == "import"]
    if not chosen:
        return ALL_TESTS_SENTINEL, True

    nodeids = sorted({e["nodeid"] for e in chosen})
    return nodeids, False


async def run_tests_gate(sandbox, c0_image, diff_text: str, nodeids: list[str], *,
                          timeout_s: int, on_operation=None) -> tuple[Verdict, TestRun]:
    run = await run_attempt(sandbox, c0_image, diff_text, nodeids, timeout_s=timeout_s,
                             on_operation=on_operation)
    if run.apply_failed:
        return Verdict.fail(VerdictKind.SYNTAX, "syntax", "git apply rejected the diff on the real tree"), run
    if run.green:
        return Verdict.ok_(), run
    return Verdict.fail(
        VerdictKind.RED, "tests", f"{run.failed} failing, {run.errors} errors",
        failing=run.failing[:20],
    ), run
