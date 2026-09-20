"""Mutation generation: the measurement that makes a generated safety net
defensible instead of merely present.

The question a characterisation suite has to answer is not "does it pass" — it
was built to pass — but "would it notice if the code broke". Mutation testing
answers that deterministically and without a second model: change the target one
operator at a time and check the tests fail. A suite that cannot detect a flipped
comparison on the function it supposedly covers will not detect a broken refactor
of that function either.

The classification is Cosmic Ray's and so is the score: each mutant is killed
(the tests failed, which is the good outcome), survived (the tests still passed,
which means the suite is blind to that change) or incompetent (the mutant does
not even import, which measures nothing and is excluded from the denominator).

    score = killed / (killed + survived)

Mutants are generated here rather than by invoking Cosmic Ray for two reasons
that both come from the cost model. Mutation testing is slow because it runs a
suite per mutant, so the bounds matter more than the operator catalogue: only the
target symbol's own line range is mutated, never the module and certainly never
the repository, and the count is capped. Generating them in-process with `ast`
makes that line-range targeting exact, keeps the sandbox image free of another
dependency, and lets the whole mutant loop run inside a single sandbox operation
instead of paying a microVM spin-up per mutant.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

DEFAULT_CAP = 30

# Deterministic, and chosen for what they catch rather than for catalogue size.
# Each one corresponds to a mistake a refactor actually makes: an inverted
# guard, an off-by-one, a dropped negation, a short-circuit that changed order.
_COMPARE_SWAP: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE, ast.GtE: ast.Lt,
    ast.Gt: ast.LtE, ast.LtE: ast.Gt,
    ast.Is: ast.IsNot, ast.IsNot: ast.Is,
    ast.In: ast.NotIn, ast.NotIn: ast.In,
}

_BINOP_SWAP: dict[type[ast.operator], type[ast.operator]] = {
    ast.Add: ast.Sub, ast.Sub: ast.Add,
    ast.Mult: ast.FloorDiv, ast.FloorDiv: ast.Mult,
    ast.Div: ast.Mult,
}

_BOOLOP_SWAP: dict[type[ast.boolop], type[ast.boolop]] = {
    ast.And: ast.Or, ast.Or: ast.And,
}


@dataclass(frozen=True, slots=True)
class Mutant:
    """One single-operator change, as replacement source for the whole file."""

    mutant_id: str
    operator: str
    line: int
    description: str
    source: str


class _Mutator(ast.NodeTransformer):
    """Applies exactly one mutation, identified by its visit ordinal.

    One at a time is the whole point: two simultaneous changes can cancel, and a
    killed double-mutant says nothing about whether either half was detected.
    """

    def __init__(self, target_index: int, lo: int, hi: int, skip_ids: frozenset[int] = frozenset()):
        self.target_index = target_index
        self.lo = lo
        self.hi = hi
        self.index = -1
        self.applied: tuple[str, int, str] | None = None
        # Docstrings, identified by object identity on this specific parse.
        # A docstring cannot be "killed" by any test that asserts observable
        # behaviour — nothing runs it — so mutating one always survives and
        # would deflate the score of exactly the well-documented code this
        # system is supposed to handle well.
        self.skip_ids = skip_ids

    def _in_range(self, node: ast.AST) -> bool:
        line = getattr(node, "lineno", None)
        return line is not None and self.lo <= line <= self.hi

    def _take(self, node: ast.AST, operator: str, description: str) -> bool:
        if not self._in_range(node):
            return False
        self.index += 1
        if self.index != self.target_index:
            return False
        self.applied = (operator, getattr(node, "lineno", 0), description)
        return True

    def visit_Compare(self, node: ast.Compare) -> ast.AST:  # noqa: N802
        self.generic_visit(node)
        for i, op in enumerate(node.ops):
            replacement = _COMPARE_SWAP.get(type(op))
            if replacement is None:
                continue
            if self._take(node, "comparison",
                          f"{type(op).__name__} becomes {replacement.__name__}"):
                node.ops[i] = replacement()
                return node
        return node

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:  # noqa: N802
        self.generic_visit(node)
        replacement = _BINOP_SWAP.get(type(node.op))
        if replacement is not None and self._take(
            node, "arithmetic", f"{type(node.op).__name__} becomes {replacement.__name__}"
        ):
            node.op = replacement()
        return node

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:  # noqa: N802
        self.generic_visit(node)
        replacement = _BOOLOP_SWAP.get(type(node.op))
        if replacement is not None and self._take(
            node, "boolean", f"{type(node.op).__name__} becomes {replacement.__name__}"
        ):
            node.op = replacement()
        return node

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:  # noqa: N802
        self.generic_visit(node)
        if isinstance(node.op, ast.Not) and self._take(node, "negation", "drops a `not`"):
            return node.operand
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:  # noqa: N802
        if id(node) in self.skip_ids:
            return node
        # `is` comparisons against True/None are structural, not numeric, and
        # bool is a subclass of int, so it is checked first.
        if isinstance(node.value, bool):
            if self._take(node, "boolean_constant", f"{node.value} becomes {not node.value}"):
                return ast.copy_location(ast.Constant(value=not node.value), node)
            return node
        if isinstance(node.value, int) and self._take(
            node, "integer_constant", f"{node.value} becomes {node.value + 1}"
        ):
            return ast.copy_location(ast.Constant(value=node.value + 1), node)
        if isinstance(node.value, str) and node.value != "" and self._take(
            node, "string_constant", "a string literal becomes empty"
        ):
            return ast.copy_location(ast.Constant(value=""), node)
        return node

    def visit_Return(self, node: ast.Return) -> ast.AST:  # noqa: N802
        self.generic_visit(node)
        if node.value is not None and self._take(node, "return_value", "returns None instead"):
            return ast.copy_location(ast.Return(value=ast.Constant(value=None)), node)
        return node


def _docstring_ids(tree: ast.AST) -> frozenset[int]:
    """Object ids, on this exact parse, of every docstring's string constant.

    A docstring is the first statement of a module, function or class body when
    that statement is a bare string expression. Recomputed per parse because
    `ast.parse` returns new node objects every time and identity is the only
    cheap way to exclude specific nodes from `NodeTransformer.visit`.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if not node.body:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(
            first.value.value, str
        ):
            ids.add(id(first.value))
    return frozenset(ids)


def _count_sites(source: str, lo: int, hi: int) -> int:
    n = 0
    while True:
        tree = ast.parse(source)
        mutator = _Mutator(n, lo, hi, skip_ids=_docstring_ids(tree))
        mutator.visit(tree)
        if mutator.applied is None:
            # visit() advances `index` once per candidate site; when the target
            # ordinal is past the last one, nothing was applied.
            return n
        n += 1
        if n > 500:  # a pathological file should not hang the run
            return n


def generate_mutants(source: str, *, line_start: int, line_end: int, cap: int = DEFAULT_CAP) -> list[Mutant]:
    """Every single-operator mutant of one symbol's line range, capped.

    Returns replacement sources, not diffs: the mutant is applied by overwriting
    the file inside a disposable sandbox fork, so there is nothing for a patch
    parser to reject and no interaction with the gates at all.
    """
    try:
        ast.parse(source)
    except SyntaxError:
        return []

    total = _count_sites(source, line_start, line_end)
    if total == 0:
        return []

    # Even spread across the symbol rather than the first N sites, so a capped
    # run still samples the end of a long function instead of only its guard
    # clauses.
    indices = list(range(total)) if total <= cap else [
        round(i * (total - 1) / (cap - 1)) for i in range(cap)
    ]

    mutants: list[Mutant] = []
    seen: set[int] = set()
    for i in indices:
        if i in seen:
            continue
        seen.add(i)
        tree = ast.parse(source)
        mutator = _Mutator(i, line_start, line_end, skip_ids=_docstring_ids(tree))
        mutated = mutator.visit(tree)
        if mutator.applied is None:
            continue
        operator, line, description = mutator.applied
        ast.fix_missing_locations(mutated)
        try:
            text = ast.unparse(mutated)
        except (ValueError, RecursionError):
            continue
        mutants.append(
            Mutant(
                mutant_id=f"m{i:03d}", operator=operator, line=line,
                description=description, source=text,
            )
        )
    return mutants


@dataclass(slots=True)
class MutationOutcome:
    killed: list[str]
    survived: list[str]
    incompetent: list[str]

    @property
    def score(self) -> float | None:
        """Killed over killed plus survived. Incompetent mutants are excluded
        from the denominator: a mutant that does not import measures the parser,
        not the tests, and counting it either way would be a lie in one
        direction. `None` when nothing scoreable ran at all."""
        denominator = len(self.killed) + len(self.survived)
        if denominator == 0:
            return None
        return len(self.killed) / denominator

    def to_payload(self) -> dict[str, object]:
        return {
            "score": self.score,
            "killed": len(self.killed),
            "survived": len(self.survived),
            "incompetent": len(self.incompetent),
            "survivors": sorted(self.survived)[:20],
        }


def classify(results: dict[str, int]) -> MutationOutcome:
    """Map each mutant's pytest exit code to its classification.

    0  the suite passed with broken code underneath: the mutant SURVIVED, and
       that is the finding — the tests are blind to this change.
    1  tests failed: the mutant was KILLED, which is the desired outcome.
    2+ collection or import error: INCOMPETENT. The mutant broke the module
       rather than its behaviour, so it measures nothing.
    """
    killed: list[str] = []
    survived: list[str] = []
    incompetent: list[str] = []
    for mutant_id, exit_code in sorted(results.items()):
        if exit_code == 0:
            survived.append(mutant_id)
        elif exit_code == 1:
            killed.append(mutant_id)
        else:
            incompetent.append(mutant_id)
    return MutationOutcome(killed=killed, survived=survived, incompetent=incompetent)
