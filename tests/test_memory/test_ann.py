from nyx.enums import MemoryType
from nyx.memory.ann import AnnIndex, ann_fingerprint, hash_embedding
from nyx.types import Memory


def _mem(id: str, embedding: list[float] | None, created_at: float = 1.0) -> Memory:
    return Memory(
        id,
        created_at,
        id,
        "t",
        id,
        1.0,
        MemoryType.SHORT_TERM,
        embedding=embedding,
    )


def test_ann_empty_and_invalid_query() -> None:
    index = AnnIndex.build([])
    assert index.query([1.0], candidate_k=10) == []
    assert index.query([1.0], candidate_k=0) == []


def test_ann_skips_none_and_wrong_dimensions() -> None:
    index = AnnIndex.build([
        _mem("a", [1.0, 0.0]),
        _mem("bad", [1.0, 0.0, 0.0]),
        _mem("none", None),
    ])
    assert [c.memory_id for c in index.query([1.0, 0.0], 10)] == ["a"]
    assert index.query([1.0], 10) == []


def test_ann_candidate_limit_and_order_are_stable() -> None:
    memories = [
        _mem("a", [1.0, 0.0], created_at=1.0),
        _mem("b", [0.9, 0.1], created_at=3.0),
        _mem("c", [0.0, 1.0], created_at=2.0),
    ]
    index = AnnIndex.build(memories, planes=4, tables=2, seed=0)
    result = index.query([1.0, 0.0], candidate_k=2)
    assert [c.memory_id for c in result] == ["a", "b"]
    assert len(result) == 2


def test_hash_embedding_and_fingerprint_change_on_embedding_update() -> None:
    old = [_mem("a", [0.123456789])]
    new = [_mem("a", [0.223456789])]
    assert hash_embedding([0.123456789]) == hash_embedding([0.123456788])
    assert ann_fingerprint(old) != ann_fingerprint(new)
    assert ann_fingerprint(old) != ann_fingerprint([])
