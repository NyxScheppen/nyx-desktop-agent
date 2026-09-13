from nyx.enums import MemoryEdgeKind
from nyx.memory.graph import AssociationHit, MemoryGraph
from nyx.types import MemoryEdge


def _edge(
    a: str, b: str, kind: MemoryEdgeKind, weight: float, created_at: float = 1.0
) -> MemoryEdge:
    return MemoryEdge(a, b, kind, weight, created_at)


def test_associate_depth_two_scores_and_excludes_seeds() -> None:
    g = MemoryGraph([
        _edge("a", "b", MemoryEdgeKind.SEMANTIC, 0.8),
        _edge("b", "c", MemoryEdgeKind.KEYWORD, 0.5),
    ])
    hits: list[AssociationHit] = g.associate(
        {"a": 1.0}, depth=2, limit=10, exclude={"a"}
    )
    assert [(h.memory_id, h.depth, h.via, h.kinds) for h in hits] == [
        ("b", 1, "a", ["semantic"]),
        ("c", 2, "b", ["semantic", "keyword"]),
    ]
    assert hits[0].score == 0.8
    assert hits[1].score == 1.0 * 0.8 * 0.5 * 0.55 * 0.75


def test_associate_parallel_typed_edges_take_best_path() -> None:
    g = MemoryGraph([
        _edge("a", "b", MemoryEdgeKind.TEMPORAL, 1.0),
        _edge("a", "b", MemoryEdgeKind.SEMANTIC, 0.7),
    ])
    [hit] = g.associate({"a": 1.0}, limit=10, exclude={"a"})
    assert hit.memory_id == "b"
    assert hit.kinds == ["semantic"]


def test_associate_equal_paths_keep_lexicographically_smaller_prefix_via() -> None:
    g = MemoryGraph([
        _edge("s", "a", MemoryEdgeKind.SEMANTIC, 1.0),
        _edge("s", "aa", MemoryEdgeKind.SEMANTIC, 1.0),
        _edge("a", "z", MemoryEdgeKind.SEMANTIC, 1.0),
        _edge("aa", "z", MemoryEdgeKind.SEMANTIC, 1.0),
    ])
    z_hit = next(
        hit
        for hit in g.associate({"s": 1.0}, depth=2, limit=10)
        if hit.memory_id == "z"
    )
    assert z_hit.via == "a"


def test_clusters_include_isolated_nodes_with_stable_ids() -> None:
    g = MemoryGraph([
        _edge("a", "b", MemoryEdgeKind.SEMANTIC, 1.0),
    ], memory_ids={"a", "b", "z"})
    clusters = g.clusters()
    assert set(clusters) == {"a", "b", "z"}
    assert clusters["a"] == clusters["b"]
    assert clusters["z"] != clusters["a"]
