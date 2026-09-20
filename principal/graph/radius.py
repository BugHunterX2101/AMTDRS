"""Blast radius: reverse-edge BFS.

The question is not what the target calls, it is who calls the target. Expansion
runs over reverse edges, heuristic edges expand the frontier *and* are recorded,
and exceeding the cap raises rather than truncating — a truncated blast radius is
worse than no blast radius, because it looks like an answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from principal.db.store import Store, SymbolRow
from principal.errors import Code, PrincipalError, RadiusTooLarge


@dataclass(slots=True)
class BlastRadius:
    target: SymbolRow
    files: set[str] = field(default_factory=set)
    tests: list[str] = field(default_factory=list)
    test_files: set[str] = field(default_factory=set)
    symbols: set[int] = field(default_factory=set)
    unresolved: list[dict[str, object]] = field(default_factory=list)
    call_sites: list[dict[str, object]] = field(default_factory=list)
    depth_reached: int = 0

    def to_payload(self) -> dict[str, object]:
        return {
            "target": {
                "fqn": self.target.fqn, "path": self.target.path,
                "signature": self.target.signature, "line": self.target.line_start,
            },
            "files": sorted(self.files),
            "tests": self.tests,
            "unresolved": self.unresolved,
            "call_sites": self.call_sites,
            "depth_reached": self.depth_reached,
        }


def find_target(store: Store, graph_id: str, target_fqn: str, kind: str | None = None) -> SymbolRow:
    matches = store.find_symbols(graph_id, target_fqn, kind)
    if not matches:
        raise PrincipalError(
            Code.TARGET_NOT_FOUND,
            f"no symbol matches {target_fqn!r} in this repository at this commit",
        )
    # Prefer an exact fqn hit, then a non-test definition, deterministically.
    exact = [m for m in matches if m.fqn == target_fqn]
    pool = exact or matches
    return sorted(pool, key=lambda s: (s.path.count("/"), s.path))[0]


def blast_radius(
    store: Store, graph_id: str, target: SymbolRow, *, max_depth: int = 3, cap: int = 40
) -> BlastRadius:
    seen: set[int] = {target.id}
    frontier: set[int] = {target.id}
    radius = BlastRadius(target=target)
    radius.files.add(target.path)

    depth = 0
    for depth in range(1, max_depth + 1):
        if not frontier:
            break
        callers = store.callers_of(graph_id, frontier)
        importers = store.importers_of(graph_id, frontier)

        for edge in callers:
            radius.files.add(edge.path)
            radius.call_sites.append(
                {"file": edge.path, "line": edge.line, "symbol": edge.callee_name,
                 "confidence": edge.confidence}
            )
            if edge.confidence == "heuristic":
                radius.unresolved.append(
                    {"file": edge.path, "line": edge.line, "name": edge.callee_name,
                     "reason": "ambiguous or dynamic dispatch"}
                )

        nxt: set[int] = set()
        for edge in callers:
            if edge.caller_symbol_id:
                nxt.add(edge.caller_symbol_id)

        # Every path that enters the radius at this depth, resolved to symbols in
        # one query rather than one per path. The previous shape issued a query
        # per importer row — including repeats for a path already seen — which on
        # a wide importer set is the dominant cost of computing a radius at all.
        reached: set[str] = {row["path"] for row in importers}

        # Files that import the *module* containing anything in the frontier.
        # A module-level import binds no symbol, so without this step a file that
        # reaches the target through attribute access or a registry lookup never
        # enters the radius at all.
        frontier_files = _files_of_symbols(store, graph_id, frontier | {target.id})
        reached |= {row["path"] for row in store.importers_of_files(graph_id, frontier_files)}

        radius.files |= reached
        for sym in store.symbols_in_files(graph_id, reached):
            nxt.add(sym.id)

        frontier = nxt - seen
        seen |= frontier
        radius.depth_reached = depth

    # Calls that could not be resolved at all but name the target. These are the
    # silent-failure surface: no static edge proves they reach the target, and
    # pretending they do not exist is how a migration misses a call site.
    for edge in store.unresolved_matching(graph_id, {target.name}):
        radius.files.add(edge.path)
        radius.unresolved.append(
            {"file": edge.path, "line": edge.line, "name": edge.callee_name,
             "reason": "unresolved call matching the target name"}
        )

    # Dynamic dispatch inside files that reached the radius. getattr and
    # registry lookups bind at runtime, so the call graph cannot say whether they
    # hit the target. Recording them is the honest alternative to claiming they
    # do not exist.
    for edge in store.dynamic_calls_in(graph_id, radius.files):
        radius.unresolved.append(
            {"file": edge.path, "line": edge.line, "name": edge.callee_name,
             "reason": "dynamic dispatch, target cannot be proved statically"}
        )

    radius.symbols = seen
    is_test_by_path = store.test_flags_of(graph_id, radius.files)
    non_test = {p for p in radius.files if not is_test_by_path.get(p, False)}
    if len(non_test) > cap:
        raise RadiusTooLarge(len(non_test), cap)

    covering = store.tests_covering(graph_id, seen)
    test_paths = store.paths_of_files({int(row["test_file_id"]) for row in covering})
    for row in covering:
        radius.tests.append(row["nodeid"])
        path = test_paths.get(int(row["test_file_id"]), "")
        if path:
            radius.test_files.add(path)
    radius.tests = sorted(set(radius.tests))
    radius.files = non_test
    radius.unresolved = _dedupe(radius.unresolved)
    return radius


def _files_of_symbols(store: Store, graph_id: str, symbol_ids: set[int]) -> set[str]:
    if not symbol_ids:
        return set()
    marks = ",".join("?" * len(symbol_ids))
    rows = store.q(
        "SELECT DISTINCT f.path AS path FROM symbol s JOIN file f ON f.id = s.file_id"
        f" WHERE s.graph_id = ? AND s.id IN ({marks})",
        (graph_id, *sorted(symbol_ids)),
    )
    return {r["path"] for r in rows}


def _dedupe(items: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[tuple] = set()
    out: list[dict[str, object]] = []
    for item in items:
        key = (item.get("file"), item.get("line"), item.get("name"))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return sorted(out, key=lambda i: (str(i.get("file")), int(i.get("line") or 0)))
