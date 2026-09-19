"""Resolve configured model ids against the list-models API at startup.

Model ids drift, casing is inconsistent across sources, and Token Factory
publishes deprecation notices that remove serverless model ids on a date. A job
that dies in week five on a 404 from a hardcoded id is an avoidable loss, and the
check is one request.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from principal.config import Tier
from principal.errors import ModelIdUnknown


@dataclass(slots=True)
class ModelRegistry:
    resolved: dict[Tier, str]
    available: list[str]


async def resolve_models(
    base_url: str, api_key: str, wanted: dict[Tier, str], *, timeout: float = 15.0
) -> ModelRegistry:
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        try:
            resp = await client.get("models", headers={"Authorization": f"Bearer {api_key}"})
            resp.raise_for_status()
            data = resp.json()
            available = sorted({row.get("id", "") for row in data.get("data", []) if row.get("id")})
        except (httpx.HTTPError, ValueError):
            # The endpoint being unreachable at boot is itself informative, but it
            # should not stop local development against recorded fixtures.
            available = []

    resolved: dict[Tier, str] = {}
    lower_index = {a.lower(): a for a in available}
    for tier, model_id in wanted.items():
        if not available:
            resolved[tier] = model_id
            continue
        if model_id in available:
            resolved[tier] = model_id
        elif model_id.lower() in lower_index:
            resolved[tier] = lower_index[model_id.lower()]
        else:
            raise ModelIdUnknown(model_id, available)

    return ModelRegistry(resolved=resolved, available=available)
