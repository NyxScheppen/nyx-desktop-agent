from __future__ import annotations

import bisect
import heapq
import math
import random
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from itertools import combinations
from typing import TypeVar

from nyx.types import Memory


@dataclass
class AnnCandidate:
    memory_id: str
    cosine: float


_T = TypeVar("_T", str, tuple[float, str])
Projection = tuple[tuple[tuple[float, ...], ...], ...]


def add_sorted_run(runs: list[list[_T]], item: _T) -> None:
    """Insert one item using binary-carry merges instead of shifting a large list."""
    carry = [item]
    level = 0
    while True:
        if level == len(runs):
            runs.append(carry)
            return
        if not runs[level]:
            runs[level] = carry
            return
        carry = list(heapq.merge(runs[level], carry))
        runs[level] = []
        level += 1


def discard_sorted_run(runs: list[list[_T]], item: _T) -> None:
    """Remove an item from every occupied sorted run; deletion is off the hot path."""
    for run in runs:
        position = bisect.bisect_left(run, item)
        if position < len(run) and run[position] == item:
            run.pop(position)


def _merge_runs(runs: list[list[_T]]) -> Iterator[_T]:
    return heapq.merge(*(run for run in runs if run))


class AnnIndex:
    """Deterministic random-projection LSH index with incremental updates."""

    def __init__(
        self,
        *,
        planes: int = 16,
        tables: int = 4,
        seed: int = 0,
        projection_cache: dict[int, Projection] | None = None,
    ) -> None:
        self._dimension: int | None = None
        self._planes_by_table: Projection = ()
        self._buckets: list[dict[int, list[list[str]]]] = []
        self._memories_by_id: dict[str, Memory] = {}
        self._newest_runs: list[list[tuple[float, str]]] = []
        self._planes = planes
        self._tables = tables
        self._seed = seed
        self._projection_cache = projection_cache

    @classmethod
    def build(
        cls,
        memories: list[Memory],
        *,
        planes: int = 16,
        tables: int = 4,
        seed: int = 0,
        projection_cache: dict[int, Projection] | None = None,
    ) -> AnnIndex:
        """Build an index from memories sharing the first embedding dimension."""
        index = cls(
            planes=planes, tables=tables, seed=seed,
            projection_cache=projection_cache,
        )
        for memory in memories:
            index.add(memory)
        return index

    def _initialize_planes(self, dimension: int) -> None:
        projection = (
            self._projection_cache.get(dimension)
            if self._projection_cache is not None else None
        )
        if projection is None:
            rng = random.Random(self._seed)
            projection = tuple(
                tuple(
                    tuple(rng.gauss(0.0, 1.0) for _ in range(dimension))
                    for _ in range(self._planes)
                )
                for _ in range(self._tables)
            )
            if self._projection_cache is not None:
                self._projection_cache[dimension] = projection
        self._planes_by_table = projection
        self._buckets = [
            dict[int, list[list[str]]]() for _ in range(self._tables)
        ]

    def add(self, memory: Memory) -> None:
        """Add or replace an eligible memory in the index."""
        self.remove(memory.id)
        embedding = memory.embedding
        if embedding is None or self._planes <= 0 or self._tables <= 0:
            return
        if self._dimension is None:
            self._dimension = len(embedding)
            self._initialize_planes(self._dimension)
        if len(embedding) != self._dimension:
            return
        self._memories_by_id[memory.id] = memory
        add_sorted_run(self._newest_runs, (-memory.created_at, memory.id))
        for table_index, table_planes in enumerate(self._planes_by_table):
            bucket_key = _hash_with_planes(embedding, table_planes)
            runs = self._buckets[table_index].setdefault(bucket_key, [])
            add_sorted_run(runs, memory.id)

    def remove(self, memory_id: str) -> None:
        """Remove a memory; repeated removal is harmless."""
        memory = self._memories_by_id.pop(memory_id, None)
        if memory is None:
            return
        discard_sorted_run(self._newest_runs, (-memory.created_at, memory.id))
        embedding = memory.embedding
        if embedding is None or self._dimension is None:
            return
        for table_index, table_planes in enumerate(self._planes_by_table):
            bucket_key = _hash_with_planes(embedding, table_planes)
            runs = self._buckets[table_index].get(bucket_key)
            if runs is not None:
                discard_sorted_run(runs, memory.id)
        if not self._memories_by_id:
            self._dimension = None
            self._planes_by_table = ()
            self._buckets = []
            self._newest_runs = []

    def __len__(self) -> int:
        return len(self._memories_by_id)

    def query(
        self,
        vector: list[float],
        candidate_k: int,
        *,
        allowed_ids: set[str] | None = None,
    ) -> list[AnnCandidate]:
        """Return bounded eligible candidates sorted by exact cosine."""
        if (
            candidate_k <= 0
            or self._dimension is None
            or len(vector) != self._dimension
        ):
            return []
        candidate_ids: list[str] = []
        seen: set[str] = set()
        query_hashes = [
            _hash_with_planes(vector, table_planes)
            for table_planes in self._planes_by_table
        ]
        for radius in (0, 1, 2):
            layer_runs: list[list[str]] = []
            for table_index, query_hash in enumerate(query_hashes):
                for bucket_key in _hamming_neighbors(
                    query_hash, radius, self._plane_count
                ):
                    layer_runs.extend(self._buckets[table_index].get(bucket_key, []))
            self._append_candidates(
                candidate_ids, seen, _merge_runs(layer_runs), candidate_k, allowed_ids,
            )
            if len(candidate_ids) >= candidate_k:
                break
        if len(candidate_ids) < candidate_k:
            self._append_candidates(
                candidate_ids, seen,
                (memory_id for _, memory_id in _merge_runs(self._newest_runs)),
                candidate_k, allowed_ids,
            )
        candidates = [
            AnnCandidate(
                memory_id,
                _cosine(vector, self._memories_by_id[memory_id].embedding or []),
            )
            for memory_id in candidate_ids
        ]
        candidates.sort(
            key=lambda candidate: (
                -candidate.cosine,
                -self._memories_by_id[candidate.memory_id].created_at,
                candidate.memory_id,
            )
        )
        return candidates

    @property
    def _plane_count(self) -> int:
        if not self._planes_by_table:
            return 0
        return len(self._planes_by_table[0])

    @staticmethod
    def _append_candidates(
        candidate_ids: list[str],
        seen: set[str],
        memory_ids: Iterator[str],
        candidate_k: int,
        allowed_ids: set[str] | None,
    ) -> None:
        for memory_id in memory_ids:
            if memory_id in seen or (
                allowed_ids is not None and memory_id not in allowed_ids
            ):
                continue
            seen.add(memory_id)
            candidate_ids.append(memory_id)
            if len(candidate_ids) >= candidate_k:
                return


def hash_embedding(embedding: list[float]) -> int:
    return hash(tuple(round(value, 8) for value in embedding))


def ann_fingerprint(memories: list[Memory]) -> tuple[tuple[str, float, int], ...]:
    return tuple(
        (memory.id, memory.created_at, hash_embedding(memory.embedding))
        for memory in memories
        if memory.embedding is not None
    )


def _hash_with_planes(vector: list[float], planes: Sequence[Sequence[float]]) -> int:
    value = 0
    for index, plane in enumerate(planes):
        dot = sum(x * y for x, y in zip(vector, plane))
        if dot >= 0.0:
            value |= 1 << index
    return value


def _hamming_neighbors(value: int, radius: int, bit_count: int) -> list[int]:
    if radius == 0:
        return [value]
    neighbors: list[int] = []
    for bits in combinations(range(bit_count), radius):
        mask = 0
        for bit in bits:
            mask |= 1 << bit
        neighbors.append(value ^ mask)
    return neighbors


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
