"""Configuration. Every tunable in the specification lives here and nowhere else.

Values are written into the run metadata at job start, because two benchmark arms
run at different concurrency are not comparable and without the values recorded
that fact is undiscoverable afterwards.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


# SWE Atlas reports that on Test Writing the leading model under a common
# scaffold writes the fewest tests on average and lands the highest score: the
# benchmark rewards agents that find the right tests rather than agents that add
# more. Asking for "comprehensive" coverage reliably produces forty shallow
# assertions, so the cap is low and mutation score decides whether it sufficed.
# It lives here rather than beside either user because the Characteriser (an
# agent) and the characterisation gate are siblings in the layer contract and
# cannot import one another.
MAX_GENERATED_TESTS = 6


class Tier(str, Enum):
    NANO = "nano"
    SUPER = "super"
    ULTRA = "ultra"


class Protocol(str, Enum):
    """Output protocol, chosen per model by the boot-time probe."""

    SCHEMA = "schema"
    TOOLS = "tools"
    TEXT = "text"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", protected_namespaces=()
    )

    # Credentials
    nebius_api_key: str = ""
    nebius_project_id: str = ""
    # Sandboxes are authorised per project and may be issued a different token
    # from the inference key. Blank means "reuse the inference key", which is the
    # common case; `sandbox_key` is the single accessor so no call site has to
    # remember the fallback.
    contree_token: str = ""
    github_token: str = ""
    principal_fork_repo: str = ""

    # Endpoints and models
    principal_inference_base: str = "https://api.tokenfactory.nebius.com/v1/"
    principal_sandbox_base: str = "https://api.tokenfactory.nebius.com/sandboxes/"
    principal_model_nano: str = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    principal_model_super: str = "nvidia/nemotron-3-super-120b-a12b"
    principal_model_ultra: str = "nvidia/Nemotron-3-Ultra-550b-a55b"
    principal_planner_tier: Tier = Tier.ULTRA

    # Base OCI image for baselines. Pinned by digest in production; a tag is
    # acceptable for the hackathon and the risk is recorded in the threat matrix.
    principal_base_image: str = "python:3.11-slim"

    # Tunables
    max_parallel_tasks: int = 8
    max_inflight_ops: int = 24
    candidates_per_task: int = 3
    max_repairs: int = 2
    radius_max_depth: int = 3
    radius_file_cap: int = 40
    max_tasks: int = 12
    task_timeout_s: int = 300
    sandbox_timeout_s: int = 600
    token_budget_default: int = 2_000_000
    max_discard_ratio: float = 0.30

    # Benchmark only. Arm A must run in a sandbox like every other arm — else
    # the comparison is between execution environments rather than between
    # strategies — but it must not *gate* on the result. This flag accepts a
    # patch whatever the tests say, which is the only honest way to represent
    # "what one unsupervised model call achieves" with everything else held
    # fixed. It is never set outside bench/, and the orchestrator refuses to
    # publish a pull request while it is on.
    bench_accept_without_gates: bool = False

    # Demo flags. There is deliberately no replay flag: every run is persisted in
    # the append-only event log and the console reopens any past run at
    # /app/?job=<job-id> through the same reducer as a live one, so a recorded
    # run needs no separate code path to serve it.
    principal_slow_mo_ms: int = 0
    principal_force_protocol: str = ""
    principal_model_cache: bool = True

    # Storage
    principal_db: Path = Field(default=ROOT / "principal.db")
    principal_runs_dir: Path = Field(default=ROOT / "runs")
    principal_snapshots_dir: Path = Field(default=ROOT / "snapshots")
    principal_cache_dir: Path = Field(default=ROOT / ".model_cache")

    @property
    def sandbox_key(self) -> str:
        """The token Sandboxes authenticate with. A working inference key tells
        you nothing about whether sandboxes are enabled for the project, and on
        some accounts the two credentials differ outright."""
        return self.contree_token or self.nebius_api_key

    def model_id(self, tier: Tier) -> str:
        return {
            Tier.NANO: self.principal_model_nano,
            Tier.SUPER: self.principal_model_super,
            Tier.ULTRA: self.principal_model_ultra,
        }[tier]

    def run_dir(self, job_id: str) -> Path:
        d = self.principal_runs_dir / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def recorded_tunables(self) -> dict[str, object]:
        """Written into every job's metadata so published numbers are reproducible."""
        return {
            "max_parallel_tasks": self.max_parallel_tasks,
            "max_inflight_ops": self.max_inflight_ops,
            "candidates_per_task": self.candidates_per_task,
            "max_repairs": self.max_repairs,
            "radius_max_depth": self.radius_max_depth,
            "radius_file_cap": self.radius_file_cap,
            "max_tasks": self.max_tasks,
            "task_timeout_s": self.task_timeout_s,
            "sandbox_timeout_s": self.sandbox_timeout_s,
            "max_discard_ratio": self.max_discard_ratio,
            "planner_tier": self.principal_planner_tier.value,
            "models": {
                "nano": self.principal_model_nano,
                "super": self.principal_model_super,
                "ultra": self.principal_model_ultra,
            },
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
