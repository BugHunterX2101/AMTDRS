"""One run per attempt, not three, and the sentinel parsing that reads it back."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from principal.sandbox.client import RunResult, SandboxClient
from principal.sandbox.scripts import (
    SENTINEL_COLLECT,
    SENTINEL_COVERAGE,
    SENTINEL_REPORT,
    attempt_script,
    collected_count,
    extract,
    integration_script,
)

APPLY_FAILED_EXIT = 90
CONFLICT_EXIT = 91


@dataclass(slots=True)
class TestRun:
    exit_code: int
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    collected: int | None = None
    failing: list[str] = field(default_factory=list)
    stdout: str = ""
    coverage_json: str = ""
    report_json: str = ""
    image: str | None = None
    operation_id: str | None = None
    duration_ms: int = 0
    apply_failed: bool = False
    conflict_index: int | None = None

    @property
    def green(self) -> bool:
        return self.exit_code == 0 and not self.failed and not self.errors and not self.apply_failed


async def run_attempt(
    sandbox: SandboxClient, c0_image, diff: str, test_nodeids: list[str], *,
    timeout_s: int = 300, on_operation=None,
) -> TestRun:
    result = await sandbox.run(
        c0_image,
        attempt_script(diff, test_nodeids),
        disposable=False,
        timeout_s=timeout_s,
        on_operation=on_operation,
    )
    return _parse(result)


async def run_integration(
    sandbox: SandboxClient, c0_image, diffs: list[str], *, timeout_s: int = 900, on_operation=None
) -> TestRun:
    result = await sandbox.run(
        c0_image,
        integration_script(diffs),
        disposable=False,
        timeout_s=timeout_s,
        on_operation=on_operation,
    )
    run = _parse(result)
    if result.exit_code == CONFLICT_EXIT:
        run.conflict_index = _conflict_index(result.stdout)
    return run


def _parse(result: RunResult) -> TestRun:
    report = extract(result.stdout, SENTINEL_REPORT)
    coverage = extract(result.stdout, SENTINEL_COVERAGE)
    collected = collected_count(extract(result.stdout, SENTINEL_COLLECT))

    run = TestRun(
        exit_code=result.exit_code,
        stdout=result.stdout,
        report_json=report,
        coverage_json=coverage,
        collected=collected,
        image=result.image,
        operation_id=result.operation_id,
        duration_ms=result.duration_ms,
        apply_failed="PRINCIPAL_APPLY_FAILED" in result.stdout
        or result.exit_code == APPLY_FAILED_EXIT,
    )

    try:
        data = json.loads(report or "{}")
    except json.JSONDecodeError:
        data = {}

    summary = data.get("summary", {}) or {}
    run.passed = int(summary.get("passed", 0) or 0)
    run.failed = int(summary.get("failed", 0) or 0)
    run.errors = int(summary.get("error", 0) or 0)
    run.skipped = int(summary.get("skipped", 0) or 0)
    if run.collected is None and summary.get("collected") is not None:
        run.collected = int(summary["collected"])

    for test in data.get("tests", []) or []:
        if test.get("outcome") in {"failed", "error"}:
            run.failing.append(str(test.get("nodeid", "")))

    # A run that produced no parseable report but exited non-zero still failed.
    # Trusting an absent report would turn an infrastructure problem into a pass.
    if not data and result.exit_code != 0 and not run.failed:
        run.errors = run.errors or 1
    return run


def _conflict_index(stdout: str) -> int | None:
    import re

    m = re.search(r"PRINCIPAL_CONFLICT (\d+)", stdout)
    return int(m.group(1)) if m else None


def stack_trace(run: TestRun, limit: int = 4000) -> str:
    """What the Repairer sees. The failure, and nothing else.

    Narrowing its context to the concrete failure stops it from rationalising a
    broken change as goal-aligned, which is the standard failure mode of
    self-healing loops that pass the original prompt back in on every retry.
    """
    try:
        data = json.loads(run.report_json or "{}")
    except json.JSONDecodeError:
        data = {}

    chunks: list[str] = []
    for test in data.get("tests", []) or []:
        if test.get("outcome") not in {"failed", "error"}:
            continue
        call = test.get("call") or test.get("setup") or {}
        crash = call.get("crash") or {}
        longrepr = call.get("longrepr") or crash.get("message") or ""
        chunks.append(f"{test.get('nodeid', '?')}\n{longrepr}".strip())

    text = "\n\n".join(chunks) if chunks else run.stdout
    return text[-limit:]
