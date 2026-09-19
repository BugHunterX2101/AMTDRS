"""Content-addressed disk cache for model responses.

Optional for correctness, close to essential in practice. Re-running the
benchmark after a scoring bug costs nothing, and the demo becomes replayable from
cache when the network is hostile. Cache entries live in the run directory so
published traces carry the exact responses that produced the published numbers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class CachedCompletion:
    text: str
    prompt_tokens: int
    completion_tokens: int
    used_reasoning_fallback: bool
    protocol: str


def cache_key(model: str, messages: list[dict[str, Any]], temperature: float, protocol: str) -> str:
    blob = json.dumps(
        {"model": model, "messages": messages, "temperature": temperature, "protocol": protocol},
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class DiskCache:
    def __init__(self, root: Path, enabled: bool = True):
        self.root = root
        self.enabled = enabled
        if enabled:
            root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> CachedCompletion | None:
        if not self.enabled:
            return None
        p = self._path(key)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return CachedCompletion(**data)

    def put(self, key: str, value: CachedCompletion) -> None:
        if not self.enabled:
            return
        self._path(key).write_text(
            json.dumps(
                {
                    "text": value.text,
                    "prompt_tokens": value.prompt_tokens,
                    "completion_tokens": value.completion_tokens,
                    "used_reasoning_fallback": value.used_reasoning_fallback,
                    "protocol": value.protocol,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
