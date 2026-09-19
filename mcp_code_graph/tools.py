"""The five code-graph tools, as plain functions over a Store.

Kept free of any MCP import on purpose. The orchestrator's agents call these
directly and an MCP client calls them over the wire, and neither path should be
able to break the other. server.py is a thin adapter.

Every one of these is read-only. There is no tool here that writes, applies a
patch or runs anything — an agent holding this toolset can look at the code and
nothing else, which is the only reason it is safe to expose over MCP at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from principal.db.store import Store
from principal.errors import Code, PrincipalError
from principal.graph.radius import blast_radius as _blast_radius
from principal.graph.radius import find_target

MAX_SPAN_LINES = 400


@dataclass(slots=True)
class GraphContext:
    """Which graph the tools answer against, and where its files live on disk."""

    store: Store
    graph_id: str

    @property
    def snapshot(self) -> Path:
        snap = self.store.snapshot_of(self.graph_id)
        if snap is None:
            raise PrincipalError(Code.TARGET_NOT_FOUND, f"graph {self.graph_id} has no snapshot")
        return snap


def find_symbol(ctx: GraphContext, name: str, kind: str | None = None) -> dict[str, Any]:
    """Locate a symbol by name or fully qualified name.

    Accepts either spelling because an agent reading a diff sees `create` and an
    agent reading a plan sees `src.auth.session.create`, and making the caller
    know which one it holds is a pointless source of failed lookups.
    """
    rows = ctx.store.find_symbols(ctx.graph_id, name, kind)
    return {
        "query": name,
        "count": len(rows),
        "symbols": [
            {
                "fqn": s.fqn, "name": s.name, "kind": s.kind, "path": s.path,
                "line_start": s.line_start, "line_end": s.line_end,
                "signature": s.signature, "exported": bool(s.exported),
            }
            for s in rows
        ],
    }


def callers_of(ctx: GraphContext, fqn: str) -> dict[str, Any]:
    """Every call site that can reach this symbol, with how sure we are.

    `confidence` is the field that matters. `static` means the edge was proved
    from the syntax; `heuristic` means it was matched by name and could be a
    different function with the same name. Collapsing the two would make the
    result look more authoritative than it is.
    """
    symbols = ctx.store.find_symbols(ctx.graph_id, fqn)
    if not symbols:
        return {"fqn": fqn, "found": False, "callers": []}

    ids = {s.id for s in symbols}
    edges = ctx.store.callers_of(ctx.graph_id, ids)
    return {
        "fqn": fqn,
        "found": True,
        "count": len(edges),
        "callers": [
            {"path": e.path, "line": e.line, "symbol": e.callee_name, "confidence": e.confidence}
            for e in edges
        ],
    }


def blast_radius(ctx: GraphContext, fqn: str, max_depth: int = 3, cap: int = 40) -> dict[str, Any]:
    """Everything a change to this symbol can reach: files, tests, call sites,
    and the calls that cannot be proved at all.

    This raises rather than truncating when the radius exceeds `cap`. A
    truncated radius produces a patch that looks complete and is not, which is
    the most dangerous thing this whole system could return.
    """
    target = find_target(ctx.store, ctx.graph_id, fqn)
    radius = _blast_radius(ctx.store, ctx.graph_id, target, max_depth=max_depth, cap=cap)
    return radius.to_payload()


def tests_covering(ctx: GraphContext, fqn: str) -> dict[str, Any]:
    """The tests that actually execute this symbol, from real coverage data.

    Derived from `pytest --cov-context=test` on the baseline run, so this is a
    measurement rather than a guess from file naming. It is what lets a fork run
    five tests instead of five hundred.
    """
    symbols = ctx.store.find_symbols(ctx.graph_id, fqn)
    if not symbols:
        return {"fqn": fqn, "found": False, "tests": []}

    rows = ctx.store.tests_covering(ctx.graph_id, {s.id for s in symbols})
    tests = sorted({r["nodeid"] for r in rows})
    # Both edge sources are reported rather than merged. An 'import' edge is a
    # guess from module structure; a 'coverage' edge is a measurement from the
    # baseline run. A caller deciding which tests to trust needs to know which
    # it is holding.
    by_source: dict[str, list[str]] = {}
    for r in rows:
        by_source.setdefault(r["source"], []).append(r["nodeid"])
    return {
        "fqn": fqn,
        "found": True,
        "count": len(tests),
        "tests": tests,
        "by_source": {k: sorted(set(v)) for k, v in sorted(by_source.items())},
        "source": "pytest --cov-context=test on the verified baseline",
    }


def read_span(ctx: GraphContext, path: str, start: int, end: int) -> dict[str, Any]:
    """Read a line range from the snapshot.

    Confined to the snapshot directory, and bounded. The path check is not
    ceremony: `path` reaches this function from model output, and without the
    resolved-prefix check a `../../` prefix would read anything the process can.
    """
    snapshot = ctx.snapshot
    target = (snapshot / path).resolve()
    try:
        target.relative_to(snapshot.resolve())
    except ValueError as exc:
        raise PrincipalError(
            Code.GATE_SCOPE, f"path escapes the snapshot: {path}"
        ) from exc

    if not target.is_file():
        return {"path": path, "found": False, "lines": []}

    start = max(1, int(start))
    end = max(start, int(end))
    if end - start + 1 > MAX_SPAN_LINES:
        end = start + MAX_SPAN_LINES - 1

    text = target.read_text(encoding="utf-8", errors="replace").splitlines()
    span = text[start - 1 : end]
    return {
        "path": path,
        "found": True,
        "start": start,
        "end": start + len(span) - 1,
        "truncated": end - start + 1 >= MAX_SPAN_LINES,
        "text": "\n".join(span),
    }


TOOLS = {
    "find_symbol": find_symbol,
    "callers_of": callers_of,
    "blast_radius": blast_radius,
    "tests_covering": tests_covering,
    "read_span": read_span,
}
