"""Gate 5: security, differential.

Two design points carry this gate, and both are about what it does *not* do.

It is not an agent. A model asked "is this diff secure" will sometimes say yes
about an injection and sometimes say no about a parameterised query, and neither
answer is reproducible across runs. A benchmark number produced behind a
non-deterministic check is not a number, and security review is the worst place
to accept a confident wrong answer. Everything here is rule-based.

It is differential, not absolute. Legacy repositories are full of pre-existing
findings; a gate that fails on absolute findings fails on every interesting
target and teaches everyone to ignore it. This scans the baseline, scans the
result, and fails only on what is *new* — the same shape as the coverage floor
check that already exists.

Fingerprints deliberately exclude the line number. Line numbers shift in every
refactor, so a positional fingerprint reports an entire file as new findings and
the gate becomes useless on exactly the changes it exists to check.

Two named threats need no rule here at all. Outdated dependencies and licence
risk are closed by construction: gate 1 rejects any diff touching a manifest or
a lockfile, so there is no code path by which a dependency changes. A threat
closed by construction is a better answer than a threat caught by a checker.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
from dataclasses import dataclass

from principal.gates.pipeline import Verdict, VerdictKind


@dataclass(frozen=True, slots=True)
class Finding:
    rule: str
    path: str
    line: int
    message: str
    snippet: str = ""

    @property
    def fingerprint(self) -> str:
        """Rule plus normalised code, never the line number.

        Whitespace and string *contents* are normalised out so that reindenting a
        block or rewording a message does not read as a new finding, while the
        structure that triggered the rule still does.
        """
        normalised = re.sub(r"\s+", " ", self.snippet).strip()
        normalised = re.sub(r"(['\"]).*?\1", "''", normalised)
        return hashlib.sha256(f"{self.rule}:{self.path}:{normalised}".encode()).hexdigest()[:16]

    def to_payload(self) -> dict[str, object]:
        return {"rule": self.rule, "path": self.path, "line": self.line, "message": self.message}


# --------------------------------------------------------------- secrets ---

# Entropy alone produces too many hits on hashes and base64 blobs that are not
# credentials; a keyword alone misses a bare token. Both together is the
# combination gitleaks uses and the reason its false-positive rate is tolerable.
_SECRET_ASSIGN = re.compile(
    r"""(?ix)
    \b(?P<key>pass(word|wd)?|secret|token|api[_-]?key|access[_-]?key
       |private[_-]?key|client[_-]?secret|auth)\b
    \s*[:=]\s*
    (?P<quote>['"])(?P<value>[^'"]{8,})(?P=quote)
    """
)

# Provider-specific shapes, which need no entropy check because the prefix is
# already the evidence.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("secret.aws_access_key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("secret.github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("secret.slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("secret.private_key_block", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("secret.jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
)

# Values that look like credentials and are not. Every one of these appears in
# real code as a placeholder, and flagging them is how a gate gets ignored.
_PLACEHOLDERS = frozenset(
    {
        "password", "changeme", "secret", "example", "your_password_here", "xxxxxxxx",
        "placeholder", "redacted", "dummy", "test", "testing", "none", "null", "todo",
        "not_a_real_secret", "s3cr3t", "hunter2", "fakefakefake",
    }
)


def _shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(value)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _looks_like_a_secret(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in _PLACEHOLDERS or len(value) < 8:
        return False
    # An f-string or a template is a lookup, not a literal credential.
    if "{" in value and "}" in value:
        return False
    if re.fullmatch(r"[a-z_]+", lowered):  # a plain word is a placeholder
        return False
    return _shannon_entropy(value) >= 3.0


def scan_secrets(path: str, source: str) -> list[Finding]:
    findings: list[Finding] = []
    for i, line in enumerate(source.splitlines(), start=1):
        for rule, pattern in _SECRET_PATTERNS:
            if pattern.search(line):
                findings.append(Finding(rule, path, i, "a credential literal appears in source", line.strip()))
        m = _SECRET_ASSIGN.search(line)
        if m and _looks_like_a_secret(m.group("value")):
            findings.append(
                Finding("secret.hardcoded_credential", path, i,
                        f"high-entropy literal assigned to {m.group('key')!r}", line.strip())
            )
    return findings


# ----------------------------------------------------------- code patterns ---


class _PythonRules(ast.NodeVisitor):
    """Rule-based AST inspection for the patterns a refactor can plausibly
    introduce while rewriting a helper or migrating callers.

    Scoped on purpose. This is not a replacement for a full ruleset; it is the
    subset that is both deterministic and reachable from "an agent rewrote this
    file". When `semgrep` is present its findings are merged in on top.
    """

    def __init__(self, path: str, source: str):
        self.path = path
        self.lines = source.splitlines()
        self.findings: list[Finding] = []

    def _snippet(self, node: ast.AST) -> str:
        lineno = getattr(node, "lineno", 0)
        return self.lines[lineno - 1].strip() if 0 < lineno <= len(self.lines) else ""

    def _add(self, node: ast.AST, rule: str, message: str) -> None:
        self.findings.append(
            Finding(rule, self.path, getattr(node, "lineno", 0), message, self._snippet(node))
        )

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = _dotted_name(node.func)

        if name in {"eval", "exec"} and node.args and not isinstance(node.args[0], ast.Constant):
            self._add(node, "python.dangerous_eval", f"{name}() on a non-literal expression")

        if name in {"pickle.loads", "pickle.load", "dill.loads", "cPickle.loads"}:
            self._add(node, "python.insecure_deserialisation", f"{name}() deserialises arbitrary objects")

        if name in {"yaml.load"} and not _has_safe_loader(node):
            self._add(node, "python.yaml_unsafe_load", "yaml.load() without SafeLoader")

        if name.startswith("subprocess.") or name in {"os.system", "os.popen"}:
            if _keyword_is_true(node, "shell") or name in {"os.system", "os.popen"}:
                if _any_arg_is_dynamic(node):
                    self._add(node, "python.shell_injection",
                              f"{name}() builds a shell command from a non-literal")

        if name.endswith(("cursor.execute", "cursor.executemany")) or name.endswith(".execute"):
            if node.args and _is_interpolated_string(node.args[0]):
                self._add(node, "python.sql_injection",
                          "SQL built by string interpolation rather than bound parameters")

        if name in {"hashlib.md5", "hashlib.sha1"}:
            self._add(node, "python.weak_hash", f"{name}() is unsuitable for credentials")

        if _keyword_is_false(node, "verify"):
            self._add(node, "python.tls_verification_disabled", "TLS certificate verification disabled")

        if name == "tempfile.mktemp":
            self._add(node, "python.insecure_temp_file", "tempfile.mktemp() is race-prone; use mkstemp()")

        self.generic_visit(node)


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _has_safe_loader(node: ast.Call) -> bool:
    for kw in node.keywords:
        if kw.arg in {"Loader", "loader"} and "Safe" in _dotted_name(kw.value):
            return True
    return any("Safe" in _dotted_name(a) for a in node.args[1:])


def _keyword_is_true(node: ast.Call, name: str) -> bool:
    return any(
        kw.arg == name and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in node.keywords
    )


def _keyword_is_false(node: ast.Call, name: str) -> bool:
    return any(
        kw.arg == name and isinstance(kw.value, ast.Constant) and kw.value.value is False
        for kw in node.keywords
    )


def _is_interpolated_string(node: ast.AST) -> bool:
    """An f-string, a `%` format, a `.format()` call or a `+` concatenation that
    produces the query text. A plain constant is fine and a bound parameter is
    the correct spelling, so neither is flagged."""
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
        return not (isinstance(node.left, ast.Constant) and isinstance(node.right, ast.Constant))
    if isinstance(node, ast.Call) and _dotted_name(node.func).endswith(".format"):
        return True
    return False


def _any_arg_is_dynamic(node: ast.Call) -> bool:
    for arg in node.args:
        if isinstance(arg, ast.Constant):
            continue
        if isinstance(arg, ast.List) and all(isinstance(e, ast.Constant) for e in arg.elts):
            continue
        return True
    return False


def scan_python(path: str, source: str) -> list[Finding]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Gate 2 owns syntax. A file that does not parse is not this gate's
        # problem and must not be reported as a security finding.
        return []
    rules = _PythonRules(path, source)
    rules.visit(tree)
    return rules.findings


def scan_source(path: str, source: str) -> list[Finding]:
    """Every rule that applies to one file. Secrets apply to any language;
    the AST rules apply to Python only."""
    findings = scan_secrets(path, source)
    if path.endswith(".py"):
        findings += scan_python(path, source)
    return findings


def scan_tree(files: dict[str, str]) -> list[Finding]:
    out: list[Finding] = []
    for path in sorted(files):
        out.extend(scan_source(path, files[path]))
    return out


# ------------------------------------------------------------- semgrep ------


def parse_semgrep_json(blob: str) -> list[Finding]:
    """Merge a real scanner's output when one is available in the image.

    Principal does not depend on semgrep being installed — the built-in rules
    always run so the gate always has an opinion — but where semgrep *is*
    present its rules are maintained by people who do this full time, and
    ignoring them to preserve a tidy dependency story would be the wrong trade.
    """
    try:
        data = json.loads(blob or "{}")
    except json.JSONDecodeError:
        return []

    out: list[Finding] = []
    for result in data.get("results", []) or []:
        extra = result.get("extra", {}) or {}
        start = result.get("start", {}) or {}
        out.append(
            Finding(
                rule=f"semgrep.{result.get('check_id', 'unknown')}",
                path=_normalise_path(str(result.get("path", ""))),
                line=int(start.get("line", 0) or 0),
                message=str(extra.get("message", "")).strip(),
                snippet=str(extra.get("lines", "")).strip(),
            )
        )
    return out


def _normalise_path(path: str) -> str:
    p = path.replace("\\", "/").lstrip("./")
    for prefix in ("work/", "/work/"):
        if p.startswith(prefix):
            p = p[len(prefix):]
    return p


# ------------------------------------------------------------ the gate ------


@dataclass(slots=True)
class SecurityResult:
    introduced: list[Finding]
    baseline_count: int
    result_count: int
    scanned_files: list[str]
    inconclusive: bool = False

    @property
    def verdict(self) -> Verdict:
        if self.inconclusive:
            return Verdict.fail(
                VerdictKind.INCONCLUSIVE, "security",
                "no baseline scan to compare against; the gate has no opinion",
            )
        if self.introduced:
            return Verdict.fail(
                VerdictKind.SECURITY, "security",
                f"{len(self.introduced)} new security finding(s) introduced",
                findings=[f.to_payload() for f in self.introduced],
            )
        return Verdict.ok_()

    def to_payload(self) -> dict[str, object]:
        return {
            "introduced": [f.to_payload() for f in self.introduced],
            "baseline_findings": self.baseline_count,
            "result_findings": self.result_count,
            "scanned_files": sorted(self.scanned_files),
            "inconclusive": self.inconclusive,
        }


def check_security(
    baseline: dict[str, str], patched: dict[str, str], *, extra_baseline: list[Finding] | None = None,
    extra_patched: list[Finding] | None = None,
) -> SecurityResult:
    """Scan both trees, subtract, report what is new.

    `baseline` and `patched` map repo-relative path to file text and should
    contain the same key set: the touched files, before and after. Restricting
    to touched files is what keeps the gate honest about what this change did,
    rather than what the repository already contained.
    """
    if not patched:
        return SecurityResult([], 0, 0, [], inconclusive=True)
    if not baseline:
        return SecurityResult([], 0, len(scan_tree(patched)), sorted(patched), inconclusive=True)

    before = scan_tree(baseline) + list(extra_baseline or [])
    after = scan_tree(patched) + list(extra_patched or [])

    known = {f.fingerprint for f in before}
    introduced = [f for f in after if f.fingerprint not in known]
    introduced.sort(key=lambda f: (f.path, f.line, f.rule))

    return SecurityResult(
        introduced=introduced, baseline_count=len(before), result_count=len(after),
        scanned_files=sorted(patched),
    )
