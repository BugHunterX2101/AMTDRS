"""The five code-graph tools.

These are reachable from outside the orchestrator, so the property that matters
most is that none of them can write, run or read outside the snapshot.
"""

from __future__ import annotations

import pytest

from mcp_code_graph import tools as T
from principal.errors import PrincipalError
from tests.conftest import TARGET_FQN


@pytest.fixture
def ctx(store, graph):
    return T.GraphContext(store=store, graph_id=graph.graph_id)


async def test_find_symbol_by_fqn_and_by_bare_name(ctx):
    for query in (TARGET_FQN, "create"):
        result = T.find_symbol(ctx, query)
        assert result["count"] >= 1
        assert any(s["path"] == "src/auth/session.py" for s in result["symbols"])


async def test_find_symbol_reports_absence_rather_than_raising(ctx):
    """A lookup that finds nothing is an ordinary answer for an agent to act on,
    not an exception to handle."""
    assert T.find_symbol(ctx, "definitely_not_a_symbol")["count"] == 0


async def test_callers_of_returns_sites_with_confidence(ctx):
    result = T.callers_of(ctx, TARGET_FQN)
    assert result["found"] and result["count"] > 0
    assert {c["confidence"] for c in result["callers"]} <= {"static", "heuristic"}
    assert all(c["line"] > 0 for c in result["callers"])


async def test_blast_radius_matches_the_orchestrator(ctx):
    """The MCP surface and the orchestrator must answer the same question the
    same way. Two implementations of the radius would be two chances to be
    wrong about what a patch is allowed to touch."""
    from principal.graph.radius import blast_radius as direct
    from principal.graph.radius import find_target

    via_mcp = T.blast_radius(ctx, TARGET_FQN)
    target = find_target(ctx.store, ctx.graph_id, TARGET_FQN)
    via_core = direct(ctx.store, ctx.graph_id, target, max_depth=3, cap=40).to_payload()
    assert sorted(via_mcp["files"]) == sorted(via_core["files"])


async def test_blast_radius_error_is_translated_not_leaked(ctx):
    """PrincipalError carries a code that means something inside the
    orchestrator and nothing across an MCP boundary."""
    with pytest.raises(PrincipalError):
        T.blast_radius(ctx, "no.such.symbol")


async def test_read_span_is_bounded(ctx):
    result = T.read_span(ctx, "src/auth/session.py", 1, 10_000)
    assert result["found"]
    assert len(result["text"].splitlines()) <= T.MAX_SPAN_LINES


@pytest.mark.parametrize(
    "path", ["../../../etc/passwd", "../../principal/config.py", "src/../../outside.py"]
)
async def test_read_span_refuses_to_escape_the_snapshot(ctx, path):
    """`path` reaches this function from model output. Without the resolved
    prefix check it would read anything this process can."""
    with pytest.raises(PrincipalError):
        T.read_span(ctx, path, 1, 5)


async def test_read_span_on_a_missing_file_is_an_answer_not_a_crash(ctx):
    assert T.read_span(ctx, "src/does_not_exist.py", 1, 5)["found"] is False


async def test_every_tool_is_read_only(ctx, snapshot):
    """The safety property that makes exposing this over MCP acceptable at all.
    Run the whole toolset and assert the tree is byte-identical afterwards."""
    before = {p: p.read_bytes() for p in snapshot.rglob("*.py")}

    T.find_symbol(ctx, "create")
    T.callers_of(ctx, TARGET_FQN)
    T.blast_radius(ctx, TARGET_FQN)
    T.tests_covering(ctx, TARGET_FQN)
    T.read_span(ctx, "src/auth/session.py", 1, 20)

    after = {p: p.read_bytes() for p in snapshot.rglob("*.py")}
    assert before == after


async def test_server_exposes_exactly_the_five_tools(store, graph):
    from mcp_code_graph.server import build_server

    server = build_server(store, graph.graph_id)
    names = {t.name for t in await server.list_tools()}
    assert names == {"find_symbol", "callers_of", "blast_radius", "tests_covering", "read_span"}
