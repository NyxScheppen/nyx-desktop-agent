from __future__ import annotations

import math
import random
from dataclasses import dataclass
from itertools import combinations

from nyx.types import Memory


@dataclass
class AnnCandidate:
    memory_id: str
    cosine: float


class AnnIndex:
    """Deterministic random-projection LSH index for memory embeddings."""

    def __init__(
        self,
        dimension: int | None,
        planes_by_table: list[list[list[float]]],
        buckets: list[dict[int, list[str]]],
        memories_by_id: dict[str, Memory],
        indexed_memories: list[Memory],
    ) -> None:
        self._dimension = dimension
        self._planes_by_table = planes_by_table
        self._buckets = buckets
        self._memories_by_id = memories_by_id
        self._indexed_memories = indexed_memories

    @classmethod
    def build(
        cls,
        memories: list[Memory],
        *,
        planes: int = 16,
        tables: int = 4,
        seed: int = 0,
    ) -> AnnIndex:
        """Build an index from memories that share the first embedding dimension."""
        dimension = _first_embedding_dimension(memories)
        if dimension is None or planes <= 0 or tables <= 0:
            return cls(None, [], [], {}, [])

        indexed_memories = [
            memory
            for memory in memories
            if memory.embedding is not None and len(memory.embedding) == dimension
        ]
        rng = random.Random(seed)
        planes_by_table = [
            [
                [rng.gauss(0.0, 1.0) for _ in range(dimension)]
                for _ in range(planes)
            ]
            for _ in range(tables)
        ]
        buckets: list[dict[int, list[str]]] = [{} for _ in range(tables)]
        for memory in indexed_memories:
            embedding = memory.embedding
            if embedding is None:
                continue
            for table_index, table_planes in enumerate(planes_by_table):
                bucket_key = _hash_with_planes(embedding, table_planes)
                buckets[table_index].setdefault(bucket_key, []).append(memory.id)

        for table in buckets:
            for memory_ids in table.values():
                memory_ids.sort()

        memories_by_id = {memory.id: memory for memory in indexed_memories}
        return cls(
            dimension,
            planes_by_table,
            buckets,
            memories_by_id,
            indexed_memories,
        )

    def query(self, vector: list[float], candidate_k: int) -> list[AnnCandidate]:
        """Return bounded candidates sorted by exact cosine and stable tie-breaks."""
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
            layer_ids: set[str] = set()
            for table_index, query_hash in enumerate(query_hashes):
                for bucket_key in _hamming_neighbors(
                    query_hash, radius, self._plane_count
                ):
                    layer_ids.update(self._buckets[table_index].get(bucket_key, []))
            self._append_candidates(candidate_ids, seen, sorted(layer_ids), candidate_k)
            if len(candidate_ids) >= candidate_k:
                break

        if len(candidate_ids) < candidate_k:
            newest_ids = [
                memory.id
                for memory in sorted(
                    self._indexed_memories,
                    key=lambda memory: (-memory.created_at, memory.id),
                )
            ]
            self._append_candidates(candidate_ids, seen, newest_ids, candidate_k)

        candidates = [
            AnnCandidate(
                memory_id,
                _cosine(
                    vector,
                    self._memories_by_id[memory_id].embedding or [],
                ),
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
        memory_ids: list[str],
        candidate_k: int,
    ) -> None:
        for memory_id in memory_ids:
            if memory_id in seen:
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


def _first_embedding_dimension(memories: list[Memory]) -> int | None:
    for memory in memories:
        if memory.embedding is not None:
            return len(memory.embedding)
    return None


def _hash_with_planes(vector: list[float], planes: list[list[float]]) -> int:
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
