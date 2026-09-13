# pyright: reportPrivateUsage=false
from typing import cast

from nyx.enums import EmotionCategory, EnergyState, EventType, ReadingBehavior
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.expression.facade import ExpressionFacade
from nyx.llm.client import LlmClient, LlmMessage
from nyx.memory.facade import MemoryFacade
from nyx.reading.companions import ReadingCompanion
from nyx.types import CurrentState, Event, LLMOutput, Personality, Values


class _Llm:
    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
        tools: list[dict[str, object]] | None = None,
    ) -> LLMOutput:
        return LLMOutput(
            module=module,
            type=output_type,
            model="fake",
            content="为什么这句话重要？",
            correlation_id=correlation_id,
        )


class _Evaluator:
    async def evaluate(self, output: LLMOutput) -> None:
        return None


class _Bus:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.events.append(event)


class _Memory:
    async def search(self, query: str) -> list[object]:
        return []


class _Expression:
    def __init__(self) -> None:
        self.turns: list[str] = []

    def record_proactive_turn(self, text: str) -> None:
        self.turns.append(text)


def _state() -> CurrentState:
    personality: Personality = {
        "openness": 5.0,
        "conscientiousness": 5.0,
        "extraversion": 5.0,
        "agreeableness": 5.0,
        "neuroticism": 5.0,
    }
    values: Values = {
        "attitude_to_human": 5.0,
        "ai_identity_acceptance": 5.0,
        "altruism": 5.0,
        "optimism": 5.0,
    }
    return CurrentState(
        valence=0.0,
        arousal=0.0,
        emotion=EmotionCategory.NEUTRAL,
        personality=personality,
        values=values,
        aesthetic={"ornate": 5.0, "lyrical": 5.0, "classical": 5.0, "somber": 5.0},
        energy=50.0,
        energy_state=EnergyState.OKAY,
        current_activity=None,
        active_desires=[],
    )


async def test_dispatch_question_publishes_and_records_output() -> None:
    bus = _Bus()
    expression = _Expression()
    recorded: list[tuple[str, int, str, str]] = []

    async def record(book_id: str, index: int, content: str, source: str) -> None:
        recorded.append((book_id, index, content, source))

    companion = ReadingCompanion(
        cast(LlmClient, _Llm()),
        cast(Evaluator, _Evaluator()),
        cast(EventBus, bus),
        cast(MemoryFacade, _Memory()),
        cast(ExpressionFacade, expression),
        "canon",
        record,
    )
    await companion.dispatch(
        "book-1",
        3,
        "一段原文",
        [ReadingBehavior.QUESTION_REFLECTIVE],
        False,
        _state(),
    )

    assert [event.type for event in bus.events] == [EventType.READING_QUESTION]
    assert recorded == [("book-1", 3, "为什么这句话重要？", "question")]
    assert expression.turns == ["为什么这句话重要？"]
