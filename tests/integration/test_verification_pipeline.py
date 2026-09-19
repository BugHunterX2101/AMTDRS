"""The verification pipeline, end to end, against the bundled fixture.

This runs real `git` and real `pytest` in the in-process sandbox. It is the test
that would catch the system quietly accepting a broken patch — which is the one
failure that would make every claim in the README false at once.

No credentials, no network, no cost.
"""

from __future__ import annotations

import pytest

from principal.graph.build import ingest_coverage
from principal.sandbox.baseline import build_baseline
from principal.sandbox.runner import run_attempt
from tests.conftest import FIXTURE_SHA, MINI_REPO

# A correct change: `ttl` becomes keyword-only at the definition. Every caller in
# the fixture passes it positionally, so this alone MUST break the suite.
BREAKING_DIFF = """--- a/src/auth/session.py
+++ b/src/auth/session.py
@@ -14,7 +14,7 @@ class Session:
         return age >= self.ttl
 
 
-def create(user, ttl=DEFAULT_TTL):
+def create(user, *, ttl=DEFAULT_TTL):
     \"\"\"Positional-ttl signature. Every consumer passes ttl positionally, which is
     the reason this migration exists and the reason it touches so many files.\"\"\"
     if not user:
"""

# A change that alters nothing observable: a comment. The suite must stay green.
HARMLESS_DIFF = """--- a/src/auth/session.py
+++ b/src/auth/session.py
@@ -14,6 +14,7 @@ class Session:
         return age >= self.ttl
 
 
+# touched by the verification pipeline test
 def create(user, ttl=DEFAULT_TTL):
     \"\"\"Positional-ttl signature. Every consumer passes ttl positionally, which is
     the reason this migration exists and the reason it touches so many files.\"\"\"
"""


@pytest.fixture
async def baseline(sandbox, snapshot):
    return await build_baseline(
        sandbox, base_image="python:3.11-slim", repo_url=str(snapshot),
        commit_sha=FIXTURE_SHA, timeout_s=300,
    )


async def test_baseline_is_green_and_produces_an_image(baseline):
    assert baseline.image
    assert baseline.failed == 0
    assert baseline.passed == 14, "the fixture ships exactly 14 passing tests"
    assert baseline.collected == 14


async def test_baseline_captures_per_test_coverage(baseline):
    """`--cov-context=test` is what makes per-fork test selection possible. If
    this map is empty every fork runs the whole suite and gate 3 stops being
    cheap — the performance argument is downstream of this one artifact."""
    import json

    data = json.loads(baseline.coverage_json)
    assert data.get("files"), "coverage.json has no files — contexts were not recorded"
    contexts = [
        ctx
        for entry in data["files"].values()
        for ctxs in (entry.get("contexts") or {}).values()
        for ctx in ctxs
        if ctx
    ]
    assert contexts, "no per-test contexts recorded"


async def test_coverage_ingests_into_test_edges(store, graph, baseline):
    """The measured test→symbol map. An import-derived guess misses tests that
    reach a symbol through three layers of indirection; this does not."""
    inserted = ingest_coverage(store, graph.graph_id, baseline.coverage_json)
    assert inserted > 0

    rows = store.q(
        "SELECT * FROM test_edge WHERE graph_id = ? AND source = 'coverage'", (graph.graph_id,)
    )
    assert rows
    assert all(r["nodeid"] for r in rows)


async def test_a_breaking_patch_is_rejected(sandbox, baseline):
    """The single most important assertion in this repository. A patch that
    breaks callers must go red. If this ever passes green, the guarantee is
    gone and everything the project claims is false."""
    c0 = await sandbox.from_uuid(baseline.image)
    run = await run_attempt(sandbox, c0, BREAKING_DIFF, [], timeout_s=180)

    assert not run.apply_failed, "the diff should apply cleanly; it is the tests that must fail"
    assert run.exit_code != 0
    assert run.failed > 0
    assert run.failing, "a red run must name which tests failed, or a repair has nothing to work from"


async def test_a_harmless_patch_stays_green(sandbox, baseline):
    """The complement of the test above. A gate that rejects everything is
    trivially safe and completely useless."""
    c0 = await sandbox.from_uuid(baseline.image)
    run = await run_attempt(sandbox, c0, HARMLESS_DIFF, [], timeout_s=180)

    assert not run.apply_failed
    assert run.exit_code == 0
    assert run.failed == 0
    assert run.passed == 14


async def test_an_unapplyable_patch_is_reported_as_such_not_as_a_test_failure(sandbox, baseline):
    """These are different failures and must not be conflated. A diff that does
    not apply is the coder's mistake and is repairable; a red suite is a
    statement about the code. Merging them makes the error taxonomy lie."""
    c0 = await sandbox.from_uuid(baseline.image)
    # Corrupt a context line so the hunk no longer matches the tree. This is
    # what a stale diff from a model that hallucinated its surroundings looks
    # like, and it is the single most common malformed output in practice.
    stale = BREAKING_DIFF.replace("        return age >= self.ttl", "        return NOT_A_REAL_LINE")
    assert stale != BREAKING_DIFF, "the corruption did not take; this test would prove nothing"
    run = await run_attempt(sandbox, c0, stale, [], timeout_s=180)
    assert run.apply_failed


async def test_attempts_do_not_contaminate_the_baseline(sandbox, baseline):
    """Failure is free because it is private. After a patch has broken its own
    fork, the baseline image must still be green — otherwise racing candidates
    would poison each other and the whole design collapses."""
    c0 = await sandbox.from_uuid(baseline.image)
    broken = await run_attempt(sandbox, c0, BREAKING_DIFF, [], timeout_s=180)
    assert broken.exit_code != 0

    again = await run_attempt(sandbox, c0, HARMLESS_DIFF, [], timeout_s=180)
    assert again.exit_code == 0, "the second fork saw the first fork's damage"


async def test_baseline_is_deduplicated_across_calls(sandbox, snapshot):
    """Content addressing means re-running the baseline command costs nothing.
    Crash recovery is built on this: restart and simply ask again."""
    kwargs = dict(base_image="python:3.11-slim", repo_url=str(snapshot),
                  commit_sha=FIXTURE_SHA, timeout_s=300)
    first = await build_baseline(sandbox, **kwargs)
    second = await build_baseline(sandbox, **kwargs)
    assert first.image == second.image


async def test_repo_url_is_recorded_on_the_fixture(snapshot):
    assert (snapshot / ".git").exists(), "gates and publishing both need a real repo"
    assert (MINI_REPO / "src" / "auth" / "session.py").exists()
