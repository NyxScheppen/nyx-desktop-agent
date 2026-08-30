# pyright: reportPrivateUsage=false
import time
from typing import Any, cast

import pytest

from nyx.activity.facade import ActivityFacade
from nyx.config import ExpressionConfig
from nyx.desire.facade import DesireFacade
from nyx.enums import (
    ActivityStatus,
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
from nyx.expression.mutter import _MUTTER_TEMPLATES, MutterCategory
from nyx.inner_life.facade import InnerLifeFacade
from nyx.llm.client import LlmClient, LlmMessage
from nyx.memory.facade import MemoryFacade
from nyx.tools.registry import ToolRegistry
from nyx.types import (
    Activity,
    CurrentState,
    Event,
    LLMOutput,
    Memory,
    Message,
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
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        self.calls: list[tuple[str, list[LlmMessage], str]] = []
        self._speak_override = speak_override
        self._chat_content = chat_content
        self._tool_calls = list(tool_calls) if tool_calls is not None else []
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
        tools: list[dict[str, Any]] | None = None,
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
            module=module,
            type=output_type,
            model="fake",
            content=content,
            correlation_id=correlation_id,
            tool_calls=list(self._tool_calls),
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
        self.no_answers: list[str] = []
        self.search_results: list[Memory] = []
        self.recalled: list[str] = []
        self.recent_memories: list[Memory] = []
        self.user_profile: list[Memory] = []

    async def search(self, query: str) -> list[Memory]:
        self.search_calls += 1
        return list(self.search_results)

    async def list_memories(
        self,
        tag: str | None = None,
        type: MemoryType | None = None,
        limit: int | None = None,
    ) -> list[Memory]:
        source = self.user_profile if tag == "user" else self.recent_memories
        return list(source[:limit]) if limit is not None else list(source)

    async def record_recall(self, memory_id: str) -> None:
        self.recalled.append(memory_id)

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

    async def record_no_answer(self, question: str, correlation_id: str) -> None:
        self.no_answers.append(question)


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


class _FakeDesire:
    def __init__(self) -> None:
        self.expired: list[str] = []
        self.satisfied: list[tuple[str, bool]] = []

    async def expire(self, desire_id: str) -> None:
        self.expired.append(desire_id)

    async def satisfy(self, desire_id: str, goal_met: bool) -> None:
        self.satisfied.append((desire_id, goal_met))


class _FakeActivity:
    def __init__(self) -> None:
        self.results: list[Activity] = []

    async def get_results(self, limit: int) -> list[Activity]:
        return list(self.results)


class _FakeTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.results: dict[str, Any] = {}

    def schema(self) -> list[dict[str, Any]]:
        return [{"name": "local_search", "description": "search", "parameters": {}}]

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, args))
        return self.results.get(name, [])


def _user_content(messages: list[LlmMessage]) -> str:
    return messages[-1]["content"]


def _new_facade(
    energy: float = 80.0,
    arousal: float = 0.0,
    llm: _FakeLlm | None = None,
    desire: _FakeDesire | None = None,
    tools: _FakeTools | None = None,
    memory: _FakeMemory | None = None,
    activity: _FakeActivity | None = None,
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
    memory = memory if memory is not None else _FakeMemory()
    inner_life = _FakeInnerLife(_mk_state(energy, arousal))
    bus = _FakeBus()
    activity_obj = activity if activity is not None else _FakeActivity()
    desire_obj = (
        cast(DesireFacade, desire)
        if desire is not None
        else cast(DesireFacade, object())
    )
    tools_obj = cast(ToolRegistry, tools if tools is not None else _FakeTools())
    facade = ExpressionFacade(
        cast(EventBus, bus),
        cast(LlmClient, fake_llm),
        cast(Evaluator, evaluator),
        cast(MemoryFacade, memory),
        cast(ActivityFacade, activity_obj),
        desire_obj,
        cast(InnerLifeFacade, inner_life),
        canon="你是尼克斯，一个想成为人类的 AI。",
        ask_guidance="[主动提问指导]\n在合适的时候向用户提问。",
        config=ExpressionConfig(),
        tools=tools_obj,
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


async def test_reply_fast_question_sets_ask() -> None:
    # 快通道问句结尾也置 ask/_waiting_user（快通道绕过 should_ask，问句无人答信号不丢）
    facade, llm, _evaluator, _memory, _inner_life, bus = _new_facade(
        energy=20.0, arousal=0.9, llm=_FakeLlm(speak_override="你还好吗？")
    )
    await facade.reply("哦", "corr-fast-q")
    assert [t for t, _m, _c in llm.calls] == ["think", "speak"]
    assert [e.type for e in bus.published] == [EventType.THINK, EventType.ASK]
    assert facade._waiting_user is True
    assert facade._ask_text == "你还好吗？"
    assert facade._ask_cid == "corr-fast-q"


async def test_reply_slow_non_question() -> None:
    facade, llm, _evaluator, memory, _inner_life, bus = _new_facade(
        energy=100.0, arousal=0.0
    )
    await facade.reply("在吗", "corr-slow")
    assert [t for t, _m, _c in llm.calls] == ["tool"] + ["think", "speak"] * 3
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
    assert [t for t, _m, _c in llm.calls] == ["tool", "think", "speak"]
    assert [e.type for e in bus.published] == [EventType.THINK, EventType.ASK]
    assert len(memory.scene_memories) == 1


async def test_reply_slow_tool_executes_and_flows_into_prompt() -> None:
    tools = _FakeTools()
    tools.results["local_search"] = [{"title": "骑士小说"}]
    facade, llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        energy=100.0,
        arousal=0.0,
        llm=_FakeLlm(tool_calls=[{"name": "local_search", "args": {"q": "骑士"}}]),
        tools=tools,
    )
    await facade.reply("在吗", "corr-tool")
    assert [t for t, _m, _c in llm.calls][0] == "tool"
    assert tools.calls == [("local_search", {"q": "骑士"})]
    think_system = [m[0]["content"] for t, m, _c in llm.calls if t == "think"][0]
    assert "[工具查询结果]" in think_system
    assert "local_search" in think_system


async def test_reply_slow_no_tool_calls() -> None:
    facade, llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        energy=100.0, arousal=0.0
    )
    await facade.reply("在吗", "corr-empty")
    think_system = [m[0]["content"] for t, m, _c in llm.calls if t == "think"][0]
    assert "[工具查询结果]" not in think_system
    assert [t for t, _m, _c in llm.calls][0] == "tool"


async def test_reply_slow_tool_failure_fallback() -> None:
    class _BoomTools(_FakeTools):
        async def call(self, name: str, args: dict[str, Any]) -> Any:
            raise RuntimeError("boom")

    facade, llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        energy=100.0,
        arousal=0.0,
        llm=_FakeLlm(tool_calls=[{"name": "file_io", "args": {}}]),
        tools=_BoomTools(),
    )
    await facade.reply("在吗", "corr-boom")
    think_system = [m[0]["content"] for t, m, _c in llm.calls if t == "think"][0]
    assert "工具 file_io 执行失败" in think_system


async def test_reply_slow_tool_output_truncated() -> None:
    # 大工具结果注入 prompt 时被截断：尾部 sentinel 被裁掉、带「…」省略号
    tools = _FakeTools()
    tools.results["file_io"] = "x" * 5000 + "TAIL_SENTINEL"
    facade, llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        energy=100.0,
        arousal=0.0,
        llm=_FakeLlm(tool_calls=[{"name": "file_io", "args": {}}]),
        tools=tools,
    )
    await facade.reply("在吗", "corr-trunc")
    think_system = [m[0]["content"] for t, m, _c in llm.calls if t == "think"][0]
    assert "file_io" in think_system
    assert "…" in think_system
    assert "TAIL_SENTINEL" not in think_system


async def test_reply_slow_records_recall() -> None:
    # 慢通道检索命中记忆 → 逐条 record_recall（短期→长期升级触发源）
    memory = _FakeMemory()
    memory.search_results = [
        Memory(
            id="m1",
            created_at=0.0,
            content="c1",
            tag="user",
            summary="",
            freshness=1.0,
            type=MemoryType.SHORT_TERM,
        ),
        Memory(
            id="m2",
            created_at=0.0,
            content="c2",
            tag="user",
            summary="",
            freshness=1.0,
            type=MemoryType.SHORT_TERM,
        ),
    ]
    facade, _llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        energy=100.0, arousal=0.0, memory=memory
    )
    await facade.reply("在吗", "corr-recall")
    assert memory.recalled == ["m1", "m2"]


async def test_reply_ask_guidance_slow_only() -> None:
    # 慢通道（精力高+平静）注入主动提问指导，快通道（精力低+激动）不注入
    slow, slow_llm, *_ = _new_facade(energy=100.0, arousal=0.0)
    await slow.reply("在吗", "corr-slow")
    slow_systems = [m[0]["content"] for _t, m, _c in slow_llm.calls]
    assert slow_systems
    assert all("[主动提问指导]" in s for s in slow_systems)

    fast, fast_llm, *_ = _new_facade(energy=20.0, arousal=0.9)
    await fast.reply("哦", "corr-fast")
    fast_systems = [m[0]["content"] for _t, m, _c in fast_llm.calls]
    assert fast_systems
    assert all("[主动提问指导]" not in s for s in fast_systems)


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


async def test_slow_channel_progressive() -> None:
    # 慢通道三段递进：第 1 段是「第一句话」，第 2 段起是「接着往下说」续写
    facade, llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        energy=100.0, arousal=0.0
    )
    await facade.reply("在吗", "corr-prog")
    speak_calls = [m for t, m, _c in llm.calls if t == "speak"]
    first = _user_content(speak_calls[0])
    second = _user_content(speak_calls[1])
    assert "第一句话" in first
    assert "继续往下说" not in first
    assert "继续往下说" in second


async def test_current_message_not_duplicated() -> None:
    # 慢通道 + 相关历史：当前消息只在 [本次消息]，不混进 [对话历史]
    facade, llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        energy=100.0, arousal=0.0
    )
    facade._history.append(
        Message(role="user", content="我上周去爬山了", timestamp=time.time())
    )
    await facade.reply("你喜欢爬山吗", "corr-2")
    first_think = _user_content(
        [m for t, m, _c in llm.calls if t == "think"][0]
    )
    assert "你喜欢爬山吗" not in first_think.split("[本次消息]")[0]
    assert first_think.count("[本次消息]") == 1


async def test_history_order() -> None:
    # 会话历史按「用户 → Nyx」交替累积（快慢通道都落历史）
    facade, _llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        energy=20.0, arousal=0.9
    )
    await facade.reply("哦", "corr-1")
    await facade.reply("在吗", "corr-2")
    assert [m.role for m in facade._history] == ["user", "nyx", "user", "nyx"]


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


async def test_record_message_marks_fast() -> None:
    # 快通道 nyx 消息标 fast=True、慢通道标 False（回溯截断的依据）
    fast, *_ = _new_facade(energy=20.0, arousal=0.9)
    await fast.reply("哦", "corr-fast")
    assert fast._history[-1].fast is True

    slow, *_ = _new_facade(energy=100.0, arousal=0.0)
    await slow.reply("在吗", "corr-slow")
    assert slow._history[-1].fast is False


async def test_reply_slow_backtrack_skips_fast_nyx() -> None:
    # 慢通道回溯：跳过快通道 nyx 消息、保留相关用户消息（端到端接线）
    facade, llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        energy=100.0, arousal=0.0
    )
    facade._history.append(
        Message(role="user", content="我上周去爬山了", timestamp=time.time())
    )
    facade._history.append(
        Message(role="nyx", content="嗯嗯", timestamp=time.time(), fast=True)
    )
    await facade.reply("你喜欢爬山吗", "corr-bt")
    think_user = _user_content([m for t, m, _c in llm.calls if t == "think"][0])
    assert "用户：我上周去爬山了" in think_user
    assert "Nyx：嗯嗯" not in think_user


# ---- mutter ----


def _mk_memory(summary: str, tag: str = "") -> Memory:
    return Memory(
        id="m1",
        created_at=0.0,
        content="",
        tag=tag,
        summary=summary,
        freshness=1.0,
        type=MemoryType.SHORT_TERM,
    )


def _mk_activity(type_: ActivityType) -> Activity:
    return Activity(
        id="a1",
        type=type_,
        schedule_block_id="",
        status=ActivityStatus.COMPLETED,
        progress={},
        started_at=0.0,
    )


async def test_mutter_skips_when_busy() -> None:
    facade, _llm, _evaluator, _memory, _inner_life, bus = _new_facade()
    busy = _mk_state(80.0, 0.0)
    busy.current_activity = ActivityType.READING
    await facade.mutter(busy, "corr-m")
    assert bus.published == []


async def test_mutter_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    facade, _llm, _evaluator, _memory, _inner_life, bus = _new_facade()
    monkeypatch.setattr("nyx.expression.facade.random.random", lambda: 0.5)
    await facade.mutter(_mk_state(80.0, 0.0), "corr-m")
    assert bus.published == []


async def test_mutter_activity_fills(monkeypatch: pytest.MonkeyPatch) -> None:
    activity = _FakeActivity()
    activity.results = [_mk_activity(ActivityType.READING)]
    facade, _llm, _evaluator, _memory, _inner_life, bus = _new_facade(activity=activity)
    monkeypatch.setattr(
        "nyx.expression.facade.random.random",
        iter([0.05, 0.0, 0.0]).__next__,
    )
    await facade.mutter(_mk_state(80.0, 0.0), "corr-m")
    assert len(bus.published) == 1
    assert bus.published[0].content["content"] == (
        _MUTTER_TEMPLATES[MutterCategory.ACTIVITY][0].format(activity="读书")
    )


async def test_mutter_memory_fills(monkeypatch: pytest.MonkeyPatch) -> None:
    memory = _FakeMemory()
    memory.recent_memories = [_mk_memory("你上周去爬山了")]
    facade, _llm, _evaluator, _memory, _inner_life, bus = _new_facade(memory=memory)
    monkeypatch.setattr(
        "nyx.expression.facade.random.random",
        iter([0.05, 0.25, 0.0]).__next__,
    )
    await facade.mutter(_mk_state(80.0, 0.0), "corr-m")
    assert bus.published[0].content["content"] == (
        _MUTTER_TEMPLATES[MutterCategory.MEMORY][0].format(memory="你上周去爬山了")
    )


async def test_mutter_desire_fills(monkeypatch: pytest.MonkeyPatch) -> None:
    facade, _llm, _evaluator, _memory, _inner_life, bus = _new_facade()
    state = _mk_state(80.0, 0.0)
    state.active_desires = [
        ShortTermDesire(
            id="d1",
            created_at=0.0,
            type=DesireType.INTERACTION,
            strength=1.0,
            description="想聊聊天",
            goal=None,
        )
    ]
    monkeypatch.setattr(
        "nyx.expression.facade.random.random",
        iter([0.05, 0.5, 0.0]).__next__,
    )
    await facade.mutter(state, "corr-m")
    assert bus.published[0].content["content"] == (
        _MUTTER_TEMPLATES[MutterCategory.DESIRE][0].format(desire="想聊聊天")
    )


async def test_mutter_user_fills(monkeypatch: pytest.MonkeyPatch) -> None:
    memory = _FakeMemory()
    memory.user_profile = [_mk_memory("你喜欢安静", tag="user")]
    facade, _llm, _evaluator, _memory, _inner_life, bus = _new_facade(memory=memory)
    monkeypatch.setattr(
        "nyx.expression.facade.random.random",
        iter([0.05, 0.75, 0.0]).__next__,
    )
    await facade.mutter(_mk_state(80.0, 0.0), "corr-m")
    assert bus.published[0].content["content"] == (
        _MUTTER_TEMPLATES[MutterCategory.USER][0].format(user="你喜欢安静")
    )


async def test_mutter_no_data_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    facade, _llm, _evaluator, _memory, _inner_life, bus = _new_facade()
    monkeypatch.setattr(
        "nyx.expression.facade.random.random",
        iter([0.05, 0.0]).__next__,
    )
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
    assert "[主动提问指导]" in llm.calls[0][1][0]["content"]


async def test_initiate_chat_appends_history() -> None:
    # 搭话开场白应落会话历史：用户随后回复能回溯到这句搭话（记忆互通）
    facade, _llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        llm=_FakeLlm(chat_content="你在忙吗？")
    )
    await facade.initiate_chat(_desire(), _mk_state(80.0, 0.0))
    assert [m.role for m in facade._history] == ["nyx"]
    assert facade._history[0].content == "你在忙吗？"


# ---- wait_user / 搭话被忽略回灌（V2 表达交互闭环） ----


async def test_reply_question_sets_waiting_user() -> None:
    # 慢通道问句结尾 → reply 置 wait_user 状态（供 tick 超时收尾）
    facade, _llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        energy=100.0, arousal=0.0, llm=_FakeLlm(speak_override="你还好吗？")
    )
    await facade.reply("在吗", "corr-q")
    assert facade._waiting_user is True
    assert facade._ask_text == "你还好吗？"
    assert facade._ask_cid == "corr-q"


async def test_reply_clears_pending_state() -> None:
    # 用户说话即视为回应：清 wait_user + 待回搭话，并 satisfy 该互动欲
    desire = _FakeDesire()
    facade, _llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        energy=20.0, arousal=0.9, desire=desire
    )
    facade._waiting_user = True
    facade._ask_cid = "corr-old"
    facade._pending_chat_desire_id = "d1"
    await facade.reply("哦", "corr-new")
    assert facade._waiting_user is False
    assert facade._ask_cid is None
    assert facade._pending_chat_desire_id is None
    assert desire.satisfied == [("d1", True)]


async def test_initiate_chat_sets_pending_desire() -> None:
    # 搭话发出 → 记「待回应」互动欲，超时未回则 check_timeouts 回灌
    facade, _llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        llm=_FakeLlm(chat_content="你在忙吗？")
    )
    await facade.initiate_chat(_desire(), _mk_state(80.0, 0.0))
    assert facade._pending_chat_desire_id == "d1"


async def test_check_timeouts_records_no_answer() -> None:
    # wait_user 超时 → 落一条「用户没回答」记忆，清等待态
    facade, _llm, _evaluator, memory, _inner_life, _bus = _new_facade()
    facade._waiting_user = True
    facade._ask_text = "你还好吗？"
    facade._ask_cid = "corr-ask"
    facade._ask_at = 100.0
    await facade.check_timeouts(100.0 + ExpressionConfig().ask_timeout)
    assert memory.no_answers == ["你还好吗？"]
    assert facade._waiting_user is False
    assert facade._ask_cid is None


async def test_check_timeouts_before_timeout_noop() -> None:
    # 未到超时点 → 不动作（wait_user 与待回搭话都保持）
    facade, _llm, _evaluator, memory, _inner_life, _bus = _new_facade()
    facade._waiting_user = True
    facade._ask_text = "你还好吗？"
    facade._ask_cid = "corr-ask"
    facade._ask_at = 100.0
    facade._pending_chat_desire_id = "d1"
    facade._chat_at = 100.0
    await facade.check_timeouts(100.0 + 1.0)
    assert memory.no_answers == []
    assert facade._waiting_user is True
    assert facade._pending_chat_desire_id == "d1"


async def test_check_timeouts_expires_ignored_chat() -> None:
    # 搭话超时未回 → expire 该互动欲（内部值回灌 +0.3），清待回应
    desire = _FakeDesire()
    facade, _llm, _evaluator, _memory, _inner_life, _bus = _new_facade(desire=desire)
    facade._pending_chat_desire_id = "d1"
    facade._chat_at = 100.0
    await facade.check_timeouts(100.0 + ExpressionConfig().chat_ignore_timeout)
    assert desire.expired == ["d1"]
    assert facade._pending_chat_desire_id is None
