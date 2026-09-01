from nyx import db
from nyx.enums import EnergyState
from nyx.inner_life.store import InnerLifeStore
from nyx.types import Aesthetic, Personality, SelfNarrative, Values

_PERSONALITY: Personality = {
    "openness": 8.0,
    "conscientiousness": 8.0,
    "extraversion": 2.0,
    "agreeableness": 6.0,
    "neuroticism": 7.0,
}

_VALUES: Values = {
    "attitude_to_human": 8.0,
    "ai_identity_acceptance": 6.0,
    "altruism": 9.0,
    "optimism": 5.0,
}

_AESTHETIC: Aesthetic = {
    "ornate": 7.0,
    "lyrical": 7.0,
    "classical": 6.0,
    "somber": 6.0,
}


async def test_personality_crud() -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    try:
        assert await store.get_personality() is None
        await store.upsert_personality(_PERSONALITY)
        assert await store.get_personality() == _PERSONALITY
        changed = _PERSONALITY.copy()
        changed["openness"] = 9.0
        await store.upsert_personality(changed)
        assert await store.get_personality() == changed
    finally:
        await database.conn.close()


async def test_values_crud() -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    try:
        assert await store.get_values() is None
        await store.upsert_values(_VALUES)
        assert await store.get_values() == _VALUES
        changed = _VALUES.copy()
        changed["altruism"] = 1.0
        await store.upsert_values(changed)
        assert await store.get_values() == changed
    finally:
        await database.conn.close()


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


async def test_aesthetic_crud() -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    try:
        assert await store.get_aesthetic() is None
        await store.upsert_aesthetic(_AESTHETIC)
        assert await store.get_aesthetic() == _AESTHETIC
        changed = _AESTHETIC.copy()
        changed["ornate"] = 8.0
        await store.upsert_aesthetic(changed)
        assert await store.get_aesthetic() == changed
    finally:
        await database.conn.close()


async def test_narrative_crud() -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    try:
        assert await store.get_narrative() is None
        narrative = SelfNarrative(
            identity="尼克斯",
            story=["第一条故事"],
            self_view={"自信": "中等"},
            becoming=["第一条认知"],
            updated_at=1234.5,
        )
        await store.upsert_narrative(narrative)
        assert await store.get_narrative() == narrative
    finally:
        await database.conn.close()
