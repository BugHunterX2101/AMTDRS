"""Planner: goal + code graph -> ordered atomic tasks. Ultra (or Super, measured).

Never sees raw repository bulk. Sees the code graph, symbol signatures and call-
site locations, not file bodies — a large context window is a capacity, not a
strategy. Rejected and reprompted once on any structural violation; a second
failure aborts the job with a reason, which is the never-a-partial-plan rule
expressed as validation.
"""

from __future__ import annotations

import json
from pathlib import Path

from principal.agents.contracts import Plan
from principal.config import Tier
from principal.errors import PlanInvalid
from principal.graph.radius import BlastRadius
from principal.models.client import ModelClient

PROMPT = (Path(__file__).parent / "prompts" / "planner.md").read_text(encoding="utf-8")


def _render(goal: str, target_fqn: str, radius: BlastRadius, max_depth: int) -> str:
    files_block = "\n".join(f"- {p}" for p in sorted(radius.files)) or "(none)"
    calls = [c for c in radius.call_sites if c["confidence"] == "static"]
    call_sites_block = "\n".join(
        f"- {c['file']}:{c['line']} calls `{c['symbol']}`" for c in calls[:200]
    ) or "(none found)"
    unresolved_block = "\n".join(
        f"- {u['file']}:{u['line']} `{u['name']}` — {u['reason']}" for u in radius.unresolved[:50]
    ) or "(none)"

    return PROMPT.format(
        goal=goal, target_fqn=target_fqn, signature=radius.target.signature or "(no signature captured)",
        target_path=radius.target.path, target_line=radius.target.line_start, max_depth=max_depth,
        files_block=files_block, call_sites_block=call_sites_block, unresolved_block=unresolved_block,
        schema=json.dumps(Plan.model_json_schema()),
    )


def validate_plan(plan: Plan, radius_files: set[str], max_tasks: int) -> None:
    seqs = [t.seq for t in plan.tasks]
    if len(seqs) != len(set(seqs)):
        raise PlanInvalid("duplicate task seq values")
    if len(plan.tasks) > max_tasks:
        raise PlanInvalid(f"{len(plan.tasks)} tasks exceeds cap of {max_tasks}")

    outside = [t.target_file for t in plan.tasks if t.target_file not in radius_files]
    if outside:
        raise PlanInvalid(f"tasks target files outside the blast radius: {outside}")

    seq_set = set(seqs)
    for t in plan.tasks:
        bad_deps = [d for d in t.depends_on if d not in seq_set]
        if bad_deps:
            raise PlanInvalid(f"task {t.seq} depends on unknown seq(s) {bad_deps}")

    # Cycle check, done here rather than deferred to ordering.py, because a
    # cyclic plan is invalid input and should fail the plan gate, not the
    # scheduler.
    graph = {t.seq: set(t.depends_on) for t in plan.tasks}
    visiting: set[int] = set()
    visited: set[int] = set()

    def dfs(node: int) -> None:
        if node in visited:
            return
        if node in visiting:
            raise PlanInvalid(f"dependency cycle involving task {node}")
        visiting.add(node)
        for dep in graph.get(node, ()):
            dfs(dep)
        visiting.discard(node)
        visited.add(node)

    for seq in seq_set:
        dfs(seq)


async def plan(
    client: ModelClient, *, job_id: str, goal: str, target_fqn: str, radius: BlastRadius,
    max_depth: int, max_tasks: int, tier: Tier = Tier.ULTRA,
) -> Plan:
    prompt = _render(goal, target_fqn, radius, max_depth)
    messages = [{"role": "user", "content": prompt}]

    last_error: str | None = None
    for _attempt in range(2):
        msgs = list(messages)
        if last_error is not None:
            msgs.append({"role": "user", "content": f"Your previous response was invalid: {last_error}\n\nTry again, returning only the corrected JSON object."})
        completion = await client.complete(
            tier, msgs, schema=Plan, temperature=0.0, job_id=job_id, max_tokens=6000
        )
        try:
            parsed = Plan.model_validate_json(_strip_fences(completion.text))
            validate_plan(parsed, radius.files, max_tasks)
            return parsed
        except (ValueError, PlanInvalid) as exc:
            last_error = str(exc)

    raise PlanInvalid(f"plan invalid after reprompt: {last_error}")


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
    return t.strip()
