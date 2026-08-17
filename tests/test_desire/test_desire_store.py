from nyx import db
from nyx.desire.store import DesireStore
from nyx.enums import DesireStatus, DesireType, GoalAction
from nyx.types import DesireValue, Goal, LongTermDesire, ShortTermDesire


def _desire(
    id: str,
    created_at: float = 1000.0,
    *,
    type: DesireType = DesireType.INTERACTION,
    goal: Goal | None = None,
    retry_count: int = 0,
    status: DesireStatus = DesireStatus.PENDING,
) -> ShortTermDesire:
    return ShortTermDesire(
        id=id,
        created_at=created_at,
        type=type,
        strength=0.5,
        description="读骑士小说",
        goal=goal,
        retry_count=retry_count,
        status=status,
    )


def _dv(
    type: DesireType, value: float, updated_at: float = 1000.0
) -> DesireValue:
    return DesireValue(
        type=type,
        value=value,
        expression_weight=0.7,
        suppression_threshold=0.5,
        updated_at=updated_at,
    )


def _lt(type: DesireType) -> LongTermDesire:
    return LongTermDesire(
        id="lt1",
        created_at=1000.0,
        type=type,
        name="探索世界",
        description="了解骑士团历史",
        strength=0.8,
        progress=0.3,
        subtopics=["骑士团", "城堡"],
        linked_values=["curiosity"],
    )


async def test_add_get_roundtrip() -> None:
    database = await db.connect(":memory:")
    store = DesireStore(database)
    try:
        desire = _desire(
            "d1",
            goal=Goal(action=GoalAction.READ, count=3, topic="骑士团"),
            retry_count=1,
            status=DesireStatus.ACTIVE,
        )
        await store.add_desire(desire)
        got = await store.get_desire("d1")
        assert got is not None
        assert got == desire                       # 全字段往返（goal JSON + 枚举）
        assert got.goal is not None and got.goal.topic == "骑士团"
    finally:
        await database.conn.close()


async def test_goal_none_roundtrip() -> None:
    database = await db.connect(":memory:")
    store = DesireStore(database)
    try:
        await store.add_desire(_desire("d1", goal=None))
        got = await store.get_desire("d1")
        assert got is not None
        assert got.goal is None                   # SQL NULL 非 "null" 字符串
    finally:
        await database.conn.close()


async def test_list_pending_filters_and_orders() -> None:
    database = await db.connect(":memory:")
    store = DesireStore(database)
    try:
        await store.add_desire(_desire("p1", created_at=100.0))
        await store.add_desire(
            _desire("a1", created_at=200.0, status=DesireStatus.ACTIVE)
        )
        await store.add_desire(
            _desire("s1", created_at=300.0, status=DesireStatus.SATISFIED)
        )
        await store.add_desire(
            _desire("e1", created_at=50.0, status=DesireStatus.EXPIRED)
        )
        assert [d.id for d in await store.list_pending()] == ["p1", "a1"]
    finally:
        await database.conn.close()


async def test_list_short_term_all_desc() -> None:
    database = await db.connect(":memory:")
    store = DesireStore(database)
    try:
        await store.add_desire(_desire("p1", created_at=100.0))
        await store.add_desire(
            _desire("a1", created_at=200.0, status=DesireStatus.ACTIVE)
        )
        await store.add_desire(
            _desire("s1", created_at=300.0, status=DesireStatus.SATISFIED)
        )
        await store.add_desire(
            _desire("e1", created_at=50.0, status=DesireStatus.EXPIRED)
        )
        assert [d.id for d in await store.list_short_term()] == [
            "s1", "a1", "p1", "e1",
        ]
    finally:
        await database.conn.close()


async def test_update_desire() -> None:
    database = await db.connect(":memory:")
    store = DesireStore(database)
    try:
        await store.add_desire(_desire("d1"))
        desire = await store.get_desire("d1")
        assert desire is not None
        desire.status = DesireStatus.SATISFIED
        desire.retry_count = 2
        await store.update_desire(desire)
        got = await store.get_desire("d1")
        assert got is not None
        assert got.status is DesireStatus.SATISFIED
        assert got.retry_count == 2
    finally:
        await database.conn.close()


async def test_upsert_value_new_and_update() -> None:
    database = await db.connect(":memory:")
    store = DesireStore(database)
    try:
        await store.upsert_value(_dv(DesireType.INTERACTION, 0.5, updated_at=1000.0))
        values = await store.list_values()
        assert len(values) == 1
        assert values[0].value == 0.5
        # 同 type 再 upsert → ON CONFLICT 更新不新建
        await store.upsert_value(_dv(DesireType.INTERACTION, 0.9, updated_at=2000.0))
        values = await store.list_values()
        assert len(values) == 1
        assert values[0].value == 0.9
        assert values[0].updated_at == 2000.0
    finally:
        await database.conn.close()


async def test_long_term_roundtrip_and_update() -> None:
    database = await db.connect(":memory:")
    store = DesireStore(database)
    try:
        lt = _lt(DesireType.EXPLORATION)
        await store.insert_long_term(lt)
        assert await store.list_long_term() == [lt]   # JSON 数组 + 枚举往返
        lt.progress = 0.4
        lt.strength = 0.75
        await store.update_long_term(lt)
        rows = await store.list_long_term()
        assert rows[0].progress == 0.4
        assert rows[0].strength == 0.75
        assert rows[0].subtopics == ["骑士团", "城堡"]
    finally:
        await database.conn.close()
