"""Convention examples, and nothing else.

Retrieval here is 80% static analysis and 20% embeddings, which is the opposite of
the usual code RAG. For interface evolution the question is "which files call this
symbol", and that has an exact answer no vector search should be asked to
approximate. Embeddings earn their place on one job only: finding the two or three
files that best show how this repository already does the thing the coder is about
to do. That measurably improves diff quality and costs almost nothing.

A normalised float32 matrix with cosine as a single matmul is the right retrieval
implementation at this scale. No vector database is needed for 2,000 files.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

Embedder = Callable[[list[str]], Awaitable[list[list[float]]]]

WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


@dataclass(slots=True)
class Example:
    path: str
    score: float
    excerpt: str


class ConventionIndex:
    """Lexical by default, embedding-backed when an embedder is supplied.

    The lexical path exists so the feature degrades to something useful rather
    than to nothing when the embeddings endpoint is unavailable, which is the
    difference between a cut feature and a broken one.
    """

    def __init__(self, snapshot: Path, paths: list[str], *, embedder: Embedder | None = None):
        self.snapshot = snapshot
        self.paths = paths
        self.embedder = embedder
        self._matrix: np.ndarray | None = None
        self._docs: list[str] = []
        self._vocab: dict[str, int] = {}
        # One index is shared by every task in a wave, and WaveRunner runs a
        # wave's tasks concurrently. Without this, each task's first query()
        # sees an unbuilt matrix at the same instant and triggers its own
        # build() — for the embedder path that is the same embedding request
        # fired once per concurrent task instead of once per job, paying for
        # and rate-limiting against work that was already in flight.
        self._build_lock = asyncio.Lock()

    def _read(self, rel: str, limit: int = 4000) -> str:
        try:
            return (self.snapshot / rel).read_text(encoding="utf-8", errors="replace")[:limit]
        except OSError:
            return ""

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        for p in self.paths:
            h.update(p.encode())
        return h.hexdigest()[:16]

    async def build(self) -> None:
        self._docs = [self._read(p) for p in self.paths]
        if self.embedder is not None:
            vectors = await self.embedder(self._docs)
            m = np.asarray(vectors, dtype=np.float32)
        else:
            m = self._lexical_matrix(self._docs, fit=True)
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._matrix = m / norms

    def _lexical_matrix(self, docs: list[str], *, fit: bool) -> np.ndarray:
        if fit:
            counts: dict[str, int] = {}
            for d in docs:
                for w in set(WORD.findall(d.lower())):
                    counts[w] = counts.get(w, 0) + 1
            vocab = [w for w, c in counts.items() if 1 < c < max(2, len(docs) * 0.8)]
            self._vocab = {w: i for i, w in enumerate(sorted(vocab)[:8000])}
            self._idf = {
                w: math.log(len(docs) / (1 + counts[w])) + 1.0 for w in self._vocab
            }
        m = np.zeros((len(docs), max(1, len(self._vocab))), dtype=np.float32)
        for row, d in enumerate(docs):
            for w in WORD.findall(d.lower()):
                idx = self._vocab.get(w)
                if idx is not None:
                    m[row, idx] += self._idf.get(w, 1.0)
        return m

    async def query(self, description: str, k: int = 2, exclude: set[str] | None = None) -> list[Example]:
        if self._matrix is None:
            async with self._build_lock:
                # Re-check inside the lock: the task that was already building
                # while this one waited has finished by the time it's granted,
                # and building a second time would be exactly the duplicate
                # work the lock exists to prevent.
                if self._matrix is None:
                    await self.build()
        assert self._matrix is not None
        if self.embedder is not None:
            q = np.asarray(await self.embedder([description]), dtype=np.float32)
        else:
            q = self._lexical_matrix([description], fit=False)
        norm = np.linalg.norm(q)
        if norm == 0:
            return []
        q = q / norm

        scores = (self._matrix @ q.T).ravel()
        exclude = exclude or set()
        order = np.argsort(-scores)
        out: list[Example] = []
        for idx in order:
            path = self.paths[int(idx)]
            if path in exclude:
                continue
            out.append(
                Example(path=path, score=float(scores[int(idx)]), excerpt=self._excerpt(int(idx)))
            )
            if len(out) >= k:
                break
        return out

    def _excerpt(self, idx: int, lines: int = 60) -> str:
        return "\n".join(self._docs[idx].splitlines()[:lines])
