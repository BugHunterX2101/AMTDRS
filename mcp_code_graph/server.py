"""MCP adapter over the code graph.

A thin wrapper. All five tools are read-only, and the reason that is worth
stating is that this server is reachable from outside the orchestrator: an agent
holding this toolset can read the code and nothing else. There is no tool here
that writes a file, applies a patch or runs a command.

The graph id is bound when the server is built, so a client cannot ask about a
graph it was not given.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from pydantic import Field

from mcp_code_graph import tools as T
from principal.config import Settings
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

The same graph is also offered as resources a host can attach without a tool call:
`amtdrs://graph/tree` walks it as a directory (open one branch at a time, then read
a definition from `amtdrs://graph/entities/{fqn}`), and
`amtdrs://graph/blast-radius/{fqn}` is the radius of a change. The `plan_change` and
`relocate_symbol` prompts pre-fill the radius for a symbol. This server cannot edit
anything; applying a change is `principal run`.
"""


def build_server(store: Store, graph_id: str | None = None, settings: Settings | None = None):
    """Build the MCP server. `graph_id` defaults to the most recent graph, which
    is what an interactive client almost always wants. `settings` is only needed
    for the job evidence resource, which reads the run directory."""
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

    # ---- resources: the same read-only queries, attachable without a tool call.

    def as_json(value: Any) -> str:
        return json.dumps(value, indent=2, default=str)

    @mcp.resource("amtdrs://graph/tree", name="graph-tree-root", mime_type="application/json")
    def tree_root() -> str:
        """Top level of the code graph, as a directory."""
        return as_json(guard(T.graph_tree, ctx(), ""))

    @mcp.resource("amtdrs://graph/tree/{path*}", name="graph-tree", mime_type="application/json")
    def tree(path: str) -> str:
        """One level of the code graph: a directory's children, or a file's symbols."""
        return as_json(guard(T.graph_tree, ctx(), path))

    @mcp.resource("amtdrs://graph/entities/{fqn}", name="graph-entity", mime_type="application/json")
    def entity(fqn: str) -> str:
        """A symbol's signature, file, line range and source."""
        return as_json(guard(T.entity, ctx(), fqn))

    @mcp.resource("amtdrs://graph/blast-radius/{fqn}{?depth}", name="graph-blast-radius", mime_type="application/json")
    def radius(fqn: str, depth: int = 3) -> str:
        """Everything a change to this symbol can reach, with call sites tagged by confidence."""
        return as_json(guard(T.blast_radius, ctx(), fqn, max(1, min(int(depth), 6))))

    if settings is not None:
        @mcp.resource("amtdrs://jobs/{job_id}/evidence", name="job-evidence", mime_type="application/json")
        def job_evidence(job_id: str) -> str:
            """The evidence bundle for a run: outcome, gates, usage and artifact hashes."""
            from principal.orchestrator.evidence import build_evidence

            evidence = build_evidence(store, settings, job_id)
            return as_json(evidence if evidence is not None else {"error": "no such job", "job_id": job_id})

    # ---- prompts: pre-filled starting points. They brief a host model with the
    # real radius; none of them can change anything.

    def brief(fqn: str, goal: str, header: str, payload: dict) -> str:
        static = [c for c in payload.get("call_sites", []) if c["confidence"] == "static"]
        return "\n".join([
            header,
            f"Goal: {goal}",
            f"Target: `{payload['target']['fqn']}` at {payload['target']['path']}:{payload['target']['line']}"
            f" — signature `{payload['target']['signature']}`",
            f"Files a change can reach ({len(payload['files'])}): " + ", ".join(payload["files"]),
            f"Static call sites: {len(static)}. Calls that could NOT be proved and need a human: "
            f"{len(payload['unresolved'])}"
            + "".join(f"\n  - {u['file']}:{u['line']} `{u['name']}` ({u['reason']})" for u in payload["unresolved"][:20]),
            "Tests that execute it: " + (", ".join(payload["tests"][:20]) or "none recorded"),
            "",
            "This server is read-only. Use `callers_of`, `tests_covering` and `read_span` to check the "
            "above before proposing anything, and say plainly which call sites you could not verify. "
            f"To have Principal apply and verify a change, run `principal run --target {fqn}`.",
        ])

    @mcp.prompt
    def plan_change(
        fqn: Annotated[str, Field(description="Fully qualified name of the symbol to change")],
        goal: Annotated[str, Field(description="What the change should achieve, in a sentence")],
    ) -> str:
        """Brief the model on a symbol's blast radius before it plans a change."""
        out = guard(T.blast_radius, ctx(), fqn)
        if "error" in out:
            return f"Cannot brief `{fqn}`: {out['message']}"
        return brief(fqn, goal, f"Plan a change to `{fqn}`.", out)

    @mcp.prompt
    def relocate_symbol(
        fqn: Annotated[str, Field(description="Fully qualified name of the symbol to move")],
        destination: Annotated[str, Field(description="Module path to move it to, e.g. src.auth.tokens")],
    ) -> str:
        """Brief the model on moving a symbol: who imports it, and what could break silently."""
        from principal.graph.radius import find_target
        from principal.routines.relocation import importers_only_radius

        try:
            c = ctx()
            target = find_target(c.store, c.graph_id, fqn)
            out = importers_only_radius(c.store, c.graph_id, target, max_depth=1, cap=40).to_payload()
        except PrincipalError as exc:
            return f"Cannot brief `{fqn}`: {exc.message}"
        text = brief(fqn, f"move it to `{destination}` and update every import", f"Relocate `{fqn}`.", out)
        return text + (
            "\nA move changes who must import it, not who calls it. A string-keyed or dynamic import"
            " breaks at runtime and no static check finds it, so treat the unresolved list as the risk section."
            f" Principal's relocation routine is `principal run --routine relocation --target {fqn}`."
        )

    return mcp


def _latest_graph(store: Store) -> str | None:
    row = store.q1("SELECT id FROM graph ORDER BY rowid DESC LIMIT 1")
    return row["id"] if row else None


def main() -> None:  # pragma: no cover
    """Run standalone over stdio, for use from any MCP client."""
    from principal.config import get_settings

    settings = get_settings()
    build_server(Store(settings.principal_db), settings=settings).run()


if __name__ == "__main__":  # pragma: no cover
    main()
