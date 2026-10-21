"""Gate 4b: predicted impact against actual impact.

The blast radius is computed before anything is edited. Until now it was a
visualisation: it told a reviewer which files might be affected and then played no
part in deciding whether the resulting patch was safe. This module closes that
gap. It takes the prediction and the patched tree, and answers the questions a
test run cannot:

  did the patch stay inside the scope that was approved;
  does every file with a known call site have a task, or an explanation;
  does every known caller still fit the interface the patch leaves behind;
  which call paths could the graph not resolve, and did any test run them;
  does a string still name something the patch removed.

It is deterministic and uses no model, so it belongs in the accept path. It is
also honest about its reach: it understands Python call shapes and string-keyed
lookups, it does not understand reflection it cannot see, and where it cannot
check a dependency it says so with a finding that stops the run rather than
passing it silently.

Severity is the whole contract:

  info   recorded for the evidence bundle, never shown as a concern
  warn   behaviour that remains unverified; the job may continue but the outcome
         cannot be VERIFIED and a human has to acknowledge it
  block  the patch is known to be wrong or could not be checked; nothing is
         offered for approval
"""

from __future__ import annotations

import ast
import difflib
import json
from dataclasses import dataclass, field
from typing import Any

INFO = "info"
WARN = "warn"
BLOCK = "block"

# Kinds whose block means "this could not be checked" rather than "this patch is
# defective". The distinction decides the outcome: UNVERIFIED against FAILED.
UNCHECKABLE_KINDS = frozenset({"uncheckable"})


@dataclass(slots=True)
class Finding:
    kind: str
    severity: str
    detail: str
    file: str | None = None
    line: int | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "severity": self.severity, "detail": self.detail}
        if self.file is not None:
            out["file"] = self.file
        if self.line is not None:
            out["line"] = self.line
        if self.data:
            out["data"] = self.data
        return out


@dataclass(frozen=True, slots=True)
class CallRef:
    file: str
    line: int
    name: str


@dataclass(slots=True)
class StaleRef:
    """A string that still names something the patch removed."""

    file: str
    line: int
    name: str
    strength: str          # "strong": getattr(module, "name"); "medium": registry beside a dynamic lookup
    module: str            # dotted module the lookup resolves to

    def to_payload(self) -> dict[str, Any]:
        return {"file": self.file, "line": self.line, "name": self.name,
                "strength": self.strength, "module": self.module}


@dataclass(slots=True)
class ImpactInputs:
    target_name: str
    target_path: str
    target_kind: str
    target_parent: str | None
    radius_files: set[str]
    planned_files: set[str]
    unhandled: list[str]
    static_call_sites: list[CallRef]
    unresolved: list[CallRef]
    baseline_tree: dict[str, str]
    patched_tree: dict[str, str]
    touched_files: set[str]
    declared_removals: set[str]
    # path -> (executed lines, missing lines) from the baseline suite. None means
    # coverage was not available at all, which is reported rather than assumed.
    baseline_coverage: dict[str, tuple[set[int], set[int]]] | None
    discarded_files: set[str] = field(default_factory=set)
    consumers: dict[tuple[str, str], list[str]] = field(default_factory=dict)


@dataclass(slots=True)
class ImpactReport:
    expected_files: list[str] = field(default_factory=list)
    planned_files: list[str] = field(default_factory=list)
    actual_files: list[str] = field(default_factory=list)
    unexpected_files: list[str] = field(default_factory=list)
    untouched_expected_files: list[str] = field(default_factory=list)
    expected_call_sites: int = 0
    call_sites_changed: int = 0
    call_sites_unchanged: list[dict[str, Any]] = field(default_factory=list)
    target_signature: dict[str, Any] = field(default_factory=dict)
    interface_changes: list[dict[str, Any]] = field(default_factory=list)
    dynamic_paths: list[dict[str, Any]] = field(default_factory=list)
    stale_references: list[StaleRef] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def blocks(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == BLOCK]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == WARN]

    @property
    def blocked(self) -> bool:
        return bool(self.blocks)

    def to_payload(self) -> dict[str, Any]:
        return {
            "expected_files": self.expected_files,
            "planned_files": self.planned_files,
            "actual_files": self.actual_files,
            "unexpected_files": self.unexpected_files,
            "untouched_expected_files": self.untouched_expected_files,
            "expected_call_sites": self.expected_call_sites,
            "call_sites_changed": self.call_sites_changed,
            "call_sites_unchanged": self.call_sites_unchanged,
            "target_signature": self.target_signature,
            "interface_changes": self.interface_changes,
            "dynamic_paths": self.dynamic_paths,
            "stale_references": [s.to_payload() for s in self.stale_references],
            "findings": [f.to_payload() for f in self.findings],
            "blocked": self.blocked,
            "warnings": len(self.warnings),
        }


# ----------------------------------------------------------------- signatures --


@dataclass(slots=True)
class Params:
    positional: list[tuple[str, bool]]   # (name, has_default), positional-only included
    posonly: int
    kwonly: list[tuple[str, bool]]
    vararg: bool
    kwarg: bool
    text: str

    def positional_names(self) -> list[str]:
        return [n for n, _ in self.positional]

    def keyword_names(self) -> set[str]:
        return {n for n, _ in self.positional[self.posonly:]} | {n for n, _ in self.kwonly}


def _params(fn: ast.FunctionDef | ast.AsyncFunctionDef, *, skip_first: bool) -> Params:
    a = fn.args
    pos = [*a.posonlyargs, *a.args]
    defaults = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
    positional = [(p.arg, d is not None) for p, d in zip(pos, defaults, strict=True)]
    posonly = len(a.posonlyargs)
    if skip_first and positional:
        positional = positional[1:]
        posonly = max(0, posonly - 1)
    kwonly = [(p.arg, d is not None) for p, d in zip(a.kwonlyargs, a.kw_defaults, strict=True)]
    return Params(positional, posonly, kwonly, a.vararg is not None, a.kwarg is not None,
                  ast.unparse(a))


def _find_function(
    tree: ast.AST, name: str, parent: str | None,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    scopes: list[ast.AST] = [tree]
    if parent:
        scopes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == parent]
    for scope in scopes:
        for node in getattr(scope, "body", []):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
                return node
    return None


def _module_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
    return names


def call_compat(call: ast.Call, params: Params) -> str | None:
    """Why this call would not be accepted by `params`, or None if it would be.

    Anything that cannot be decided statically — a starred argument, a keyword
    splat — is treated as compatible. That direction is deliberate: this check
    blocks a patch, so it must only ever do so on a definite mismatch.
    """
    if any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords):
        return None
    n_pos = len(call.args)
    keywords = [k.arg for k in call.keywords if k.arg is not None]

    if n_pos > len(params.positional) and not params.vararg:
        accepts = len(params.positional)
        return f"passes {n_pos} positional argument(s) but the signature accepts {accepts}"

    consumed = set(params.positional_names()[:n_pos])
    for kw in keywords:
        if kw in consumed:
            return f"'{kw}' is given both positionally and by keyword"
        if kw not in params.keyword_names() and not params.kwarg:
            return f"unexpected keyword argument '{kw}'"

    supplied = consumed | set(keywords)
    for name, has_default in params.positional[n_pos:]:
        if not has_default and name not in supplied:
            return f"missing required argument '{name}'"
    for name, has_default in params.kwonly:
        if not has_default and name not in supplied:
            return f"missing required keyword-only argument '{name}'"
    return None


def signature_breaking(before: Params, after: Params) -> bool:
    """Could a call that was valid before be invalid now."""
    before_names = {n for n, _ in [*before.positional, *before.kwonly]}
    after_names = {n for n, _ in [*after.positional, *after.kwonly]}
    if before_names - after_names and not after.kwarg:
        return True
    for name, has_default in [*after.positional, *after.kwonly]:
        if name not in before_names and not has_default:
            return True
    bp, ap = before.positional_names(), after.positional_names()
    for i, name in enumerate(bp):
        if name in ap and ap.index(name) != i:
            return True
        if name not in ap and name in after.keyword_names():
            return True  # positional became keyword-only
    return len(ap) < len(bp) and not after.vararg


# ------------------------------------------------------------------- changes --


def changed_lines(before: str, after: str) -> tuple[set[int], set[int]]:
    """(1-based lines of `before` that were changed or removed, 1-based lines of
    `after` that were added or changed). Line numbers shift in every refactor, so
    both coordinate systems are needed and neither can stand in for the other."""
    a, b = before.splitlines(), after.splitlines()
    old: set[int] = set()
    new: set[int] = set()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        old.update(range(i1 + 1, i2 + 1))
        new.update(range(j1 + 1, j2 + 1))
    return old, new


def parse_coverage(coverage_json: str) -> dict[str, tuple[set[int], set[int]]] | None:
    """path -> (executed lines, missing lines). None when there is no usable
    coverage at all, so the caller can say 'unmeasured' instead of 'zero'."""
    try:
        data = json.loads(coverage_json or "{}")
    except json.JSONDecodeError:
        return None
    files = data.get("files") or {}
    if not files:
        return None
    out: dict[str, tuple[set[int], set[int]]] = {}
    for path, entry in files.items():
        out[_norm(path)] = (set(entry.get("executed_lines") or []), set(entry.get("missing_lines") or []))
    return out


def _norm(path: str) -> str:
    p = path.replace("\\", "/").lstrip("./")
    for prefix in ("work/", "/work/"):
        if p.startswith(prefix):
            p = p[len(prefix):]
    return p


def unexecuted_changed_lines(
    baseline_tree: dict[str, str], patched_tree: dict[str, str], touched: set[str],
    final_coverage: dict[str, tuple[set[int], set[int]]] | None,
) -> dict[str, list[int]]:
    """Changed lines that a statement-level coverage run says no test executed.

    Comments, blank lines and docstring continuations are in neither of
    coverage's two lists, so they are ignored rather than reported as gaps.
    """
    out: dict[str, list[int]] = {}
    if final_coverage is None:
        return out
    for path in sorted(touched):
        cov = final_coverage.get(path)
        if cov is None:
            continue
        executed, missing = cov
        _old, new = changed_lines(baseline_tree.get(path, ""), patched_tree.get(path, ""))
        gaps = sorted(line for line in new if line in missing and line not in executed)
        if gaps:
            out[path] = gaps
    return out


# ------------------------------------------------------------- stale strings --


def module_name_of(path: str) -> str:
    p = path[:-3] if path.endswith(".py") else path
    if p.endswith("/__init__"):
        p = p[: -len("/__init__")]
    return p.replace("/", ".")


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    """local name -> dotted module it is bound to, for module imports only."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    out[alias.asname] = alias.name
                else:
                    out[alias.name.split(".")[0]] = alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                out[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return out


def _receiver_module(expr: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(expr, ast.Name):
        return aliases.get(expr.id)
    if isinstance(expr, ast.Attribute):
        base = _receiver_module(expr.value, aliases)
        return f"{base}.{expr.attr}" if base else None
    return None


def find_stale_references(
    patched_tree: dict[str, str], removed: set[str], target_module: str,
) -> list[StaleRef]:
    """Strings in the patched tree that name a removed symbol in a position where
    they are looked up on the module that used to define it.

    Two shapes, because they carry different certainty:

      getattr(mod, "old_name")   strong — the lookup names the symbol outright
      a file that does getattr(mod, <something computed>) and also holds the
      literal "old_name"         medium — the classic string-keyed registry

    Anything else is ignored. A bare string equal to a common word is far more
    often a label than a lookup, and a gate that blocks on labels teaches people
    to override it.
    """
    out: list[StaleRef] = []
    for path, text in sorted(patched_tree.items()):
        if not path.endswith(".py") or "/tests/" in f"/{path}" or path.startswith("tests/"):
            continue
        tree = _parse(text)
        if tree is None:
            continue
        aliases = _import_aliases(tree)

        computed_lookup = False
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in {"getattr", "hasattr"} and len(node.args) >= 2):
                continue
            if _receiver_module(node.args[0], aliases) != target_module:
                continue
            arg = node.args[1]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if arg.value in removed:
                    out.append(StaleRef(path, node.lineno, arg.value, "strong", target_module))
            else:
                computed_lookup = True

        if computed_lookup:
            seen = {(s.file, s.line, s.name) for s in out}
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in removed:
                    key = (path, node.lineno, node.value)
                    if key not in seen:
                        seen.add(key)
                        out.append(StaleRef(path, node.lineno, node.value, "medium", target_module))
    return out


# ------------------------------------------------------------------ analysis --


def _call_exprs_at(tree: ast.AST, lines: set[int]) -> set[str]:
    return {
        ast.unparse(n.func) for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n, "lineno", None) in lines
    }


def analyse_impact(inp: ImpactInputs) -> ImpactReport:
    rep = ImpactReport(
        expected_files=sorted(inp.radius_files), planned_files=sorted(inp.planned_files),
        actual_files=sorted(inp.touched_files),
    )
    add = rep.findings.append

    # 1. Scope. Gate 1 stops this per candidate; seeing it here would mean a patch
    # got past that gate, which is a reason to refuse rather than to note.
    rep.unexpected_files = sorted(inp.touched_files - inp.radius_files)
    for path in rep.unexpected_files:
        add(Finding("out_of_scope", BLOCK, f"the patch changes {path}, which is outside the approved"
                    " blast radius", file=path))

    # 2. Plan completeness: a file with a known static call site and neither a
    # task nor an explanation is a caller nobody looked at.
    call_files = sorted({c.file for c in inp.static_call_sites})
    explained = " ".join(inp.unhandled).lower()
    for path in call_files:
        if path in inp.planned_files:
            continue
        base = path.rsplit("/", 1)[-1].lower()
        if path.lower() in explained or base in explained:
            add(Finding("call_site_declined", INFO, f"{path} has a known call site and the plan"
                        " explained why it was left alone", file=path))
            continue
        add(Finding("incomplete_plan", BLOCK,
                    f"{path} calls the target but has no task and no explanation in the plan: the"
                    " refactor is incomplete", file=path))
    for path in sorted(inp.discarded_files & set(call_files)):
        add(Finding("unmigrated_call_site", WARN, f"{path} has a known call site and its task was"
                    " not verified, so it still uses the old form", file=path))
    rep.untouched_expected_files = sorted(inp.radius_files - inp.touched_files)

    # 3. The target's own interface before and after.
    base_tree = _parse(inp.baseline_tree.get(inp.target_path))
    new_tree = _parse(inp.patched_tree.get(inp.target_path))
    skip_first = inp.target_kind == "method"
    before_fn = _find_function(base_tree, inp.target_name, inp.target_parent) if base_tree else None
    after_fn = _find_function(new_tree, inp.target_name, inp.target_parent) if new_tree else None
    removed_names = set(inp.declared_removals)
    after_params: Params | None = None

    if inp.target_kind not in ("function", "method"):
        # A class, a constant, a type alias: there is no parameter list to compare
        # callers against. Saying so is a warning; blocking would refuse every run
        # whose target this gate simply cannot read.
        add(Finding("unchecked_kind", WARN, f"{inp.target_name} is a {inp.target_kind}: how its"
                    " callers use it is not checked statically", file=inp.target_path))
    elif not inp.target_path.endswith(".py"):
        add(Finding("unchecked_language", WARN, f"{inp.target_path} is not Python: how callers use"
                    f" {inp.target_name} is not checked statically", file=inp.target_path))
    elif base_tree is None or before_fn is None:
        add(Finding("uncheckable", BLOCK, f"the baseline definition of {inp.target_name} could not"
                    " be read, so the callers cannot be compared against it", file=inp.target_path))
    elif inp.target_path in inp.touched_files and new_tree is None:
        add(Finding("uncheckable", BLOCK, f"{inp.target_path} no longer parses, so the interface the"
                    " callers depend on cannot be read", file=inp.target_path))
    else:
        before_params = _params(before_fn, skip_first=skip_first)
        defined_after = _module_level_names(new_tree) if new_tree else set()
        if after_fn is None and inp.target_name in defined_after:
            # Renamed with the old name kept as an alias (`old = new`). The name
            # still resolves, so nothing that uses it is dangling and no string
            # naming it is stale; what the alias points at is not analysed.
            rep.target_signature = {"before": before_params.text, "after": None, "status": "aliased"}
        elif after_fn is None:
            removed_names.add(inp.target_name)
            rep.target_signature = {"before": before_params.text, "after": None, "status": "removed"}
        else:
            after_params = _params(after_fn, skip_first=skip_first)
            changed = after_params.text != before_params.text
            rep.target_signature = {
                "before": before_params.text, "after": after_params.text,
                "status": "changed" if changed else "unchanged",
                "breaking": signature_breaking(before_params, after_params) if changed else False,
            }

    # A name the target's module still defines — an alias, a re-binding — still
    # resolves, whatever the plan declared. Only names that are really gone count.
    if new_tree is not None:
        removed_names -= _module_level_names(new_tree)

    # 4. Every known caller against the interface that is left behind.
    old_changed: dict[str, set[int]] = {}
    for path in {c.file for c in inp.static_call_sites}:
        old_changed[path], _ = changed_lines(inp.baseline_tree.get(path, ""), inp.patched_tree.get(path, ""))

    rep.expected_call_sites = len(inp.static_call_sites)
    by_file: dict[str, list[CallRef]] = {}
    for c in inp.static_call_sites:
        by_file.setdefault(c.file, []).append(c)

    target_removed = rep.target_signature.get("status") == "removed"
    for path, refs in sorted(by_file.items()):
        patched_text = inp.patched_tree.get(path)
        baseline_ast = _parse(inp.baseline_tree.get(path))
        patched_ast = _parse(patched_text) if patched_text is not None else None
        if patched_text is not None and patched_ast is None:
            add(Finding("uncheckable", BLOCK, f"{path} no longer parses, so its calls to the target"
                        " cannot be checked", file=path))
            continue
        exprs = _call_exprs_at(baseline_ast, {r.line for r in refs}) if baseline_ast else set()
        exprs = exprs or {r.name for r in refs}

        for ref in refs:
            if ref.line in old_changed.get(path, set()):
                rep.call_sites_changed += 1
            else:
                rep.call_sites_unchanged.append({"file": ref.file, "line": ref.line, "name": ref.name})

        if patched_ast is None:
            continue
        for node in ast.walk(patched_ast):
            if not isinstance(node, ast.Call) or ast.unparse(node.func) not in exprs:
                continue
            if target_removed:
                add(Finding("dangling_call", BLOCK, f"{path}:{node.lineno} still calls"
                            f" {inp.target_name}, which the patch removed", file=path, line=node.lineno))
            elif after_params is not None:
                why = call_compat(node, after_params)
                if why:
                    add(Finding("incompatible_call", BLOCK, f"{path}:{node.lineno} {why}",
                                file=path, line=node.lineno))

    if rep.call_sites_unchanged:
        add(Finding("call_sites_unchanged", INFO, f"{len(rep.call_sites_unchanged)} of"
                    f" {rep.expected_call_sites} known call sites were left unchanged"
                    " (compatible with the resulting signature unless flagged above)",
                    data={"sites": rep.call_sites_unchanged[:20]}))

    # 5. Other exported interfaces the patch changed, and who consumes them.
    for path in sorted(inp.touched_files):
        if not path.endswith(".py"):
            continue
        b, a = _parse(inp.baseline_tree.get(path)), _parse(inp.patched_tree.get(path))
        if b is None or a is None:
            continue
        for node in b.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name.startswith("_"):
                continue
            if path == inp.target_path and node.name == inp.target_name:
                continue
            new_node = _find_function(a, node.name, None)
            if new_node is None:
                continue
            bp, ap = _params(node, skip_first=False), _params(new_node, skip_first=False)
            if bp.text == ap.text:
                continue
            breaking = signature_breaking(bp, ap)
            users = sorted(inp.consumers.get((path, node.name), []))
            rep.interface_changes.append({
                "symbol": f"{path}::{node.name}", "before": bp.text, "after": ap.text,
                "breaking": breaking, "consumers": users,
            })
            untouched = [u for u in users if u not in inp.touched_files]
            if breaking and untouched:
                add(Finding("interface_break_unchecked", WARN,
                            f"{node.name} changed incompatibly and {', '.join(untouched)} use it"
                            " without being changed", file=path))

    # 6. Call paths the graph could not resolve, and whether any test ran them.
    if inp.unresolved:
        if inp.baseline_coverage is None:
            add(Finding("coverage_unavailable", WARN, "no baseline coverage was recorded, so it is"
                        " unknown whether any test exercises the calls the graph could not resolve"))
        for ref in sorted(set(inp.unresolved), key=lambda r: (r.file, r.line, r.name)):
            cov = (inp.baseline_coverage or {}).get(ref.file)
            exercised = bool(cov and ref.line in cov[0])
            rep.dynamic_paths.append({"file": ref.file, "line": ref.line, "name": ref.name,
                                      "exercised_by_tests": exercised})
            if inp.baseline_coverage is not None and not exercised:
                add(Finding("dynamic_path_unexercised", WARN,
                            f"{ref.file}:{ref.line} dispatches dynamically ({ref.name}) and no test"
                            " executes that line, so whether it reaches the target is unverified",
                            file=ref.file, line=ref.line))

    # 7. Strings that still name something the patch removed.
    if removed_names:
        rep.stale_references = find_stale_references(
            inp.patched_tree, removed_names, module_name_of(inp.target_path),
        )
        for s in rep.stale_references:
            add(Finding("stale_reference", WARN,
                        f"{s.file}:{s.line} still names '{s.name}' in a lookup on"
                        f" {s.module}, and the patch removed it", file=s.file, line=s.line,
                        data={"strength": s.strength}))

    # 8. Anything in the radius written in a language this gate cannot read.
    for path in sorted(p for p in inp.radius_files if not p.endswith(".py")):
        add(Finding("unchecked_language", WARN, f"{path} is not Python: its call shapes are not"
                    " checked statically", file=path))

    return rep


def _parse(text: str | None) -> ast.Module | None:
    if text is None:
        return None
    try:
        return ast.parse(text)
    except SyntaxError:
        return None
