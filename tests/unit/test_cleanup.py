"""Artifact cleanup: what the refactor itself orphaned, and only that.

Every guard here exists to keep detection refactor-local. Static reachability in
Python is unsound, so the failure mode this file protects against is not "missed
an orphan" — it is "proposed deleting something that is actually still alive",
which for this specific pass would be the worst thing a tool that only claims to
make changes it can verify could do.
"""

from __future__ import annotations

from principal.orchestrator.cleanup import (
    orphaned_symbols,
    plan_cleanup,
    unused_imports,
)


class _FakeStore:
    """Enough of Store's surface for detection: no heuristic call edges, no
    test coverage, by default — each test opts a symbol into "protected"
    explicitly, the same way the real store would via real rows."""

    def __init__(self, dynamic_names: set[str] | None = None, covered: dict[str, set[str]] | None = None):
        self._dynamic = dynamic_names or set()
        self._covered = covered or {}

    def q(self, sql: str, args: tuple = ()):
        if "call_edge" in sql:
            return [{"callee_name": n} for n in self._dynamic]
        if "test_edge" in sql:
            path = args[1]
            return [{"name": n} for n in self._covered.get(path, set())]
        return []


# ------------------------------------------------------------ unused_imports --


def test_unused_import_is_detected():
    src = "import os\n\n\ndef f():\n    return 1\n"
    assert unused_imports(src) == ["os"]


def test_used_import_is_not_flagged():
    src = "import os\n\n\ndef f():\n    return os.getcwd()\n"
    assert unused_imports(src) == []


def test_from_import_unused_is_detected():
    src = "from collections import OrderedDict\n\n\ndef f():\n    return 1\n"
    assert unused_imports(src) == ["OrderedDict"]


def test_aliased_import_tracks_the_local_name():
    src = "import numpy as np\n\n\ndef f():\n    return 1\n"
    assert unused_imports(src) == ["np"]
    used = "import numpy as np\n\n\ndef f():\n    return np.zeros(1)\n"
    assert unused_imports(used) == []


def test_star_import_makes_every_name_unknowable():
    src = "from os import *\n\n\ndef f():\n    return getcwd()\n"
    assert unused_imports(src) == []


def test_dunder_all_export_counts_as_used():
    src = "import os\n\n__all__ = ['os']\n"
    assert unused_imports(src) == []


def test_syntax_error_yields_nothing():
    assert unused_imports("import os\ndef f(:\n") == []


def test_private_names_are_not_reported_as_unused_imports():
    """Leading-underscore imports are an internal convention this pass leaves
    alone; the orphan side of detection handles private definitions instead."""
    src = "import os as _os\n\n\ndef f():\n    return 1\n"
    assert unused_imports(src) == []


# --------------------------------------------------------- orphaned_symbols --


def test_orphaned_private_helper_is_detected():
    store = _FakeStore()
    tree = {
        "a.py": "def _helper():\n    return 1\n\n\ndef public():\n    return 2\n",
    }
    out = orphaned_symbols(store, "g", tree, {"a.py"})
    assert out == {"a.py": ["_helper"]}


def test_helper_referenced_elsewhere_is_not_orphaned():
    store = _FakeStore()
    tree = {
        "a.py": "def _helper():\n    return 1\n",
        "b.py": "from a import _helper\n\n\ndef f():\n    return _helper()\n",
    }
    out = orphaned_symbols(store, "g", tree, {"a.py"})
    assert out == {}


def test_string_keyed_registry_entry_is_not_orphaned():
    """A registry looks up handlers by name at runtime; a reference counter that
    only understands `ast.Name` would misclassify every registered handler as
    dead the moment its registration call is the only remaining mention."""
    store = _FakeStore()
    tree = {
        "a.py": "def _on_create():\n    return 1\n",
        "reg.py": 'HANDLERS = {"create": "_on_create"}\n',
    }
    out = orphaned_symbols(store, "g", tree, {"a.py"})
    assert out == {}


def test_covered_symbol_is_never_orphaned():
    """A symbol under test is reachable by definition, whatever a static
    reference count says."""
    store = _FakeStore(covered={"a.py": {"_helper"}})
    tree = {"a.py": "def _helper():\n    return 1\n"}
    out = orphaned_symbols(store, "g", tree, {"a.py"})
    assert out == {}


def test_dynamically_dispatched_name_is_never_orphaned():
    store = _FakeStore(dynamic_names={"_helper"})
    tree = {"a.py": "def _helper():\n    return 1\n"}
    out = orphaned_symbols(store, "g", tree, {"a.py"})
    assert out == {}


def test_exported_via_dunder_all_is_not_orphaned():
    store = _FakeStore()
    tree = {"a.py": "def _helper():\n    return 1\n\n__all__ = ['_helper']\n"}
    out = orphaned_symbols(store, "g", tree, {"a.py"})
    assert out == {}


def test_public_symbols_are_never_candidates():
    """Only private names are considered at all — an exported symbol may have
    callers outside the snapshot, and its removal has to be declared by the
    plan, not inferred by this pass."""
    store = _FakeStore()
    tree = {"a.py": "def helper():\n    return 1\n"}
    out = orphaned_symbols(store, "g", tree, {"a.py"})
    assert out == {}


def test_a_syntax_error_anywhere_in_the_tree_aborts_the_whole_pass():
    """One unparseable file makes every reference count unsafe, because the
    names it uses are unknown. Returning partial results here is the direction
    that could delete something live."""
    store = _FakeStore()
    tree = {
        "a.py": "def _helper():\n    return 1\n",
        "b.py": "def f(:\n",
    }
    assert orphaned_symbols(store, "g", tree, {"a.py"}) == {}


def test_untouched_files_are_never_candidates():
    store = _FakeStore()
    tree = {"a.py": "def _helper():\n    return 1\n"}
    assert orphaned_symbols(store, "g", tree, set()) == {}


# ------------------------------------------------------------- plan_cleanup --


def test_plan_cleanup_produces_one_task_per_file_with_something_to_remove():
    store = _FakeStore()
    tree = {
        "a.py": "import os\n\n\ndef _helper():\n    return 1\n\n\ndef public():\n    return os.getcwd()\n",
    }
    tasks = plan_cleanup(store, "g", tree, {"a.py"})
    assert len(tasks) == 1
    assert tasks[0].target_file == "a.py"
    assert "_helper" in tasks[0].orphaned_symbols


def test_plan_cleanup_produces_nothing_when_the_file_is_clean():
    store = _FakeStore()
    tree = {"a.py": "def public():\n    return 1\n"}
    assert plan_cleanup(store, "g", tree, {"a.py"}) == []


def test_cleanup_task_declares_exactly_what_it_removes():
    store = _FakeStore()
    tree = {"a.py": "import os\n\n\ndef public():\n    return 1\n"}
    tasks = plan_cleanup(store, "g", tree, {"a.py"})
    assert tasks[0].names == ("os",)
    assert "os" in tasks[0].acceptance
