"""MCP adapter over the code graph.

A thin wrapper. All five tools are read-only, and the reason that is worth
stating is that this server is reachable from outside the orchestrator: an agent
holding this toolset can read the code and nothing else. There is no tool here
that writes a file, applies a patch or runs a command.

The graph id is bound when the server is built, so a client cannot ask about a
graph it was not given.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from pydantic import Field

from mcp_code_graph import tools as T
from principal.db.store import Store
from principal.errors import PrincipalError

logger = logging.getLogger("mcp_code_graph")

INSTRUCTIONS = """\
Read-only queries over a parsed code graph: symbols, call edges, import edges and
per-test coverage for one repository at one commit.

Use `blast_radius` before proposing any change to a symbol — it returns every
file a change can reach, plus the call sites that cannot be statically proved and
therefore need a human. It raises rather than truncating when the radius is too
large, because a truncated radius produces a patch that looks complete and is not.

`tests_covering` comes from real coverage data (`pytest --cov-context=test`), not
from file naming, so it is a measurement and can be trusted as one.
"""


def build_server(store: Store, graph_id: str | None = None):
    """Build the MCP server. `graph_id` defaults to the most recent graph, which
    is what an interactive client almost always wants."""
    from fastmcp import FastMCP

    resolved = graph_id or _latest_graph(store)
    mcp = FastMCP(name="code-graph", instructions=INSTRUCTIONS)

    def ctx() -> T.GraphContext:
        gid = resolved or _latest_graph(store)
        if gid is None:
            raise ValueError("no code graph has been built yet; run a job or `principal radius` first")
        return T.GraphContext(store=store, graph_id=gid)

    def guard(fn, *args, **kwargs) -> Any:
        """PrincipalError carries a code that means something inside the
        orchestrator and nothing to an MCP client. Translate rather than leaking
        an internal exception type across the boundary."""
        try:
            return fn(*args, **kwargs)
        except PrincipalError as exc:
            return {"error": exc.code.value, "message": exc.message}

    @mcp.tool
    def find_symbol(
        name: Annotated[str, Field(description="A name or fully qualified name, e.g. 'create' or 'src.auth.session.create'")],
        kind: Annotated[str | None, Field(description="Optional filter: function, class, method")] = None,
    ) -> dict:
        """Locate a symbol and return its file, line range and signature."""
        return guard(T.find_symbol, ctx(), name, kind)

    @mcp.tool
    def callers_of(
        fqn: Annotated[str, Field(description="Fully qualified name of the symbol")],
    ) -> dict:
        """Every call site that can reach this symbol, each tagged `static`
        (proved from syntax) or `heuristic` (matched by name only)."""
        return guard(T.callers_of, ctx(), fqn)

    @mcp.tool
    def blast_radius(
        fqn: Annotated[str, Field(description="Fully qualified name of the symbol to be changed")],
        max_depth: Annotated[int, Field(description="Reverse-edge BFS depth", ge=1, le=6)] = 3,
        cap: Annotated[int, Field(description="Abort above this many files", ge=1, le=200)] = 40,
    ) -> dict:
        """Everything a change to this symbol can reach: files, tests, call
        sites, and the calls that cannot be proved statically."""
        return guard(T.blast_radius, ctx(), fqn, max_depth, cap)

    @mcp.tool
    def tests_covering(
        fqn: Annotated[str, Field(description="Fully qualified name of the symbol")],
    ) -> dict:
        """The tests that actually execute this symbol, from coverage data."""
        return guard(T.tests_covering, ctx(), fqn)

    @mcp.tool
    def read_span(
        path: Annotated[str, Field(description="Repository-relative path")],
        start: Annotated[int, Field(description="First line, 1-indexed", ge=1)],
        end: Annotated[int, Field(description="Last line, inclusive", ge=1)],
    ) -> dict:
        """Read a bounded line range from the snapshot."""
        return guard(T.read_span, ctx(), path, start, end)

    return mcp


def _latest_graph(store: Store) -> str | None:
    row = store.q1("SELECT id FROM graph ORDER BY rowid DESC LIMIT 1")
    return row["id"] if row else None


def main() -> None:  # pragma: no cover
    """Run standalone over stdio, for use from any MCP client."""
    from principal.config import get_settings

    settings = get_settings()
    build_server(Store(settings.principal_db)).run()


if __name__ == "__main__":  # pragma: no cover
    main()
