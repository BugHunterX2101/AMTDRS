"""tree-sitter wrapper. One file in, three lists out.

The queries in languages/*.scm decide which nodes matter. This module does the
uniform field extraction that works across grammars, so adding a language does
not mean adding a code path here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import tree_sitter as ts

from principal.graph.languages import lang_of, parser_for, query_for

TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|testing)(/|$)|(^|/)(test_[^/]+|[^/]+_test|[^/]+\.test|[^/]+\.spec)\.[a-z]+$")

FUNC_NODES = {
    "function_definition", "function_declaration", "generator_function_declaration",
    "method_definition", "arrow_function", "function_expression",
}
CLASS_NODES = {"class_definition", "class_declaration"}
SCOPE_NODES = FUNC_NODES | CLASS_NODES


def is_test_path(path: str | Path) -> bool:
    return bool(TEST_PATH.search(str(path).replace("\\", "/")))


@dataclass(slots=True)
class Definition:
    name: str
    kind: str                 # function | class | method | const | type
    line_start: int
    line_end: int
    signature: str
    exported: bool
    parent: str | None = None  # enclosing class, for methods


@dataclass(slots=True)
class ImportBinding:
    local_name: str
    symbol_name: str          # the name inside the source module, "*" for wildcard
    source_module: str
    line: int


@dataclass(slots=True)
class CallSite:
    callee_name: str
    receiver: str | None
    line: int
    enclosing: str | None
    dynamic: bool = False


@dataclass(slots=True)
class ParsedFile:
    path: str
    lang: str
    source: bytes
    is_test: bool
    definitions: list[Definition] = field(default_factory=list)
    imports: list[ImportBinding] = field(default_factory=list)
    calls: list[CallSite] = field(default_factory=list)
    dunder_all: list[str] | None = None
    has_error: bool = False


def _text(node: ts.Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _name_of(node: ts.Node, src: bytes) -> str | None:
    named = node.child_by_field_name("name")
    if named is not None:
        return _text(named, src)
    return None


def _enclosing_scope(node: ts.Node, src: bytes) -> str | None:
    cur = node.parent
    while cur is not None:
        if cur.type in SCOPE_NODES:
            n = _name_of(cur, src)
            if n:
                return n
            declarator = cur.child_by_field_name("declarator")
            if declarator is not None:
                return _text(declarator, src)
        cur = cur.parent
    return None


def _enclosing_class(node: ts.Node, src: bytes) -> str | None:
    cur = node.parent
    while cur is not None:
        if cur.type in CLASS_NODES:
            return _name_of(cur, src)
        cur = cur.parent
    return None


def _signature(node: ts.Node, src: bytes) -> str:
    """Everything up to the body. Grammar-independent and good enough to show a
    reviewer what the interface was."""
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else min(node.end_byte, node.start_byte + 400)
    text = src[node.start_byte : end].decode("utf-8", "replace").strip()
    text = " ".join(text.split())
    return text[:400].rstrip("{:( ")


def _is_exported_ts(node: ts.Node) -> bool:
    cur = node.parent
    while cur is not None:
        if cur.type in {"export_statement", "export_clause"}:
            return True
        if cur.type == "program":
            return False
        cur = cur.parent
    return False


def _dunder_all(root: ts.Node, src: bytes) -> list[str] | None:
    for child in root.children:
        if child.type != "expression_statement":
            continue
        assign = child.child(0)
        if assign is None or assign.type != "assignment":
            continue
        left = assign.child_by_field_name("left")
        right = assign.child_by_field_name("right")
        if left is None or right is None or _text(left, src) != "__all__":
            continue
        if right.type in {"list", "tuple"}:
            return [
                _text(c, src).strip("\"'")
                for c in right.named_children
                if c.type == "string"
            ]
    return None


# ------------------------------------------------------------- definitions --


def _definition(node: ts.Node, capture: str, src: bytes, lang: str, dunder: list[str] | None) -> Definition | None:
    kind = capture.split(".", 1)[1]

    if kind == "const":
        # Python: (assignment) inside a module-level expression_statement.
        # TypeScript: (variable_declarator) inside a lexical_declaration.
        target = node.child_by_field_name("left") or node.child_by_field_name("name")
        if target is None or target.type not in {"identifier", "property_identifier"}:
            return None
        name = _text(target, src)
        value = node.child_by_field_name("right") or node.child_by_field_name("value")
        if value is not None and value.type in {"arrow_function", "function_expression"}:
            kind = "function"
        signature = _signature(node, src) if kind == "function" else f"{name} = ..."
    else:
        name = _name_of(node, src)
        if name is None:
            return None
        signature = _signature(node, src)
        if kind == "function" and lang == "python" and _enclosing_class(node, src):
            kind = "method"

    if lang == "python":
        exported = name in dunder if dunder is not None else not name.startswith("_")
    else:
        exported = _is_exported_ts(node)

    return Definition(
        name=name,
        kind=kind,
        line_start=node.start_point[0] + 1,
        line_end=node.end_point[0] + 1,
        signature=signature,
        exported=exported,
        parent=_enclosing_class(node, src) if kind == "method" else None,
    )


# ----------------------------------------------------------------- imports --


def _python_imports(node: ts.Node, src: bytes) -> list[ImportBinding]:
    line = node.start_point[0] + 1
    out: list[ImportBinding] = []

    if node.type == "import_statement":
        for child in node.named_children:
            if child.type == "aliased_import":
                mod = child.child_by_field_name("name")
                alias = child.child_by_field_name("alias")
                if mod is not None and alias is not None:
                    dotted = _text(mod, src)
                    out.append(ImportBinding(_text(alias, src), dotted.rsplit(".", 1)[-1], dotted, line))
            elif child.type == "dotted_name":
                dotted = _text(child, src)
                out.append(ImportBinding(dotted.split(".", 1)[0], dotted.rsplit(".", 1)[-1], dotted, line))
        return out

    module_node = node.child_by_field_name("module_name")
    module = _text(module_node, src) if module_node is not None else ""
    # Relative imports: `from . import x` has dots in the module_name text.
    for child in node.named_children:
        if child is module_node:
            continue
        if child.type == "aliased_import":
            orig = child.child_by_field_name("name")
            alias = child.child_by_field_name("alias")
            if orig is not None and alias is not None:
                out.append(ImportBinding(_text(alias, src), _text(orig, src), module, line))
        elif child.type == "dotted_name":
            name = _text(child, src)
            out.append(ImportBinding(name.split(".", 1)[0], name, module, line))
        elif child.type == "wildcard_import":
            out.append(ImportBinding("*", "*", module, line))
    return out


def _ts_imports(node: ts.Node, src: bytes) -> list[ImportBinding]:
    line = node.start_point[0] + 1
    source_node = node.child_by_field_name("source")
    module = _text(source_node, src).strip("\"'`") if source_node is not None else ""
    out: list[ImportBinding] = []

    def walk(n: ts.Node) -> None:
        if n.type == "import_specifier":
            name = n.child_by_field_name("name")
            alias = n.child_by_field_name("alias")
            if name is not None:
                original = _text(name, src)
                local = _text(alias, src) if alias is not None else original
                out.append(ImportBinding(local, original, module, line))
        elif n.type in {"namespace_import", "identifier"} and n.parent is not None and n.parent.type in {
            "import_clause",
            "namespace_import",
        }:
            local = _text(n, src).replace("* as ", "").strip()
            if local and local != "*":
                out.append(ImportBinding(local, "default" if n.type == "identifier" else "*", module, line))
        for c in n.named_children:
            walk(c)

    walk(node)
    if not out and module:
        out.append(ImportBinding(module.rsplit("/", 1)[-1], "*", module, line))
    return out


# ------------------------------------------------------------------- calls --


def _call_site(node: ts.Node, src: bytes) -> CallSite | None:
    fn = node.child_by_field_name("function")
    if fn is None:
        return None
    line = node.start_point[0] + 1
    enclosing = _enclosing_scope(node, src)

    if fn.type in {"identifier", "property_identifier"}:
        return CallSite(_text(fn, src), None, line, enclosing)

    if fn.type in {"attribute", "member_expression"}:
        attr = fn.child_by_field_name("attribute") or fn.child_by_field_name("property")
        obj = fn.child_by_field_name("object")
        if attr is None:
            return None
        return CallSite(
            _text(attr, src), _text(obj, src)[:80] if obj is not None else None, line, enclosing
        )

    # A call whose callee is itself an expression: registry[name](), fn()(), await x().
    # No static name to bind to, so it is recorded as dynamic and the blast radius
    # admits it cannot prove what this reaches.
    return CallSite(_text(fn, src)[:80], None, line, enclosing, dynamic=True)


# ------------------------------------------------------------------ parse ---


def parse_file(path: Path, root: Path) -> ParsedFile | None:
    lang = lang_of(path)
    if lang is None:
        return None
    try:
        src = path.read_bytes()
    except OSError:
        return None

    rel = path.relative_to(root).as_posix()
    parser = parser_for(lang, path)
    tree = parser.parse(src)
    query = query_for(lang, path)

    out = ParsedFile(path=rel, lang=lang, source=src, is_test=is_test_path(rel))
    out.has_error = tree.root_node.has_error
    if lang == "python":
        out.dunder_all = _dunder_all(tree.root_node, src)

    cursor = ts.QueryCursor(query)
    captures = cursor.captures(tree.root_node)

    for capture, nodes in captures.items():
        if capture.startswith("def."):
            for node in nodes:
                d = _definition(node, capture, src, lang, out.dunder_all)
                if d is not None:
                    out.definitions.append(d)
        elif capture == "import":
            for node in nodes:
                out.imports.extend(
                    _python_imports(node, src) if lang == "python" else _ts_imports(node, src)
                )
        elif capture == "call":
            for node in nodes:
                c = _call_site(node, src)
                if c is not None:
                    out.calls.append(c)
        elif capture == "dynamic":
            for node in nodes:
                enclosing = _enclosing_scope(node, src)
                literal = _first_string_argument(node, src)
                out.calls.append(
                    CallSite(literal or "<dynamic>", None, node.start_point[0] + 1, enclosing, dynamic=True)
                )

    out.definitions.sort(key=lambda d: (d.line_start, d.name))
    out.calls.sort(key=lambda c: (c.line, c.callee_name))
    return out


def _first_string_argument(node: ts.Node, src: bytes) -> str | None:
    """getattr(obj, "create") names its target in a string. Recover it so the
    heuristic edge points somewhere instead of nowhere."""
    args = node.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.named_children:
        if child.type in {"string", "template_string"}:
            return _text(child, src).strip("\"'`")
    return None


def parse_source(source: bytes, lang: str, rel_path: str = "<memory>") -> ParsedFile:
    """Used by the syntax gate, which re-parses a patched file without touching disk."""
    parser = parser_for(lang, rel_path)
    tree = parser.parse(source)
    out = ParsedFile(path=rel_path, lang=lang, source=source, is_test=is_test_path(rel_path))
    out.has_error = tree.root_node.has_error
    if lang == "python":
        out.dunder_all = _dunder_all(tree.root_node, source)
    cursor = ts.QueryCursor(query_for(lang, rel_path))
    for capture, nodes in cursor.captures(tree.root_node).items():
        if capture.startswith("def."):
            for node in nodes:
                d = _definition(node, capture, source, lang, out.dunder_all)
                if d is not None:
                    out.definitions.append(d)
    return out
