"""`_characterisation_only_files` against a real graph, not a mock.

`_uncovered_files` and this function both start from `tests_covering`, and the
only thing distinguishing them is whether every covering edge's `source` is
`'characterisation'`. That is easy to get backwards (e.g. testing "any edge is
characterisation" instead of "every edge is"), which would either hide a
legitimate real-coverage file behind a false risk warning or, worse, let a
file covered by nothing but a generated test slip through as ordinarily safe
— exactly the failure mode this function exists to prevent.
"""

from __future__ import annotations

from pathlib import Path

from principal.db.store import Store
from principal.orchestrator.job import _characterisation_only_files, _uncovered_files
from tests.conftest import FIXTURE_SHA, MINI_REPO


def _symbol_id(store: Store, graph_id: str, fqn: str) -> int:
    row = store.q1("SELECT id FROM symbol WHERE graph_id = ? AND fqn = ?", (graph_id, fqn))
    assert row is not None, f"fixture symbol not found: {fqn}"
    return int(row["id"])


def _file_id(store: Store, graph_id: str, path: str) -> int:
    row = store.q1("SELECT id FROM file WHERE graph_id = ? AND path = ?", (graph_id, path))
    assert row is not None, f"fixture file not found: {path}"
    return int(row["id"])


def _add_test_edge(store: Store, graph_id: str, test_file: str, symbol_fqn: str, source: str) -> None:
    tfid = _file_id(store, graph_id, test_file)
    sid = _symbol_id(store, graph_id, symbol_fqn)
    store.exec(
        "INSERT INTO test_edge (graph_id, test_file_id, symbol_id, nodeid, source)"
        " VALUES (?,?,?,?,?)",
        (graph_id, tfid, sid, f"{test_file}::test_x", source),
    )


async def test_a_file_covered_only_by_characterisation_edges_is_reported(store: Store, snapshot: Path):
    from principal.graph.build import build

    stats = build(store, snapshot, str(MINI_REPO), FIXTURE_SHA)
    graph_id = stats.graph_id

    # internal._normalise_ttl is a private helper no fixture test calls
    # directly — give it a lone characterisation edge, the exact scenario a
    # NoSafetyNet-avoiding job produces.
    _add_test_edge(store, graph_id, "tests/test_session.py", "src.auth.internal._normalise_ttl",
                    source="characterisation")

    only = _characterisation_only_files(store, graph_id, {"src/auth/internal.py"})
    assert only == ["src/auth/internal.py"]

    # And it must not also appear as flatly "uncovered" — the two categories
    # are mutually exclusive claims about the same file.
    uncovered = _uncovered_files(store, graph_id, {"src/auth/internal.py"})
    assert "src/auth/internal.py" not in uncovered


async def test_a_file_with_any_real_covering_edge_is_not_characterisation_only(store: Store, snapshot: Path):
    from principal.graph.build import build

    stats = build(store, snapshot, str(MINI_REPO), FIXTURE_SHA)
    graph_id = stats.graph_id

    # session.create already has real import-derived coverage from the
    # fixture's own test suite. Adding one characterisation edge alongside it
    # must not flip the file into "characterisation only" — one real edge is
    # enough to make the claim false.
    _add_test_edge(store, graph_id, "tests/test_session.py", "src.auth.session.create",
                    source="characterisation")

    only = _characterisation_only_files(store, graph_id, {"src/auth/session.py"})
    assert only == []


async def test_a_file_with_no_coverage_at_all_is_not_characterisation_only(store: Store, snapshot: Path):
    from principal.graph.build import build

    stats = build(store, snapshot, str(MINI_REPO), FIXTURE_SHA)
    graph_id = stats.graph_id

    only = _characterisation_only_files(store, graph_id, {"src/auth/internal.py"})
    assert only == []  # it's "uncovered", a different claim, not this one

    uncovered = _uncovered_files(store, graph_id, {"src/auth/internal.py"})
    assert uncovered == ["src/auth/internal.py"]
