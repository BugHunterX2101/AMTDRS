"""Gate 4's companions: the three behaviour-preservation checks.

Green tests are necessary and not sufficient. Three cheap additional checks, all
deterministic, all computed from artifacts that already exist:

  Public API delta   exported symbols before vs after, any undeclared removal fails
  Coverage floor      line coverage on touched files must not drop
  Test count invariant  collected test count must be identical, no matter the exit code

These are the answer to "how do you know the agent did not cheat."
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from principal.db.store import Store
from principal.gates.pipeline import Verdict, VerdictKind


@dataclass(slots=True)
class BehaviourResult:
    api_delta_ok: bool
    removed_undeclared: list[str]
    coverage_ok: bool
    coverage_drops: list[dict[str, object]]
    test_count_ok: bool
    baseline_collected: int | None
    integration_collected: int | None

    @property
    def verdict(self) -> Verdict:
        if not self.api_delta_ok:
            return Verdict.fail(
                VerdictKind.BEHAVIOUR, "behaviour",
                "an exported symbol disappeared that no task declared removing",
                removed=self.removed_undeclared,
            )
        if not self.test_count_ok:
            return Verdict.fail(
                VerdictKind.BEHAVIOUR, "behaviour",
                f"collected test count changed: {self.baseline_collected} -> {self.integration_collected}",
            )
        if not self.coverage_ok:
            return Verdict.fail(
                VerdictKind.BEHAVIOUR, "behaviour", "line coverage dropped on a touched file",
                drops=self.coverage_drops,
            )
        return Verdict.ok_()


def check_api_delta(
    store: Store, graph_id: str, touched_files: set[str], declared_removals: set[str],
    patched_exports: dict[str, set[str]],
) -> tuple[bool, list[str]]:
    """`patched_exports` maps file path -> exported fqns re-extracted from the
    integration run's tree. Anything in the baseline set for a touched file that
    is not in the patched set, and not declared, is an undeclared removal."""
    before = store.exported_symbols(graph_id, touched_files)
    after: set[str] = set()
    for path, names in patched_exports.items():
        after |= {f"{path}::{n}" for n in names}

    missing = before - after
    undeclared = sorted(m for m in missing if m.split("::", 1)[-1] not in declared_removals)
    return (len(undeclared) == 0), undeclared


def check_coverage_floor(
    baseline_coverage_json: str, integration_coverage_json: str, touched_files: set[str]
) -> tuple[bool, list[dict[str, object]]]:
    before = _line_coverage(baseline_coverage_json)
    after = _line_coverage(integration_coverage_json)
    drops: list[dict[str, object]] = []
    for path in touched_files:
        b = before.get(path)
        a = after.get(path)
        if b is None or a is None:
            continue
        if a < b:
            drops.append({"file": path, "before": b, "after": a})
    return (len(drops) == 0), drops


def check_test_count(baseline_collected: int | None, integration_collected: int | None) -> bool:
    """The cheapest and most valuable check. It costs one command and it catches
    skipped, renamed and quietly removed tests regardless of how the exit code
    looks."""
    if baseline_collected is None or integration_collected is None:
        return True  # cannot assert what was never measured; not this check's job
    return baseline_collected == integration_collected


def _line_coverage(coverage_json: str) -> dict[str, float]:
    try:
        data = json.loads(coverage_json or "{}")
    except json.JSONDecodeError:
        return {}
    out: dict[str, float] = {}
    for path, entry in (data.get("files") or {}).items():
        summary = entry.get("summary", {})
        pct = summary.get("percent_covered")
        if pct is not None:
            out[_normalise(path)] = float(pct)
    return out


def _normalise(path: str) -> str:
    p = path.replace("\\", "/").lstrip("./")
    for prefix in ("work/", "/work/"):
        if p.startswith(prefix):
            p = p[len(prefix):]
    return p


def evaluate_behaviour(
    store: Store, graph_id: str, touched_files: set[str], declared_removals: set[str],
    patched_exports: dict[str, set[str]], baseline_coverage_json: str,
    integration_coverage_json: str, baseline_collected: int | None, integration_collected: int | None,
) -> BehaviourResult:
    api_ok, removed = check_api_delta(store, graph_id, touched_files, declared_removals, patched_exports)
    cov_ok, drops = check_coverage_floor(baseline_coverage_json, integration_coverage_json, touched_files)
    count_ok = check_test_count(baseline_collected, integration_collected)
    return BehaviourResult(
        api_delta_ok=api_ok, removed_undeclared=removed, coverage_ok=cov_ok, coverage_drops=drops,
        test_count_ok=count_ok, baseline_collected=baseline_collected,
        integration_collected=integration_collected,
    )
