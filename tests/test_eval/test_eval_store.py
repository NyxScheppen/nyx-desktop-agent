import aiosqlite
import pytest

from nyx import db
from nyx.eval.store import EvalStore
from nyx.types import EvalRecord, LlmMessage


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


async def test_prompt_round_trip_is_shared_by_call_id() -> None:
    database = await db.connect(":memory:")
    store = EvalStore(database)
    prompt: list[LlmMessage] = [
        {"role": "system", "content": "设定\n第二行"},
        {"role": "user", "content": "你好"},
    ]
    try:
        await store.insert(_rec("think", "call-1"), prompt)
        await store.insert(_rec("speak", "call-1"), prompt)
        think = await store.get_prompt("think")
        speak = await store.get_prompt("speak")
        count = await (await database.conn.execute(
            "SELECT COUNT(*) AS count FROM eval_prompt"
        )).fetchone()
    finally:
        await database.conn.close()
    assert think == (True, prompt)
    assert speak == (True, prompt)
    assert count is not None and count["count"] == 1


async def test_prompt_distinguishes_legacy_and_missing_rows() -> None:
    database = await db.connect(":memory:")
    store = EvalStore(database)
    try:
        await store.insert(_rec("legacy", "call-old"))
        legacy = await store.get_prompt("legacy")
        missing = await store.get_prompt("missing")
    finally:
        await database.conn.close()
    assert legacy == (True, None)
    assert missing == (False, None)


async def test_prompt_rejects_corrupt_json() -> None:
    database = await db.connect(":memory:")
    store = EvalStore(database)
    try:
        await store.insert(_rec("e1", "call-1"))
        await database.conn.execute(
            "INSERT INTO eval_prompt (call_id, prompt_json) VALUES (?, ?)",
            ("call-1", '{"role":"user"}'),
        )
        await database.conn.commit()
        with pytest.raises(ValueError, match="prompt"):
            await store.get_prompt("e1")
    finally:
        await database.conn.close()


async def test_prompt_and_eval_record_insert_roll_back_together() -> None:
    database = await db.connect(":memory:")
    store = EvalStore(database)
    try:
        await store.insert(_rec("duplicate", "call-1"))
        with pytest.raises(aiosqlite.IntegrityError):
            await store.insert(
                _rec("duplicate", "call-2"),
                [{"role": "user", "content": "不能留下"}],
            )
        row = await (await database.conn.execute(
            "SELECT COUNT(*) AS count FROM eval_prompt WHERE call_id = 'call-2'"
        )).fetchone()
    finally:
        await database.conn.close()
    assert row is not None and row["count"] == 0


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
