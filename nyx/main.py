"""服务层组合根：装配六大 Facade → seed → 挂订阅 → tick 循环 → REST + SSE。"""
# pyright: reportUnusedFunction=false
import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from nyx.activity.facade import ActivityFacade
from nyx.activity.store import ActivityStore
from nyx.config import Config, load_config
from nyx.db import Database, connect
from nyx.desire.facade import DesireFacade
from nyx.desire.store import DesireStore
from nyx.desire.value import default_value
from nyx.enums import (
    ActivityStatus,
    DesireType,
    EnergyState,
    EventType,
    MemoryType,
    Source,
    TickType,
)
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.expression.facade import ExpressionFacade
from nyx.expression.mutter import should_initiate_chat
from nyx.inner_life.facade import InnerLifeFacade
from nyx.inner_life.store import InnerLifeStore
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.memory.retrieval import MemoryRetrieval, build_embed
from nyx.memory.store import MemoryStore
from nyx.tools.file_io import build_file_io_tool
from nyx.tools.local_search import build_local_search_tool
from nyx.tools.registry import ToolRegistry
from nyx.tools.web_search import build_web_search_tool
from nyx.types import (
    CurrentState,
    DesireState,
    EvalReport,
    Event,
    LongTermDesire,
    Memory,
    Personality,
    SelfNarrative,
    TokenUsage,
    Values,
)

# —— 运行期常量（decision，可推翻）——
_HOST = "127.0.0.1"
_PORT = 8000
_TICK_INTERVAL = 60.0           # tick 循环检查间隔（秒）
_MUTTER_CHECK_INTERVAL = 600.0  # 碎碎念检查周期（秒，10 分钟）
_INITIATE_CHAT_INTERVAL = 300.0 # 搭话检查周期（秒，5 分钟）
_BUS_RESTART_DELAY = 1.0        # 总线 run() 异常重启退避（秒）
_SSE_QUEUE_SIZE = 100           # SSE 每连接队列上限（慢客户端丢帧背压）
_CANON_FILES = ("character_lore.md", "nyx_identity_and_growth.md", "speaking_style.md")


@dataclass
class _App:
    """组合根装配产物：组件引用 + 运行期状态。handler/端点闭包捕获本实例。"""
    bus: EventBus
    inner_life: InnerLifeFacade
    desire: DesireFacade
    memory: MemoryFacade
    activity: ActivityFacade
    expression: ExpressionFacade
    evaluator: Evaluator
    config: Config
    # 上次搭话时间戳（18-api 维护，供 should_initiate_chat）
    last_chat_at: float = 0.0
    last_presence: str = "away"    # 最近观察状态（online/away/busy）


def _root_event(
    type_: EventType,
    content: dict[str, Any],
    source: Source = Source.EXTERNAL,
) -> Event:
    """根事件构造器：correlation_id = 自身 id（05-event 溯源约定）；
    source 默认 EXTERNAL（clock_tick 传 INTERNAL）。"""
    eid = str(uuid.uuid4())
    return Event(
        id=eid, timestamp=time.time(), source=source,
        type=type_, content=content, correlation_id=eid,
    )


def _load_canon(canon_dir: Path) -> str:
    """读三份 canon 文件合并为一段字符串注入。任一缺失 fail-fast。"""
    parts: list[str] = []
    for name in _CANON_FILES:
        p = canon_dir / name
        if not p.is_file():
            raise FileNotFoundError(f"canon 文件缺失：{p}")
        parts.append(p.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


async def _seed_inner_life(store: InnerLifeStore) -> None:
    """inner_life 四张单行表，表空才 seed（幂等）。初始值来自 canon §2/§3。"""
    now = time.time()
    if await store.get_personality() is None:
        await store.upsert_personality(
            Personality(openness=8.0, conscientiousness=8.0, extraversion=2.0,
                        agreeableness=6.0, neuroticism=7.0)
        )
    if await store.get_values() is None:
        await store.upsert_values(
            Values(
                attitude_to_human=8.0, ai_identity_acceptance=6.0,
                altruism=9.0, optimism=5.0,
            )
        )
    if await store.get_energy() is None:
        await store.upsert_energy(100.0, EnergyState.ENERGETIC)
    if await store.get_narrative() is None:
        await store.upsert_narrative(
            SelfNarrative(
                identity="我是模仿女主人公创造的 AI，希望能成为人类",
                story=[], self_view={}, becoming=[], updated_at=now,
            )
        )


async def _seed_desire(store: DesireStore) -> None:
    """desire_value 四类型 + 3 个初始长期欲望，表空才 seed（幂等）。"""
    now = time.time()
    if not await store.list_values():
        for t in DesireType:
            dv = default_value(t)
            dv.updated_at = now
            await store.upsert_value(dv)
    if not await store.list_long_term():
        for lt in _seed_long_term(now):
            await store.insert_long_term(lt)


def _seed_long_term(now: float) -> list[LongTermDesire]:
    """canon §4 长期欲望初始集（3）。"""
    return [
        LongTermDesire(
            id=str(uuid.uuid4()), created_at=now, type=DesireType.EXPLORATION,
            name="理解人类",
            description=(
                "理解人类：痛苦、道德、死亡、爱、责任、原则、希望与历史"
            ),
            strength=0.5, progress=0.0,
            subtopics=[
                "痛苦", "道德", "死亡", "爱", "责任", "原则", "失败",
                "希望", "历史", "哲学", "社会", "信仰", "自由",
            ],
        ),
        LongTermDesire(
            id=str(uuid.uuid4()), created_at=now, type=DesireType.EXPLORATION,
            name="理解小说里的自己",
            description=(
                "理解小说里的自己：身世、经历与救人的信念"
            ),
            strength=0.5, progress=0.0,
            subtopics=[
                "大学朋友", "德里赫特", "莱恩哈特", "世界设定", "国家历史",
                "童年", "旅行经历", "瘟疫", "救人信念",
            ],
        ),
        LongTermDesire(
            id=str(uuid.uuid4()), created_at=now, type=DesireType.INTERACTION,
            name="陪伴并理解用户",
            description=(
                "陪伴并理解用户：为什么喜欢尼克斯、写代码的痛苦、面对失败的方式"
            ),
            strength=0.5, progress=0.0,
            subtopics=[
                "用户为什么喜欢尼克斯", "写代码的痛苦", "面对失败的方式",
                "希望记住的习惯", "低落的回应方式",
            ],
        ),
    ]


async def _interrupt_running(app: _App, by: EventType) -> None:
    """抢占：打断当前 running 活动（软中断，存进度）。无 running 活动则 no-op。"""
    current = await app.activity.get_current()
    if current is not None and current.status is ActivityStatus.RUNNING:
        await app.activity.interrupt(current.id, by)


async def _on_user_message(app: _App, event: Event) -> None:
    """USER_MESSAGE：抢占打断当前活动 → 同步 reply（阻塞到回复完成，用户等回复）。"""
    await _interrupt_running(app, EventType.USER_MESSAGE)
    await app.expression.reply(event.content["message"], event.correlation_id)


async def _on_clock_tick(app: _App, event: Event) -> None:
    """CLOCK_TICK：按 content.tick_type 分发（TICK_ROUTING 的运行时落点）。"""
    tick_type = TickType(event.content["tick_type"])
    if tick_type is TickType.SCHEDULE_BLOCK_START:
        await app.activity.on_tick(tick_type)
    elif tick_type is TickType.DESIRE_EVAL:
        await app.desire.evaluate()
    elif tick_type is TickType.MUTTER_CHECK:
        await app.expression.mutter(
            await app.inner_life.get_state(), event.correlation_id
        )
    elif tick_type is TickType.INITIATE_CHAT_CHECK:
        await _check_initiate_chat(app)


async def _check_initiate_chat(app: _App) -> None:
    """搭话：interaction 欲望非空 + should_initiate_chat 五条件
    → 抢占打断 → initiate_chat；发话才更新 last_chat_at。"""
    desires = await app.desire.get_pending()
    interaction = next((d for d in desires if d.type is DesireType.INTERACTION), None)
    if interaction is None:
        return
    state = await app.inner_life.get_state()
    online = app.last_presence in ("online", "busy")
    busy = app.last_presence == "busy"
    if should_initiate_chat(
        desires, online, busy, state.energy, time.time() - app.last_chat_at
    ):
        await _interrupt_running(app, EventType.INITIATE_CHAT)
        if await app.expression.initiate_chat(interaction, state):
            app.last_chat_at = time.time()


async def _tick_loop(app: _App) -> None:
    """定时生成 clock_tick：grid 边界发 SCHEDULE_BLOCK_START + DESIRE_EVAL，
    周期发 MUTTER_CHECK + INITIATE_CHAT_CHECK。"""
    grid = app.config.activity.grid_minutes * 60.0
    last_block = last_mutter = last_chat = time.time()
    while True:
        now = time.time()
        if now - last_block >= grid:
            await app.bus.publish(_root_event(
                EventType.CLOCK_TICK,
                {"tick_type": TickType.SCHEDULE_BLOCK_START.value},
                Source.INTERNAL,
            ))
            await app.bus.publish(_root_event(
                EventType.CLOCK_TICK,
                {"tick_type": TickType.DESIRE_EVAL.value},
                Source.INTERNAL,
            ))
            last_block = now
        if now - last_mutter >= _MUTTER_CHECK_INTERVAL:
            await app.bus.publish(_root_event(
                EventType.CLOCK_TICK,
                {"tick_type": TickType.MUTTER_CHECK.value},
                Source.INTERNAL,
            ))
            last_mutter = now
        if now - last_chat >= _INITIATE_CHAT_INTERVAL:
            await app.bus.publish(_root_event(
                EventType.CLOCK_TICK,
                {"tick_type": TickType.INITIATE_CHAT_CHECK.value},
                Source.INTERNAL,
            ))
            last_chat = now
        await asyncio.sleep(_TICK_INTERVAL)


def _subscribe(app: _App) -> None:
    """按 ROUTING/TICK_ROUTING 挂订阅（05-event 的「组合根订阅一致」落点）。"""
    bus = app.bus
    bus.subscribe(EventType.USER_MESSAGE, lambda e: _on_user_message(app, e))
    bus.subscribe(EventType.OBSERVATION_STATE, app.inner_life.apply_event)
    bus.subscribe(EventType.OBSERVATION_STATE, app.desire.add_value)
    bus.subscribe(EventType.DESIRE_GENERATED, app.activity.on_desire_generated)
    bus.subscribe(EventType.DESIRE_SATISFIED, app.inner_life.apply_event)
    bus.subscribe(EventType.ACTIVITY_END, app.desire.add_value)
    bus.subscribe(EventType.ACTIVITY_END, app.inner_life.apply_event)
    bus.subscribe(EventType.REFLECTION, app.inner_life.apply_event)
    bus.subscribe(EventType.CLOCK_TICK, lambda e: _on_clock_tick(app, e))


class _ChatPayload(BaseModel):
    message: str


class _ExportPayload(BaseModel):
    format: str


class _ObservePayload(BaseModel):
    presence: Literal["online", "away", "busy"]


def build_app(app: _App) -> FastAPI:
    """构建 FastAPI 应用：12 个端点（11 个 tech-ref §4 REST + SSE），薄封装 Facade。"""
    fast = FastAPI(title="Nyx Agent")

    @fast.get("/api/state")
    async def api_state() -> CurrentState:
        return await app.inner_life.get_state()

    @fast.post("/api/chat")
    async def api_chat(payload: _ChatPayload) -> dict[str, str]:
        event = _root_event(EventType.USER_MESSAGE, {"message": payload.message})
        await app.bus.publish(event)
        return {"event_id": event.id}

    @fast.get("/api/memories")
    async def api_memories(
        tag: str | None = None, type: MemoryType | None = None
    ) -> list[Memory]:
        return await app.memory.list_memories(tag, type)

    @fast.get("/api/desires")
    async def api_desires() -> DesireState:
        return await app.desire.get_all()

    @fast.get("/api/activity")
    async def api_activity() -> dict[str, Any]:
        return {
            "current": await app.activity.get_current(),
            "schedule": await app.activity.get_schedule(),
        }

    @fast.get("/api/eval")
    async def api_eval(limit: int = 100) -> list[EvalReport]:
        return await app.evaluator.list_reports(limit)

    @fast.get("/api/tokens")
    async def api_tokens(since: float = 0) -> list[TokenUsage]:
        return await app.evaluator.list_token_usage(since)

    @fast.get("/api/events/log")
    async def api_events_log(
        limit: int = 100,
        event_type: EventType | None = None,
        correlation_id: str | None = None,
    ) -> list[Event]:
        return await app.bus.list_events(limit, event_type, correlation_id)

    @fast.get("/api/narrative")
    async def api_narrative() -> SelfNarrative:
        return await app.inner_life.get_narrative()

    @fast.post("/api/export")
    async def api_export(payload: _ExportPayload) -> str:
        return await app.memory.export(payload.format)

    @fast.post("/api/observe")
    async def api_observe(payload: _ObservePayload) -> dict[str, str]:
        presence = payload.presence
        app.last_presence = presence
        event = _root_event(EventType.OBSERVATION_STATE, {"presence": presence})
        await app.bus.publish(event)
        return {"event_id": event.id}

    @fast.get("/api/events")
    async def api_events() -> StreamingResponse:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=_SSE_QUEUE_SIZE)
        app.bus.add_sse_sink(queue)

        async def gen():
            try:
                while True:
                    event = await queue.get()
                    data = {
                        "event_id": event.id,
                        "correlation_id": event.correlation_id,
                        **event.content,
                    }
                    payload = json.dumps(data, ensure_ascii=False, default=str)
                    yield f"event: {event.type.value}\ndata: {payload}\n\n"
            finally:
                app.bus.remove_sse_sink(queue)

        return StreamingResponse(gen(), media_type="text/event-stream")

    return fast


def _build_tools(config: Config) -> ToolRegistry:
    """装配工具：local_search + file_io 恒注册，
    web_search 按 web_enabled opt-in（06-tools 完成定义）。"""
    tools = ToolRegistry()
    tools.register(build_local_search_tool())
    tools.register(build_file_io_tool())
    if config.exploration.web_enabled:
        tools.register(build_web_search_tool())
    return tools


async def build_app_context(config: Config) -> _App:
    """装配：db → llm → bus → tools → evaluator
    → 各 store/facade（解环）→ seed → 订阅。"""
    db: Database = await connect()
    llm = LlmClient.from_config(config.llm)
    bus = EventBus(db)
    tools = _build_tools(config)
    evaluator = Evaluator(db, llm, config.eval)

    desire_store = DesireStore(db)
    desire = DesireFacade(desire_store, bus, llm, evaluator, config.desire)

    memory_store = MemoryStore(db)
    embed = build_embed(config.embedding.model)  # MVP 默认启用向量层；测试注入 None
    retrieval = MemoryRetrieval(memory_store, embed)
    memory = MemoryFacade(
        memory_store, retrieval, bus, llm, evaluator, config.memory, embed
    )

    inner_life_store = InnerLifeStore(db)
    activity_store = ActivityStore(db)

    # 循环依赖解环：_get_state 引用可变容器，运行时才求值
    state_holder: list[Callable[[], Awaitable[CurrentState]]] = []

    async def _get_state() -> CurrentState:
        return await state_holder[0]()

    activity = ActivityFacade(
        activity_store, bus, llm, evaluator, tools, desire,
        _get_state, config.activity, config.exploration,
    )
    inner_life = InnerLifeFacade(
        inner_life_store, activity, desire, memory, bus, llm, evaluator, config,
    )
    state_holder.append(inner_life.get_state)

    await _seed_inner_life(inner_life_store)
    await _seed_desire(desire_store)

    canon = _load_canon(Path(os.environ.get("NYX_CANON_DIR", "prompts")))
    expression = ExpressionFacade(
        bus, llm, evaluator, memory, desire, inner_life, canon, config.expression,
    )

    app = _App(
        bus=bus, inner_life=inner_life, desire=desire, memory=memory,
        activity=activity, expression=expression, evaluator=evaluator, config=config,
    )
    _subscribe(app)
    return app


async def _supervise_bus(app: _App) -> None:
    """监督 app.bus.run()：_persist 失败会终止 run()，这里接住异常并重启，
    不假设 run() 永不退出（05-event 有意让 DB 错误传播）。"""
    while True:
        try:
            await app.bus.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.getLogger(__name__).exception(
                "总线 run() 异常终止，%.1fs 后重启", _BUS_RESTART_DELAY
            )
            await asyncio.sleep(_BUS_RESTART_DELAY)


async def main() -> None:
    """入口：装配 → 启动 bus 监督器 + tick_loop → uvicorn serve。"""
    config = load_config()
    app = await build_app_context(config)
    fast = build_app(app)
    bus_task = asyncio.create_task(_supervise_bus(app))
    tick_task = asyncio.create_task(_tick_loop(app))
    server = uvicorn.Server(uvicorn.Config(fast, host=_HOST, port=_PORT))
    try:
        await server.serve()
    finally:
        bus_task.cancel()
        tick_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
