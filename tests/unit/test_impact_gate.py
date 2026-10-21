"""Predicted impact against actual impact.

Every case here is a refactor that a test run can pass and a reviewer should not
trust. The suite is built to fail in both directions: a gate that blocks a
correct, complete migration is as useless as one that waves through an incomplete
one, so the correct cases are asserted just as hard as the broken ones.
"""

from __future__ import annotations

import ast

import pytest

from principal.gates.impact import (
    BLOCK,
    INFO,
    WARN,
    CallRef,
    ImpactInputs,
    Params,
    _params,
    analyse_impact,
    call_compat,
    changed_lines,
    find_stale_references,
    module_name_of,
    parse_coverage,
    signature_breaking,
    unexecuted_changed_lines,
)

SESSION_BEFORE = "def create(user, ttl=3600):\n    return (user, ttl)\n"
SESSION_AFTER = "def create(user, *, ttl=3600):\n    return (user, ttl)\n"

LOGIN_BEFORE = "from src.auth.session import create\n\n\ndef login(user):\n    return create(user, 3600)\n"
LOGIN_AFTER = "from src.auth.session import create\n\n\ndef login(user):\n    return create(user, ttl=3600)\n"

ADMIN_BEFORE = "from src.auth.session import create\n\n\ndef admin(user):\n    return create(user, 30)\n"


def _inputs(**over) -> ImpactInputs:
    base = dict(
        target_name="create", target_path="src/auth/session.py", target_kind="function",
        target_parent=None,
        radius_files={"src/auth/session.py", "src/api/login.py", "src/admin/tools.py"},
        planned_files={"src/auth/session.py", "src/api/login.py", "src/admin/tools.py"},
        unhandled=[],
        static_call_sites=[CallRef("src/api/login.py", 5, "create"),
                           CallRef("src/admin/tools.py", 5, "create")],
        unresolved=[],
        baseline_tree={"src/auth/session.py": SESSION_BEFORE, "src/api/login.py": LOGIN_BEFORE,
                       "src/admin/tools.py": ADMIN_BEFORE},
        patched_tree={"src/auth/session.py": SESSION_AFTER, "src/api/login.py": LOGIN_AFTER,
                      "src/admin/tools.py": ADMIN_BEFORE.replace("30)", "ttl=30)")},
        touched_files={"src/auth/session.py", "src/api/login.py", "src/admin/tools.py"},
        declared_removals=set(), baseline_coverage={},
    )
    base.update(over)
    return ImpactInputs(**base)


def _kinds(report, severity=None):
    return sorted(f.kind for f in report.findings if severity is None or f.severity == severity)


# ----------------------------------------------------- the complete migration ----


def test_a_complete_migration_is_not_blocked():
    report = analyse_impact(_inputs())

    assert not report.blocked, [f.detail for f in report.blocks]
    assert report.target_signature["status"] == "changed"
    assert report.target_signature["breaking"] is True
    assert report.call_sites_changed == 2
    assert report.call_sites_unchanged == []
    assert report.unexpected_files == []


# ------------------------------------------------- the easy-to-miss call site ----


def test_a_caller_left_on_the_old_form_is_a_definite_break():
    """Tests that never call `admin` pass; the signature change still broke it."""
    inp = _inputs()
    inp.patched_tree["src/admin/tools.py"] = ADMIN_BEFORE
    inp.touched_files = {"src/auth/session.py", "src/api/login.py"}

    report = analyse_impact(inp)

    assert report.blocked
    block = report.blocks[0]
    assert block.kind == "incompatible_call"
    assert block.file == "src/admin/tools.py"
    assert "positional" in block.detail
    assert {"file": "src/admin/tools.py", "line": 5, "name": "create"} in report.call_sites_unchanged


def test_an_unchanged_caller_that_is_still_compatible_is_only_information():
    """`create(user)` never passed ttl, so a keyword-only ttl does not touch it."""
    inp = _inputs()
    inp.baseline_tree["src/api/login.py"] = LOGIN_BEFORE.replace("create(user, 3600)", "create(user)")
    inp.patched_tree["src/api/login.py"] = inp.baseline_tree["src/api/login.py"]

    report = analyse_impact(inp)

    assert not report.blocked
    assert "call_sites_unchanged" in _kinds(report, INFO)


# ------------------------------------------------------------ plan completeness ----


def test_a_file_with_a_call_site_and_no_task_blocks_the_plan():
    inp = _inputs(planned_files={"src/auth/session.py", "src/api/login.py"})

    report = analyse_impact(inp)

    assert report.blocked
    assert [f.kind for f in report.blocks] == ["incomplete_plan"]
    assert report.blocks[0].file == "src/admin/tools.py"


def test_a_declined_call_site_is_accepted_when_the_plan_says_why():
    inp = _inputs(planned_files={"src/auth/session.py", "src/api/login.py"},
                  unhandled=["src/admin/tools.py: owned by another team, leaving alone"])

    report = analyse_impact(inp)

    assert "incomplete_plan" not in _kinds(report)
    assert "call_site_declined" in _kinds(report, INFO)


def test_a_discarded_call_site_file_is_a_warning_not_a_block():
    inp = _inputs(discarded_files={"src/admin/tools.py"})
    inp.patched_tree["src/admin/tools.py"] = ADMIN_BEFORE
    inp.touched_files = {"src/auth/session.py", "src/api/login.py"}
    inp.patched_tree["src/auth/session.py"] = SESSION_BEFORE  # signature untouched: partial migration

    report = analyse_impact(inp)

    assert "unmigrated_call_site" in _kinds(report, WARN)
    assert not report.blocked


# ------------------------------------------------------------------------ scope ----


def test_a_changed_file_outside_the_radius_blocks():
    inp = _inputs()
    inp.touched_files = inp.touched_files | {"src/secret/keys.py"}
    inp.patched_tree["src/secret/keys.py"] = "X = 1\n"

    report = analyse_impact(inp)

    assert any(f.kind == "out_of_scope" and f.file == "src/secret/keys.py" for f in report.blocks)
    assert report.unexpected_files == ["src/secret/keys.py"]


# ----------------------------------------------------------------- removed names ----


REGISTRY = (
    "from src.auth import session\n\n"
    'HANDLERS = {"create": "create"}\n\n\n'
    "def dispatch(name, *args):\n"
    "    return getattr(session, HANDLERS[name])(*args)\n"
)
DIRECT = 'from src.auth import session\n\n\ndef make():\n    return getattr(session, "create")("u")\n'


def _rename_inputs(**over):
    """`create` is renamed `make_session`; the planned callers are updated."""
    session_after = "def make_session(user, ttl=3600):\n    return (user, ttl)\n"
    login_after = ("from src.auth.session import make_session\n\n\ndef login(user):\n"
                   "    return make_session(user, 3600)\n")
    base = dict(
        radius_files={"src/auth/session.py", "src/api/login.py", "src/registry.py"},
        planned_files={"src/auth/session.py", "src/api/login.py"},
        static_call_sites=[CallRef("src/api/login.py", 5, "create")],
        baseline_tree={"src/auth/session.py": SESSION_BEFORE, "src/api/login.py": LOGIN_BEFORE,
                       "src/registry.py": REGISTRY},
        patched_tree={"src/auth/session.py": session_after, "src/api/login.py": login_after,
                      "src/registry.py": REGISTRY},
        touched_files={"src/auth/session.py", "src/api/login.py"},
        declared_removals={"create"},
    )
    base.update(over)
    return _inputs(**base)


def test_a_registry_that_still_holds_the_old_name_is_found_though_tests_pass():
    report = analyse_impact(_rename_inputs())

    stale = report.stale_references
    assert [(s.file, s.name, s.strength) for s in stale] == [("src/registry.py", "create", "medium")]
    assert "stale_reference" in _kinds(report, WARN)
    assert not report.blocked, "the string alone is unverified, not proven wrong; the probe decides"


def test_a_direct_getattr_on_the_removed_name_is_a_strong_finding():
    inp = _rename_inputs()
    inp.patched_tree["src/registry.py"] = DIRECT

    stale = analyse_impact(inp).stale_references

    assert [(s.name, s.strength) for s in stale] == [("create", "strong")]


def test_keeping_the_old_name_as_an_alias_clears_the_finding():
    inp = _rename_inputs()
    inp.patched_tree["src/auth/session.py"] = (
        "def make_session(user, ttl=3600):\n    return (user, ttl)\n\n\ncreate = make_session\n"
    )

    report = analyse_impact(inp)

    assert report.stale_references == []
    assert report.target_signature["status"] == "aliased"
    assert not report.blocked


def test_a_caller_that_still_calls_the_removed_function_is_a_definite_break():
    inp = _rename_inputs()
    inp.patched_tree["src/api/login.py"] = LOGIN_BEFORE  # never updated

    report = analyse_impact(inp)

    assert any(f.kind == "dangling_call" and f.file == "src/api/login.py" for f in report.blocks)


def test_a_label_that_is_not_a_lookup_is_not_flagged():
    """A gate that blocks on the word 'create' wherever it appears gets overridden."""
    trees = {"src/ui.py": 'TITLE = "create"\nprint("create")\n'}

    assert find_stale_references(trees, {"create"}, "src.auth.session") == []


def test_a_lookup_on_a_different_module_is_not_flagged():
    trees = {"src/x.py": 'import os\n\n\ndef f():\n    return getattr(os, "create", None)\n'}

    assert find_stale_references(trees, {"create"}, "src.auth.session") == []


# --------------------------------------------------------------- dynamic paths ----


def test_a_dynamic_call_no_test_executes_is_flagged():
    inp = _inputs(unresolved=[CallRef("src/registry.py", 7, "fn")],
                  baseline_coverage={"src/registry.py": ({1, 2}, {7})})

    report = analyse_impact(inp)

    assert report.dynamic_paths == [
        {"file": "src/registry.py", "line": 7, "name": "fn", "exercised_by_tests": False}
    ]
    assert "dynamic_path_unexercised" in _kinds(report, WARN)


def test_a_dynamic_call_a_test_executes_is_recorded_without_a_warning():
    inp = _inputs(unresolved=[CallRef("src/registry.py", 7, "fn")],
                  baseline_coverage={"src/registry.py": ({7}, set())})

    report = analyse_impact(inp)

    assert report.dynamic_paths[0]["exercised_by_tests"] is True
    assert "dynamic_path_unexercised" not in _kinds(report)


def test_missing_coverage_is_reported_as_unknown_not_as_zero():
    inp = _inputs(unresolved=[CallRef("src/registry.py", 7, "fn")], baseline_coverage=None)

    report = analyse_impact(inp)

    assert "coverage_unavailable" in _kinds(report, WARN)
    assert "dynamic_path_unexercised" not in _kinds(report), "unknown must not read as unexercised"


# --------------------------------------------------------------- uncheckable ----


def test_a_target_file_that_no_longer_parses_cannot_be_checked_and_blocks():
    inp = _inputs()
    inp.patched_tree["src/auth/session.py"] = "def create(user, :\n"

    report = analyse_impact(inp)

    assert any(f.kind == "uncheckable" for f in report.blocks)


def test_a_caller_file_that_no_longer_parses_blocks_rather_than_passing():
    inp = _inputs()
    inp.patched_tree["src/api/login.py"] = "def login(:\n"

    report = analyse_impact(inp)

    assert any(f.kind == "uncheckable" and f.file == "src/api/login.py" for f in report.blocks)


def test_a_non_python_file_in_the_radius_is_disclosed():
    inp = _inputs(radius_files={"src/auth/session.py", "web/client.ts"})
    inp.static_call_sites = []

    assert "unchecked_language" in _kinds(analyse_impact(inp), WARN)


# ---------------------------------------------------------------- call shapes ----


def _call(src: str) -> ast.Call:
    node = ast.parse(src, mode="eval").body
    assert isinstance(node, ast.Call)
    return node


def _sig(src: str, *, skip_first=False) -> Params:
    fn = ast.parse(src).body[0]
    return _params(fn, skip_first=skip_first)


@pytest.mark.parametrize(
    ("sig", "call", "expected"),
    [
        ("def f(a, b=1): ...", "f(1)", None),
        ("def f(a, b=1): ...", "f(1, 2)", None),
        ("def f(a, b=1): ...", "f(1, 2, 3)", "positional"),
        ("def f(a, *, b=1): ...", "f(1, 2)", "positional"),
        ("def f(a, *, b=1): ...", "f(1, b=2)", None),
        ("def f(a, *, b): ...", "f(1)", "missing required keyword-only argument 'b'"),
        ("def f(a, b): ...", "f(1)", "missing required argument 'b'"),
        ("def f(a): ...", "f(1, c=2)", "unexpected keyword argument 'c'"),
        ("def f(a, b=1): ...", "f(1, a=2)", "both positionally and by keyword"),
        ("def f(a, **kw): ...", "f(1, c=2)", None),
        ("def f(a, *args): ...", "f(1, 2, 3)", None),
        ("def f(a, b): ...", "f(*xs)", None),
        ("def f(a, b): ...", "f(**kw)", None),
    ],
)
def test_call_compat(sig, call, expected):
    why = call_compat(_call(call), _sig(sig))
    if expected is None:
        assert why is None
    else:
        assert why is not None and expected in why


def test_a_bound_method_does_not_count_self():
    params = _sig("def m(self, x): ...", skip_first=True)
    assert call_compat(_call("obj.m(1)"), params) is None
    assert call_compat(_call("obj.m()"), params) is not None


@pytest.mark.parametrize(
    ("before", "after", "breaking"),
    [
        ("def f(a, b=1): ...", "def f(a, b=1, c=2): ...", False),
        ("def f(a, b=1): ...", "def f(a, b=1, *, c): ...", True),
        ("def f(a, b=1): ...", "def f(a, *, b=1): ...", True),
        ("def f(a, b): ...", "def f(b, a): ...", True),
        ("def f(a, b): ...", "def f(a): ...", True),
        ("def f(a, b): ...", "def f(a, b, **kw): ...", False),
    ],
)
def test_signature_breaking(before, after, breaking):
    assert signature_breaking(_sig(before), _sig(after)) is breaking


# ------------------------------------------------------------------- coverage ----


def test_changed_lines_use_both_coordinate_systems():
    before = "a = 1\nb = 2\nc = 3\n"
    after = "a = 1\nnew = 0\nb = 22\nc = 3\n"

    old, new = changed_lines(before, after)

    assert old == {2}
    assert new == {2, 3}


def test_a_changed_line_no_test_executed_is_reported():
    before = "def f(x):\n    if x:\n        return 1\n    return 2\n"
    after = "def f(x):\n    if x:\n        return 10\n    return 20\n"
    cov = parse_coverage(
        '{"files": {"m.py": {"executed_lines": [1, 2, 3], "missing_lines": [4]}}}'
    )

    gaps = unexecuted_changed_lines({"m.py": before}, {"m.py": after}, {"m.py"}, cov)

    assert gaps == {"m.py": [4]}


def test_comments_and_blank_lines_are_never_reported_as_gaps():
    before = "x = 1\n"
    after = "x = 1\n\n# a note\n"
    cov = parse_coverage('{"files": {"m.py": {"executed_lines": [1], "missing_lines": []}}}')

    assert unexecuted_changed_lines({"m.py": before}, {"m.py": after}, {"m.py"}, cov) == {}


def test_no_coverage_means_no_claim():
    assert unexecuted_changed_lines({}, {}, {"m.py"}, None) == {}
    assert parse_coverage("not json") is None
    assert parse_coverage('{"files": {}}') is None


def test_module_names_follow_the_path():
    assert module_name_of("src/auth/session.py") == "src.auth.session"
    assert module_name_of("src/auth/__init__.py") == "src.auth"


def test_every_finding_has_a_known_severity():
    report = analyse_impact(_inputs(unresolved=[CallRef("src/registry.py", 7, "fn")],
                                    baseline_coverage={"src/registry.py": (set(), {7})}))
    assert {f.severity for f in report.findings} <= {INFO, WARN, BLOCK}
