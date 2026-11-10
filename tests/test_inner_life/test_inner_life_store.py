from nyx import db
from nyx.enums import EnergyState
from nyx.inner_life.store import InnerLifeStore
from nyx.types import InnerDocument, InnerTopic


async def test_energy_crud() -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    try:
        assert await store.get_energy() is None
        await store.upsert_energy(75.5, EnergyState.OKAY)
        assert await store.get_energy() == (75.5, EnergyState.OKAY)
        await store.upsert_energy(40.0, EnergyState.TIRED)
        assert await store.get_energy() == (40.0, EnergyState.TIRED)
    finally:
        await database.conn.close()


async def test_narrative_crud() -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    try:
        assert await store.get_document() is None
        narrative = InnerDocument(
            topics=[
                InnerTopic("identity", "self-understanding", "自我理解", "尼克斯"),
                InnerTopic("narrative", "experience-0", "个人经历", "第一条故事"),
                InnerTopic("personality", "self-view-0", "自信", "中等"),
                InnerTopic("narrative", "understanding-0", "认知变化", "第一条认知"),
            ],
            revision=1,
            updated_at=1234.5,
        )
        await store.upsert_document(narrative)
        assert await store.get_document() == narrative
    finally:
        await database.conn.close()
