"""Topological sort over depends_on, then a wave split so no two tasks in the
same wave touch the same file.

The file-conflict split is independent of what the planner said. The planner
emits depends_on and is often right, but two tasks editing one file is a
correctness question and it does not get delegated to a model.
"""

from __future__ import annotations

from principal.db.store import Task
from principal.errors import DependencyCycle


def order_waves(tasks: list[Task]) -> list[list[Task]]:
    by_seq = {t.seq: t for t in tasks}
    waves: list[list[Task]] = []
    remaining = dict(by_seq)
    done: set[int] = set()

    while remaining:
        ready = [t for t in remaining.values() if set(t.depends_on) <= done]
        if not ready:
            raise DependencyCycle(sorted(remaining))

        wave: list[Task] = []
        deferred: list[Task] = []
        claimed: set[str] = set()
        for t in sorted(ready, key=lambda t: t.seq):
            if t.target_file in claimed:
                deferred.append(t)
            else:
                claimed.add(t.target_file)
                wave.append(t)

        waves.append(wave)
        for t in wave:
            done.add(t.seq)
            del remaining[t.seq]
        # deferred tasks simply were not ready this round because of the file
        # claim; they retry next iteration once the wave that used their file
        # has completed and, if depends_on allows, without an explicit edge.

    return waves
