"""Artifact cleanup: removing the mess the refactor itself just made.

SWE Atlas makes Artifact Cleanup a must-have rubric — old definitions, helpers
and now-unused imports fully removed — and it was previously impossible here for
a structural reason worth naming. Gate 1 scopes each task to its single
`target_file`, so an import that became unused in a *different* file cannot be
removed by the task that orphaned it. The gate is right; the architecture was
missing a step.

The step is a second planning pass, not a cleanup agent. Detection is entirely
deterministic — a reparse of the integrated tree plus reference counting — and
the model only ever performs the edit it is handed. Everything after detection
reuses machinery that already exists: cleanup tasks go through the same Coder,
the same gates and the same candidate race.

What this deliberately does not do is hunt dead code across the repository.
Static reachability in Python is unsound: entry points, plugin registries,
dynamic imports and framework magic all make live code look unreferenced. An
agent that deletes code it believes is dead, in a language where reachability
cannot be proven, is the most destructive thing a tool like this could do, and it
would contradict the one thing the product claims. Every guard below exists to
keep detection refactor-local.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from principal.db.store import Store


@dataclass(frozen=True, slots=True)
class CleanupTask:
    target_file: str
    unused_imports: tuple[str, ...] = ()
    orphaned_symbols: tuple[str, ...] = ()
    depends_on: tuple[int, ...] = field(default_factory=tuple)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted({*self.unused_imports, *self.orphaned_symbols}))

    @property
    def instruction(self) -> str:
        parts = []
        if self.unused_imports:
            parts.append(f"unused import(s) {', '.join(sorted(self.unused_imports))}")
        if self.orphaned_symbols:
            parts.append(f"now-unreferenced definition(s) {', '.join(sorted(self.orphaned_symbols))}")
        return (
            f"Remove {' and '.join(parts)} from this file. They became unreferenced as a"
            " result of the migration. Change nothing else: do not reformat, do not"
            " reorder surviving imports, and do not alter any remaining behaviour."
        )

    @property
    def acceptance(self) -> str:
        return (
            f"declares removal of {', '.join(self.names)}; the file still imports everything"
            " it uses and the test suite is unchanged"
        )


# ------------------------------------------------------- reference counting ---


class _References(ast.NodeVisitor):
    """Every name this module *uses*, as opposed to defines.

    Attribute access counts the attribute name as well as the base, because
    `mod.helper()` is a use of `helper` for our purposes even though the binding
    it resolves through is `mod`.
    """

    def __init__(self) -> None:
        self.used: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if isinstance(node.ctx, ast.Load):
            self.used.add(node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        self.used.add(node.attr)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        # A string-keyed registry looks exactly like a comment to a reference
        # counter. Treating every string literal as a potential reference is
        # what stops this pass proposing to delete a registered handler.
        if isinstance(node.value, str):
            self.used.add(node.value)
            for part in node.value.replace(":", ".").split("."):
                if part:
                    self.used.add(part)
        self.generic_visit(node)


def _references(source: str) -> set[str]:
    # Unparseable means unknowable. Returning an empty set would read as
    # "nothing is referenced here", which is the direction that deletes live
    # code, so the SyntaxError propagates and callers treat it as a reason to
    # abandon the whole pass rather than to conclude anything.
    tree = ast.parse(source)
    visitor = _References()
    visitor.visit(tree)
    return visitor.used


def unused_imports(source: str) -> list[str]:
    """Imports bound in this module and never read. The F401 case, computed
    directly so the pass carries no linter dependency into the sandbox.

    Conservative by construction: a module with a star import, an `__all__` that
    cannot be read statically, or any syntax error yields nothing at all.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    bound: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".", 1)[0]
                bound[name] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    return []  # a star import makes every name unknowable
                bound[alias.asname or alias.name] = node.lineno

    visitor = _References()
    visitor.visit(tree)
    used = visitor.used

    # An import re-exported through __all__ is used, by definition, by whoever
    # imports this module.
    exported = _dunder_all(tree)

    # `import a.b` binds `a` but the code reads `a.b.thing`; the visitor records
    # both, so no special case is needed beyond not counting the import itself.
    return sorted(
        name for name, _line in bound.items()
        if name not in used and name not in exported and not name.startswith("_")
    )


def _dunder_all(tree: ast.Module) -> set[str]:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                if isinstance(node.value, (ast.List, ast.Tuple)):
                    return {
                        e.value for e in node.value.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)
                    }
    return set()


def _module_level_defs(source: str) -> dict[str, int]:
    """Top-level function and class definitions, name -> line."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    out: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[node.name] = node.lineno
    return out


# --------------------------------------------------------------- detection ---


def orphaned_symbols(
    store: Store, graph_id: str, tree: dict[str, str], touched: set[str],
) -> dict[str, list[str]]:
    """Private definitions in touched files that nothing in the tree still names.

    Five guards, each closing a way this could delete something live:

      only definitions in files this job actually changed — not a repo audit;
      only private names, since anything exported may have callers outside the
        snapshot and its removal would have to be declared by the plan instead;
      only names absent from every string literal in the tree, which is how a
        string-keyed registry entry survives;
      only names with no test edge, because a symbol under test is by definition
        reachable;
      only names that appear in no heuristic or dynamic call edge, which is the
        set of call sites the graph already admits it cannot resolve.
    """
    dynamic_names = _unresolvable_names(store, graph_id)
    out: dict[str, list[str]] = {}

    for path in sorted(touched):
        source = tree.get(path)
        if source is None or not path.endswith(".py"):
            continue

        candidates = {
            name: line for name, line in _module_level_defs(source).items()
            if name.startswith("_") and not name.startswith("__")
        }
        if not candidates:
            continue

        exported = _dunder_all(_safe_parse(source))
        covered = _covered_names(store, graph_id, path)

        referenced: set[str] = set()
        for other_path, other_source in tree.items():
            if not other_path.endswith(".py"):
                continue
            try:
                used = _references(other_source)
            except SyntaxError:
                # One unparseable file anywhere makes every conclusion unsafe,
                # because the names it uses are unknown.
                return {}
            if other_path == path:
                # Within the defining file, a name's own definition does not
                # count as a use of it.
                used = used - {n for n in candidates if _only_self_reference(other_source, n)}
            referenced |= used

        orphans = sorted(
            name for name in candidates
            if name not in referenced
            and name not in exported
            and name not in covered
            and name not in dynamic_names
        )
        if orphans:
            out[path] = orphans
    return out


def _safe_parse(source: str) -> ast.Module:
    try:
        return ast.parse(source)
    except SyntaxError:
        return ast.Module(body=[], type_ignores=[])


def _only_self_reference(source: str, name: str) -> bool:
    """True when the only occurrence of `name` is its own def/class statement."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    uses = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load):
            uses += 1
        elif isinstance(node, ast.Attribute) and node.attr == name:
            uses += 1
    return uses == 0


def _unresolvable_names(store: Store, graph_id: str) -> set[str]:
    rows = store.q(
        "SELECT DISTINCT callee_name FROM call_edge"
        " WHERE graph_id = ? AND (confidence = 'heuristic' OR dynamic = 1)",
        (graph_id,),
    )
    return {r["callee_name"] for r in rows}


def _covered_names(store: Store, graph_id: str, path: str) -> set[str]:
    rows = store.q(
        "SELECT DISTINCT s.name AS name FROM test_edge t"
        " JOIN symbol s ON s.id = t.symbol_id"
        " JOIN file f ON f.id = s.file_id"
        " WHERE t.graph_id = ? AND f.path = ?",
        (graph_id, path),
    )
    return {r["name"] for r in rows}


def plan_cleanup(
    store: Store, graph_id: str, tree: dict[str, str], touched: set[str],
    *, depends_on: tuple[int, ...] = (),
) -> list[CleanupTask]:
    """The whole pass. Deterministic, no model call, returns tasks to schedule.

    `tree` is the integrated repository as text: every file's post-patch content
    for the files that changed, and its original content for everything else.
    Passing only the touched files would make every reference outside them
    invisible and would propose deleting most of the codebase.
    """
    orphans = orphaned_symbols(store, graph_id, tree, touched)

    tasks: list[CleanupTask] = []
    for path in sorted(touched):
        source = tree.get(path)
        if source is None or not path.endswith(".py"):
            continue
        imports = tuple(unused_imports(source))
        symbols = tuple(orphans.get(path, ()))
        if imports or symbols:
            tasks.append(
                CleanupTask(
                    target_file=path, unused_imports=imports, orphaned_symbols=symbols,
                    depends_on=depends_on,
                )
            )
    return tasks
