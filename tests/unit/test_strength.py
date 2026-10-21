"""The verification-strength audit: planning, accounting, and honesty.

The accounting matters more than it looks. A number that quietly counts a run that
never happened as 'all faults detected' would turn the one section of the
evidence report that exists to expose weak verification into decoration.
"""

from __future__ import annotations

from principal.errors import SandboxTimeout
from principal.gates.strength import (
    DISCLAIMER,
    StrengthResult,
    plan_mutants,
    run_strength_audit,
)
from principal.sandbox.client import RunResult

BEFORE = "def f(x):\n    if x > 10:\n        return x\n    return 0\n"
AFTER = "def f(x):\n    if x > 10:\n        return x + 1\n    return 0\n"


def _blob(results: dict[str, int]) -> str:
    body = "\n".join(f"{k} {v}" for k, v in results.items())
    return f"noise\n__PRINCIPAL_MUTATION__\n{body}\n__PRINCIPAL_END__\n"


class _Sandbox:
    def __init__(self, stdout="", exc=None, exit_code=0):
        self.stdout, self.exc, self.exit_code = stdout, exc, exit_code
        self.script = None

    async def run(self, image, script, **kwargs):
        self.script = script
        if self.exc:
            raise self.exc
        return RunResult(image=None, exit_code=self.exit_code, stdout=self.stdout, stderr="",
                         duration_ms=1)


# -------------------------------------------------------------------- planning ----


def test_mutants_are_confined_to_the_changed_lines():
    planned = plan_mutants({"m.py": BEFORE}, {"m.py": AFTER}, {"m.py"})

    assert planned
    assert {p.mutant.line for p in planned} == {3}, "only `return x + 1` changed"
    assert all(p.path == "m.py" for p in planned)


def test_an_unchanged_file_yields_no_mutants():
    assert plan_mutants({"m.py": BEFORE}, {"m.py": BEFORE}, {"m.py"}) == []


def test_non_python_files_are_ignored():
    assert plan_mutants({"a.ts": "x"}, {"a.ts": "y"}, {"a.ts"}) == []


def test_the_cap_is_shared_across_files_before_it_is_spent():
    body = "\n".join(f"def f{i}(x):\n    return x + {i + 1}\n" for i in range(12))
    changed = body.replace("x +", "x -")
    base = {"a.py": body, "b.py": body}
    new = {"a.py": changed, "b.py": changed}

    planned = plan_mutants(base, new, {"a.py", "b.py"}, cap=6)

    per_file = {p: sum(1 for m in planned if m.path == p) for p in ("a.py", "b.py")}
    assert len(planned) <= 6
    assert per_file["a.py"] > 0 and per_file["b.py"] > 0, "one file used the whole budget"


def test_mutant_ids_are_unique_across_files():
    planned = plan_mutants({"a.py": BEFORE, "b.py": BEFORE}, {"a.py": AFTER, "b.py": AFTER}, {"a.py", "b.py"})
    ids = [p.mutant_id for p in planned]
    assert len(ids) == len(set(ids))


# ------------------------------------------------------------------ accounting ----


async def test_detected_and_surviving_are_both_reported():
    planned = plan_mutants({"m.py": BEFORE}, {"m.py": AFTER}, {"m.py"})
    ids = [p.mutant_id for p in planned]
    results = {ids[0]: 1, **{i: 0 for i in ids[1:]}}   # first killed, the rest survive

    result = await run_strength_audit(_Sandbox(_blob(results)), object(), planned, [])

    assert result.status == "measured"
    assert result.killed == 1
    assert result.survived == len(ids) - 1
    assert len(result.survivors) == len(ids) - 1
    assert result.survivors[0]["file"] == "m.py" and result.survivors[0]["line"] == 3
    assert result.score == 1 / len(ids)


async def test_a_score_under_the_floor_is_flagged_and_a_score_over_it_is_not():
    planned = plan_mutants({"m.py": BEFORE}, {"m.py": AFTER}, {"m.py"})
    ids = [p.mutant_id for p in planned]

    weak = await run_strength_audit(_Sandbox(_blob(dict.fromkeys(ids, 0))), object(), planned, [])
    strong = await run_strength_audit(_Sandbox(_blob(dict.fromkeys(ids, 1))), object(), planned, [])

    assert weak.below_floor and not strong.below_floor
    assert strong.score == 1.0


async def test_incompetent_mutants_do_not_count_either_way():
    planned = plan_mutants({"m.py": BEFORE}, {"m.py": AFTER}, {"m.py"})
    ids = [p.mutant_id for p in planned]

    result = await run_strength_audit(
        _Sandbox(_blob({ids[0]: 1, **{i: 2 for i in ids[1:]}})), object(), planned, [],
    )

    assert result.score == 1.0
    assert result.incompetent == len(ids) - 1


async def test_nothing_to_mutate_is_reported_as_unmeasured_not_as_perfect():
    result = await run_strength_audit(_Sandbox(), object(), [], [])

    assert result.status == "no_mutants"
    assert result.score is None
    assert not result.below_floor, "unmeasured is not the same statement as weak"
    assert result.reason


async def test_a_sandbox_failure_is_unavailable_and_never_raises():
    planned = plan_mutants({"m.py": BEFORE}, {"m.py": AFTER}, {"m.py"})

    result = await run_strength_audit(_Sandbox(exc=SandboxTimeout(30)), object(), planned, [])

    assert result.status == "unavailable"
    assert result.score is None
    assert result.killed == 0 and result.survived == 0


async def test_a_run_with_no_results_is_inconclusive_not_clean():
    planned = plan_mutants({"m.py": BEFORE}, {"m.py": AFTER}, {"m.py"})

    result = await run_strength_audit(_Sandbox("no sentinel", exit_code=137), object(), planned, [])

    assert result.status == "unavailable"
    assert "inconclusive" in result.reason


def test_the_payload_carries_the_disclaimer_and_the_raw_counts():
    payload = StrengthResult(status="measured", killed=3, survived=1, generated=4).to_payload()

    assert payload["detected"] == 3 and payload["survived"] == 1 and payload["score"] == 0.75
    assert payload["disclaimer"] == DISCLAIMER
    assert "not a proof" in DISCLAIMER


async def test_the_script_restores_every_file_and_clears_bytecode():
    planned = plan_mutants({"m.py": BEFORE}, {"m.py": AFTER}, {"m.py"})
    sandbox = _Sandbox(_blob({p.mutant_id: 1 for p in planned}))

    await run_strength_audit(sandbox, object(), planned, ["tests/test_m.py::test_f"])

    script = sandbox.script
    assert "PYTHONDONTWRITEBYTECODE=1" in script
    assert script.count("__pycache__") >= 2 * len(planned), "bytecode is cleared around every mutant"
    assert "tests/test_m.py::test_f" in script
    assert script.rstrip().endswith("exit 0")
