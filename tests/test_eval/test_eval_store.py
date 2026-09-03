from nyx import db
from nyx.eval.store import EvalStore
from nyx.types import EvalRecord


def _rec(
    id: str,
    call_id: str,
    created_at: float = 1000.0,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> EvalRecord:
    return EvalRecord(
        id=id,
        created_at=created_at,
        call_id=call_id,
        module="expression",
        output_type="speak",
        model="m",
        correlation_id="c",
        ooc_keyword=1.0,
        ooc_embed=0.9,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


async def test_insert_and_list_recent_order() -> None:
    database = await db.connect(":memory:")
    store = EvalStore(database)
    try:
        await store.insert(_rec("a", "call-1", created_at=100.0))
        await store.insert(_rec("b", "call-2", created_at=200.0))
        await store.insert(_rec("c", "call-3", created_at=300.0))
        rows = await store.list_recent(2)
        assert [r.id for r in rows] == ["c", "b"]   # 倒序 + limit
    finally:
        await database.conn.close()


async def test_total_tokens_dedups_call_id() -> None:
    database = await db.connect(":memory:")
    store = EvalStore(database)
    try:
        # think/speak 共享 call-1：同一调用只计一次
        await store.insert(
            _rec("t", "call-1", prompt_tokens=10, completion_tokens=5)
        )
        await store.insert(
            _rec("s", "call-1", prompt_tokens=10, completion_tokens=5)
        )
        await store.insert(
            _rec("x", "call-2", prompt_tokens=3, completion_tokens=2)
        )
        stats = await store.total_tokens()
        assert stats.prompt_tokens == 13
        assert stats.completion_tokens == 7
        assert stats.total_tokens == 20
    finally:
        await database.conn.close()


async def test_total_tokens_empty() -> None:
    database = await db.connect(":memory:")
    store = EvalStore(database)
    try:
        stats = await store.total_tokens()
        assert stats.total_tokens == 0
        assert stats.prompt_tokens == 0
        assert stats.completion_tokens == 0
    finally:
        await database.conn.close()
