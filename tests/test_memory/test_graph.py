from nyx.memory.graph import MemoryGraph
from nyx.types import MemoryEdge


def _edge(a: str, b: str, weight: float = 1.0) -> MemoryEdge:
    return MemoryEdge(from_id=a, to_id=b, weight=weight)


def test_neighbors_empty_and_missing() -> None:
    g = MemoryGraph([])
    assert g.neighbors([]) == []
    assert g.neighbors(["x"]) == []


def test_neighbors_single_edge() -> None:
    g = MemoryGraph([_edge("a", "b")])
    assert g.neighbors(["a"]) == ["b"]
    assert g.neighbors(["a", "b"]) == []


def test_neighbors_chain_depth() -> None:
    g = MemoryGraph([_edge("a", "b"), _edge("b", "c")])
    assert g.neighbors(["a"], depth=1) == ["b"]
    assert g.neighbors(["a"], depth=2) == ["b", "c"]


def test_neighbors_diamond_dedup() -> None:
    g = MemoryGraph([
        _edge("a", "b"), _edge("a", "c"), _edge("b", "d"), _edge("c", "d"),
    ])
    assert g.neighbors(["a"], depth=2) == ["b", "c", "d"]


def test_weight_does_not_affect_spread() -> None:
    g = MemoryGraph([_edge("a", "b", weight=99.0)])
    assert g.neighbors(["a"]) == ["b"]
