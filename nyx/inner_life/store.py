import json

from nyx.db import Database
from nyx.enums import EnergyState
from nyx.types import Aesthetic, Personality, SelfNarrative, Values

_PERSONALITY_COLS = (
    "openness, conscientiousness, extraversion, agreeableness, neuroticism"
)
_VALUES_COLS = "attitude_to_human, ai_identity_acceptance, altruism, optimism"
_AESTHETIC_COLS = "ornate, lyrical, classical, somber"


class InnerLifeStore:
    """personality / value_system / aesthetic / energy / self_narrative 五张单行表
    （id='self'）的 CRUD。

    db 由组合根注入（同所有 store 共享）。每个方法一个 `async with db.lock` 的 SQL 块。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_personality(self) -> Personality | None:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_PERSONALITY_COLS} FROM personality WHERE id = 'self'"
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "openness": row["openness"],
            "conscientiousness": row["conscientiousness"],
            "extraversion": row["extraversion"],
            "agreeableness": row["agreeableness"],
            "neuroticism": row["neuroticism"],
        }

    async def upsert_personality(self, p: Personality) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                "INSERT INTO personality (id, openness, conscientiousness, "
                "extraversion, "
                "agreeableness, neuroticism) VALUES ('self', ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET openness = excluded.openness, "
                "conscientiousness = excluded.conscientiousness, "
                "extraversion = excluded.extraversion, "
                "agreeableness = excluded.agreeableness, "
                "neuroticism = excluded.neuroticism",
                (p["openness"], p["conscientiousness"], p["extraversion"],
                 p["agreeableness"], p["neuroticism"]),
            )
            await self._db.conn.commit()

    async def get_values(self) -> Values | None:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_VALUES_COLS} FROM value_system WHERE id = 'self'"
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "attitude_to_human": row["attitude_to_human"],
            "ai_identity_acceptance": row["ai_identity_acceptance"],
            "altruism": row["altruism"],
            "optimism": row["optimism"],
        }

    async def upsert_values(self, v: Values) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                "INSERT INTO value_system (id, attitude_to_human, "
                "ai_identity_acceptance, "
                "altruism, optimism) VALUES ('self', ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET attitude_to_human = "
                "excluded.attitude_to_human, "
                "ai_identity_acceptance = excluded.ai_identity_acceptance, "
                "altruism = excluded.altruism, optimism = excluded.optimism",
                (
                    v["attitude_to_human"],
                    v["ai_identity_acceptance"],
                    v["altruism"],
                    v["optimism"],
                ),
            )
            await self._db.conn.commit()

    async def get_aesthetic(self) -> Aesthetic | None:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_AESTHETIC_COLS} FROM aesthetic WHERE id = 'self'"
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "ornate": row["ornate"],
            "lyrical": row["lyrical"],
            "classical": row["classical"],
            "somber": row["somber"],
        }

    async def upsert_aesthetic(self, a: Aesthetic) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                "INSERT INTO aesthetic (id, ornate, lyrical, classical, somber) "
                "VALUES ('self', ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET ornate = excluded.ornate, "
                "lyrical = excluded.lyrical, classical = excluded.classical, "
                "somber = excluded.somber",
                (a["ornate"], a["lyrical"], a["classical"], a["somber"]),
            )
            await self._db.conn.commit()

    async def get_energy(self) -> tuple[float, EnergyState] | None:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT value, state FROM energy WHERE id = 'self'"
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return row["value"], EnergyState(row["state"])

    async def upsert_energy(self, value: float, state: EnergyState) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                "INSERT INTO energy (id, value, state) VALUES ('self', ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET value = excluded.value, "
                "state = excluded.state",
                (value, state.value),
            )
            await self._db.conn.commit()

    async def get_narrative(self) -> SelfNarrative | None:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT identity, story, self_view, becoming, updated_at "
                "FROM self_narrative WHERE id = 'self'"
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return SelfNarrative(
            identity=row["identity"],
            story=json.loads(row["story"]),
            self_view=json.loads(row["self_view"]),
            becoming=json.loads(row["becoming"]),
            updated_at=row["updated_at"],
        )

    async def upsert_narrative(self, n: SelfNarrative) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                "INSERT INTO self_narrative (id, identity, story, self_view, "
                "becoming, updated_at) "
                "VALUES ('self', ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET identity = excluded.identity, "
                "story = excluded.story, self_view = excluded.self_view, "
                "becoming = excluded.becoming, updated_at = excluded.updated_at",
                (n.identity, json.dumps(n.story), json.dumps(n.self_view),
                 json.dumps(n.becoming), n.updated_at),
            )
            await self._db.conn.commit()
