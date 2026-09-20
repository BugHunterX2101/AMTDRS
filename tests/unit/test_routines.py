"""The routine registry, and the relocation seed specifically.

`routine_for` is the one place a job's stored routine name turns into behaviour,
and `importers_only_radius` is the one place a seed narrower than the full
consumer closure is deliberate rather than a bug. Both are load-bearing: get the
registry's fallback wrong and an unknown routine silently runs as something it
did not ask to be; get the seed wrong and either relocation drags in files that
need no edit, or it misses one that does.
"""

from __future__ import annotations

import pytest

from principal.errors import RadiusTooLarge
from principal.graph.radius import blast_radius, find_target
from principal.routines.registry import RefactorKind, default_routine, routine_for
from principal.routines.relocation import importers_only_radius, moved_symbols
from tests.conftest import TARGET_FQN


def test_default_routine_is_interface_evolution():
    assert default_routine().kind is RefactorKind.INTERFACE_EVOLUTION


def test_routine_for_none_falls_back_to_default():
    assert routine_for(None) is default_routine()


def test_routine_for_unknown_name_falls_back_rather_than_raising():
    """A job that asked for a routine this registry does not implement should
    run as the generic case and say so, not abort the job over a naming
    mismatch."""
    assert routine_for("extraction").kind is RefactorKind.INTERFACE_EVOLUTION
    assert routine_for("not_a_real_routine").kind is RefactorKind.INTERFACE_EVOLUTION


def test_routine_for_relocation_resolves_to_the_relocation_routine():
    routine = routine_for("relocation")
    assert routine.kind is RefactorKind.RELOCATION
    assert routine.allows_file_moves is True


def test_every_registered_routine_forbids_new_files():
    """Extraction is the routine that will eventually need this; until it
    exists, nothing registered should be able to create a file gate 1 has never
    seen in the blast radius."""
    from principal.routines.registry import ROUTINES

    assert all(not r.allows_new_files for r in ROUTINES.values())


def test_relocation_prompt_is_distinct_from_the_default_prompt():
    assert routine_for("relocation").prompt_text != default_routine().prompt_text
    assert "moving" in routine_for("relocation").prompt_text.lower()


# --------------------------------------------------------- importers_only --


async def test_relocation_seed_pulls_in_fewer_tests_than_the_full_closure(store, graph):
    """`test_consumers.py::test_dispatch_reaches_create` covers the dynamic
    dispatch path, not the symbol itself, so the coverage-derived test_edge for
    it does not name `create` directly. The full consumer closure pulls that
    test file in because a *consumer* is covered by it; a move does not need
    it, because the symbol's own behaviour has not changed."""
    target = find_target(store, graph.graph_id, TARGET_FQN)
    full = blast_radius(store, graph.graph_id, target, max_depth=3, cap=40)
    reloc = importers_only_radius(store, graph.graph_id, target, max_depth=3, cap=40)

    assert set(reloc.tests) <= set(full.tests)
    assert "tests/test_session.py" in reloc.tests


async def test_relocation_seed_still_reaches_every_importer(store, graph):
    """A move breaks exactly the files that import the moved symbol or its
    module — never fewer than that, whatever the seed's other savings are."""
    target = find_target(store, graph.graph_id, TARGET_FQN)
    reloc = importers_only_radius(store, graph.graph_id, target, max_depth=3, cap=40)

    expected = {
        "src/auth/session.py", "src/api/login.py", "src/api/refresh.py",
        "src/admin/tools.py", "src/workers/cleanup.py", "src/legacy/compat.py",
        "src/registry.py",
    }
    assert expected <= reloc.files


async def test_relocation_seed_still_flags_dynamic_dispatch_as_a_risk(store, graph):
    """Worse for relocation than for anything else: a string-keyed import breaks
    silently at runtime when the module moves, and nothing static can find it."""
    target = find_target(store, graph.graph_id, TARGET_FQN)
    reloc = importers_only_radius(store, graph.graph_id, target, max_depth=3, cap=40)
    assert reloc.unresolved
    assert all("reference the moved module" in u["reason"] for u in reloc.unresolved)


async def test_relocation_seed_raises_rather_than_truncating_over_cap(store, graph):
    target = find_target(store, graph.graph_id, TARGET_FQN)
    with pytest.raises(RadiusTooLarge):
        importers_only_radius(store, graph.graph_id, target, max_depth=3, cap=1)


async def test_relocation_seed_ignores_max_depth(store, graph):
    """Import dependence is not transitive for a move: only the file that
    directly imports the moved module has a broken import statement."""
    target = find_target(store, graph.graph_id, TARGET_FQN)
    shallow = importers_only_radius(store, graph.graph_id, target, max_depth=1, cap=40)
    deep = importers_only_radius(store, graph.graph_id, target, max_depth=99, cap=40)
    assert shallow.files == deep.files


# ------------------------------------------------------------ moved_symbols --


def test_moved_symbols_finds_a_declared_removal_that_reappeared_elsewhere():
    moved = moved_symbols(["create"], {"src/new_home.py": {"create"}})
    assert moved == {"create"}


def test_moved_symbols_ignores_a_declared_removal_that_vanished_entirely():
    """A declared removal that never reappears is not this helper's problem to
    flag — it is either a genuine deletion or a destination that does not
    export the symbol, and both are legitimate."""
    moved = moved_symbols(["create"], {"src/new_home.py": {"something_else"}})
    assert moved == set()
