"""What the planner is told about each call site.

The graph resolves `from src.auth.session import create as make_session` to the
target, and mini_repo keeps that file so the claim can be checked. A live run
showed the cost of printing only the written name: the planner read
``src/legacy/compat.py:7 calls `make_session` `` as unrelated, listed it as
"unclear if it wraps `create`" and left it out, so making `ttl` keyword-only broke
`test_legacy_login` and the task for the target file could never pass.
"""

from __future__ import annotations

from principal.agents import planner
from principal.graph.radius import blast_radius, find_target
from tests.conftest import TARGET_FQN


def _radius(store, graph):
    target = find_target(store, graph.graph_id, TARGET_FQN)
    return blast_radius(store, graph.graph_id, target, max_depth=3)


def _prompt(store, graph) -> str:
    return planner._render("migrate it", TARGET_FQN, _radius(store, graph), 3, planner.PROMPT)


async def test_the_aliased_call_site_is_marked_as_the_target(store, graph):
    line = next(ln for ln in _prompt(store, graph).splitlines() if "src/legacy/compat.py:" in ln)
    assert "`make_session`" in line
    assert f"the target `{TARGET_FQN}` imported under another name" in line


async def test_a_call_written_with_the_targets_own_name_is_left_unannotated(store, graph):
    lines = [ln for ln in _prompt(store, graph).splitlines() if "src/admin/tools.py:" in ln]
    assert lines and all("imported under another name" not in ln for ln in lines)


async def test_only_calls_that_reach_the_target_are_marked(store, graph):
    """Callers of a caller (`login` for `create`) are in the radius but are not the target."""
    marked = {c["symbol"] for c in _radius(store, graph).call_sites if c["to_target"]}
    assert {"create", "make_session"} <= marked
    assert not {"login", "refresh", "legacy_login"} & marked
