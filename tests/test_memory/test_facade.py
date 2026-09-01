# pyright: reportPrivateUsage=false
import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator
from typing import cast

import pytest

from nyx import db
from nyx.config import MemoryConfig
from nyx.db import Database
from nyx.enums import EventType, MemoryType, Source
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.llm.client import LlmClient, LlmMessage
from nyx.memory.facade import (
    _SUMMARY_MAX_CHARS,
    MemoryFacade,
    _activity_memory_fields,
    _build_contradiction_prompt,
    _build_scene_prompt,
    _content_preview,
    _has_negation,
    _join_list,
    _memory_to_dict,
    _memory_to_markdown,
    _parse_contradiction,
    _parse_scene,
    decay_freshness,
)
from nyx.memory.retrieval import EmbedFn, MemoryRetrieval
from nyx.memory.store import MemoryStore
from nyx.types import Event, LLMOutput, Memory


def _scene(content: str) -> str:
    return json.dumps({"content": content, "tag": "cat", "summary": "喜欢猫"})


_SCENE_JSON = _scene("用户喜欢猫")
_NEG_SCENE_JSON = json.dumps(
    {"content": "我不喜欢猫", "tag": "cat", "summary": "不喜欢猫"}
)


def _mem(
    id: str,
    embedding: list[float] | None,
    content: str = "旧记忆内容",
) -> Memory:
    return Memory(
        id=id,
        created_at=1000.0,
        content=content,
        tag="old",
        summary="旧",
        freshness=1.0,
        type=MemoryType.SHORT_TERM,
        recall_count=0,
        aspect=[],
        embedding=embedding,
    )


def _ctx(correlation_id: str = "corr-1") -> dict[str, str]:
    return {
        "correlation_id": correlation_id,
        "user_message": "我喜欢猫",
        "nyx_think": "用户喜欢猫",
        "nyx_speak": "猫很可爱",
    }


def _embed(vec: list[float]) -> EmbedFn:
    async def fn(text: str) -> list[float]:
        return vec

    return fn


class _FakeLlm:
    """complete 按 output_type 返回预设 JSON，记录 output_type 与 user content。"""

    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self._responses = responses if responses is not None else {
            "scene_memory": _SCENE_JSON
        }
        self.calls: list[str] = []          # output_type 序列
        self.user_contents: list[str] = []  # 每次 complete 的 user content

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
    ) -> LLMOutput:
        self.calls.append(output_type)
        self.user_contents.append(messages[1]["content"])
        return LLMOutput(
            module=module,
            type=output_type,
            model="fake",
            content=self._responses.get(output_type, "{}"),
            correlation_id=correlation_id,
        )


class _FakeEvaluator:
    """记录 evaluate 调用（facade 只调不返回值）。"""

    def __init__(self) -> None:
        self.evaluated: list[LLMOutput] = []

    async def evaluate(self, output: LLMOutput) -> None:
        self.evaluated.append(output)


class _FakeRetrieval:
    """记录 search 调用并返回预设结果。"""

    def __init__(self, result: list[Memory]) -> None:
        self._result = result
        self.queries: list[str] = []

    async def search(self, query: str) -> list[Memory]:
        self.queries.append(query)
        return self._result


def _make_facade(
    store: MemoryStore,
    bus: EventBus,
    llm: _FakeLlm,
    evaluator: _FakeEvaluator,
    *,
    embed: EmbedFn | None = None,
    config: MemoryConfig | None = None,
    retrieval: MemoryRetrieval | None = None,
) -> MemoryFacade:
    ret = retrieval if retrieval is not None else MemoryRetrieval(store, embed)
    return MemoryFacade(
        store,
        ret,
        bus,
        cast(LlmClient, llm),
        cast(Evaluator, evaluator),
        config if config is not None else MemoryConfig(),
        embed,
    )


async def _new_stack() -> tuple[MemoryStore, EventBus, Database]:
    database = await db.connect(":memory:")
    return MemoryStore(database), EventBus(database), database


def _subscribe(bus: EventBus) -> list[Event]:
    events: list[Event] = []

    async def record(event: Event) -> None:
        events.append(event)

    event_types = (
        EventType.MEMORY_CREATED,
        EventType.MEMORY_PROMOTED,
        EventType.REFLECTION,
    )
    for t in event_types:
        bus.subscribe(t, record)
    return events


@contextlib.asynccontextmanager
async def _running(bus: EventBus) -> AsyncGenerator[None]:
    """以 task 跑 run()，yield 后等待队列排空，退出时 cancel。"""
    task = asyncio.create_task(bus.run())
    try:
        yield
        await asyncio.wait_for(bus._queue.join(), timeout=1.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# ---- 纯函数 ----


def test_decay_freshness() -> None:
    assert decay_freshness(1.0, 100.0, 100.0, 0.01) == 1.0        # 同刻不变
    assert decay_freshness(1.0, 100.0, 100.0 + 86400.0, 0.01) == pytest.approx(0.99)
    assert decay_freshness(1.0, 200.0, 100.0, 0.01) == 1.0        # 倒挂不变
    assert decay_freshness(0.5, 0.0, 100.0 * 86400.0, 0.01) == 0.0  # 夹到 0


def test_parse_scene() -> None:
    assert _parse_scene(_SCENE_JSON) == ("用户喜欢猫", "cat", "喜欢猫")
    with pytest.raises(ValueError):
        _parse_scene('{"content": "x", "summary": "s"}')   # 缺 tag
    with pytest.raises(ValueError):
        _parse_scene('{"content": "", "tag": "t", "summary": "s"}')  # 空 content
    with pytest.raises(ValueError):
        _parse_scene("[]")                                 # 非对象


def test_build_scene_prompt() -> None:
    prompt = _build_scene_prompt(_ctx())
    assert "我喜欢猫" in prompt      # user_message
    assert "用户喜欢猫" in prompt    # nyx_think
    assert "猫很可爱" in prompt      # nyx_speak
    with pytest.raises(KeyError):
        _build_scene_prompt({"user_message": "x"})


def test_has_negation() -> None:
    assert _has_negation("我不喜欢猫") is True
    assert _has_negation("我喜欢猫") is False


def test_content_preview() -> None:
    assert _content_preview(_mem("m", None, content="短内容")) == "旧 | 短内容"
    preview = _content_preview(_mem("m", None, content="x" * 100))
    assert preview.startswith("旧 | ")
    assert preview.endswith("…")
    assert preview.count("x") == 60


def test_build_contradiction_prompt() -> None:
    new = _mem("new", None, content="我不喜欢猫")
    candidates = [_mem("old-1", None, content="我喜欢猫")]
    prompt = _build_contradiction_prompt(new, candidates)
    assert "我不喜欢猫" in prompt   # 新记忆 content
    assert "old-1" in prompt        # 候选 id
    assert "我喜欢猫" in prompt     # 候选 content 预览（非只 summary）
    assert "重点核对" in prompt     # 否定词 → 提示
    # 无否定词 → 无提示
    prompt2 = _build_contradiction_prompt(
        _mem("new2", None, content="我喜欢猫"), candidates
    )
    assert "重点核对" not in prompt2


def test_parse_contradiction() -> None:
    assert _parse_contradiction('{"conflicts_with": "old-1"}') == "old-1"
    assert _parse_contradiction('{"conflicts_with": null}') is None
    with pytest.raises(ValueError):
        _parse_contradiction('{"conflicts_with": 123}')
    assert _parse_contradiction("{}") is None


def test_memory_to_dict() -> None:
    m = _mem("m1", [0.1, 0.2])
    d = _memory_to_dict(m)
    assert d["type"] == "short_term"   # .value 字符串
    assert d["embedding"] == [0.1, 0.2]


def test_memory_to_markdown() -> None:
    md = _memory_to_markdown(_mem("m1", None, content="内容A"))
    assert "旧" in md       # summary
    assert "内容A" in md    # content


# ---- 活动记忆纯函数 ----


def test_join_list() -> None:
    assert _join_list("x") == "x"                     # str 原样
    assert _join_list(["a", "b"]) == "a\nb"           # list 换行拼接
    assert _join_list([]) == ""                       # 空 list
    assert _join_list(None) == ""                     # None
    assert _join_list(123) == ""                      # 非 str/list


def test_activity_memory_fields_reading() -> None:
    result = {"book": "某书", "note": "读后感", "read_chars": 100, "total_chars": 200}
    assert _activity_memory_fields("reading", result) == ("读后感", "某书", "reading")


def test_activity_memory_fields_creation() -> None:
    result = {"title": "标题", "content": "正文"}
    assert _activity_memory_fields("creation", result) == ("正文", "标题", "creation")


def test_activity_memory_fields_exploration() -> None:
    result = {"summary": "s1", "core_discovery": "cd1"}
    assert _activity_memory_fields("free_exploration", result) == (
        "s1", "cd1", "free_exploration",
    )


def test_activity_memory_fields_skip() -> None:
    assert _activity_memory_fields("rest", {}) is None
    assert _activity_memory_fields("reading", {}) is None
    assert _activity_memory_fields("reading", {"book": "", "note": ""}) is None
    assert _activity_memory_fields(None, {"book": "x", "note": "y"}) is None
    assert _activity_memory_fields("reading", "not-dict") is None


def test_activity_memory_fields_summary_truncated() -> None:
    result = {"book": "x" * 100, "note": "y"}
    mapped = _activity_memory_fields("reading", result)
    assert mapped is not None
    assert mapped[1] == "x" * _SUMMARY_MAX_CHARS + "…"


# ---- create_scene_memory ----


async def test_create_scene_memory_basic() -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator)
    events = _subscribe(bus)
    try:
        async with _running(bus):
            memory = await facade.create_scene_memory(_ctx())
        assert memory.content == "用户喜欢猫"
        assert memory.tag == "cat"
        assert memory.summary == "喜欢猫"
        assert memory.freshness == 1.0
        assert memory.type is MemoryType.SHORT_TERM
        assert memory.embedding is None
        assert llm.calls == ["scene_memory"]
        assert [o.type for o in evaluator.evaluated] == ["scene_memory"]
        [created] = [e for e in events if e.type is EventType.MEMORY_CREATED]
        assert created.content["memory_id"] == memory.id
        assert created.source is Source.INTERNAL
        assert created.correlation_id == "corr-1"
    finally:
        await database.conn.close()


async def test_contradiction_gating_under_threshold() -> None:
    store, bus, database = await _new_stack()
    await store.add(_mem("old-1", [1.0, 0.0]))   # 与新记忆正交
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator, embed=_embed([0.0, 1.0]))
    events = _subscribe(bus)
    try:
        async with _running(bus):
            await facade.create_scene_memory(_ctx())
        assert llm.calls == ["scene_memory"]      # 无 contradiction 调用
        assert [e for e in events if e.type is EventType.REFLECTION] == []
    finally:
        await database.conn.close()


async def test_contradiction_detected() -> None:
    store, bus, database = await _new_stack()
    await store.add(_mem("old-1", [0.8, 0.6]))
    llm = _FakeLlm({
        "scene_memory": _SCENE_JSON,
        "contradiction": json.dumps({"conflicts_with": "old-1"}),
    })
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator, embed=_embed([1.0, 0.0]))
    events = _subscribe(bus)
    try:
        async with _running(bus):
            memory = await facade.create_scene_memory(_ctx())
        assert llm.calls == ["scene_memory", "contradiction"]
        assert [o.type for o in evaluator.evaluated] == [
            "scene_memory", "contradiction",
        ]
        [reflection] = [e for e in events if e.type is EventType.REFLECTION]
        assert memory.id in reflection.content["summary"]
        assert "old-1" in reflection.content["summary"]
    finally:
        await database.conn.close()


async def test_contradiction_null_no_reflection() -> None:
    store, bus, database = await _new_stack()
    await store.add(_mem("old-1", [0.8, 0.6]))
    llm = _FakeLlm({
        "scene_memory": _SCENE_JSON,
        "contradiction": json.dumps({"conflicts_with": None}),
    })
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator, embed=_embed([1.0, 0.0]))
    events = _subscribe(bus)
    try:
        async with _running(bus):
            await facade.create_scene_memory(_ctx())
        assert llm.calls == ["scene_memory", "contradiction"]
        assert [e for e in events if e.type is EventType.REFLECTION] == []
    finally:
        await database.conn.close()


async def test_contradiction_recall_top_k() -> None:
    store, bus, database = await _new_stack()
    for i in range(6):
        await store.add(_mem(f"old-{i}", [0.8, 0.6]))
    llm = _FakeLlm({
        "scene_memory": _SCENE_JSON,
        "contradiction": json.dumps({"conflicts_with": None}),
    })
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator, embed=_embed([1.0, 0.0]))
    try:
        async with _running(bus):
            await facade.create_scene_memory(_ctx())
        assert llm.user_contents[1].count("- [old-") == 5   # 召回 top-K=5
    finally:
        await database.conn.close()


async def test_contradiction_prompt_negation_hint() -> None:
    store, bus, database = await _new_stack()
    await store.add(_mem("old-1", [0.8, 0.6]))
    llm = _FakeLlm({
        "scene_memory": _NEG_SCENE_JSON,   # content 含「不」
        "contradiction": json.dumps({"conflicts_with": None}),
    })
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator, embed=_embed([1.0, 0.0]))
    try:
        async with _running(bus):
            await facade.create_scene_memory(_ctx())
        assert "重点核对" in llm.user_contents[1]
    finally:
        await database.conn.close()


async def test_contradiction_parse_failure_no_crash() -> None:
    store, bus, database = await _new_stack()
    await store.add(_mem("old-1", [0.8, 0.6]))
    llm = _FakeLlm({
        "scene_memory": _SCENE_JSON,
        "contradiction": "not-json{{{",   # 解析失败
    })
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator, embed=_embed([1.0, 0.0]))
    events = _subscribe(bus)
    try:
        async with _running(bus):
            memory = await facade.create_scene_memory(_ctx())
        assert memory.content == "用户喜欢猫"   # 主流程不受影响（记忆已入库）
        [created] = [e for e in events if e.type is EventType.MEMORY_CREATED]
        assert created.content["memory_id"] == memory.id
        assert [e for e in events if e.type is EventType.REFLECTION] == []
        assert llm.calls == ["scene_memory", "contradiction"]
    finally:
        await database.conn.close()


async def test_build_edges() -> None:
    store, bus, database = await _new_stack()
    await store.add(_mem("old-1", [0.8, 0.6]))
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator, embed=_embed([1.0, 0.0]))
    try:
        async with _running(bus):
            memory = await facade.create_scene_memory(_ctx())
        edges = await store.list_edges()
        assert any(
            e.from_id == memory.id and e.to_id == "old-1" and e.weight > 0
            for e in edges
        )
    finally:
        await database.conn.close()


async def test_eviction(monkeypatch: pytest.MonkeyPatch) -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    config = MemoryConfig(short_term_capacity=1)
    facade = _make_facade(store, bus, llm, evaluator, config=config)
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.memory.facade.time.time", lambda: t0)
        llm._responses["scene_memory"] = _scene("用户喜欢猫1")
        async with _running(bus):
            mem1 = await facade.create_scene_memory(_ctx())
        monkeypatch.setattr("nyx.memory.facade.time.time", lambda: t0 + 86400.0)
        llm._responses["scene_memory"] = _scene("用户喜欢猫2")
        async with _running(bus):
            mem2 = await facade.create_scene_memory(_ctx())
        assert await store.get(mem1.id) is None     # 旧记忆被挤掉
        assert await store.get(mem2.id) is not None
        assert len(await facade.list_memories()) == 1
    finally:
        await database.conn.close()


async def test_eviction_tie_break_oldest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    config = MemoryConfig(short_term_capacity=2, freshness_decay=0.0)
    facade = _make_facade(store, bus, llm, evaluator, config=config)
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.memory.facade.time.time", lambda: t0)
        llm._responses["scene_memory"] = _scene("用户喜欢猫1")
        async with _running(bus):
            oldest = await facade.create_scene_memory(_ctx())
        monkeypatch.setattr("nyx.memory.facade.time.time", lambda: t0 + 86400.0)
        llm._responses["scene_memory"] = _scene("用户喜欢猫2")
        async with _running(bus):
            middle = await facade.create_scene_memory(_ctx())
        monkeypatch.setattr("nyx.memory.facade.time.time", lambda: t0 + 2 * 86400.0)
        llm._responses["scene_memory"] = _scene("用户喜欢猫3")
        async with _running(bus):
            newest = await facade.create_scene_memory(_ctx())
        # 新鲜度相等（decay=0.0）→ 平局按 created_at 升序，挤掉最旧的而非最新
        assert await store.get(oldest.id) is None
        assert await store.get(middle.id) is not None
        assert await store.get(newest.id) is not None
        assert len(await facade.list_memories()) == 2
    finally:
        await database.conn.close()


async def test_decay_writeback(monkeypatch: pytest.MonkeyPatch) -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator)
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.memory.facade.time.time", lambda: t0)
        llm._responses["scene_memory"] = _scene("用户喜欢猫1")
        async with _running(bus):
            mem1 = await facade.create_scene_memory(_ctx())
        monkeypatch.setattr("nyx.memory.facade.time.time", lambda: t0 + 86400.0)
        llm._responses["scene_memory"] = _scene("用户喜欢猫2")
        async with _running(bus):
            await facade.create_scene_memory(_ctx())
        decayed = await store.get(mem1.id)
        assert decayed is not None
        assert decayed.freshness < 1.0   # 衰减回写
    finally:
        await database.conn.close()


# ---- 去重 ----
# _persist_memory 两层去重：精确（content 哈希）→ 语义（embedding 余弦 ≥ 0.95）。
# 命中合并强化（freshness 重置 + created_at 刷新，不涨 recall_count），
# 不新建行、不发 MEMORY_CREATED。


async def test_dedup_exact_same_content() -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator)   # embed=None，仅精确去重
    events = _subscribe(bus)
    try:
        async with _running(bus):
            await facade.create_scene_memory(_ctx())
            await facade.create_scene_memory(_ctx())     # 同 content 二次写入
        memories = await facade.list_memories()
        assert len(memories) == 1
        assert memories[0].recall_count == 0             # 合并强化不涨 recall_count
        assert memories[0].content == "用户喜欢猫"
        [created] = [e for e in events if e.type is EventType.MEMORY_CREATED]
        assert created.content["memory_id"] == memories[0].id
    finally:
        await database.conn.close()


async def test_dedup_semantic_merge() -> None:
    store, bus, database = await _new_stack()
    await store.add(_mem("old-1", [1.0, 0.0]))
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator, embed=_embed([1.0, 0.0]))
    events = _subscribe(bus)
    try:
        async with _running(bus):
            await facade.create_scene_memory(_ctx())   # 与 old-1 cos=1.0 → 语义命中
        memories = await facade.list_memories()
        assert len(memories) == 1
        assert memories[0].id == "old-1"
        assert memories[0].recall_count == 0
        assert [e for e in events if e.type is EventType.MEMORY_CREATED] == []
    finally:
        await database.conn.close()


async def test_dedup_semantic_below_threshold() -> None:
    store, bus, database = await _new_stack()
    await store.add(_mem("old-1", [1.0, 0.0]))
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator, embed=_embed([0.0, 1.0]))
    events = _subscribe(bus)
    try:
        async with _running(bus):
            memory = await facade.create_scene_memory(_ctx())   # cos=0.0 < 0.95
        memories = await facade.list_memories()
        assert len(memories) == 2                     # 不合并，正常新建
        assert memory.content == "用户喜欢猫"
        assert len([e for e in events if e.type is EventType.MEMORY_CREATED]) == 1
    finally:
        await database.conn.close()


async def test_dedup_embed_none_skips_semantic() -> None:
    """embed 禁用：即使库里旧记忆带 embedding，也跳过语义去重（只精确去重）。"""
    store, bus, database = await _new_stack()
    await store.add(_mem("old-1", [1.0, 0.0]))
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator)   # embed=None
    try:
        async with _running(bus):
            await facade.create_scene_memory(_ctx())   # 新记忆无 embedding → 无语义比较
        assert len(await facade.list_memories()) == 2
    finally:
        await database.conn.close()


# ---- search / list_memories ----


async def test_search_delegates_to_retrieval() -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    preset = [_mem("m1", None), _mem("m2", None)]
    fake_retrieval = _FakeRetrieval(preset)
    facade = _make_facade(
        store, bus, llm, evaluator, retrieval=cast(MemoryRetrieval, fake_retrieval)
    )
    try:
        assert await facade.search("猫") == preset
        assert fake_retrieval.queries == ["猫"]
    finally:
        await database.conn.close()


async def test_list_memories_delegates() -> None:
    store, bus, database = await _new_stack()
    await store.add(_mem("m1", None, content="a"))
    long = _mem("m2", None, content="b")
    long.type = MemoryType.LONG_TERM
    await store.add(long)
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator)
    try:
        result = await facade.list_memories(type=MemoryType.SHORT_TERM)
        assert [m.id for m in result] == ["m1"]
    finally:
        await database.conn.close()


async def test_count_new_delegates() -> None:
    store, bus, database = await _new_stack()
    m = _mem("m1", None)   # created_at=1000.0
    m.tag = "reading"
    await store.add(m)
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator)
    try:
        assert await facade.count_new("reading", 500.0) == 1
        assert await facade.count_new("reading", 2000.0) == 0
        assert await facade.count_new("user", 0.0) == 0
    finally:
        await database.conn.close()


# ---- record_recall ----


async def test_record_recall_below_threshold() -> None:
    store, bus, database = await _new_stack()
    await store.add(_mem("m1", None))
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator)
    events = _subscribe(bus)
    try:
        async with _running(bus):
            await facade.record_recall("m1")
        memory = await store.get("m1")
        assert memory is not None
        assert memory.recall_count == 1
        assert memory.type is MemoryType.SHORT_TERM
        assert [e for e in events if e.type is EventType.MEMORY_PROMOTED] == []
    finally:
        await database.conn.close()


async def test_record_recall_promotes() -> None:
    store, bus, database = await _new_stack()
    await store.add(_mem("m1", None))
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(
        store, bus, llm, evaluator, config=MemoryConfig(promote_threshold=1)
    )
    events = _subscribe(bus)
    try:
        async with _running(bus):
            await facade.record_recall("m1")
        memory = await store.get("m1")
        assert memory is not None
        assert memory.type is MemoryType.LONG_TERM
        assert memory.recall_count == 1
        [promoted] = [e for e in events if e.type is EventType.MEMORY_PROMOTED]
        assert promoted.content["memory_id"] == "m1"
        assert promoted.source is Source.INTERNAL
    finally:
        await database.conn.close()


async def test_record_recall_long_term_no_repromote() -> None:
    store, bus, database = await _new_stack()
    long = _mem("m1", None)
    long.type = MemoryType.LONG_TERM
    long.recall_count = 5
    await store.add(long)
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(
        store, bus, llm, evaluator, config=MemoryConfig(promote_threshold=1)
    )
    events = _subscribe(bus)
    try:
        async with _running(bus):
            await facade.record_recall("m1")
        memory = await store.get("m1")
        assert memory is not None
        assert memory.type is MemoryType.LONG_TERM
        assert memory.recall_count == 6
        assert [e for e in events if e.type is EventType.MEMORY_PROMOTED] == []
    finally:
        await database.conn.close()


async def test_record_recall_concurrent_single_promote() -> None:
    store, bus, database = await _new_stack()
    await store.add(_mem("m1", None))
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(
        store, bus, llm, evaluator, config=MemoryConfig(promote_threshold=1)
    )
    events = _subscribe(bus)
    try:
        async with _running(bus):
            await asyncio.gather(
                facade.record_recall("m1"), facade.record_recall("m1")
            )
        memory = await store.get("m1")
        assert memory is not None
        assert memory.recall_count == 2          # 两次加一不丢计数
        promoted = [e for e in events if e.type is EventType.MEMORY_PROMOTED]
        assert len(promoted) == 1                # 只升一次、只发一条 promoted
    finally:
        await database.conn.close()


# ---- export ----


async def test_export_json() -> None:
    store, bus, database = await _new_stack()
    await store.add(_mem("m1", None, content="内容A"))
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator)
    try:
        data = json.loads(await facade.export("json"))
        assert len(data) == 1
        assert data[0]["id"] == "m1"
        assert data[0]["type"] == "short_term"
        assert data[0]["embedding"] is None
    finally:
        await database.conn.close()


async def test_export_md() -> None:
    store, bus, database = await _new_stack()
    await store.add(_mem("m1", None, content="内容A"))
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator)
    try:
        text = await facade.export("md")
        assert "旧" in text      # summary
        assert "内容A" in text   # content
    finally:
        await database.conn.close()


async def test_export_unknown() -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator)
    try:
        with pytest.raises(ValueError):
            await facade.export("csv")
    finally:
        await database.conn.close()


# ---- remember_activity ----

def _activity_event(type_: str, result: dict[str, object]) -> Event:
    return Event(
        id="evt-1",
        timestamp=1000.0,
        source=Source.INTERNAL,
        type=EventType.ACTIVITY_END,
        content={"type": type_, "result": result, "activity_id": "act-1"},
        correlation_id="corr-1",
    )


async def test_remember_activity_reading() -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator)
    events = _subscribe(bus)
    try:
        async with _running(bus):
            await facade.remember_activity(
                _activity_event("reading", {"book": "某书", "note": "读后感"})
            )
        memories = await facade.list_memories()
        assert len(memories) == 1
        assert memories[0].content == "读后感"
        assert memories[0].summary == "某书"
        assert memories[0].tag == "reading"
        assert memories[0].type is MemoryType.SHORT_TERM
        assert llm.calls == []   # 无 LLM 调用（确定性落库）
        [created] = [e for e in events if e.type is EventType.MEMORY_CREATED]
        assert created.content["memory_id"] == memories[0].id
    finally:
        await database.conn.close()


async def test_remember_activity_creation_and_exploration() -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator)
    try:
        async with _running(bus):
            await facade.remember_activity(
                _activity_event("creation", {"title": "标题", "content": "正文"})
            )
            await facade.remember_activity(
                _activity_event(
                    "free_exploration",
                    {"summary": "s1", "core_discovery": "cd1"},
                )
            )
        by_tag = {m.tag: m for m in await facade.list_memories()}
        assert by_tag["creation"].content == "正文"
        assert by_tag["creation"].summary == "标题"
        assert by_tag["free_exploration"].content == "s1"
        assert by_tag["free_exploration"].summary == "cd1"
        assert llm.calls == []
    finally:
        await database.conn.close()


async def test_remember_activity_skips_empty_or_other_type() -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator)
    events = _subscribe(bus)
    try:
        async with _running(bus):
            await facade.remember_activity(_activity_event("rest", {}))
            await facade.remember_activity(_activity_event("reading", {}))
            await facade.remember_activity(
                _activity_event("observe_user", {"note": "x"})
            )
        assert await facade.list_memories() == []
        assert [e for e in events if e.type is EventType.MEMORY_CREATED] == []
    finally:
        await database.conn.close()


async def test_remember_activity_contradiction() -> None:
    store, bus, database = await _new_stack()
    await store.add(_mem("old-1", [0.8, 0.6]))
    llm = _FakeLlm({"contradiction": json.dumps({"conflicts_with": "old-1"})})
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator, embed=_embed([1.0, 0.0]))
    events = _subscribe(bus)
    try:
        async with _running(bus):
            await facade.remember_activity(
                _activity_event("reading", {"book": "某书", "note": "读后感"})
            )
        assert llm.calls == ["contradiction"]   # 参与矛盾判断（无场景构建）
        [reflection] = [e for e in events if e.type is EventType.REFLECTION]
        assert "old-1" in reflection.content["summary"]
    finally:
        await database.conn.close()


async def test_remember_activity_observe_sediments_profile() -> None:
    """观察活动 result 带 presence → 沉淀一条 tag='user' 的长期画像记忆（无 LLM）。"""
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator)
    try:
        async with _running(bus):
            await facade.remember_activity(
                _activity_event(
                    "observe_user",
                    {"presence": "online", "window_title": "编辑器",
                     "summary": "用户（online）正在浏览 编辑器"},
                )
            )
        memories = await facade.list_memories()
        assert len(memories) == 1
        m = memories[0]
        assert m.type is MemoryType.LONG_TERM
        assert m.tag == "user"
        assert m.aspect == ["presence", "window_title"]
        assert llm.calls == []
    finally:
        await database.conn.close()


async def test_remember_activity_observe_skips_unchanged() -> None:
    """观察「变化才沉淀」：同 presence/window_title 快照重复上报不新增画像记忆。"""
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator)
    try:
        obs = _activity_event(
            "observe_user",
            {"presence": "online", "window_title": "编辑器", "summary": "s"},
        )
        async with _running(bus):
            await facade.remember_activity(obs)
        async with _running(bus):
            await facade.remember_activity(obs)
        assert len(await facade.list_memories()) == 1
    finally:
        await database.conn.close()


# ---- remember_user_profile ----

async def test_remember_user_profile_fields() -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator)
    events = _subscribe(bus)
    try:
        async with _running(bus):
            await facade.remember_user_profile(
                "用户（online）正在浏览 骑士团史.md",
                "浏览骑士团史",
                ["presence", "window_title"],
                "corr-1",
            )
        memories = await facade.list_memories()
        assert len(memories) == 1
        m = memories[0]
        assert (m.type, m.tag, m.aspect, m.summary) == (
            MemoryType.LONG_TERM, "user", ["presence", "window_title"], "浏览骑士团史",
        )
        assert llm.calls == []   # 无 LLM（确定性落库）
        [created] = [e for e in events if e.type is EventType.MEMORY_CREATED]
        assert created.content["memory_id"] == m.id
        assert created.correlation_id == "corr-1"
    finally:
        await database.conn.close()


# ---- remember_knowledge ----


async def test_remember_knowledge() -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator)
    events = _subscribe(bus)
    try:
        async with _running(bus):
            await facade.remember_knowledge(
                [
                    {"topic": "骑士团", "content": "骑士团成立于 1147 年"},
                    {"topic": "", "content": "第二条知识点"},
                    {"topic": "空内容", "content": "   "},   # content 空 → 跳过
                ],
                "corr-1",
            )
        memories = await facade.list_memories()
        assert len(memories) == 2
        assert {(m.type, m.tag) for m in memories} == {
            (MemoryType.LONG_TERM, "knowledge")
        }
        by_summary = {m.summary: m.content for m in memories}
        assert by_summary == {
            "骑士团": "骑士团成立于 1147 年",
            "第二条知识点": "第二条知识点",
        }
        assert llm.calls == []   # 无 LLM（确定性落库）
        created = [e for e in events if e.type is EventType.MEMORY_CREATED]
        assert len(created) == 2
    finally:
        await database.conn.close()


# ---- remember_reading ----

async def test_remember_reading() -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator)
    events = _subscribe(bus)
    try:
        async with _running(bus):
            await facade.remember_reading(
                "这一章我陪你读，记住了这段话", "读某章", "corr-1"
            )
        memories = await facade.list_memories()
        assert len(memories) == 1
        m = memories[0]
        assert (m.type, m.tag, m.summary) == (
            MemoryType.LONG_TERM, "reading", "读某章",
        )
        assert llm.calls == []   # 无 LLM（确定性落库）
        [created] = [e for e in events if e.type is EventType.MEMORY_CREATED]
        assert created.content["memory_id"] == m.id
        assert created.correlation_id == "corr-1"
    finally:
        await database.conn.close()


# ---- record_no_answer ----

async def test_record_no_answer() -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade = _make_facade(store, bus, llm, evaluator)
    events = _subscribe(bus)
    try:
        async with _running(bus):
            await facade.record_no_answer("你还好吗？", "corr-1")
        memories = await facade.list_memories()
        assert len(memories) == 1
        m = memories[0]
        assert (m.type, m.tag, m.summary) == (
            MemoryType.SHORT_TERM, "interaction", "用户没有回答我的提问",
        )
        assert "你还好吗？" in m.content
        assert llm.calls == []   # 无 LLM（确定性落库）
        [created] = [e for e in events if e.type is EventType.MEMORY_CREATED]
        assert created.content["memory_id"] == m.id
    finally:
        await database.conn.close()


def test_activity_memory_fields_free_exploration_new_shape() -> None:
    result = {"summary": "弄懂了退相干", "core_discovery": "环境纠缠抹去相干性"}
    mapped = _activity_memory_fields("free_exploration", result)
    assert mapped is not None
    content, summary, tag = mapped
    assert tag == "free_exploration"
    assert "退相干" in content
    assert "抹去相干性" in summary
