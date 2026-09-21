# pyright: reportPrivateUsage=false
import asyncio
import json
import time
from datetime import datetime
from typing import Any, cast

import pytest

from nyx import db
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
    InteractionKind,
    InteractionStatus,
    MemoryKind,
    MemoryType,
    Source,
)
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.expression.facade import ExpressionFacade
from nyx.expression.mutter import _MUTTER_SKELETONS, MutterCategory
from nyx.expression.store import ExpressionInteractionStore
from nyx.inner_life.facade import InnerLifeFacade
from nyx.llm.client import LlmClient, LlmMessage
from nyx.memory.facade import MemoryFacade
from nyx.tools.registry import ToolRegistry
from nyx.types import (
    Activity,
    CurrentState,
    Event,
    InteractionAttempt,
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
        aesthetic={
            "ornate": 7.0, "lyrical": 7.0, "classical": 6.0, "somber": 6.0,
        },
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
        if output_type == "reply":
            self._think_n += 1
            think = f"想法{self._think_n}"
            if self._speak_override is not None:
                speak = self._speak_override
            else:
                self._speak_n += 1
                speak = f"回答{self._speak_n}"
            content = json.dumps({"think": think, "speak": speak}, ensure_ascii=False)
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
        self.fact_search_calls = 0
        self.scene_memories: list[dict[str, str]] = []
        self.no_answers: list[str] = []
        self.search_results: list[Memory] = []
        self.recalled: list[str] = []
        self.recent_memories: list[Memory] = []
        self.user_profile: list[Memory] = []

    async def search(self, query: str) -> list[Memory]:
        self.search_calls += 1
        return list(self.search_results)

    async def search_facts(self, query: str) -> list[object]:
        self.fact_search_calls += 1
        return []

    async def list_memories(
        self,
        kind: MemoryKind | None = None,
        type: MemoryType | None = None,
        limit: int | None = None,
    ) -> list[Memory]:
        source = (
            self.user_profile
            if kind is MemoryKind.USER_PROFILE
            else self.recent_memories
        )
        return list(source[:limit]) if limit is not None else list(source)

    async def record_recall(self, memory_id: str) -> None:
        self.recalled.append(memory_id)

    async def create_scene_memory(self, reply_context: dict[str, str]) -> Memory:
        self.scene_memories.append(reply_context)
        return Memory(
            id="mem-1",
            created_at=0.0,
            content="",
            kind=MemoryKind.EPISODE,
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

    async def is_durable(self, event_id: str) -> bool:
        return any(event.id == event_id for event in self.published)

    async def list_events(
        self,
        limit: int = 100,
        event_type: EventType | None = None,
        correlation_id: str | None = None,
    ) -> list[Event]:
        events = [
            event
            for event in self.published
            if (event_type is None or event.type is event_type)
            and (correlation_id is None or event.correlation_id == correlation_id)
        ]
        return sorted(
            events, key=lambda event: (event.timestamp, event.id), reverse=True
        )[:limit]


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


class _ReturnState:
    def __init__(self) -> None:
        self.pending: dict[str, float] | None = {
            "returned_at": time.time(),
            "away_duration_seconds": 600.0,
        }
        self.finished = 0
        self.released = 0
        self.claimed: dict[str, float] | None = None

    def claim(self) -> dict[str, float] | None:
        if self.claimed is not None:
            return None
        claim = self.pending
        self.pending = None
        self.claimed = claim
        return claim

    def finish(self, claim: dict[str, float] | None) -> None:
        if claim is not None and self.claimed is claim:
            self.claimed = None
            self.finished += 1

    def release(self, claim: dict[str, float] | None) -> None:
        if claim is not None and self.claimed is claim:
            self.claimed = None
            self.released += 1
            if self.pending is None:
                self.pending = claim


async def _observation() -> dict[str, object]:
    return {"presence": "online", "window_title": "编辑器"}


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
    interaction_store: ExpressionInteractionStore | None = None,
    return_state: _ReturnState | None = None,
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
        interaction_store=interaction_store,
        observation_reader=_observation,
        claim_return=(return_state.claim if return_state is not None else None),
        finish_return=(return_state.finish if return_state is not None else None),
        release_return=(return_state.release if return_state is not None else None),
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


def _event(
    id_: str,
    timestamp: float,
    type_: EventType,
    content: dict[str, object],
    correlation_id: str,
) -> Event:
    return Event(
        id=id_,
        timestamp=timestamp,
        source=Source.EXTERNAL if type_ is EventType.USER_MESSAGE else Source.INTERNAL,
        type=type_,
        content=content,
        correlation_id=correlation_id,
    )


# ---- reply ----


@pytest.mark.parametrize("failure", ["scene", "second_round", "cancel"])
async def test_return_is_consumed_when_normal_reply_precedes_failure(
    failure: str,
) -> None:
    scene_entered = asyncio.Event()

    class _SceneFailure(_FakeMemory):
        async def create_scene_memory(self, reply_context: dict[str, str]) -> Memory:
            if failure == "cancel":
                scene_entered.set()
                await asyncio.Event().wait()
            if failure == "scene":
                raise RuntimeError("scene failure")
            return await super().create_scene_memory(reply_context)

    class _LaterFailure(_FakeLlm):
        async def complete(
            self, messages: list[LlmMessage], **kwargs: Any
        ) -> LLMOutput:
            if (
                failure == "second_round"
                and kwargs["output_type"] == "reply" and self._speak_n
            ):
                raise RuntimeError("later round failure")
            return await super().complete(messages, **kwargs)

    returns = _ReturnState()
    facade, _llm, _eval, _memory, _inner, bus = _new_facade(
        llm=_LaterFailure(), memory=_SceneFailure(), return_state=returns,
    )
    if failure == "second_round":
        await facade.reply("为什么" * 30 + "？", "partial")
    elif failure == "cancel":
        task = asyncio.create_task(facade.reply("为什么" * 30 + "？", "partial"))
        await asyncio.wait_for(scene_entered.wait(), timeout=1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(RuntimeError):
            await facade.reply("为什么" * 30 + "？", "partial")

    assert any(e.type is EventType.SPEAK for e in bus.published)
    assert returns.finished == 1
    assert returns.pending is None
    assert returns.released == 0


async def test_last_dialogue_anchor_skips_current_and_incomplete_turns() -> None:
    facade, _llm, _evaluator, _memory, _inner_life, bus = _new_facade()
    bus.published.extend(
        [
            _event("u-old", 10.0, EventType.USER_MESSAGE, {"message": "旧问题"}, "old"),
            _event("t-old", 11.0, EventType.THINK, {"content": "不应引用"}, "old"),
            _event("s-old", 12.0, EventType.SPEAK, {"content": "旧回答"}, "old"),
            _event("a-old", 13.0, EventType.ASK, {"content": "最后追问"}, "old"),
            _event("u-half", 20.0, EventType.USER_MESSAGE, {"message": "半截"}, "half"),
            _event(
                "u-now", 30.0, EventType.USER_MESSAGE, {"message": "当前"}, "current"
            ),
        ]
    )

    anchor = await facade._last_dialogue_anchor("current")

    assert anchor is not None
    assert (anchor[0].content, anchor[0].timestamp) == ("旧问题", 10.0)
    assert (anchor[1].content, anchor[1].timestamp) == ("最后追问", 13.0)


async def test_last_dialogue_anchor_returns_none_without_complete_turn() -> None:
    facade, _llm, _evaluator, _memory, _inner_life, bus = _new_facade()
    bus.published.append(
        _event("u-half", 20.0, EventType.USER_MESSAGE, {"message": "半截"}, "half")
    )

    assert await facade._last_dialogue_anchor(None) is None


async def test_reply_recovers_overnight_dialogue_from_durable_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evening = datetime(2026, 9, 17, 19).astimezone().timestamp()
    morning = datetime(2026, 9, 18, 8).astimezone().timestamp()
    database = await db.connect(":memory:")
    try:
        bus = EventBus(database)
        await bus.publish(
            _event(
                "dinner-user",
                evening,
                EventType.USER_MESSAGE,
                {"message": "尼克斯，我去吃饭了"},
                "dinner",
            )
        )
        await bus.publish(
            _event(
                "dinner-nyx",
                evening + 1,
                EventType.SPEAK,
                {"content": "好的，我在这里等着你"},
                "dinner",
            )
        )
        # A fresh facade has no in-memory history, as after a backend restart.
        facade, llm, _eval, _memory, _inner, _fake_bus = _new_facade(
            energy=20.0, arousal=0.9
        )
        facade._bus = bus
        monkeypatch.setattr("nyx.expression.facade.time.time", lambda: morning)

        await facade.reply("早上好，尼克斯", "morning")

        system = llm.calls[0][1][0]["content"]
        assert "2026-09-18" in system and "早上 08:00" in system
        assert "13小时" in system and "昨天晚上" in system
        assert "用户已经很久没有和你说话了" in system
        assert "尼克斯，我去吃饭了" in system and "好的，我在这里等着你" in system
        assert facade._history[-1].role == "nyx"
    finally:
        await database.close()


async def test_reply_fast() -> None:
    facade, llm, evaluator, memory, _inner_life, bus = _new_facade(
        energy=20.0, arousal=0.9
    )
    await facade.reply("哦", "corr-fast")
    assert [t for t, _m, _c in llm.calls] == ["reply"]
    assert len(evaluator.evaluated) == 2
    assert memory.search_calls == 0
    assert memory.fact_search_calls == 1
    assert memory.scene_memories == []
    assert [e.type for e in bus.published] == [EventType.THINK, EventType.SPEAK]


async def test_reply_fast_uses_temporal_context_and_consumes_return() -> None:
    return_state = _ReturnState()
    facade, llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        energy=20.0, arousal=0.9, return_state=return_state
    )

    await facade.reply("哦", "corr-temporal")

    system = llm.calls[0][1][0]["content"]
    assert "[时间与重逢上下文]" in system
    assert "用户刚刚回来" in system
    assert "当前观察：用户状态为 online" in system
    assert return_state.finished == 1
    assert return_state.released == 0


async def test_reply_cancelled_at_publish_return_does_not_restore_durable_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    returns = _ReturnState()
    facade, _llm, _eval, _memory, _inner, bus = _new_facade(
        energy=20.0, arousal=0.9, return_state=returns
    )
    committed = asyncio.Event()
    original = bus.publish

    async def publish_then_pause(event: Event) -> None:
        await original(event)
        if event.type is EventType.SPEAK:
            committed.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(bus, "publish", publish_then_pause)
    task = asyncio.create_task(facade.reply("哦", "commit-cancel"))
    await asyncio.wait_for(committed.wait(), 1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (returns.finished, returns.released) == (1, 0)
    assert returns.pending is None


async def test_reply_fallback_releases_return_context() -> None:
    class _FailLlm(_FakeLlm):
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
            del messages, module, output_type, correlation_id, json_mode, tools
            raise RuntimeError("llm down")

    return_state = _ReturnState()
    facade, _llm, _evaluator, _memory, _inner_life, bus = _new_facade(
        energy=20.0,
        arousal=0.9,
        llm=_FailLlm(),
        return_state=return_state,
    )

    await facade.reply("哦", "corr-fallback")

    assert bus.published[-1].content["response_kind"] == "fallback"
    assert return_state.finished == 0
    assert return_state.released == 1
    assert return_state.pending is not None


async def test_reply_fast_question_sets_ask() -> None:
    # 快通道问句结尾也置 ask/_waiting_user（快通道绕过 should_ask，问句无人答信号不丢）
    facade, llm, _evaluator, _memory, _inner_life, bus = _new_facade(
        energy=20.0, arousal=0.9, llm=_FakeLlm(speak_override="你还好吗？")
    )
    await facade.reply("哦", "corr-fast-q")
    assert [t for t, _m, _c in llm.calls] == ["reply"]
    assert [e.type for e in bus.published] == [EventType.THINK, EventType.ASK]
    assert facade._waiting_user is True
    assert facade._ask_text == "你还好吗？"
    assert facade._ask_cid == "corr-fast-q"


async def test_reply_slow_non_question() -> None:
    facade, llm, _evaluator, memory, _inner_life, bus = _new_facade(
        energy=100.0, arousal=0.0
    )
    await facade.reply("在吗", "corr-slow")
    assert [t for t, _m, _c in llm.calls] == ["tool"] + ["reply"] * 3
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
    assert [t for t, _m, _c in llm.calls] == ["tool", "reply"]
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
    tool_system = llm.calls[0][1][0]["content"]
    think_system = [m[0]["content"] for t, m, _c in llm.calls if t == "reply"][0]
    time_blocks = [
        messages[0]["content"].split("[时间与重逢上下文]", 1)[1].split(
            "[当前欲望]", 1
        )[0]
        for _type, messages, _cid in llm.calls
    ]
    assert "[时间与重逢上下文]" in tool_system and len(set(time_blocks)) == 1
    assert "[工具查询结果]" in think_system
    assert "local_search" in think_system


async def test_reply_slow_no_tool_calls() -> None:
    facade, llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        energy=100.0, arousal=0.0
    )
    await facade.reply("在吗", "corr-empty")
    think_system = [m[0]["content"] for t, m, _c in llm.calls if t == "reply"][0]
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
    think_system = [m[0]["content"] for t, m, _c in llm.calls if t == "reply"][0]
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
    think_system = [m[0]["content"] for t, m, _c in llm.calls if t == "reply"][0]
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
            kind=MemoryKind.USER_PROFILE,
            summary="",
            freshness=1.0,
            type=MemoryType.SHORT_TERM,
        ),
        Memory(
            id="m2",
            created_at=0.0,
            content="c2",
            kind=MemoryKind.USER_PROFILE,
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
    # 一轮 think+speak 一次生成：第 2 轮 prompt 带前一轮 think/speak 累积，
    # 不再单独注入「我刚刚的内心想法」（think/speak 同源，无需事后拼接）。
    facade, llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        energy=100.0, arousal=0.0
    )
    await facade.reply("在吗", "corr-cum")
    reply_calls = [m for t, m, _c in llm.calls if t == "reply"]
    round2 = _user_content(reply_calls[1])
    assert "第1轮内心：想法1" in round2
    assert "第1轮对外：回答1" in round2
    assert "[我刚刚的内心想法]" not in round2


async def test_slow_channel_progressive() -> None:
    # 慢通道三段递进：第 1 段是「说出口的第一句话」，第 2 段起是「往下说一句」续写
    facade, llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        energy=100.0, arousal=0.0
    )
    await facade.reply("在吗", "corr-prog")
    reply_calls = [m for t, m, _c in llm.calls if t == "reply"]
    first = _user_content(reply_calls[0])
    second = _user_content(reply_calls[1])
    assert "说出口的第一句话" in first
    assert "往下说一句" not in first
    assert "往下说一句" in second


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
        [m for t, m, _c in llm.calls if t == "reply"][0]
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
        [m for t, m, _c in llm.calls if t == "reply"][0]
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


def test_record_proactive_turn_appends_to_history() -> None:
    facade, *_ = _new_facade()
    facade.record_proactive_turn("她在读书时问了个问题")
    assert facade._history[-1].role == "nyx"
    assert facade._history[-1].content == "她在读书时问了个问题"
    assert facade._history[-1].fast is False


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
    think_user = _user_content([m for t, m, _c in llm.calls if t == "reply"][0])
    assert "用户：我上周去爬山了" in think_user
    assert "Nyx：嗯嗯" not in think_user


async def test_reading_turn_slow_backtrack_preserved() -> None:
    # 读书 turn（fast=False）在慢通道回溯里保留——与
    # test_reply_slow_backtrack_skips_fast_nyx 各证一半，
    # （fast=True 跳 / fast=False 留）合起来闭合 fast 标志的唯一消费方契约。
    facade, llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        energy=100.0, arousal=0.0
    )
    facade.record_proactive_turn("她刚才问：你觉得自由是什么")
    await facade.reply("自由很虚无", "corr-bt")
    think_user = _user_content([m for t, m, _c in llm.calls if t == "reply"][0])
    assert "Nyx：她刚才问：你觉得自由是什么" in think_user


# ---- mutter ----


def _mk_memory(
    summary: str, kind: MemoryKind = MemoryKind.EPISODE
) -> Memory:
    return Memory(
        id="m1",
        created_at=0.0,
        content="",
        kind=kind,
        summary=summary,
        freshness=1.0,
        type=MemoryType.SHORT_TERM,
    )


def _mk_activity(
    type_: ActivityType, result: dict[str, Any] | None = None
) -> Activity:
    return Activity(
        id="a1",
        type=type_,
        schedule_block_id="",
        status=ActivityStatus.COMPLETED,
        progress={"result": result or {}},
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
    activity.results = [_mk_activity(ActivityType.READING, {"book": "挪威的森林"})]
    facade, _llm, _evaluator, _memory, _inner_life, bus = _new_facade(activity=activity)
    monkeypatch.setattr(
        "nyx.expression.facade.random.random",
        iter([0.05, 0.9, 0.0, 0.0]).__next__,
    )
    await facade.mutter(_mk_state(80.0, 0.0), "corr-m")
    assert len(bus.published) == 1
    assert bus.published[0].content["content"] == (
        _MUTTER_SKELETONS[MutterCategory.ACTIVITY][0].format(subject="读了《挪威的森林》")
    )


async def test_mutter_memory_fills(monkeypatch: pytest.MonkeyPatch) -> None:
    memory = _FakeMemory()
    memory.recent_memories = [_mk_memory("你上周去爬山了")]
    facade, _llm, _evaluator, _memory, _inner_life, bus = _new_facade(memory=memory)
    monkeypatch.setattr(
        "nyx.expression.facade.random.random",
        iter([0.05, 0.9, 0.25, 0.0]).__next__,
    )
    await facade.mutter(_mk_state(80.0, 0.0), "corr-m")
    assert bus.published[0].content["content"] == (
        _MUTTER_SKELETONS[MutterCategory.MEMORY][0].format(subject="你上周去爬山了")
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
        iter([0.05, 0.9, 0.5, 0.0]).__next__,
    )
    await facade.mutter(state, "corr-m")
    assert bus.published[0].content["content"] == (
        _MUTTER_SKELETONS[MutterCategory.DESIRE][0].format(subject="想聊聊天")
    )


async def test_mutter_user_fills(monkeypatch: pytest.MonkeyPatch) -> None:
    memory = _FakeMemory()
    memory.user_profile = [
        _mk_memory("你喜欢安静", kind=MemoryKind.USER_PROFILE)
    ]
    facade, _llm, _evaluator, _memory, _inner_life, bus = _new_facade(memory=memory)
    monkeypatch.setattr(
        "nyx.expression.facade.random.random",
        iter([0.05, 0.9, 0.75, 0.0]).__next__,
    )
    await facade.mutter(_mk_state(80.0, 0.0), "corr-m")
    assert bus.published[0].content["content"] == (
        _MUTTER_SKELETONS[MutterCategory.USER][0].format(subject="你喜欢安静")
    )


async def test_mutter_user_naturalizes_presence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 用户画像 summary 是观察串「用户（away）」→ 润色成「你走开了」，raw 枚举不泄漏
    memory = _FakeMemory()
    memory.user_profile = [
        _mk_memory("用户（away）", kind=MemoryKind.USER_PROFILE)
    ]
    facade, _llm, _evaluator, _memory, _inner_life, bus = _new_facade(memory=memory)
    monkeypatch.setattr(
        "nyx.expression.facade.random.random",
        iter([0.05, 0.9, 0.75, 0.0]).__next__,
    )
    await facade.mutter(_mk_state(80.0, 0.0), "corr-m")
    content = bus.published[0].content["content"]
    assert "你走开了" in content
    assert "away" not in content


async def test_mutter_llm_wander(monkeypatch: pytest.MonkeyPatch) -> None:
    return_state = _ReturnState()
    facade, llm, _evaluator, _memory, _inner_life, bus = _new_facade(
        llm=_FakeLlm(chat_content="嗯……有点走神了。"),
        return_state=return_state,
    )
    monkeypatch.setattr(
        "nyx.expression.facade.random.random",
        iter([0.05, 0.0]).__next__,
    )
    await facade.mutter(_mk_state(80.0, 0.0), "corr-m")
    assert [t for t, _m, _c in llm.calls] == ["mutter_wander"]
    assert "[时间与重逢上下文]" in llm.calls[0][1][0]["content"]
    assert len(bus.published) == 1
    assert bus.published[0].content["content"] == "嗯……有点走神了。"
    assert (return_state.finished, return_state.released) == (1, 0)


async def test_mutter_llm_wander_empty_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # LLM 即兴空 → 回退模板填空
    activity = _FakeActivity()
    activity.results = [_mk_activity(ActivityType.READING, {"book": "挪威的森林"})]
    return_state = _ReturnState()
    facade, llm, _evaluator, _memory, _inner_life, bus = _new_facade(
        activity=activity,
        llm=_FakeLlm(chat_content="   "),
        return_state=return_state,
    )
    monkeypatch.setattr(
        "nyx.expression.facade.random.random",
        iter([0.05, 0.0, 0.0, 0.0]).__next__,
    )
    await facade.mutter(_mk_state(80.0, 0.0), "corr-m")
    assert [t for t, _m, _c in llm.calls] == ["mutter_wander"]
    assert len(bus.published) == 1
    assert bus.published[0].content["content"] == (
        _MUTTER_SKELETONS[MutterCategory.ACTIVITY][0].format(
            subject="读了《挪威的森林》"
        )
    )
    assert return_state.finished == 0
    assert return_state.released == 1


async def test_mutter_dedup(monkeypatch: pytest.MonkeyPatch) -> None:
    # 连续两次同样文本 → 第二次被去重抑制
    activity = _FakeActivity()
    activity.results = [_mk_activity(ActivityType.READING, {"book": "挪威的森林"})]
    facade, _llm, _evaluator, _memory, _inner_life, bus = _new_facade(activity=activity)
    monkeypatch.setattr(
        "nyx.expression.facade.random.random",
        iter([0.05, 0.9, 0.0, 0.0, 0.05, 0.9, 0.0, 0.0]).__next__,
    )
    await facade.mutter(_mk_state(80.0, 0.0), "corr-m1")
    await facade.mutter(_mk_state(80.0, 0.0), "corr-m2")
    assert len(bus.published) == 1
    assert bus.published[0].correlation_id == "corr-m1"


async def test_mutter_no_data_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    facade, _llm, _evaluator, _memory, _inner_life, bus = _new_facade()
    monkeypatch.setattr(
        "nyx.expression.facade.random.random",
        iter([0.05, 0.9, 0.0]).__next__,
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
    assert "[时间与重逢上下文]" in llm.calls[0][1][0]["content"]


async def test_initiate_chat_consumes_return_only_after_commit() -> None:
    return_state = _ReturnState()
    facade, _llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        llm=_FakeLlm(chat_content="你在忙吗？"), return_state=return_state
    )

    assert await facade.initiate_chat(_desire(), _mk_state(80.0, 0.0)) is True
    assert return_state.finished == 1
    assert return_state.released == 0


@pytest.mark.parametrize("kind", ["ask", "initiate_chat"])
async def test_committed_expression_does_not_restore_return_on_announce_failure(
    kind: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = await db.connect(":memory:")
    try:
        store = ExpressionInteractionStore(database)
        returns = _ReturnState()
        facade, *_ = _new_facade(interaction_store=store, return_state=returns)
        bus = EventBus(database)
        facade._bus = bus

        async def fail_announce(event: Event) -> None:
            raise RuntimeError("announce failed after commit")

        monkeypatch.setattr(bus, "announce_committed", fail_announce)
        with pytest.raises(RuntimeError, match="announce failed"):
            if kind == "ask":
                claim = facade._claim_pending_return()
                try:
                    await facade.register_question(
                        "在吗？", InteractionKind.CHAT_ASK, "u", "u",
                        claimed_return=claim,
                    )
                except BaseException:
                    facade._release_return_claim(claim)
                    raise
            else:
                await facade.initiate_chat(_desire(), _mk_state(80.0, 0.0))

        events = await bus.list_events()
        assert len(events) == 1
        assert (returns.finished, returns.released) == (1, 0)
        assert returns.pending is None
    finally:
        await database.close()


async def test_mutter_publish_failure_does_not_suppress_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    returns = _ReturnState()
    facade, *_ = _new_facade(
        llm=_FakeLlm(chat_content="我在这里。"), return_state=returns
    )

    class _FailOnceBus(_FakeBus):
        def __init__(self) -> None:
            super().__init__()
            self.failed = False

        async def publish(self, event: Event) -> None:
            if not self.failed:
                self.failed = True
                raise RuntimeError("admission failed")
            await super().publish(event)

    bus = _FailOnceBus()
    facade._bus = cast(EventBus, bus)
    monkeypatch.setattr("nyx.expression.facade.random.random", lambda: 0.0)
    with pytest.raises(RuntimeError, match="admission failed"):
        await facade.mutter(_mk_state(80.0, 0.0), "first")
    await facade.mutter(_mk_state(80.0, 0.0), "retry")

    assert len(bus.published) == 1
    assert bus.published[-1].correlation_id == "retry"
    assert (returns.finished, returns.released) == (1, 1)


@pytest.mark.parametrize("kind", ["ask", "initiate_chat"])
async def test_transaction_cancelled_after_commit_does_not_restore_return(
    kind: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = await db.connect(":memory:")
    try:
        returns = _ReturnState()
        facade, *_ = _new_facade(
            interaction_store=ExpressionInteractionStore(database),
            return_state=returns,
        )
        bus = EventBus(database)
        facade._bus = bus
        committed = asyncio.Event()
        original_commit = database.conn.commit

        async def commit_then_pause() -> None:
            await original_commit()
            committed.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(database.conn, "commit", commit_then_pause)
        claim = None
        if kind == "ask":
            claim = returns.claim()
            task = asyncio.create_task(facade.register_question(
                "在吗？", InteractionKind.CHAT_ASK, "u", "u", claimed_return=claim,
            ))
        else:
            task = asyncio.create_task(
                facade.initiate_chat(_desire(), _mk_state(80.0, 0.0))
            )
        await asyncio.wait_for(committed.wait(), 1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        returns.release(claim)

        assert len(await bus.list_events()) == 1
        assert (returns.finished, returns.released) == (1, 0)
        assert returns.pending is None
    finally:
        await database.close()


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


async def test_answer_waiting_releases_claim_when_desire_settlement_fails() -> None:
    database = await db.connect(":memory:")
    try:
        store = ExpressionInteractionStore(database)
        await store.create(
            InteractionAttempt(
                id="attempt-1",
                kind=InteractionKind.INITIATE_CHAT,
                source_id="d1",
                correlation_id="chat-1",
                text="在吗？",
                created_at=1.0,
                expires_at=100.0,
            )
        )

        class _FailingDesire(_FakeDesire):
            async def satisfy(self, desire_id: str, goal_met: bool) -> None:
                raise RuntimeError("settlement failed")

        facade, *_ = _new_facade(
            desire=_FailingDesire(), interaction_store=store
        )

        with pytest.raises(RuntimeError, match="settlement failed"):
            await facade.answer_waiting("reply-1")

        attempt = await store.get("attempt-1")
        assert attempt is not None
        assert attempt.status is InteractionStatus.WAITING
        assert attempt.answer_event_id is None
    finally:
        await database.close()


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
