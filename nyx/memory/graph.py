from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import networkx as nx

from nyx.enums import MemoryEdgeKind
from nyx.types import MemoryEdge

_KIND_WEIGHT = {
    MemoryEdgeKind.SEMANTIC: 1.0,
    MemoryEdgeKind.ENTITY: 0.9,
    MemoryEdgeKind.KEYWORD: 0.75,
    MemoryEdgeKind.TEMPORAL: 0.35,
    MemoryEdgeKind.SAME_TOPIC: 1.05,
    MemoryEdgeKind.ELABORATES: 1.1,
    MemoryEdgeKind.CONTRASTS: 1.0,
    MemoryEdgeKind.CAUSES: 1.0,
    MemoryEdgeKind.UPDATES_PREFERENCE: 1.15,
    MemoryEdgeKind.USER_PROFILE_LINK: 1.1,
}

_CLUSTER_KIND_WEIGHT = {
    MemoryEdgeKind.SEMANTIC: 1.0,
    MemoryEdgeKind.ENTITY: 0.9,
    MemoryEdgeKind.KEYWORD: 0.7,
    MemoryEdgeKind.TEMPORAL: 0.15,
    MemoryEdgeKind.SAME_TOPIC: 1.0,
    MemoryEdgeKind.ELABORATES: 1.0,
    MemoryEdgeKind.CONTRASTS: 1.0,
    MemoryEdgeKind.CAUSES: 1.0,
    MemoryEdgeKind.UPDATES_PREFERENCE: 1.0,
    MemoryEdgeKind.USER_PROFILE_LINK: 1.0,
}


@dataclass(frozen=True)
class AssociationHit:
    memory_id: str
    score: float
    depth: int
    via: str
    kinds: list[str]


@dataclass(frozen=True)
class _TypedEdge:
    source: str
    target: str
    kind: MemoryEdgeKind
    weight: float
    created_at: float


@dataclass(frozen=True)
class _Path:
    memory_id: str
    score: float
    depth: int
    via: str
    kinds: tuple[MemoryEdgeKind, ...]
    visited: frozenset[str]


class MemoryGraph:
    """联想图：memory 为节点、memory_edge 为 typed 加权边。"""

    def __init__(
        self,
        edges: list[MemoryEdge],
        *,
        memory_ids: set[str] | None = None,
    ) -> None:
        canonical_edges = self._canonicalize_edges(edges)
        self._edges = list(canonical_edges.values())
        self._memory_ids = set(memory_ids or set())
        self._adjacency: dict[str, list[_TypedEdge]] = defaultdict(list)
        for edge in self._edges:
            self._memory_ids.add(edge.source)
            self._memory_ids.add(edge.target)
            self._adjacency[edge.source].append(edge)
            self._adjacency[edge.target].append(
                _TypedEdge(
                    source=edge.target,
                    target=edge.source,
                    kind=edge.kind,
                    weight=edge.weight,
                    created_at=edge.created_at,
                )
            )
        for node_edges in self._adjacency.values():
            node_edges.sort(key=lambda edge: (edge.target, edge.kind.value))

    def associate(
        self,
        seeds: dict[str, float],
        *,
        depth: int = 2,
        limit: int = 10,
        exclude: set[str] | None = None,
    ) -> list[AssociationHit]:
        """从 seeds 沿 typed edges 扩散，返回最佳联想命中。"""
        if depth <= 0 or limit <= 0 or not seeds:
            return []
        omitted = set(seeds) | set(exclude or set())
        frontier = [
            _Path(
                memory_id=memory_id,
                score=score,
                depth=0,
                via=memory_id,
                kinds=(),
                visited=frozenset({memory_id}),
            )
            for memory_id, score in seeds.items()
        ]
        best: dict[str, AssociationHit] = {}
        for hop in range(1, depth + 1):
            next_frontier: list[_Path] = []
            for path in frontier:
                for edge in self._adjacency.get(path.memory_id, []):
                    if edge.target in path.visited:
                        continue
                    score = (
                        path.score
                        * edge.weight
                        * self._depth_decay(hop)
                        * _KIND_WEIGHT[edge.kind]
                    )
                    kinds = (*path.kinds, edge.kind)
                    candidate = AssociationHit(
                        memory_id=edge.target,
                        score=score,
                        depth=hop,
                        via=path.memory_id,
                        kinds=[kind.value for kind in kinds],
                    )
                    if edge.target not in omitted:
                        self._keep_best(best, candidate)
                    next_frontier.append(
                        _Path(
                            memory_id=edge.target,
                            score=score,
                            depth=hop,
                            via=path.memory_id,
                            kinds=kinds,
                            visited=path.visited | {edge.target},
                        )
                    )
            frontier = next_frontier
            if not frontier:
                break
        hits = sorted(
            best.values(), key=lambda hit: (-hit.score, hit.depth, hit.memory_id)
        )
        return hits[:limit]

    def clusters(self) -> dict[str, int]:
        """按 typed edge 聚合权重计算稳定 memory_id -> cluster_id。"""
        graph: nx.Graph[str] = nx.Graph()
        graph.add_nodes_from(sorted(self._memory_ids))
        pair_weights: dict[tuple[str, str], float] = {}
        for edge in self._edges:
            key = (edge.source, edge.target)
            weight = edge.weight * _CLUSTER_KIND_WEIGHT[edge.kind]
            pair_weights[key] = max(pair_weights.get(key, 0.0), weight)
        for (source, target), weight in pair_weights.items():
            graph.add_edge(source, target, weight=weight)
        communities = self._communities(graph)
        cluster_by_memory_id: dict[str, int] = {}
        sorted_communities = sorted(
            (sorted(community) for community in communities if community),
            key=lambda community: community[0],
        )
        for cluster_id, community in enumerate(sorted_communities):
            for memory_id in community:
                cluster_by_memory_id[memory_id] = cluster_id
        return cluster_by_memory_id

    def neighbors(self, seeds: list[str], depth: int = 1) -> list[str]:
        """Compatibility wrapper until retrieval switches to associate."""
        scores = {seed: 1.0 for seed in seeds}
        hits = self.associate(
            scores, depth=depth, limit=len(self._memory_ids), exclude=set(seeds)
        )
        return [hit.memory_id for hit in hits]

    @staticmethod
    def _canonicalize_edges(
        edges: list[MemoryEdge],
    ) -> dict[tuple[str, str, MemoryEdgeKind], _TypedEdge]:
        canonical: dict[tuple[str, str, MemoryEdgeKind], _TypedEdge] = {}
        for edge in edges:
            source, target = sorted((edge.from_id, edge.to_id))
            key = (source, target, edge.kind)
            candidate = _TypedEdge(
                source=source,
                target=target,
                kind=edge.kind,
                weight=edge.weight,
                created_at=edge.created_at,
            )
            current = canonical.get(key)
            if current is None or (
                candidate.weight,
                candidate.created_at,
            ) > (
                current.weight,
                current.created_at,
            ):
                canonical[key] = candidate
        return canonical

    @staticmethod
    def _depth_decay(hop: int) -> float:
        if hop == 1:
            return 1.0
        return 0.55

    @staticmethod
    def _keep_best(
        best: dict[str, AssociationHit], candidate: AssociationHit
    ) -> None:
        current = best.get(candidate.memory_id)
        if current is None or MemoryGraph._is_better_path(candidate, current):
            best[candidate.memory_id] = candidate

    @staticmethod
    def _is_better_path(candidate: AssociationHit, current: AssociationHit) -> bool:
        if candidate.score != current.score:
            return candidate.score > current.score
        if candidate.depth != current.depth:
            return candidate.depth < current.depth
        return candidate.via < current.via

    @staticmethod
    def _communities(graph: nx.Graph[str]) -> list[set[str]]:
        if graph.number_of_nodes() == 0:
            return []
        try:
            communities = nx.community.louvain_communities(
                graph, weight="weight", seed=0
            )
        except (AttributeError, ImportError):
            communities = nx.community.greedy_modularity_communities(
                graph, weight="weight"
            )
        return [set(community) for community in communities]
