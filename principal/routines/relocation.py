"""Relocation: move code to a better place, update the import paths.

The cheapest of the four SWE Atlas categories to support correctly, because the
import graph already answers the only question that matters. Moving a symbol
does not change who *calls* it — it changes who has to *import* it from a new
path. Seeding the radius with the full consumer closure would drag in every file
that calls the target through three layers of indirection, none of which need to
change, and would spend the token budget rendering them.

The honest limit is recorded in the same place as the capability: a file move
looks like a deletion plus an addition to a diff parser, so the API delta check
has to be told that a moved symbol moved. That is `moved_symbols` below, and the
behaviour gate consumes it.
"""

from __future__ import annotations

from principal.db.store import Store, SymbolRow
from principal.errors import RadiusTooLarge
from principal.graph.radius import BlastRadius


def importers_only_radius(
    store: Store, graph_id: str, target: SymbolRow, *, max_depth: int, cap: int
) -> BlastRadius:
    """Files that import the target symbol, or the module containing it.

    `max_depth` is accepted and unused: import dependence is not transitive for
    this purpose. If A imports B and B imports the moved module, only B's import
    statement is wrong after the move. Walking further would return files that
    need no edit, which is the mistake this seed exists to avoid.
    """
    del max_depth

    radius = BlastRadius(target=target)
    radius.files.add(target.path)
    radius.depth_reached = 1

    for row in store.importers_of(graph_id, {target.id}):
        radius.files.add(row["path"])
    for row in store.importers_of_files(graph_id, {target.path}):
        radius.files.add(row["path"])

    # Dynamic dispatch is worse for relocation than for anything else: a
    # string-keyed import ("importlib.import_module(name)") breaks silently at
    # runtime when the module moves, and nothing static can find it. Recording
    # these is the whole risk section of a relocation PR.
    for edge in store.dynamic_calls_in(graph_id, radius.files):
        radius.unresolved.append(
            {"file": edge.path, "line": edge.line, "name": edge.callee_name,
             "reason": "dynamic dispatch, may reference the moved module by name"}
        )

    radius.symbols = {target.id}
    is_test_by_path = store.test_flags_of(graph_id, radius.files)
    non_test = {p for p in radius.files if not is_test_by_path.get(p, False)}
    if len(non_test) > cap:
        raise RadiusTooLarge(len(non_test), cap)

    covering = store.tests_covering(graph_id, {target.id})
    test_paths = store.paths_of_files({int(row["test_file_id"]) for row in covering})
    for row in covering:
        radius.tests.append(row["nodeid"])
        path = test_paths.get(int(row["test_file_id"]), "")
        if path:
            radius.test_files.add(path)
    radius.tests = sorted(set(radius.tests))
    radius.files = non_test
    return radius


def moved_symbols(declared_removals: list[str], patched_exports: dict[str, set[str]]) -> set[str]:
    """Names a relocation task declared removing that reappear in another file.

    Without this the API delta check sees the old file lose an export and calls
    it an undeclared removal, which fails every correct relocation. With it, a
    symbol that left one file and arrived in another is what it looks like: a
    move, not a deletion.
    """
    present: set[str] = set()
    for names in patched_exports.values():
        present |= names
    return {name for name in declared_removals if name in present}
