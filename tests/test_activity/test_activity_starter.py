from typing import cast

from nyx.activity.material_store import MaterialStore
from nyx.activity.starter import ActivityStarter
from nyx.activity.store import ActivityStore
from nyx.config import ActivityConfig, ExplorationConfig
from nyx.desire.facade import DesireFacade
from nyx.enums import ActivityType, EmotionCategory, EnergyState
from nyx.types import Activity, CurrentState, DesireState, Personality, Values


class _Store:
    def __init__(self) -> None:
        self.inserted: list[Activity] = []

    async def get_current(self) -> None:
        return None

    async def get_paused_in_block(self, block_id: str) -> None:
        return None

    async def insert(self, activity: Activity) -> None:
        self.inserted.append(activity)


class _MaterialStore:
    pass


class _Desire:
    async def get_pending(self) -> list[object]:
        return []

    async def get_all(self) -> DesireState:
        return DesireState(values=[], short_term=[], long_term=[])


def _state() -> CurrentState:
    personality = cast(
        Personality,
        {
            "openness": 5.0,
            "conscientiousness": 5.0,
            "extraversion": 5.0,
            "agreeableness": 5.0,
            "neuroticism": 5.0,
        },
    )
    values = cast(
        Values,
        {
            "attitude_to_human": 5.0,
            "ai_identity_acceptance": 5.0,
            "altruism": 5.0,
            "optimism": 5.0,
        },
    )
    return CurrentState(
        valence=0.0,
        arousal=0.0,
        emotion=EmotionCategory.NEUTRAL,
        personality=personality,
        values=values,
        aesthetic={
            "ornate": 5.0,
            "lyrical": 5.0,
            "classical": 5.0,
            "somber": 5.0,
        },
        energy=80.0,
        energy_state=EnergyState.OKAY,
        current_activity=None,
        active_desires=[],
    )


async def test_start_next_if_idle_inserts_and_executes_default_activity() -> None:
    store = _Store()
    executed: list[Activity] = []

    async def get_state() -> CurrentState:
        return _state()

    async def execute(activity: Activity) -> None:
        executed.append(activity)

    starter = ActivityStarter(
        cast(ActivityStore, store),
        cast(MaterialStore, _MaterialStore()),
        cast(DesireFacade, _Desire()),
        get_state,
        execute,
        ActivityConfig(),
        ExplorationConfig(),
        lambda: 1.0,
    )

    task = await starter.start_next_if_idle(None)
    assert task is not None
    await task

    assert store.inserted[0].type is ActivityType.OBSERVE_USER
    assert executed == store.inserted
