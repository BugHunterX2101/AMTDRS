"""Call-site recall, measured against a ground truth hand-labelled from the
fixture's actual source — not asserted from the architecture.

Every line number below was found by reading `tests/fixtures/mini_repo` and
grepping for every real invocation of `src.auth.session.create`, independent of
whatever the graph itself reports. If this file and `principal/graph/radius.py`
ever disagree, it is because one of them is wrong, which is exactly the point of
keeping the labelling separate from the code under test.

The ground truth: 8 direct static calls, 1 static call reached only through a
renamed import (`make_session`, in `src/legacy/compat.py`), and one dynamic
dispatch through `src/registry.py`'s string-keyed handler table.
"""

from __future__ import annotations

from principal.graph.radius import blast_radius, find_target
from principal.graph.recall import GroundTruthSite, measure_recall
from tests.conftest import TARGET_FQN

GROUND_TRUTH = [
    GroundTruthSite("src/admin/tools.py", 7, static=True),
    GroundTruthSite("src/api/login.py", 5, static=True),
    GroundTruthSite("src/api/login.py", 9, static=True),
    GroundTruthSite("src/api/login.py", 13, static=True),
    GroundTruthSite("src/api/refresh.py", 5, static=True),
    GroundTruthSite("src/api/refresh.py", 9, static=True),
    GroundTruthSite("src/api/refresh.py", 10, static=True),
    GroundTruthSite("src/workers/cleanup.py", 5, static=True),
    GroundTruthSite(
        "src/legacy/compat.py", 7, static=True,
        note="reached only through `from src.auth.session import create as make_session`",
    ),
    GroundTruthSite(
        "src/registry.py", 15, static=False,
        note="dispatched through HANDLERS[name] and getattr; no static edge proves this reaches create",
    ),
]


async def test_ground_truth_matches_a_manual_grep_of_the_fixture():
    """A sanity check on the label set itself, not on the graph: if the fixture
    ever changes, this is the assertion that should fail first, before the
    recall numbers below become meaningless."""
    import re

    from tests.conftest import MINI_REPO

    hits = 0
    for path in (MINI_REPO / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        hits += len(re.findall(r"\bcreate\(", text)) - text.count("def create(")
    # 8 sites literally spelled `create(`. compat.py calls it as `make_session(`
    # and registry.py through `fn(*args)`, so neither is counted by this grep —
    # which is exactly why the graph, not a grep, is the product.
    assert hits == 8


async def test_static_recall_is_complete_including_the_aliased_call(store, graph):
    """The whole point of resolving imports rather than grepping text: an
    aliased call is still a real consumer, and missing it would mean shipping a
    signature change that silently breaks `src/legacy/compat.py`."""
    target = find_target(store, graph.graph_id, TARGET_FQN)
    radius = blast_radius(store, graph.graph_id, target, max_depth=3, cap=40)
    result = measure_recall(radius, GROUND_TRUTH)

    assert result.static_recall == 1.0, result.missed


async def test_the_dynamic_site_is_flagged_not_silently_dropped(store, graph):
    """This is the one no static analysis resolves with certainty. The honest
    claim is not "found it" — it is "did not lose track of it"."""
    target = find_target(store, graph.graph_id, TARGET_FQN)
    radius = blast_radius(store, graph.graph_id, target, max_depth=3, cap=40)
    result = measure_recall(radius, GROUND_TRUTH)

    assert result.heuristic_recall == 1.0, result.missed


async def test_overall_recall_on_this_fixture(store, graph):
    """Published as a number, not a claim of completeness. 10 of 10 on this
    fixture; a real repository's number belongs next to this one, not in place
    of it, and is expected to be lower wherever dynamic dispatch is more common
    than one registry."""
    target = find_target(store, graph.graph_id, TARGET_FQN)
    radius = blast_radius(store, graph.graph_id, target, max_depth=3, cap=40)
    result = measure_recall(radius, GROUND_TRUTH)

    assert result.recall == 1.0
    assert result.total == 10
    assert result.missed == []
