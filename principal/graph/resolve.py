"""Static versus heuristic resolution.

Resolution has exactly three outcomes and they map onto the `confidence` column.
The distinction is the honest core of the whole product: an edge is either proved
or admitted to be a guess, and guesses reach the PR risk list rather than being
quietly folded into a completeness claim.
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from pathlib import PurePosixPath

from principal.graph.parse import CallSite, ImportBinding, ParsedFile

STATIC = "static"
HEURISTIC = "heuristic"

# An unresolved call to `int` is not a migration risk. Filtering these is what
# keeps the PR risk list short enough that a reviewer reads it.
BUILTIN_NAMES = frozenset(dir(builtins)) | frozenset(
    {
        "console", "require", "window", "document", "Promise", "JSON", "Object",
        "Array", "String", "Number", "Boolean", "Math", "Date", "Error", "fetch",
        "setTimeout", "clearTimeout", "setInterval", "describe", "it", "expect",
        "super", "this",
    }
)


@dataclass(slots=True)
class SymbolKey:
    file: str
    name: str


def module_to_paths(module: str, importer: str, known: set[str], lang: str) -> list[str]:
    """Candidate files a module specifier could name, most likely first."""
    if not module:
        return []
    if lang == "python":
        return _python_module_paths(module, importer, known)
    return _ts_module_paths(module, importer, known)


def _python_module_paths(module: str, importer: str, known: set[str]) -> list[str]:
    candidates: list[str] = []
    if module.startswith("."):
        up = len(module) - len(module.lstrip("."))
        rest = module[up:]
        base = PurePosixPath(importer).parent
        for _ in range(up - 1):
            base = base.parent
        stem = base / rest.replace(".", "/") if rest else base
        candidates += [f"{stem}.py", f"{stem}/__init__.py"]
    else:
        dotted = module.replace(".", "/")
        candidates += [f"{dotted}.py", f"{dotted}/__init__.py"]
        # Repositories commonly nest the package under src/ or a project dir.
        for prefix in ("src", "lib", importer.split("/", 1)[0]):
            if prefix and "/" not in prefix:
                candidates += [f"{prefix}/{dotted}.py", f"{prefix}/{dotted}/__init__.py"]
        # And equally commonly the import is absolute while the file is not nested.
        tail = dotted.split("/", 1)[-1]
        if tail != dotted:
            candidates += [f"{tail}.py", f"{tail}/__init__.py"]
    seen: list[str] = []
    for c in candidates:
        c = str(PurePosixPath(c))
        if c in known and c not in seen:
            seen.append(c)
    return seen


def _ts_module_paths(module: str, importer: str, known: set[str]) -> list[str]:
    if not module.startswith("."):
        return []
    base = (PurePosixPath(importer).parent / module).as_posix()
    base = str(PurePosixPath(base))
    candidates = [
        base, f"{base}.ts", f"{base}.tsx", f"{base}/index.ts", f"{base}/index.tsx",
    ]
    return [c for c in candidates if c in known]


class Resolver:
    """Holds the complete symbol table. Built after pass 1, used during pass 2."""

    def __init__(self, files: dict[str, ParsedFile]):
        self.files = files
        self.known = set(files)
        # (file, name) -> symbol id, filled by the builder as rows are inserted
        self.symbol_ids: dict[tuple[str, str], int] = {}
        self.by_name: dict[str, list[tuple[str, str]]] = {}

    def register(self, file: str, name: str, symbol_id: int) -> None:
        self.symbol_ids[(file, name)] = symbol_id
        self.by_name.setdefault(name, []).append((file, name))

    # ------------------------------------------------------------ imports --

    def resolve_import(self, binding: ImportBinding, importer: str) -> int | None:
        parsed = self.files[importer]
        for path in module_to_paths(binding.source_module, importer, self.known, parsed.lang):
            if binding.symbol_name == "*":
                return None
            sid = self.symbol_ids.get((path, binding.symbol_name))
            if sid is not None:
                return sid
        return None

    def resolve_import_file(self, binding: ImportBinding, importer: str) -> str | None:
        """The file a specifier names, whether or not a symbol inside it resolved.

        `from src.auth import session` binds a module. No symbol resolves, but the
        importing file can still reach everything in src/auth/session.py through
        attribute access or dynamic dispatch, so the edge has to exist.
        """
        parsed = self.files[importer]
        combined = (
            f"{binding.source_module}.{binding.symbol_name}"
            if parsed.lang == "python" and binding.symbol_name not in {"*", "default"}
            else binding.source_module
        )
        for module in (combined, binding.source_module):
            for path in module_to_paths(module, importer, self.known, parsed.lang):
                return path
        return None

    # -------------------------------------------------------------- calls --

    def local_bindings(self, file: str) -> dict[str, int]:
        """Names this file can call and where each one actually lives."""
        out: dict[str, int] = {}
        parsed = self.files[file]
        for d in parsed.definitions:
            sid = self.symbol_ids.get((file, d.name))
            if sid is not None:
                out[d.name] = sid
        for imp in parsed.imports:
            sid = self.resolve_import(imp, file)
            if sid is not None:
                out[imp.local_name] = sid
        return out

    def module_aliases(self, file: str) -> dict[str, str]:
        """`import auth.session as s` and `from . import session` both let a call
        read `s.create(...)`. Map the local alias to the file it names."""
        out: dict[str, str] = {}
        parsed = self.files[file]
        for imp in parsed.imports:
            module = imp.source_module
            if imp.symbol_name not in {"*", "default"}:
                # `from x.y import session` where session is itself a module
                module = f"{module}.{imp.symbol_name}" if parsed.lang == "python" else module
            for path in module_to_paths(module, file, self.known, parsed.lang):
                out[imp.local_name] = path
                break
            else:
                for path in module_to_paths(imp.source_module, file, self.known, parsed.lang):
                    out.setdefault(imp.local_name, path)
                    break
        return out

    def resolve_callee(
        self, call: CallSite, file: str, bindings: dict[str, int], aliases: dict[str, str]
    ) -> tuple[int | None, str]:
        """Returns (callee_symbol_id, confidence)."""
        if call.dynamic:
            sid = self._unique_by_name(call.callee_name)
            return sid, HEURISTIC

        # receiver.method() where the receiver is a module this file imported
        if call.receiver is not None:
            target_file = aliases.get(call.receiver.split(".", 1)[0])
            if target_file is not None:
                sid = self.symbol_ids.get((target_file, call.callee_name))
                if sid is not None:
                    return sid, STATIC
            # receiver is an instance: the method name is all we have
            sid = self._unique_by_name(call.callee_name)
            return sid, HEURISTIC

        sid = bindings.get(call.callee_name)
        if sid is not None:
            return sid, STATIC

        return self._unique_by_name(call.callee_name), HEURISTIC

    def is_dynamic(self, call: CallSite, callee_id: int | None) -> bool:
        """Whether this call dispatches at runtime in a way nothing static can prove.

        True for getattr, registry lookups and calls on a value rather than a name.
        False for anything that resolved, anything with a receiver that is just an
        object, and every builtin — those are noise, not risk.
        """
        if call.dynamic:
            return call.callee_name not in BUILTIN_NAMES
        if callee_id is not None or call.receiver is not None:
            return False
        if call.callee_name in BUILTIN_NAMES:
            return False
        return call.callee_name not in self.by_name

    def _unique_by_name(self, name: str) -> int | None:
        matches = self.by_name.get(name)
        if not matches:
            return None
        if len(matches) == 1:
            return self.symbol_ids[matches[0]]
        # Ambiguous: prefer a non-test definition, deterministically.
        ranked = sorted(matches, key=lambda m: (self.files[m[0]].is_test, m[0]))
        return self.symbol_ids[ranked[0]]
