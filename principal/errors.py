"""Error taxonomy.

Every failure has a code, and every code answers two questions: is it worth
retrying, and does it count against the system or against the platform. The
second question is what keeps the benchmark honest, so it is encoded here rather
than judged case by case at the call site.
"""

from __future__ import annotations

from enum import Enum


class Code(str, Enum):
    CONFIG_MISSING = "CONFIG_MISSING"
    MODEL_ID_UNKNOWN = "MODEL_ID_UNKNOWN"
    SANDBOX_FORBIDDEN = "SANDBOX_FORBIDDEN"
    BASELINE_RED = "BASELINE_RED"
    BASELINE_INSTALL_FAILED = "BASELINE_INSTALL_FAILED"
    RADIUS_TOO_LARGE = "RADIUS_TOO_LARGE"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    DEPENDENCY_CYCLE = "DEPENDENCY_CYCLE"
    PLAN_INVALID = "PLAN_INVALID"
    DIFF_UNPARSEABLE = "DIFF_UNPARSEABLE"
    GATE_SCOPE = "GATE_SCOPE"
    GATE_SYNTAX = "GATE_SYNTAX"
    GATE_TESTS_RED = "GATE_TESTS_RED"
    GATE_BEHAVIOUR = "GATE_BEHAVIOUR"
    INTEGRATION_CONFLICT = "INTEGRATION_CONFLICT"
    MODEL_EMPTY_RESPONSE = "MODEL_EMPTY_RESPONSE"
    MODEL_PROTOCOL_REJECTED = "MODEL_PROTOCOL_REJECTED"
    MODEL_RATE_LIMITED = "MODEL_RATE_LIMITED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    SANDBOX_TIMEOUT = "SANDBOX_TIMEOUT"
    SANDBOX_OP_FAILED = "SANDBOX_OP_FAILED"
    PUBLISH_FAILED = "PUBLISH_FAILED"


RETRYABLE: frozenset[Code] = frozenset(
    {
        Code.BASELINE_INSTALL_FAILED,
        Code.PLAN_INVALID,
        Code.GATE_TESTS_RED,
        Code.INTEGRATION_CONFLICT,
        Code.MODEL_EMPTY_RESPONSE,
        Code.MODEL_RATE_LIMITED,
        Code.SANDBOX_TIMEOUT,
        Code.SANDBOX_OP_FAILED,
        Code.PUBLISH_FAILED,
    }
)

# The classification that matters. These look like failures of the system and are
# not. Collapsing them into GATE_TESTS_RED would inflate the measured failure rate
# of the patches, which sounds conservative and is dishonest in both directions:
# it understates the verified refactor rate and it hides a platform problem that
# belongs in the tooling feedback. The benchmark reports three buckets, not two.
INFRASTRUCTURE: frozenset[Code] = frozenset(
    {
        Code.SANDBOX_TIMEOUT,
        Code.SANDBOX_OP_FAILED,
        Code.MODEL_EMPTY_RESPONSE,
        Code.MODEL_RATE_LIMITED,
    }
)


class PrincipalError(Exception):
    def __init__(self, code: Code, message: str, *, job_id: str | None = None, **detail: object):
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message
        self.job_id = job_id
        self.detail = detail

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE

    @property
    def infrastructure(self) -> bool:
        return self.code in INFRASTRUCTURE

    def envelope(self) -> dict[str, object]:
        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "retryable": self.retryable,
                "job_id": self.job_id,
                **({"detail": self.detail} if self.detail else {}),
            }
        }


class ConfigMissing(PrincipalError):
    def __init__(self, variable: str):
        super().__init__(Code.CONFIG_MISSING, f"{variable} is not set", variable=variable)


class RadiusTooLarge(PrincipalError):
    def __init__(self, found: int, cap: int):
        super().__init__(
            Code.RADIUS_TOO_LARGE, f"{found} files exceeds cap of {cap}", found=found, cap=cap
        )


class DependencyCycle(PrincipalError):
    def __init__(self, involved: list[str]):
        super().__init__(Code.DEPENDENCY_CYCLE, f"cycle among {involved}", involved=involved)


class BudgetExhausted(PrincipalError):
    def __init__(self, spent: int, limit: int, requested: int):
        super().__init__(
            Code.BUDGET_EXHAUSTED,
            f"{spent} + {requested} would exceed budget of {limit}",
            spent=spent,
            limit=limit,
            requested=requested,
        )


class PlanInvalid(PrincipalError):
    def __init__(self, reason: str):
        super().__init__(Code.PLAN_INVALID, reason)


class DiffUnparseable(PrincipalError):
    def __init__(self, reason: str):
        super().__init__(Code.DIFF_UNPARSEABLE, reason)


class SandboxForbidden(PrincipalError):
    def __init__(self, detail: str = "sandbox access denied"):
        super().__init__(
            Code.SANDBOX_FORBIDDEN,
            f"{detail}. Sandbox access is granted per project: check NEBIUS_PROJECT_ID",
        )


class SandboxTimeout(PrincipalError):
    def __init__(self, seconds: float):
        super().__init__(Code.SANDBOX_TIMEOUT, f"sandbox operation exceeded {seconds}s")


class SandboxOpFailed(PrincipalError):
    def __init__(self, detail: str):
        super().__init__(Code.SANDBOX_OP_FAILED, detail)


class ModelEmptyResponse(PrincipalError):
    def __init__(self, model: str):
        super().__init__(Code.MODEL_EMPTY_RESPONSE, f"{model} returned no usable text")


class ModelIdUnknown(PrincipalError):
    def __init__(self, wanted: str, available: list[str]):
        super().__init__(
            Code.MODEL_ID_UNKNOWN,
            f"{wanted} is not served. Available: {', '.join(sorted(available)[:40])}",
            wanted=wanted,
        )
