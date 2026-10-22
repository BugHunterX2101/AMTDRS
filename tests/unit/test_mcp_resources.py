"""The MCP resources and prompts.

They are the same read-only queries as the tools, offered in the shapes a host
can attach to a conversation (resources) or offer as a starting point (prompts).
What these tests protect is that offering them did not widen what the server can
do: still no writes, still the same answers as the tools, still bounded.
"""

from __future__ import annotations

import json

import pytest
from fastmcp import Client

from mcp_code_graph import tools as T
from mcp_code_graph.server import build_server
from tests.conftest import TARGET_FQN


@pytest.fixture
def server(store, graph):
    return build_server(store, graph.graph_id)


async def _read(client: Client, uri: str) -> dict:
    contents = await client.read_resource(uri)
    return json.loads(contents[0].text)


async def test_resources_and_templates_are_advertised(server):
    async with Client(server) as c:
        static = {str(r.uri) for r in await c.list_resources()}
        templates = {t.uriTemplate for t in await c.list_resource_templates()}
    assert "amtdrs://graph/tree" in static
    assert {"amtdrs://graph/tree/{path*}", "amtdrs://graph/entities/{fqn}"} <= templates
    assert any(t.startswith("amtdrs://graph/blast-radius/{fqn}") for t in templates)


async def test_tree_walks_from_root_to_a_files_symbols(server):
    async with Client(server) as c:
        root = await _read(c, "amtdrs://graph/tree")
        assert root["type"] == "dir"
        assert "src/" in {e["name"] for e in root["entries"]}

        leaf = await _read(c, "amtdrs://graph/tree/src/auth/session.py")
    assert leaf["type"] == "file"
    create = next(e for e in leaf["entries"] if e["name"] == TARGET_FQN)
    assert create["kind"] in {"function", "method"}
    # A file listing is signatures only. Bodies come from `entity`, on request.
    assert all("source" not in e for e in leaf["entries"])


async def test_tree_reports_a_missing_path_instead_of_inventing_one(server):
    async with Client(server) as c:
        out = await _read(c, "amtdrs://graph/tree/src/no_such_dir")
    assert out["type"] == "missing" and out["entries"] == []


async def test_tree_listing_says_when_it_was_cut_off(store, graph, monkeypatch):
    ctx = T.GraphContext(store=store, graph_id=graph.graph_id)
    many = [{"path": f"f{i:03d}.py"} for i in range(T.TREE_MAX_ENTRIES + 15)]
    monkeypatch.setattr(store, "files_of_graph", lambda _gid: many)
    out = T.graph_tree(ctx, "")
    assert out["total"] > T.TREE_MAX_ENTRIES
    assert out["truncated"] is True and len(out["entries"]) == T.TREE_MAX_ENTRIES


async def test_entity_resource_carries_the_definition(server):
    async with Client(server) as c:
        out = await _read(c, f"amtdrs://graph/entities/{TARGET_FQN}")
    assert out["count"] >= 1
    assert "def create" in out["symbols"][0]["source"]


async def test_blast_radius_resource_matches_the_tool(server, store, graph):
    ctx = T.GraphContext(store=store, graph_id=graph.graph_id)
    async with Client(server) as c:
        via_resource = await _read(c, f"amtdrs://graph/blast-radius/{TARGET_FQN}")
        shallow = await _read(c, f"amtdrs://graph/blast-radius/{TARGET_FQN}?depth=1")
    assert sorted(via_resource["files"]) == sorted(T.blast_radius(ctx, TARGET_FQN)["files"])
    assert shallow["depth_reached"] <= 1


async def test_a_resource_error_is_an_answer_not_a_crash(server):
    async with Client(server) as c:
        out = await _read(c, "amtdrs://graph/blast-radius/no.such.symbol")
    assert out["error"] and out["message"]


async def test_prompts_are_listed_and_prefilled_with_the_real_radius(server):
    async with Client(server) as c:
        assert {p.name for p in await c.list_prompts()} == {"plan_change", "relocate_symbol"}
        plan = await c.get_prompt("plan_change", {"fqn": TARGET_FQN, "goal": "make ttl keyword-only"})
        move = await c.get_prompt("relocate_symbol", {"fqn": TARGET_FQN, "destination": "src.auth.tokens"})
    plan_text = plan.messages[0].content.text
    move_text = move.messages[0].content.text
    assert "src/auth/session.py" in plan_text and "make ttl keyword-only" in plan_text
    assert "principal run" in plan_text
    assert "src/auth/session.py" in move_text and "src.auth.tokens" in move_text
    assert "--routine relocation" in move_text


async def test_a_prompt_for_an_unknown_symbol_says_so(server):
    async with Client(server) as c:
        got = await c.get_prompt("plan_change", {"fqn": "no.such.symbol", "goal": "x"})
    assert got.messages[0].content.text.startswith("Cannot brief")


async def test_the_tool_list_is_still_exactly_five(server):
    """Resources and prompts were added around the tools, not instead of them, and
    not as a sixth tool: the read-only guarantee is stated per tool."""
    async with Client(server) as c:
        names = {t.name for t in await c.list_tools()}
    assert names == {"find_symbol", "callers_of", "blast_radius", "tests_covering", "read_span"}


async def test_reading_every_resource_leaves_the_snapshot_untouched(server, snapshot):
    before = {p: p.read_bytes() for p in snapshot.rglob("*.py")}
    async with Client(server) as c:
        await _read(c, "amtdrs://graph/tree")
        await _read(c, "amtdrs://graph/tree/src")
        await _read(c, f"amtdrs://graph/entities/{TARGET_FQN}")
        await _read(c, f"amtdrs://graph/blast-radius/{TARGET_FQN}")
        await c.get_prompt("plan_change", {"fqn": TARGET_FQN, "goal": "x"})
    assert before == {p: p.read_bytes() for p in snapshot.rglob("*.py")}


async def test_the_evidence_resource_exists_only_when_settings_are_given(store, graph, settings):
    async with Client(build_server(store, graph.graph_id)) as c:
        assert not any("evidence" in t.uriTemplate for t in await c.list_resource_templates())

    async with Client(build_server(store, graph.graph_id, settings)) as c:
        assert any("evidence" in t.uriTemplate for t in await c.list_resource_templates())
        out = await _read(c, "amtdrs://jobs/nonexistent/evidence")
    assert out["error"] == "no such job"
