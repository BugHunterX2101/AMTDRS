"""Refactoring routines: one object per kind of refactor.

A routine is what a generic "refactor this file" prompt is not. SWE Atlas splits
refactoring into four operations and two of them are different *kinds* of
problem, not two flavours of one: interface evolution touches every call site of
an interface across the codebase, which is a graph traversal, while decomposition
is a local judgement about one file. One prompt cannot serve both, and one blast
radius seed is wrong for one of them whichever seed is chosen.

The registry makes the differences data rather than branches: each routine
carries its own planning prompt, its own radius seed, and its own policy for what
a diff is allowed to do. The scope gate reads that policy instead of hardcoding a
single answer, which is what lets relocation move a file without weakening the
gate for everything else.
"""

from __future__ import annotations

from principal.routines.registry import (
    ROUTINES,
    RefactorKind,
    Routine,
    default_routine,
    routine_for,
)

__all__ = ["ROUTINES", "RefactorKind", "Routine", "default_routine", "routine_for"]
