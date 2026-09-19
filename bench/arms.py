"""The three arms.

Each arm is the same system with one thing switched off, so that a difference
between two arms attributes to exactly one cause:

  A → C  measures verification plus parallel search together
  A → B  measures verification alone
  B → C  measures parallel search alone, with verification held constant

Anything that varies besides the named difference makes the comparison
meaningless, so every arm shares one Settings object and changes only the fields
listed below. The tunables in force are written into each job's metadata for the
same reason.
"""

from __future__ import annotations

from dataclasses import dataclass

from principal.config import Settings


@dataclass(frozen=True, slots=True)
class Arm:
    name: str
    description: str
    candidates: int
    gates_enabled: bool
    max_repairs: int

    def settings(self, base: Settings) -> Settings:
        return base.model_copy(
            update={
                "candidates_per_task": self.candidates,
                "max_repairs": self.max_repairs,
                # Arm A still runs in a sandbox — it has to, or the comparison
                # would be between different execution environments rather than
                # between different strategies. What it does not do is *gate* on
                # the result: the patch is accepted whatever the tests say. That
                # is the honest way to represent "what a single model call
                # achieves unsupervised" while holding everything else fixed.
                "bench_accept_without_gates": not self.gates_enabled,
            }
        )


ARM_A = Arm(
    name="A",
    description="single shot, no gates — the patch is accepted whatever the tests say",
    candidates=1,
    gates_enabled=False,
    max_repairs=0,
)

ARM_B = Arm(
    name="B",
    description="one candidate, gates on — isolates the value of verification",
    candidates=1,
    gates_enabled=True,
    max_repairs=0,
)

ARM_C = Arm(
    name="C",
    description="full swarm — isolates the additional value of parallel search",
    candidates=3,
    gates_enabled=True,
    max_repairs=2,
)

ARMS = {"A": ARM_A, "B": ARM_B, "C": ARM_C}


def parse(spec: str) -> list[Arm]:
    names = [n.strip().upper() for n in spec.split(",") if n.strip()]
    unknown = [n for n in names if n not in ARMS]
    if unknown:
        raise SystemExit(f"unknown arm(s): {', '.join(unknown)}. Known arms: A, B, C")
    return [ARMS[n] for n in names]
