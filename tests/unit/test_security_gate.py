"""Gate 5: security, differential.

Every test here corresponds to a specific design constraint from the decision
record: no model in the loop, fail only on what is new, and a fingerprint that
survives a refactor's line-number churn. A regression in this file means the
gate would either miss an introduced vulnerability or, just as badly, fail
every legacy repository it is pointed at.
"""

from __future__ import annotations

from principal.gates.security import (
    Finding,
    check_security,
    scan_python,
    scan_secrets,
    scan_source,
)

# ---------------------------------------------------------------- secrets --


def test_hardcoded_password_is_flagged():
    findings = scan_secrets("a.py", 'password = "Tr0ub4dor&3xyz"\n')
    assert any(f.rule == "secret.hardcoded_credential" for f in findings)


def test_placeholder_password_is_not_flagged():
    findings = scan_secrets("a.py", 'password = "changeme"\n')
    assert findings == []


def test_low_entropy_word_is_not_flagged():
    findings = scan_secrets("a.py", 'password = "aaaaaaaaaaaa"\n')
    assert findings == []


def test_aws_access_key_pattern_is_flagged():
    findings = scan_secrets("a.py", 'key_id = "AKIAABCDEFGHIJKLMNOP"\n')
    assert any(f.rule == "secret.aws_access_key" for f in findings)


def test_github_token_pattern_is_flagged():
    findings = scan_secrets(
        "a.py", 'token = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"\n'
    )
    assert any(f.rule == "secret.github_token" for f in findings)


def test_fstring_template_is_not_a_hardcoded_secret():
    """A template is a lookup, not a literal credential. Flagging it would make
    the gate noisy on completely ordinary code."""
    findings = scan_secrets("a.py", 'token = f"{prefix}_{suffix}"\n')
    assert findings == []


# ------------------------------------------------------------ python AST --


def test_eval_on_dynamic_input_is_flagged():
    findings = scan_python("a.py", "def f(x):\n    return eval(x)\n")
    assert any(f.rule == "python.dangerous_eval" for f in findings)


def test_eval_on_a_literal_is_not_flagged():
    findings = scan_python("a.py", "def f():\n    return eval('1 + 1')\n")
    assert not any(f.rule == "python.dangerous_eval" for f in findings)


def test_pickle_loads_is_flagged():
    findings = scan_python("a.py", "import pickle\n\n\ndef f(b):\n    return pickle.loads(b)\n")
    assert any(f.rule == "python.insecure_deserialisation" for f in findings)


def test_sql_string_interpolation_is_flagged():
    src = (
        "def f(cur, name):\n"
        "    cur.execute(f\"SELECT * FROM users WHERE name = '{name}'\")\n"
    )
    findings = scan_python("a.py", src)
    assert any(f.rule == "python.sql_injection" for f in findings)


def test_sql_bound_parameter_is_not_flagged():
    src = "def f(cur, name):\n    cur.execute('SELECT * FROM users WHERE name = ?', (name,))\n"
    findings = scan_python("a.py", src)
    assert not any(f.rule == "python.sql_injection" for f in findings)


def test_shell_true_with_dynamic_command_is_flagged():
    src = "import subprocess\n\n\ndef f(cmd):\n    subprocess.run(cmd, shell=True)\n"
    findings = scan_python("a.py", src)
    assert any(f.rule == "python.shell_injection" for f in findings)


def test_tls_verification_disabled_is_flagged():
    src = "import requests\n\n\ndef f(url):\n    return requests.get(url, verify=False)\n"
    findings = scan_python("a.py", src)
    assert any(f.rule == "python.tls_verification_disabled" for f in findings)


def test_weak_hash_is_flagged():
    src = "import hashlib\n\n\ndef f(b):\n    return hashlib.md5(b)\n"
    findings = scan_python("a.py", src)
    assert any(f.rule == "python.weak_hash" for f in findings)


def test_unparseable_source_is_gate_2s_problem_not_this_gates():
    assert scan_python("a.py", "def f(:\n") == []


def test_scan_source_applies_ast_rules_only_to_python():
    findings = scan_source("a.ts", "eval(x)\n")
    assert findings == []  # no secret pattern either


# ------------------------------------------------------- fingerprint & gate --


def test_fingerprint_ignores_line_number():
    """Line numbers shift in every refactor. A positional fingerprint would
    report an entire file as new findings and the gate would fail on exactly
    the changes it exists to check."""
    a = Finding("python.weak_hash", "a.py", 5, "m", "hashlib.md5(x)")
    b = Finding("python.weak_hash", "a.py", 40, "m", "hashlib.md5(x)")
    assert a.fingerprint == b.fingerprint


def test_fingerprint_differs_by_rule():
    a = Finding("python.weak_hash", "a.py", 5, "m", "hashlib.md5(x)")
    b = Finding("python.dangerous_eval", "a.py", 5, "m", "hashlib.md5(x)")
    assert a.fingerprint != b.fingerprint


def test_preexisting_finding_does_not_fail_the_gate():
    """Legacy repositories are full of pre-existing findings. A gate that fails
    on absolute findings fails on every interesting target."""
    src = "import hashlib\n\n\ndef f(b):\n    return hashlib.md5(b)\n"
    result = check_security({"a.py": src}, {"a.py": src})
    assert result.verdict.ok
    assert result.introduced == []


def test_a_newly_introduced_finding_fails_the_gate():
    before = "def f(x):\n    return x + 1\n"
    after = "def f(x):\n    return eval(x)\n"
    result = check_security({"a.py": before}, {"a.py": after})
    assert not result.verdict.ok
    assert any(f.rule == "python.dangerous_eval" for f in result.introduced)


def test_a_fixed_finding_is_not_reported_as_introduced():
    before = "def f(x):\n    return eval(x)\n"
    after = "def f(x):\n    return x\n"
    result = check_security({"a.py": before}, {"a.py": after})
    assert result.verdict.ok


def test_no_baseline_is_inconclusive_not_a_pass_or_a_fail():
    """With nothing to subtract, the gate has no opinion, which must not be
    recorded as a pass."""
    result = check_security({}, {"a.py": "eval(x)\n"})
    assert result.inconclusive
    assert result.introduced == []


def test_empty_patched_tree_is_inconclusive():
    result = check_security({"a.py": "x = 1\n"}, {})
    assert result.inconclusive
