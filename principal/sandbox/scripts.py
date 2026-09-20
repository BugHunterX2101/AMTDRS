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
SENTINEL_MUTATION = "__PRINCIPAL_MUTATION__"
SENTINEL_SECURITY = "__PRINCIPAL_SECURITY__"
SENTINEL_END = "__PRINCIPAL_END__"

# Where characterisation tests land. It matches the TEST_PATH pattern that gate 1
# uses to reject any diff touching a test, which is the whole freeze mechanism:
# from the moment these files are written they are outside every agent's reach,
# and no new code was needed to put them there.
CHARACTERISATION_DIR = "tests/principal_characterisation"

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
# pytest-timeout earns its place only in the mutation loop: a flipped comparison
# inside a `while` is a routine mutant and an unbounded one, and without a
# per-test deadline a single such mutant consumes the whole operation budget.
TEST_TOOLING = "pytest pytest-cov pytest-json-report pytest-timeout coverage"


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


def characterisation_script(rel_path: str, test_source: str) -> str:
    """Write a generated characterisation suite and run it against untouched C0.

    This is rule 2 of the four that make generated tests safe, and it is a
    deterministic filter rather than a judgement: a test that fails against the
    baseline is simply wrong about what the code currently does, and it is
    discarded without anything having to reason about why. It removes most
    hallucinated assertions at no cognitive cost.
    """
    return "\n".join(
        [
            "set -o pipefail",
            f"cd {WORKDIR}",
            f"mkdir -p $(dirname {shlex.quote(rel_path)})",
            f"touch {shlex.quote(CHARACTERISATION_DIR)}/__init__.py 2>/dev/null || true",
            f"echo {_b64(test_source)} | base64 -d > {shlex.quote(rel_path)}",
            f"pytest -q -p no:cacheprovider {shlex.quote(rel_path)}"
            " --json-report --json-report-file=/tmp/report.json 2>&1 | tail -40",
            "PYTEST_EXIT=$?",
            _emit(SENTINEL_REPORT, "/tmp/report.json"),
            "exit $PYTEST_EXIT",
        ]
    )


def mutation_script(target_path: str, mutants: list[tuple[str, str]], test_nodeids: list[str]) -> str:
    """Run every mutant in ONE operation.

    The cost model is the reason this is a shell loop rather than a sandbox run
    per mutant. Each run costs a microVM spin-up of a couple of seconds, so
    thirty of them is ten minutes of pure overhead before a single test executes.
    Writing all the mutants up front and looping inside one operation pays that
    cost once.

    The original file is restored after each mutant, so the loop cannot drift:
    mutant N always runs against exactly one change from the pristine tree.
    """
    selected = " ".join(shlex.quote(n) for n in test_nodeids) if test_nodeids else ""
    quoted_target = shlex.quote(target_path)

    lines = [
        f"cd {WORKDIR}",
        "mkdir -p /tmp/mutants",
        f"cp {quoted_target} /tmp/original.py",
        ": > /tmp/mutation.txt",
        # Probed rather than assumed. A missing plugin would make pytest exit 4
        # on every mutant, which classifies the entire run as incompetent and
        # silently reports "no safety net" for a tooling reason.
        'TIMEOUT_FLAG=""',
        'python -c "import pytest_timeout" >/dev/null 2>&1 && TIMEOUT_FLAG="--timeout=60"',
    ]
    for mutant_id, source in mutants:
        lines.append(f"echo {_b64(source)} | base64 -d > /tmp/mutants/{mutant_id}.py")

    for mutant_id, _source in mutants:
        lines += [
            f"cp /tmp/mutants/{mutant_id}.py {quoted_target}",
            f"pytest -q -p no:cacheprovider -x $TIMEOUT_FLAG {selected} >/dev/null 2>&1",
            # Recorded immediately, before anything else can overwrite $?.
            f'echo "{mutant_id} $?" >> /tmp/mutation.txt',
            f"cp /tmp/original.py {quoted_target}",
        ]

    lines += [
        f"cp /tmp/original.py {quoted_target}",
        _emit(SENTINEL_MUTATION, "/tmp/mutation.txt"),
        "exit 0",
    ]
    return "\n".join(lines)


def parse_mutation_results(blob: str) -> dict[str, int]:
    """`m000 1` per line, mutant id to pytest exit code."""
    out: dict[str, int] = {}
    for line in blob.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("-").isdigit():
            out[parts[0]] = int(parts[1])
    return out


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
