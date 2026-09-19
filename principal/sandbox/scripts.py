"""Shell templates, one per phase.

Each execution gets its own VM and no process survives between runs, so every
script is self-contained. The temptation is to chain runs — one to apply the
patch, one to check syntax, one to test. Do not. Each extra run is another VM
spin-up for no added information.

Machine-readable output is fenced by sentinels so parsing never depends on
pytest's stdout format, which changes between versions and plugins.
"""

from __future__ import annotations

import base64
import shlex

WORKDIR = "/work"

SENTINEL_REPORT = "__PRINCIPAL_REPORT__"
SENTINEL_COVERAGE = "__PRINCIPAL_COVERAGE__"
SENTINEL_COLLECT = "__PRINCIPAL_COLLECT__"
SENTINEL_END = "__PRINCIPAL_END__"

# relative_files keeps coverage paths comparable with the graph's repo-relative
# paths. show_contexts is what turns a coverage run into a test->symbol map, and
# it is the single reason per-fork test selection is fast enough to be worth it.
COVERAGERC = """[run]
relative_files = True
branch = False

[json]
show_contexts = True
"""

# Installing the test harness is the orchestrator's doing, at baseline, once.
# It is emphatically not an agent adding a dependency: a diff that touches a
# manifest is rejected at gate 1, and no agent can reach this script.
TEST_TOOLING = "pytest pytest-cov pytest-json-report coverage"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _emit(sentinel: str, path: str) -> str:
    return f'echo "{sentinel}"; cat {path} 2>/dev/null || echo "{{}}"; echo "{SENTINEL_END}"'


def baseline_script(repo_url: str, commit_sha: str, *, install_cmd: str | None = None) -> str:
    """Clone, install, run the full suite with coverage contexts, keep the image.

    Dependency installation happens exactly once per repository and commit, which
    is where most of the wall-clock saving comes from. The coverage report
    produced here populates test_edge, so the tests relation is a by-product of a
    run that had to happen anyway.
    """
    install = install_cmd or (
        "pip install -e '.[test]' || pip install -e '.[tests]' || pip install -e . || true; "
        "[ -f requirements.txt ] && pip install -r requirements.txt || true; "
        "[ -f requirements-dev.txt ] && pip install -r requirements-dev.txt || true; "
        "[ -f test-requirements.txt ] && pip install -r test-requirements.txt || true"
    )
    return "\n".join(
        [
            "set -o pipefail",
            "export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore",
            f"git clone --filter=blob:none {shlex.quote(repo_url)} {WORKDIR} 2>&1 | tail -3",
            f"cd {WORKDIR}",
            f"git checkout --quiet {shlex.quote(commit_sha)}",
            f"echo {_b64(COVERAGERC)} | base64 -d > /tmp/principal.coveragerc",
            "export COVERAGE_RCFILE=/tmp/principal.coveragerc",
            f"pip install --quiet {TEST_TOOLING} 2>&1 | tail -3",
            install,
            # Collection count first: it is the baseline half of the test count
            # invariant, and it is cheap.
            "pytest --collect-only -q -p no:cacheprovider 2>/dev/null"
            " | tail -1 > /tmp/collect.txt || true",
            "pytest -q -p no:cacheprovider --cov=. --cov-context=test"
            " --cov-report=json:/tmp/cov.json"
            " --json-report --json-report-file=/tmp/report.json 2>&1 | tail -40",
            "PYTEST_EXIT=$?",
            _emit(SENTINEL_COVERAGE, "/tmp/cov.json"),
            _emit(SENTINEL_REPORT, "/tmp/report.json"),
            _emit(SENTINEL_COLLECT, "/tmp/collect.txt"),
            "exit $PYTEST_EXIT",
        ]
    )


def attempt_script(diff: str, test_nodeids: list[str]) -> str:
    """Apply one patch and run only the tests that cover the file it changed.

    The patch goes in as a base64 heredoc rather than an upload, because a
    single-file diff is small and an upload is a second round trip.

    `git apply --check` first means a malformed patch fails before anything is
    modified, so the image this run produces is either fully patched or untouched.
    """
    selected = " ".join(shlex.quote(n) for n in test_nodeids) if test_nodeids else ""
    return "\n".join(
        [
            "set -o pipefail",
            f"cd {WORKDIR}",
            "export COVERAGE_RCFILE=/tmp/principal.coveragerc",
            f"echo {_b64(diff)} | base64 -d > /tmp/p.diff",
            "git apply --check /tmp/p.diff || { echo 'PRINCIPAL_APPLY_FAILED'; exit 90; }",
            "git apply /tmp/p.diff",
            f"pytest -q -p no:cacheprovider {selected}"
            " --json-report --json-report-file=/tmp/report.json 2>&1 | tail -60",
            "PYTEST_EXIT=$?",
            _emit(SENTINEL_REPORT, "/tmp/report.json"),
            "exit $PYTEST_EXIT",
        ]
    )


def integration_script(diffs: list[str]) -> str:
    """Apply every surviving diff in dependency order onto a fresh fork of C0,
    then run the full suite, coverage and a collection count. One run, once.

    Patches that each pass in isolation can still conflict, which is the failure
    mode that kills naive parallel agent systems and the reason this is a
    separate gate.
    """
    lines = [
        "set -o pipefail",
        f"cd {WORKDIR}",
        "export COVERAGE_RCFILE=/tmp/principal.coveragerc",
    ]
    for i, diff in enumerate(diffs):
        lines += [
            f"echo {_b64(diff)} | base64 -d > /tmp/p{i}.diff",
            f"git apply --check /tmp/p{i}.diff || {{ echo 'PRINCIPAL_CONFLICT {i}'; exit 91; }}",
            f"git apply /tmp/p{i}.diff",
        ]
    lines += [
        "pytest --collect-only -q -p no:cacheprovider 2>/dev/null | tail -1 > /tmp/collect.txt || true",
        "pytest -q -p no:cacheprovider --cov=. --cov-context=test"
        " --cov-report=json:/tmp/cov.json"
        " --json-report --json-report-file=/tmp/report.json 2>&1 | tail -60",
        "PYTEST_EXIT=$?",
        "git diff > /tmp/combined.diff || true",
        _emit(SENTINEL_COVERAGE, "/tmp/cov.json"),
        _emit(SENTINEL_REPORT, "/tmp/report.json"),
        _emit(SENTINEL_COLLECT, "/tmp/collect.txt"),
        "exit $PYTEST_EXIT",
    ]
    return "\n".join(lines)


def snapshot_script() -> str:
    """Emit the repository tree as a tar stream the orchestrator can unpack.

    The orchestrator needs a local copy to parse and to apply diffs against for
    gates 1 and 2. Reading it out of the baseline image means the snapshot is
    byte-identical to what the tests ran against.
    """
    return "\n".join(
        [
            f"cd {WORKDIR}",
            "tar -cz --exclude-vcs --exclude=node_modules --exclude=.venv"
            " --exclude=__pycache__ . | base64 -w0",
        ]
    )


def extract(stdout: str, sentinel: str) -> str:
    """Pull one sentinel-fenced blob out of a run's stdout."""
    start = stdout.find(sentinel)
    if start == -1:
        return ""
    start += len(sentinel)
    end = stdout.find(SENTINEL_END, start)
    return stdout[start : end if end != -1 else len(stdout)].strip()


def collected_count(blob: str) -> int | None:
    """Parse `N tests collected` out of pytest's collect-only summary line."""
    import re

    m = re.search(r"(\d+)\s+tests?\s+collected", blob)
    if m:
        return int(m.group(1))
    m = re.search(r"^(\d+)$", blob.strip())
    return int(m.group(1)) if m else None
