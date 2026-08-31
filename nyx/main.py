"""服务层组合根：装配六大 Facade → seed → 挂订阅 → tick 循环 → REST + SSE。"""
# pyright: reportUnusedFunction=false
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from nyx.activity.facade import ActivityFacade
from nyx.activity.material_store import MaterialStore
from nyx.activity.screen import ScreenObserver, capture_screen
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
from nyx.llm.vision import VisionClient
from nyx.memory.facade import MemoryFacade
from nyx.memory.retrieval import MemoryRetrieval, build_embed
from nyx.memory.store import MemoryStore
from nyx.reading.facade import DuplicateBookError, ReadingFacade
from nyx.reading.store import ReadingStore
from nyx.tools.file_io import build_file_io_tool, file_io
from nyx.tools.local_search import build_local_search_tool
from nyx.tools.registry import ToolRegistry
from nyx.tools.web_fetch import build_web_fetch_tool
from nyx.tools.web_search import build_web_search_tool
from nyx.types import (
    Activity,
    Book,
    CurrentState,
    DesireState,
    Event,
    LongTermDesire,
    Material,
    Memory,
    Personality,
    ReflectionOutcome,
    SelfNarrative,
    Values,
)

# —— 运行期常量（decision，可推翻）——
_HOST = "127.0.0.1"
_PORT = 8000
_TICK_INTERVAL = 60.0           # tick 循环检查间隔（秒）
_MUTTER_CHECK_INTERVAL = 150.0  # 碎碎念检查周期（秒，2.5 分钟）
_INITIATE_CHAT_INTERVAL = 300.0 # 搭话检查周期（秒，5 分钟）
_REFLECT_CHECK_INTERVAL = 3600.0   # 反思检查周期（秒，1 小时）
_REFLECT_MIN_INTERVAL = 21600.0    # 距上次反思最小冷却（秒，6 小时）
_REFLECT_MIN_NEW_MEMORIES = 3      # 新记忆积累到几条才反思
_BUS_BACKOFF_BASE = 1.0        # 总线重启指数退避初值（秒）
_BUS_BACKOFF_MAX = 30.0        # 退避上限（秒）
_BUS_MAX_FAILURES = 8          # 连续失败熔断阈值（达到判定致命，终止进程）
_BUS_RECOVERY_STREAK = 3       # 恢复信号：崩溃前连续成功落库达此数才重置失败计数
_SSE_QUEUE_SIZE = 100           # SSE 每连接队列上限（慢客户端丢帧背压）
_CANON_FILES = ("canon.md",)
_ASK_FILES = ("ask.md",)
_MAX_UPLOAD_BYTES = 500_000                  # 上传读物大小上限（decision，可推翻）
_MAX_EPUB_BYTES = 50 * 1024 * 1024           # EPUB 导入大小上限（decision，可推翻）


@dataclass
class _App:
    """组合根装配产物：组件引用 + 运行期状态。handler/端点闭包捕获本实例。"""
    bus: EventBus
    inner_life: InnerLifeFacade
    desire: DesireFacade
    memory: MemoryFacade
    activity: ActivityFacade
    expression: ExpressionFacade
    reading: ReadingFacade
    evaluator: Evaluator
    config: Config
    # 上次搭话时间戳（18-api 维护，供 should_initiate_chat）
    last_chat_at: float = 0.0
    last_presence: str = "away"    # 最近观察状态（online/away/busy）
    last_window_title: str = ""    # 最近窗口标题（document.title，观察活动回带）
    last_screen_summary: str = ""  # 最近屏幕视觉摘要（vision 采样循环写入）
    screen_observer: ScreenObserver | None = None  # 视觉 opt-in 装配；None=关闭


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


def _load_prompt_files(canon_dir: Path, names: tuple[str, ...]) -> str:
    """读若干 prompt 文件合并为一段字符串。任一缺失 fail-fast。"""
    parts: list[str] = []
    for name in names:
        p = canon_dir / name
        if not p.is_file():
            raise FileNotFoundError(f"prompt 文件缺失：{p}")
        parts.append(p.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def _load_canon(canon_dir: Path) -> str:
    """读 canon 文件合并为一段字符串注入。任一缺失 fail-fast。"""
    return _load_prompt_files(canon_dir, _CANON_FILES)


def _load_ask(canon_dir: Path) -> str:
    """读主动提问指导（ask.md）为一段字符串。任一缺失 fail-fast。"""
    return _load_prompt_files(canon_dir, _ASK_FILES)


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
    elif tick_type is TickType.REFLECTION_CHECK:
        await _check_reflect(app, event.correlation_id)


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


async def _check_reflect(app: _App, correlation_id: str) -> None:
    """反思检查：距上次反思够久 + 新记忆积累达标才触发。

    以 narrative.updated_at 为「上次反思」基准。
    """
    narrative = await app.inner_life.get_narrative()
    if time.time() - narrative.updated_at < _REFLECT_MIN_INTERVAL:
        return
    memories = await app.memory.list_memories()
    new_count = sum(1 for m in memories if m.created_at > narrative.updated_at)
    if new_count < _REFLECT_MIN_NEW_MEMORIES:
        return
    await app.inner_life.reflect(correlation_id)


async def _tick_loop(app: _App) -> None:
    """定时生成 clock_tick：grid 边界发 SCHEDULE_BLOCK_START + DESIRE_EVAL，
    周期发 MUTTER_CHECK + INITIATE_CHAT_CHECK。"""
    grid = app.config.activity.grid_minutes * 60.0
    last_block = 0.0                       # 启动即触发首个活动块（不推迟一整个 grid）
    # 抑制启动洪峰：碎碎念/搭话/反思不立即触发（初始化为当前时间）
    last_mutter = last_chat = last_reflect = time.time()
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
        if now - last_reflect >= _REFLECT_CHECK_INTERVAL:
            await app.bus.publish(_root_event(
                EventType.CLOCK_TICK,
                {"tick_type": TickType.REFLECTION_CHECK.value},
                Source.INTERNAL,
            ))
            last_reflect = now
        await app.expression.check_timeouts(now)   # 问句/搭话 超时收尾（60s 心跳）
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
    bus.subscribe(EventType.ACTIVITY_END, app.memory.remember_activity)
    bus.subscribe(EventType.REFLECTION, app.inner_life.apply_event)
    bus.subscribe(EventType.CLOCK_TICK, lambda e: _on_clock_tick(app, e))


class _ChatPayload(BaseModel):
    message: str


class _ExportPayload(BaseModel):
    format: str


class _ObservePayload(BaseModel):
    presence: Literal["online", "away", "busy"]
    window_title: str = ""


def build_app(app: _App) -> FastAPI:
    """构建 FastAPI 应用：15 个端点（14 个 REST + SSE），薄封装 Facade。"""
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

    @fast.get("/api/memories/search")
    async def api_memory_search(q: str) -> list[Memory]:
        return await app.memory.search(q)

    @fast.get("/api/desires")
    async def api_desires() -> DesireState:
        return await app.desire.get_all()

    @fast.get("/api/activity")
    async def api_activity() -> dict[str, Any]:
        return {
            "current": await app.activity.get_current(),
            "schedule": await app.activity.get_schedule(),
        }

    @fast.get("/api/activity/results")
    async def api_activity_results(limit: int = 100) -> list[Activity]:
        return await app.activity.get_results(limit)

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
    async def api_export(payload: _ExportPayload) -> Response:
        content = await app.memory.export(payload.format)
        media_type = "application/json" if payload.format == "json" else "text/markdown"
        return Response(content=content, media_type=media_type)

    @fast.post("/api/upload")
    async def api_upload(file: UploadFile = File(...)) -> dict[str, str]:
        name = Path(file.filename or "upload.txt").name
        chunks: list[bytes] = []
        total = 0
        while chunk := await file.read(1 << 20):
            total += len(chunk)
            if total > _MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=400, detail="文件过大")
            chunks.append(chunk)
        raw = b"".join(chunks)
        text = raw.decode("utf-8", errors="replace")
        result = await file_io("write", f"uploads/{name}", text)
        path = str(result["path"])
        await app.activity.register_material(path, name, len(text))
        return {"filename": name, "path": path}

    @fast.get("/api/materials")
    async def api_materials() -> dict[str, list[Material]]:
        return {"materials": await app.activity.list_materials()}

    @fast.post("/api/books", status_code=201)
    async def api_books(file: UploadFile = File(...)) -> Book:
        filename = file.filename or "book.epub"
        if Path(filename).suffix.lower() != ".epub":
            raise HTTPException(status_code=400, detail="仅支持 .epub 文件")
        chunks: list[bytes] = []
        total = 0
        while chunk := await file.read(1 << 20):
            total += len(chunk)
            if total > _MAX_EPUB_BYTES:
                raise HTTPException(status_code=400, detail="文件过大")
            chunks.append(chunk)
        data = b"".join(chunks)
        try:
            return await app.reading.import_book(filename, data)
        except DuplicateBookError as e:
            raise HTTPException(
                status_code=409,
                detail={"existing_book_id": e.existing_book_id, "title": e.title},
            ) from e
        except ValueError:
            raise HTTPException(status_code=400, detail="EPUB 无正文") from None
        except Exception:
            raise HTTPException(status_code=500, detail="EPUB 解析失败") from None

    @fast.post("/api/observe")
    async def api_observe(payload: _ObservePayload) -> dict[str, str]:
        presence = payload.presence
        app.last_presence = presence
        app.last_window_title = payload.window_title
        event = _root_event(
            EventType.OBSERVATION_STATE,
            {"presence": presence, "window_title": payload.window_title},
        )
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
    web_search/web_fetch 按 web_enabled opt-in（06-tools 完成定义）。"""
    tools = ToolRegistry()
    tools.register(build_local_search_tool())
    tools.register(build_file_io_tool())
    if config.exploration.web_enabled:
        tools.register(build_web_search_tool())
        tools.register(build_web_fetch_tool())
    return tools


async def build_app_context(config: Config) -> _App:
    """装配：db → llm → bus → tools → evaluator
    → 各 store/facade（解环）→ seed → 订阅。"""
    db: Database = await connect()
    llm = LlmClient.from_config(config.llm)
    bus = EventBus(db)
    tools = _build_tools(config)

    memory_store = MemoryStore(db)
    embed = build_embed(config.embedding.model)  # MVP 默认启用向量层；测试注入 None
    evaluator = Evaluator(embed)  # embed 供 OOC 第 2 档复用
    retrieval = MemoryRetrieval(memory_store, embed)
    memory = MemoryFacade(
        memory_store, retrieval, bus, llm, evaluator, config.memory, embed
    )

    desire_store = DesireStore(db)
    desire = DesireFacade(
        desire_store, bus, llm, evaluator, config.desire,
        lambda: memory.list_memories(), embed,
    )

    inner_life_store = InnerLifeStore(db)
    activity_store = ActivityStore(db)
    material_store = MaterialStore(db)

    # 循环依赖解环：_get_state/_reflect/_get_observation 引用可变容器，运行时才求值
    state_holder: list[Callable[[], Awaitable[CurrentState]]] = []
    reflect_holder: list[
        Callable[[str | None], Awaitable[ReflectionOutcome | None]]
    ] = []
    observation_holder: list[Callable[[], Awaitable[dict[str, str]]]] = []

    async def _get_state() -> CurrentState:
        return await state_holder[0]()

    async def _reflect(correlation_id: str | None) -> ReflectionOutcome | None:
        return await reflect_holder[0](correlation_id)

    async def _get_observation() -> dict[str, str]:
        return await observation_holder[0]()

    prompt_dir = Path(os.environ.get("NYX_CANON_DIR", "prompts"))
    canon = _load_canon(prompt_dir)
    ask = _load_ask(prompt_dir)

    activity = ActivityFacade(
        activity_store, material_store, bus, llm, evaluator, tools, desire,
        memory, _get_state, _reflect, _get_observation,
        config.activity, config.exploration, canon,
    )
    inner_life = InnerLifeFacade(
        inner_life_store, activity, desire, memory, bus, llm, evaluator, config,
    )
    state_holder.append(inner_life.get_state)
    reflect_holder.append(inner_life.reflect)

    await _seed_inner_life(inner_life_store)
    await _seed_desire(desire_store)

    reading = ReadingFacade(ReadingStore(db))

    expression = ExpressionFacade(
        bus, llm, evaluator, memory, activity, desire, inner_life, canon, ask,
        config.expression, tools,
    )

    app = _App(
        bus=bus, inner_life=inner_life, desire=desire, memory=memory,
        activity=activity, expression=expression, reading=reading,
        evaluator=evaluator, config=config,
    )

    async def _read_observation() -> dict[str, str]:
        return {
            "presence": app.last_presence,
            "window_title": app.last_window_title,
            "screen_summary": app.last_screen_summary,
        }

    observation_holder.append(_read_observation)

    # 屏幕视觉（opt-in）：装配 ScreenObserver；main 里起后台采样循环
    if config.vision.enabled:
        vision = VisionClient.from_config(config.vision)
        app.screen_observer = ScreenObserver(
            capture_screen, vision.describe, config.vision.interval_seconds
        )

    _subscribe(app)
    return app


async def _supervise_bus(app: _App) -> None:
    """监督 app.bus.run()：_persist 失败会终止 run()（事件已由 run() 放回队首），
    指数退避重启；崩溃前连续成功落库达阈值视为恢复并重置；
    连续失败到阈值熔断致命。"""
    failures = 0
    delay = _BUS_BACKOFF_BASE
    last_persisted = app.bus.persisted_count
    while True:
        try:
            await app.bus.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            if app.bus.persisted_count - last_persisted >= _BUS_RECOVERY_STREAK:
                failures = 0          # 崩溃前连续成功落库过 → 之前是健康期，重置
                delay = _BUS_BACKOFF_BASE
            last_persisted = app.bus.persisted_count
            failures += 1
            if failures >= _BUS_MAX_FAILURES:
                logging.getLogger(__name__).critical(
                    "总线连续 %d 次异常，判定致命，终止进程", failures
                )
                raise
            logging.getLogger(__name__).exception(
                "总线 run() 异常终止，%.1fs 后重启（第 %d/%d 次）",
                delay, failures, _BUS_MAX_FAILURES,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2.0, _BUS_BACKOFF_MAX)


async def _vision_loop(app: _App) -> None:
    """屏幕视觉后台采样循环：observer.run 永不抛，仅 CancelledError 上抛。"""

    def _update(summary: str) -> None:
        app.last_screen_summary = summary

    observer = app.screen_observer
    if observer is not None:
        await observer.run(_update)


async def main() -> None:
    """入口：装配 → 启动 bus 监督器 + tick_loop + uvicorn；任一任务异常终止。"""
    config = load_config()
    app = await build_app_context(config)
    fast = build_app(app)
    server = uvicorn.Server(uvicorn.Config(fast, host=_HOST, port=_PORT))
    serve_task = asyncio.create_task(server.serve())
    bus_task = asyncio.create_task(_supervise_bus(app))
    tick_task = asyncio.create_task(_tick_loop(app))
    tasks: set[asyncio.Task[Any]] = {serve_task, bus_task, tick_task}
    if app.screen_observer is not None:
        tasks.add(asyncio.create_task(_vision_loop(app)))
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()  # 任一先完成者异常 → 重抛终止进程（非零退出）
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _run_with_reload() -> None:
    """开发模式（--reload）：监听后端源码/prompt/配置变更，自动重启整个进程。

    子进程跑 `python -m nyx.main`（无 --reload，与手动启动同路径，不递归）；父进程
    复用 watchfiles 监听变更（DefaultFilter 忽略 __pycache__/.git），见变更即
    terminate 旧进程并重启。硬杀对 SQLite 安全（WAL 可恢复）。仅 dev 便利。
    """
    import subprocess

    from watchfiles import DefaultFilter, watch

    paths: list[str] = ["nyx", "prompts"]
    if Path("config.yaml").is_file():
        paths.append("config.yaml")

    proc = subprocess.Popen([sys.executable, "-m", "nyx.main"])
    try:
        for _changes in watch(*paths, watch_filter=DefaultFilter()):
            proc.terminate()
            proc.wait()
            proc = subprocess.Popen([sys.executable, "-m", "nyx.main"])
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        proc.wait()


if __name__ == "__main__":
    if "--reload" in sys.argv:
        _run_with_reload()
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            pass  # Ctrl+C 正常退出，不打印崩溃栈
