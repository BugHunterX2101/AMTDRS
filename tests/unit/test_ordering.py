"""Wave ordering. Two tasks editing one file concurrently is a correctness
question, and it is deliberately not delegated to the planner's judgement."""

from __future__ import annotations

import pytest

from principal.db.store import Task
from principal.errors import DependencyCycle
from principal.orchestrator.ordering import order_waves


def task(seq: int, target_file: str, depends_on: list[int] | None = None) -> Task:
    return Task(
        id=f"t{seq}", job_id="j", seq=seq, target_file=target_file,
        instruction=f"task {seq}", acceptance="tests pass", declared_removals=[],
        depends_on=depends_on or [], state="pending", attempts=0,
        winning_attempt_id=None, discard_reason=None,
    )


def test_independent_tasks_on_distinct_files_share_one_wave():
    waves = order_waves([task(1, "a.py"), task(2, "b.py"), task(3, "c.py")])
    assert len(waves) == 1
    assert {t.seq for t in waves[0]} == {1, 2, 3}


def test_dependencies_are_respected():
    waves = order_waves([task(1, "a.py"), task(2, "b.py", depends_on=[1])])
    assert [t.seq for t in waves[0]] == [1]
    assert [t.seq for t in waves[1]] == [2]


def test_two_tasks_on_one_file_are_split_even_with_no_declared_dependency():
    """The planner did not say these conflict. They do anyway — both edit the
    same file, and running them concurrently would have each fork from a
    baseline that does not contain the other's change."""
    waves = order_waves([task(1, "same.py"), task(2, "same.py")])
    assert len(waves) == 2
    assert [t.seq for t in waves[0]] == [1]
    assert [t.seq for t in waves[1]] == [2]


def test_file_conflict_does_not_stall_unrelated_work():
    waves = order_waves([task(1, "same.py"), task(2, "same.py"), task(3, "other.py")])
    assert {t.seq for t in waves[0]} == {1, 3}
    assert {t.seq for t in waves[1]} == {2}


def test_cycle_raises_rather_than_looping_forever():
    with pytest.raises(DependencyCycle):
        order_waves([task(1, "a.py", depends_on=[2]), task(2, "b.py", depends_on=[1])])


def test_every_task_is_scheduled_exactly_once():
    tasks = [task(i, f"f{i % 3}.py", depends_on=[i - 1] if i > 1 else []) for i in range(1, 8)]
    seqs = [t.seq for wave in order_waves(tasks) for t in wave]
    assert sorted(seqs) == list(range(1, 8))
    assert len(seqs) == len(set(seqs))
