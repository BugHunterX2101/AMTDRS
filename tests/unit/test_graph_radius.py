"""The blast radius, against the fixture that was built to prove it.

mini_repo is not arbitrary. It contains exactly one deprecated signature, nine
call sites across six files, one aliased import, one dynamic dispatch and one
uncovered private symbol — each present so that a specific claim about the
static analysis can be checked rather than asserted.
"""

from __future__ import annotations

import pytest

from principal.errors import PrincipalError
from principal.graph.radius import blast_radius, find_target
from tests.conftest import TARGET_FQN


async def test_target_is_found_by_fully_qualified_name(store, graph):
    target = find_target(store, graph.graph_id, TARGET_FQN)
    assert target.name == "create"
    assert target.path == "src/auth/session.py"
    assert "ttl" in (target.signature or "")


async def test_target_is_also_found_by_bare_name(store, graph):
    """An agent reading a diff sees `create`; an agent reading a plan sees the
    fully qualified name. Requiring the caller to know which it holds is a
    pointless source of failed lookups."""
    assert find_target(store, graph.graph_id, "create").path == "src/auth/session.py"


async def test_unknown_symbol_raises_rather_than_returning_empty(store, graph):
    with pytest.raises(PrincipalError):
        find_target(store, graph.graph_id, "src.auth.session.no_such_function")


async def test_radius_reaches_every_consumer(store, graph):
    target = find_target(store, graph.graph_id, TARGET_FQN)
    radius = blast_radius(store, graph.graph_id, target, max_depth=3, cap=40)

    expected = {
        "src/auth/session.py",   # the target itself
        "src/api/login.py",
        "src/api/refresh.py",
        "src/admin/tools.py",
        "src/workers/cleanup.py",
        "src/legacy/compat.py",  # reached only through an aliased import
        "src/registry.py",       # reached only as a file-level importer
    }
    assert expected <= radius.files


async def test_aliased_import_is_followed(store, graph):
    """`from src.auth.session import create as make_session` is invisible to a
    grep for `create(`. The import graph is the only thing that finds it, and a
    refactor that misses this file breaks production while the tests stay
    green."""
    target = find_target(store, graph.graph_id, TARGET_FQN)
    radius = blast_radius(store, graph.graph_id, target, max_depth=3, cap=40)
    assert "src/legacy/compat.py" in radius.files


async def test_dynamic_dispatch_file_enters_the_radius(store, graph):
    """src/registry.py never names `create` — it calls through a dict. Symbol
    resolution alone cannot see it; only the file-level import edge can. A
    refactor that skips this file is the exact failure this system exists to
    prevent."""
    target = find_target(store, graph.graph_id, TARGET_FQN)
    radius = blast_radius(store, graph.graph_id, target, max_depth=3, cap=40)
    assert "src/registry.py" in radius.files


async def test_unresolved_list_is_short_and_genuine(store, graph):
    """The value of this list is inversely proportional to its length. Naive
    reporting floods it with builtins until nobody reads it, and a risk list
    nobody reads is worse than none. The fixture contains exactly two calls that
    genuinely cannot be proved."""
    target = find_target(store, graph.graph_id, TARGET_FQN)
    radius = blast_radius(store, graph.graph_id, target, max_depth=3, cap=40)
    payload = radius.to_payload()

    assert len(payload["unresolved"]) == 2, payload["unresolved"]
    assert all(u["file"] == "src/registry.py" for u in payload["unresolved"])
    names = {u["name"] for u in payload["unresolved"]}
    assert not names & {"len", "print", "dict", "isinstance", "str"}


async def test_tests_are_identified(store, graph):
    target = find_target(store, graph.graph_id, TARGET_FQN)
    radius = blast_radius(store, graph.graph_id, target, max_depth=3, cap=40)
    assert {"tests/test_session.py", "tests/test_consumers.py"} <= set(radius.tests)


async def test_call_sites_carry_confidence(store, graph):
    target = find_target(store, graph.graph_id, TARGET_FQN)
    payload = blast_radius(store, graph.graph_id, target, max_depth=3, cap=40).to_payload()
    assert payload["call_sites"]
    assert {c["confidence"] for c in payload["call_sites"]} <= {"static", "heuristic"}


async def test_radius_raises_instead_of_truncating(store, graph):
    """The most dangerous thing this system could do is return a radius that
    looks complete and is not, because the resulting patch also looks complete
    and is not. Exceeding the cap is a hard stop."""
    target = find_target(store, graph.graph_id, TARGET_FQN)
    with pytest.raises(PrincipalError) as exc:
        blast_radius(store, graph.graph_id, target, max_depth=3, cap=2)
    assert exc.value.code.value == "RADIUS_TOO_LARGE"
