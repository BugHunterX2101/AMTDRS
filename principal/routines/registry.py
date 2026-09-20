"""The routine registry.

Three things fall out of making a routine an object rather than a branch:

  The radius seed differs per routine. Relocation only needs the files that
  import the target's module; interface evolution needs the full consumer
  closure. Computing the wrong one either misses call sites or spends tokens
  rendering files that cannot possibly be affected.

  The scope policy is read by the gate rather than hardcoded in it. Relocation
  legitimately moves a file, which every other routine must still be forbidden
  from doing. Widening the gate globally to permit one routine's needs is how a
  safety property quietly becomes a suggestion.

  Each routine owns its prompt, so improving the decomposition prompt cannot
  regress interface evolution.

Two of the four SWE Atlas categories are implemented. The other two are named
here with the reason they are out of scope, because a registry that silently
omits them reads as an oversight rather than a decision.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from principal.db.store import Store, SymbolRow
from principal.graph.radius import BlastRadius, blast_radius
from principal.routines.relocation import importers_only_radius

PROMPT_DIR = Path(__file__).resolve().parent.parent / "agents" / "prompts"


class RefactorKind(str, Enum):
    INTERFACE_EVOLUTION = "interface_evolution"
    RELOCATION = "relocation"
    # Named, deliberately not registered. Decomposition is the largest category
    # (29 of 70 tasks) and fits the existing single-file model, but it is also
    # where models struggle most and the planning prompt is the hard part.
    # Extraction needs the planner to invent a new module, which breaks the
    # one-file-per-task contract the scope gate is built on.
    DECOMPOSITION = "decomposition"
    EXTRACTION = "extraction"


# The seed answers "which files could this change possibly affect", and it is the
# only part of the radius computation that differs between routines.
SeedFn = Callable[..., BlastRadius]


@dataclass(frozen=True, slots=True)
class Routine:
    kind: RefactorKind
    plan_prompt: Path
    seed_radius: SeedFn
    # What a diff produced under this routine may do. Read by gate 1. Defaults
    # are the strict policy every routine had before the registry existed, so a
    # routine that forgets to think about this gets the safe answer.
    allows_new_files: bool = False
    allows_file_moves: bool = False
    # Anticipated, unused: extraction will need to permit a new shared module.
    # Kept as the marker that the design accounted for it rather than an
    # omission a reader has to guess about.
    extra_gates: tuple[str, ...] = field(default_factory=tuple)

    @property
    def prompt_text(self) -> str:
        return self.plan_prompt.read_text(encoding="utf-8")


def _consumers_of(
    store: Store, graph_id: str, target: SymbolRow, *, max_depth: int, cap: int
) -> BlastRadius:
    """Full reverse-edge closure: callers, importers, and the dynamic dispatch
    that cannot be proved either way. This is the existing behaviour, named."""
    return blast_radius(store, graph_id, target, max_depth=max_depth, cap=cap)


ROUTINES: dict[RefactorKind, Routine] = {
    RefactorKind.INTERFACE_EVOLUTION: Routine(
        kind=RefactorKind.INTERFACE_EVOLUTION,
        plan_prompt=PROMPT_DIR / "planner.md",
        seed_radius=_consumers_of,
    ),
    RefactorKind.RELOCATION: Routine(
        kind=RefactorKind.RELOCATION,
        plan_prompt=PROMPT_DIR / "relocation.md",
        seed_radius=importers_only_radius,
        allows_file_moves=True,
    ),
}


def default_routine() -> Routine:
    """Interface evolution. The category Principal was built for, and the one a
    job that does not name a routine is overwhelmingly likely to be."""
    return ROUTINES[RefactorKind.INTERFACE_EVOLUTION]


def routine_for(kind: str | RefactorKind | None) -> Routine:
    """Resolve a stored or requested routine name.

    Unknown and unimplemented kinds fall back to interface evolution rather than
    raising: a job that asked for decomposition should run as the generic case
    and say so, not abort. The caller records which routine actually ran.
    """
    if kind is None:
        return default_routine()
    try:
        resolved = RefactorKind(kind)
    except ValueError:
        return default_routine()
    return ROUTINES.get(resolved, default_routine())
