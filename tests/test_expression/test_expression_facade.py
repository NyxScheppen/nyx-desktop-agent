# pyright: reportPrivateUsage=false
from typing import cast

import pytest

from nyx.config import ExpressionConfig
from nyx.desire.facade import DesireFacade
from nyx.enums import (
    ActivityType,
    DesireType,
    EmotionCategory,
    EnergyState,
    EventType,
    MemoryType,
)
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.expression.facade import ExpressionFacade
from nyx.expression.mutter import _MUTTER_TEMPLATES
from nyx.inner_life.facade import InnerLifeFacade
from nyx.llm.client import LlmClient, LlmMessage
from nyx.memory.facade import MemoryFacade
from nyx.types import (
    CurrentState,
    Event,
    LLMOutput,
    Memory,
    Personality,
    SelfNarrative,
    ShortTermDesire,
    Values,
)

_PERSONALITY: Personality = {
    "openness": 5.0,
    "conscientiousness": 5.0,
    "extraversion": 5.0,
    "agreeableness": 5.0,
    "neuroticism": 5.0,
}

_VALUES: Values = {
    "attitude_to_human": 5.0,
    "ai_identity_acceptance": 5.0,
    "altruism": 5.0,
    "optimism": 5.0,
}

_NARRATIVE = SelfNarrative(
    identity="我是尼克斯，一个想成为人类的 AI。",
    story=[],
    self_view={},
    becoming=[],
    updated_at=0.0,
)


def _mk_state(energy: float, arousal: float) -> CurrentState:
    return CurrentState(
        valence=0.5,
        arousal=arousal,
        emotion=EmotionCategory.NEUTRAL,
        personality=_PERSONALITY,
        values=_VALUES,
        energy=energy,
        energy_state=EnergyState.OKAY,
        current_activity=None,
        active_desires=[],
    )


class _FakeLlm:
    def __init__(
        self,
        speak_override: str | None = None,
        chat_content: str = "你好呀。",
    ) -> None:
        self.calls: list[tuple[str, list[LlmMessage], str]] = []
        self._speak_override = speak_override
        self._chat_content = chat_content
        self._think_n = 0
        self._speak_n = 0

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
    ) -> LLMOutput:
        self.calls.append((output_type, messages, correlation_id))
        if output_type == "think":
            self._think_n += 1
            content = f"想法{self._think_n}"
        elif output_type == "speak":
            if self._speak_override is not None:
                content = self._speak_override
            else:
                self._speak_n += 1
                content = f"回答{self._speak_n}"
        else:
            content = self._chat_content
        return LLMOutput(
            id=f"llm-{len(self.calls)}",
            module=module,
            type=output_type,
            model="fake",
            content=content,
            token_usage={"input": 1, "output": 1},
            correlation_id=correlation_id,
        )


class _FakeEvaluator:
    def __init__(self) -> None:
        self.evaluated: list[LLMOutput] = []

    async def evaluate(self, output: LLMOutput) -> None:
        self.evaluated.append(output)


class _FakeMemory:
    def __init__(self) -> None:
        self.search_calls = 0
        self.scene_memories: list[dict[str, str]] = []

    async def search(self, query: str) -> list[Memory]:
        self.search_calls += 1
        return []

    async def create_scene_memory(self, reply_context: dict[str, str]) -> Memory:
        self.scene_memories.append(reply_context)
        return Memory(
            id="mem-1",
            created_at=0.0,
            content="",
            tag="",
            summary="",
            freshness=1.0,
            type=MemoryType.SHORT_TERM,
        )


class _FakeInnerLife:
    def __init__(self, state: CurrentState) -> None:
        self.state = state
        self.narrative_calls = 0

    async def get_state(self) -> CurrentState:
        return self.state

    async def get_narrative(self) -> SelfNarrative:
        self.narrative_calls += 1
        return _NARRATIVE


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.published.append(event)


def _user_content(messages: list[LlmMessage]) -> str:
    return messages[-1]["content"]


def _new_facade(
    energy: float = 80.0,
    arousal: float = 0.0,
    llm: _FakeLlm | None = None,
) -> tuple[
    ExpressionFacade,
    _FakeLlm,
    _FakeEvaluator,
    _FakeMemory,
    _FakeInnerLife,
    _FakeBus,
]:
    fake_llm = llm if llm is not None else _FakeLlm()
    evaluator = _FakeEvaluator()
    memory = _FakeMemory()
    inner_life = _FakeInnerLife(_mk_state(energy, arousal))
    bus = _FakeBus()
    facade = ExpressionFacade(
        cast(EventBus, bus),
        cast(LlmClient, fake_llm),
        cast(Evaluator, evaluator),
        cast(MemoryFacade, memory),
        cast(DesireFacade, object()),
        cast(InnerLifeFacade, inner_life),
        canon="你是尼克斯，一个想成为人类的 AI。",
        config=ExpressionConfig(),
    )
    return facade, fake_llm, evaluator, memory, inner_life, bus


def _desire() -> ShortTermDesire:
    return ShortTermDesire(
        id="d1",
        created_at=0.0,
        type=DesireType.INTERACTION,
        strength=0.9,
        description="想聊聊天",
        goal=None,
    )


# ---- reply ----


async def test_reply_fast() -> None:
    facade, llm, evaluator, memory, _inner_life, bus = _new_facade(
        energy=20.0, arousal=0.9
    )
    await facade.reply("哦", "corr-fast")
    assert [t for t, _m, _c in llm.calls] == ["think", "speak"]
    assert len(evaluator.evaluated) == 2
    assert memory.search_calls == 0
    assert memory.scene_memories == []
    assert [e.type for e in bus.published] == [EventType.THINK, EventType.SPEAK]


async def test_reply_slow_non_question() -> None:
    facade, llm, _evaluator, memory, _inner_life, bus = _new_facade(
        energy=100.0, arousal=0.0
    )
    await facade.reply("在吗", "corr-slow")
    assert [t for t, _m, _c in llm.calls] == ["think", "speak"] * 3
    assert [e.type for e in bus.published] == [EventType.THINK, EventType.SPEAK] * 3
    assert memory.search_calls == 1
    assert len(memory.scene_memories) == 1
    scene = memory.scene_memories[0]
    assert (scene["nyx_think"], scene["nyx_speak"]) == (
        "想法1\n想法2\n想法3",
        "回答1\n回答2\n回答3",
    )


async def test_reply_slow_question() -> None:
    facade, llm, _evaluator, memory, _inner_life, bus = _new_facade(
        energy=100.0, arousal=0.0, llm=_FakeLlm(speak_override="你还好吗？")
    )
    await facade.reply("在吗", "corr-q")
    assert [t for t, _m, _c in llm.calls] == ["think", "speak"]
    assert [e.type for e in bus.published] == [EventType.THINK, EventType.ASK]
    assert len(memory.scene_memories) == 1


async def test_cumulative_prompt() -> None:
    facade, llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        energy=100.0, arousal=0.0
    )
    await facade.reply("在吗", "corr-cum")
    think_calls = [m for t, m, _c in llm.calls if t == "think"]
    speak_calls = [m for t, m, _c in llm.calls if t == "speak"]
    round2_think = _user_content(think_calls[1])
    round2_speak = _user_content(speak_calls[1])
    assert "第1轮内心：想法1" in round2_think
    assert "第1轮对外：回答1" in round2_think
    assert "[我刚刚的内心想法]\n想法2" in round2_speak


async def test_current_message_not_duplicated() -> None:
    facade, llm, _evaluator, _memory, inner_life, _bus = _new_facade(
        energy=20.0, arousal=0.9
    )
    await facade.reply("哦", "corr-1")
    inner_life.state = _mk_state(100.0, 0.0)
    llm.calls = []
    await facade.reply("在吗", "corr-2")
    first_think = _user_content(
        [m for t, m, _c in llm.calls if t == "think"][0]
    )
    assert "在吗" not in first_think.split("[本次消息]")[0]
    assert first_think.count("[本次消息]") == 1


async def test_history_order() -> None:
    facade, llm, _evaluator, _memory, inner_life, _bus = _new_facade(
        energy=20.0, arousal=0.9
    )
    await facade.reply("哦", "corr-1")
    inner_life.state = _mk_state(100.0, 0.0)
    llm.calls = []
    await facade.reply("在吗", "corr-2")
    assert [m.role for m in facade._history] == ["user", "nyx", "user", "nyx"]
    first_think = _user_content(
        [m for t, m, _c in llm.calls if t == "think"][0]
    )
    assert "用户：哦" in first_think
    assert "Nyx：回答1" in first_think
    assert "用户：在吗" not in first_think


async def test_history_fast_channel() -> None:
    # 两次都走快通道（精力低 + 激动）：第二次回复的 prompt 仍应带上一轮历史
    facade, llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        energy=20.0, arousal=0.9
    )
    await facade.reply("哦", "corr-1")
    llm.calls = []
    await facade.reply("嗯", "corr-2")
    first_think = _user_content(
        [m for t, m, _c in llm.calls if t == "think"][0]
    )
    assert "用户：哦" in first_think
    assert "Nyx：回答1" in first_think


# ---- mutter ----


async def test_mutter_skips_when_busy() -> None:
    facade, _llm, _evaluator, _memory, _inner_life, bus = _new_facade()
    busy = _mk_state(80.0, 0.0)
    busy.current_activity = ActivityType.READING
    await facade.mutter(busy, "corr-m")
    assert bus.published == []


async def test_mutter_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    facade, _llm, _evaluator, _memory, _inner_life, bus = _new_facade()
    monkeypatch.setattr(
        "nyx.expression.facade.random.random",
        iter([0.05, 0.0]).__next__,
    )
    await facade.mutter(_mk_state(80.0, 0.0), "corr-m")
    assert len(bus.published) == 1
    assert bus.published[0].type is EventType.MUTTER
    assert bus.published[0].content["content"] == _MUTTER_TEMPLATES[0]
    assert bus.published[0].correlation_id == "corr-m"


async def test_mutter_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    facade, _llm, _evaluator, _memory, _inner_life, bus = _new_facade()
    monkeypatch.setattr("nyx.expression.facade.random.random", lambda: 0.5)
    await facade.mutter(_mk_state(80.0, 0.0), "corr-m")
    assert bus.published == []


# ---- initiate_chat ----


async def test_initiate_chat_empty() -> None:
    facade, _llm, _evaluator, _memory, _inner_life, bus = _new_facade(
        llm=_FakeLlm(chat_content="   ")
    )
    ok = await facade.initiate_chat(_desire(), _mk_state(80.0, 0.0))
    assert ok is False
    assert bus.published == []


async def test_initiate_chat_non_empty() -> None:
    facade, llm, _evaluator, _memory, _inner_life, bus = _new_facade(
        llm=_FakeLlm(chat_content="你在忙吗？")
    )
    ok = await facade.initiate_chat(_desire(), _mk_state(80.0, 0.0))
    assert ok is True
    assert len(bus.published) == 1
    assert bus.published[0].type is EventType.INITIATE_CHAT
    assert bus.published[0].correlation_id == "d1"
    assert [t for t, _m, _c in llm.calls] == ["initiate_chat"]
