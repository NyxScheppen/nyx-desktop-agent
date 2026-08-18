# 服务层（组合根 + REST + SSE）

> 范围：`main.py` 组合根（实例化 + seed + 挂订阅 + tick 循环 + 启动）、REST 端点（薄封装 Facade 读方法）、SSE 广播（全部事件）。
> 组合根 spec：只做「装配 + 启动 + 薄封装」，不含业务逻辑（业务在 09/11/12/14/17 各 Facade）。**本文件自包含**：`main.py` 完整代码内联在下文。

## 元信息

- **前置依赖**：02-config（`load_config`/`Config`）、03-llm（`LlmClient.from_config`）、04-db（`connect`/`Database`）、05-event（`EventBus`/`ROUTING`/`TICK_ROUTING`）、06-tools（`ToolRegistry`）、08-memory-retrieval（`MemoryRetrieval`/`build_embed`）、09-memory-facade、10-desire-value（`default_value`）、11-desire、12-inner-life、13-activity-scheduler、14-activity、15-eval（`Evaluator`）、17-expression（`should_initiate_chat`/`ExpressionFacade`）
- **canon**：三份原始 prompt 文件由组合根读入合并（路径见「技术方案」）

## 用户故事

> 作为 Nyx 系统的开发者，我想要一个组合根把六大 Facade 装配起来——构造注入、启动 seed、按 ROUTING/TICK_ROUTING 挂订阅、tick 循环推进内在生命、REST 端点薄封装读方法、SSE 广播全部事件——以便一条 `python -m nyx.main` 起服务后，前端能查询快照/历史、发消息、实时收到 think/speak/ask 等全量事件流。

## 验收标准

- [ ] `main.py` 含 `main()` + `build_app()` + `build_app_context()` + `_seed_inner_life` / `_seed_desire` / `_subscribe` / `_tick_loop` / `_supervise_bus` / `_root_event` / `_load_canon` / `_interrupt_running` / `_build_tools`，与「`main.py`（完整）」段代码逐字一致
- [ ] **组合根按各 spec 完成定义装配**：`load_config` → `connect` → `LlmClient.from_config` → 各 store/facade 构造注入（循环依赖 `ActivityFacade↔InnerLifeFacade` 用 `_get_state` 延迟绑定解环；`evaluator` 注入 09/11/12/14/17 五个 Facade）
- [ ] **注册内置工具**：`local_search` + `file_io` 恒注册，`web_search` 仅当 `config.exploration.web_enabled` 注册（06-tools 完成定义）；探索链无条件调 `local_search`/`file_io`，缺失会 `KeyError`
- [ ] **seed 幂等**（表空才写）：inner_life 四张单行表（personality 8/8/2/6/7、values 8/6/9/5、energy 100/energetic、narrative 初始 identity）；desire 四类型 `desire_value`（`default_value(t)` + `updated_at=now`）+ 3 个初始长期欲望（canon §4）
- [ ] **订阅覆盖 ROUTING/TICK_ROUTING**：`USER_MESSAGE`→interrupt+reply、`OBSERVATION_STATE`→apply_event+add_value、`DESIRE_GENERATED`→on_desire_generated、`DESIRE_SATISFIED`→apply_event、`ACTIVITY_END`→add_value+apply_event、`REFLECTION`→apply_event、`CLOCK_TICK`→按 tick_type 分发四路
- [ ] **12 个端点**：tech-ref §4 的 11 个 REST + `GET /api/events`（SSE）；每个 REST 端点 = 对应 Facade 读方法的薄封装（无额外业务逻辑）
- [ ] **`POST /api/chat`**：构造 `USER_MESSAGE` 事件（`source=EXTERNAL`、`correlation_id=自身 id`）→ `publish` → 返回 `{event_id}`（回复走 SSE）
- [ ] **请求体校验**：`POST /api/chat`/`/api/export`/`/api/observe` 用 pydantic 请求模型（`_ChatPayload`/`_ExportPayload`/`_ObservePayload`），缺键/类型错 → 422（非 500）；`presence` 仅 `online`/`away`/`busy`（`Literal` 校验，拼写错误 422 而非静默禁用搭话）
- [ ] **SSE**：`data` = `event.content` 展开 + `event_id` + `correlation_id`（统一结构，不按 type 特判）；`event:` = `EventType.value`
- [ ] **SSE 背压**：每连接 `asyncio.Queue(maxsize=_SSE_QUEUE_SIZE=100)`；`_broadcast` 队列满时丢最旧保最新（`put_nowait` 捕获 `QueueFull`，慢客户端不拖垮总线）
- [ ] **`_tick_loop`**：定时 publish 四种 `CLOCK_TICK`（`content={"tick_type": ...}`）；`INITIATE_CHAT_CHECK` 走 `should_initiate_chat` 判定 + `initiate_chat`（发话才更新 `last_chat_at`）；首个活动块启动即触发（`last_block=0.0`），碎碎念/搭话不立即触发（`last_mutter`/`last_chat` 初始为 now，抑制启动洪峰）
- [ ] **总线监督器**：`main()` 启动 `_supervise_bus(app)` 而非裸 `bus.run()`；`run()` 异常终止 → `logger.exception` + 指数退避（`_BUS_BACKOFF_BASE`→`_BUS_BACKOFF_MAX`）重启；崩溃前连续成功落库达 `_BUS_RECOVERY_STREAK` 视为恢复、计数与退避重置（单次成功不足阈值仍累积，DB 抖动不假自愈）；连续 `_BUS_MAX_FAILURES` 次失败 `logger.critical` + 重抛熔断致命；`CancelledError` 重抛（组合根关闭不重启）
- [ ] **`main()` 竞速**：`asyncio.wait({serve_task, bus_task, tick_task}, FIRST_COMPLETED)` 后对**每个**先完成者 `task.result()`——serve 启动失败（`SystemExit`）/ tick 异常 / bus 熔断都重抛终止进程（非零退出，不静默吞）；`finally` cancel 后 `await asyncio.gather(..., return_exceptions=True)` 让 uvicorn 优雅关停跑完（非 fire-and-forget）
- [ ] `pyright` strict 零报错；无模块级可变全局变量（运行期状态 `last_chat_at`/`last_presence` 放 `_App` 实例）

## 技术方案

- **新文件**：`nyx/main.py`（无 Facade、无数据变更；`ROUTING`/`TICK_ROUTING` 已由 05-event 定义）
- **库**：`fastapi` + `uvicorn`（新增 web 栈；依赖 pin 同 03-llm 约定）；`httpx`（测试用 `AsyncClient` + `ASGITransport`，不触网）
- **公开面**：`main.py` 是入口（`python -m nyx.main` 或 `uvicorn nyx.main:app`），不加 `__all__`；`build_app` 供测试构造
- **web 框架选 FastAPI**：dataclass 返回值经 `jsonable_encoder` 直接序列化（`StrEnum`→`.value` 字符串），端点不声明 pydantic `response_model`（01-types「不用 pydantic」）；`GET /api/events` 用 `StreamingResponse`（手写 SSE 格式，不引 `sse-starlette`）。请求体用 pydantic `BaseModel` 请求模型（`_ChatPayload`/`_ExportPayload`/`_ObservePayload`）做缺键/取值校验 → 422——请求模型是 web 层校验，与 01-types 的 dataclass 领域类型、`response_model` 序列化互不相干
- **循环依赖解环（decision，可推翻）**：`ActivityFacade` 要 `get_state` 回调、`InnerLifeFacade` 要 `activity_facade` 实例。用 `_get_state` 闭包引用 `state_holder` 可变列表，先构造 `ActivityFacade`（占位回调）→ 再构造 `InnerLifeFacade` → 回填 `state_holder`。运行时才求值，构造期不成环
- **回复阻塞 vs 后台（decision，可推翻）**：`USER_MESSAGE` handler 里**同步 `await reply`**（阻塞事件总线到回复完成）。理由：回复是秒级、用户在等回复；活动是分钟级才后台（14-activity 已定 `create_task`）。用户连发消息顺序排队，符合 05-event「顺序分发」
- **抢占归组合根**：`USER_MESSAGE`/`INITIATE_CHAT_CHECK` 前先 `interrupt` 当前 running 活动（design §3.3）。这是跨模块编排（expression 不依赖 activity，避免耦合成环），归 18-api 组合根
- **工具注册归组合根**：`local_search` + `file_io` 恒注册（探索链 `_search_local`/`_read`/`_write_note` 无条件依赖），`web_search` 仅当 `config.exploration.web_enabled` 注册（与 14-activity `search_web` 节点同条件、06-tools 完成定义「opt-in 由组合根决定」）
- **观察输入归前端**：`classify_presence` 运行时调用方是前端 Tauri（14-activity `observe.py` 注释已定）。后端只 `POST /api/observe` 接收 `{presence}` → 维护 `app.last_presence` + publish `observation_state`。`desire.pressure_from_observation` 固定 +0.15、`inner_life.apply_event` 固定偏移，均不解析 content，故 content 仅 `{presence}` 供溯源/前端展示
- **ROUTING 的 `ACTIVITY_END`**：只消费 `desire` + `inner_life`（05-event 已删 `memory`），组合根无 memory handler
- **canon 读文件**：`_load_canon` 读三份 `character_lore.md` / `nyx_identity_and_growth.md` / `speaking_style.md` 合并为一段字符串注入 `ExpressionFacade`；路径 = `NYX_CANON_DIR` 环境变量 > `prompts/` 默认目录；任一缺失 `FileNotFoundError`（fail-fast，canon 是核心配置不兜底默认）
- **`_App` 内部 dataclass**：组合根的装配产物（不是新增抽象层——不增 Facade→子系统→内部类之外的层），持有 7 个组件引用 + 2 个运行期状态（`last_chat_at`/`last_presence`），端点/handler 闭包捕获 `_App` 实例
- **总线监督器（decision，可推翻）**：`main()` 用 `_supervise_bus(app)` 包一层 `bus.run()`。`_persist` 失败会终止 `run()`（05-event 有意让 DB 错误传播、事件放回队首不丢），监督器接住异常 `logger.exception` + 指数退避（`_BUS_BACKOFF_BASE=1.0`→`_BUS_BACKOFF_MAX=30.0`，×2 增长）后重启（重启而非降级：瞬时 `aiosqlite` 错误可自愈）；连续 `_BUS_MAX_FAILURES=8` 次失败 `logger.critical` + 重抛熔断致命（DB 永久挂时干净崩溃，不留「API 收事件但不处理」的僵尸态）；崩溃前连续成功落库达 `_BUS_RECOVERY_STREAK=3`（读 `EventBus.persisted_count` 单调计数，差 ≥ 3）视为恢复、失败计数与退避重置（避免分散瞬时故障累积假熔断）；单次成功不足阈值不重置（DB 抖动「隔一个挂一次」持续累积 → 熔断，不留无限 1s crash-loop 僵尸态）；`CancelledError` 重抛，组合根关闭不重启
- **`main()` 竞速（decision，可推翻）**：`asyncio.wait({serve_task, bus_task, tick_task}, FIRST_COMPLETED)` 把 `tick_task` 纳入监督——`_tick_loop` 异常不再静默丢弃（无 tick = 无活动调度/碎碎念/搭话，僵尸态）。对 `done` 里**每个**先完成者 `task.result()` 重抛（serve 启动失败 `SystemExit(1)` / tick 异常 / bus 熔断都非零退出），而非只取 `bus_task.result()`；`finally` cancel 后 `await asyncio.gather(..., return_exceptions=True)` 让 uvicorn 优雅关停跑完（fire-and-forget cancel 会留 uvicorn cleanup 不完整）
- **SSE 背压（decision，可推翻）**：每连接 sink 是 `asyncio.Queue(maxsize=_SSE_QUEUE_SIZE=100)`，`_broadcast` 满时丢最旧保最新（`put_nowait` 捕获 `QueueFull`）。SSE 允许丢帧、最新事件含最新状态；无界队列 + `put_nowait` 会让慢客户端无界吃内存、队列一满 `QueueFull` 反杀 `run()`
- **明确不做**：`apps/backend` 目录（项目是 `nyx/` 包结构，canon 文件放 `prompts/`）；定时器持久化（重启重置 `last_chat_at`/`last_presence`，同会话历史内存态）；`sse-starlette` / 额外中间件 / CORS（localhost 同源）

### `nyx/main.py`（完整）

```python
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
_BUS_BACKOFF_BASE = 1.0        # 总线重启指数退避初值（秒）
_BUS_BACKOFF_MAX = 30.0        # 退避上限（秒）
_BUS_MAX_FAILURES = 8          # 连续失败熔断阈值（达到判定致命，终止进程）
_BUS_RECOVERY_STREAK = 3       # 恢复信号：崩溃前连续成功落库达此数才重置失败计数
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
    last_block = 0.0                       # 启动即触发首个活动块（不推迟一整个 grid）
    last_mutter = last_chat = time.time()  # 抑制启动洪峰：碎碎念/搭话不立即触发
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


async def main() -> None:
    """入口：装配 → 启动 bus 监督器 + tick_loop + uvicorn；任一任务异常终止。"""
    config = load_config()
    app = await build_app_context(config)
    fast = build_app(app)
    server = uvicorn.Server(uvicorn.Config(fast, host=_HOST, port=_PORT))
    serve_task = asyncio.create_task(server.serve())
    bus_task = asyncio.create_task(_supervise_bus(app))
    tick_task = asyncio.create_task(_tick_loop(app))
    try:
        done, _ = await asyncio.wait(
            {serve_task, bus_task, tick_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            task.result()  # 任一先完成者异常 → 重抛终止进程（非零退出）
    finally:
        for t in (serve_task, bus_task, tick_task):
            if not t.done():
                t.cancel()
        await asyncio.gather(serve_task, bus_task, tick_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
```

## 测试要点

- [ ] 单元测试 `tests/test_api/`（`pytest-asyncio`；`db = await connect(":memory:")`；fake `LlmClient` / fake 各 Facade 按需注入）：
  - [ ] **纯函数/装配**（`test_context.py`）：
    - [ ] `_root_event`：`id == correlation_id`、默认 `source is EXTERNAL`、`timestamp` 非空、`content` 原样；显式 `source=Source.INTERNAL` → `source is INTERNAL`
    - [ ] `_load_canon`：tmp 目录放三份文件 → 合并返回（按顺序、含分隔）；缺一份 → `FileNotFoundError`（fail-fast）
    - [ ] `_seed_inner_life`（真 `InnerLifeStore` + `:memory:`）：空表 seed 四表 → `get_*` 非 None 且值 = canon §2/§3 初始值；**再跑一次幂等**（值不变、不重复行）
    - [ ] `_seed_desire`（真 `DesireStore`）：空表 seed 后 `list_values()` 四类型、`list_long_term()` 3 条；再跑幂等
    - [ ] `_build_tools`（`Config` 的 `exploration.web_enabled=False` / `True`）：`{t["name"] for t in schema()}` `False` → `{local_search, file_io}`、`True` → 多 `web_search`（工厂构造无 I/O，`roots`/`DDGS` 惰性到 `.call()`）
  - [ ] **端点薄封装**（`httpx.AsyncClient` + `ASGITransport`，fake Facade 返回 fixture）：
    - [ ] `GET /api/state` → `CurrentState` JSON（枚举字段为 `.value` 字符串）
    - [ ] `POST /api/chat` → 返回 `{event_id}`；`bus.list_events()` 含一条 `USER_MESSAGE`（`source=external`、`correlation_id == id`）
    - [ ] `GET /api/memories?tag=&type=` → `Memory[]`（`type` query 转 `MemoryType` 枚举）
    - [ ] `POST /api/observe` → 返回 `{event_id}`；`bus.list_events()` 含 `OBSERVATION_STATE`（content `{presence}`）
    - [ ] `POST /api/export` `format=json` / `md` 透传 `memory.export` 结果；`format=bogus` → `ValueError`（Facade 抛）
    - [ ] 请求体校验：`POST /api/chat` 缺 `message` → 422；`POST /api/observe` `presence=Online`（大小写拼写错误）→ 422（`Literal` 校验，不 publish 事件、不改 `last_presence`）
  - [ ] **tick 循环**（fake `bus.publish` 记录 + `monkeypatch` 常量使间隔→0 + `asyncio.sleep` 立即返回）：跑一个循环 → 收到 `CLOCK_TICK` 且 `tick_type` 覆盖 `SCHEDULE_BLOCK_START`/`DESIRE_EVAL`/`MUTTER_CHECK`/`INITIATE_CHAT_CHECK` 四种、每条 `source is INTERNAL`（系统定时器，非外部输入）；`grid_minutes=60` 时首轮只发 `schedule_block_start`/`desire_eval`（首个活动块启动即触发，`last_block=0.0`），碎碎念/搭话不立即触发
  - [ ] **订阅一致性**（构建 `_App`（fake Facade 记录 handler 调用）+ `_subscribe` + 真 `EventBus`，`run()` 作 task）：对 `ROUTING` 每个**非空消费者**的 event_type publish 一个事件 → 对应 Facade 方法被调（`OBSERVATION_STATE` → `apply_event`+`add_value` 两 handler；`ACTIVITY_END` → `add_value`+`apply_event`；`USER_MESSAGE` → `reply`；`DESIRE_GENERATED` → `on_desire_generated` 等）
  - [ ] **总线监督器**（fake `bus.run()` 每轮 raise + `monkeypatch _BUS_BACKOFF_BASE/_BUS_BACKOFF_MAX=0`）：`_supervise_bus` 连续 `_BUS_MAX_FAILURES` 次后 `RuntimeError` 重抛熔断（`run()` 调用次数 == `_BUS_MAX_FAILURES`）；崩溃前 `persisted_count` 每次 +`_BUS_RECOVERY_STREAK`（达恢复阈值）→ 计数重置、永不假熔断；崩溃前 `persisted_count` 每次 +1（单次成功不足阈值，DB 抖动）→ 计数不重置、照样熔断（`calls == _BUS_MAX_FAILURES`）；`task.cancel()` → `CancelledError` 重抛、不再重启
  - [ ] **`main()` 竞速**（monkeypatch `uvicorn.Server`/`Config` + `load_config`/`build_app_context`/`build_app`/`_tick_loop` 为 fake）：fake `server.serve()` 抛 `RuntimeError("port in use")`（端口被占；不用 `SystemExit`——它是 BaseException，asyncio 会经 `Handle._run` 直接重抛出事件循环、绕开 `task.result()` 重抛路径，无法被干净断言）→ `main()` 重抛 `RuntimeError`（非零退出，不静默吞）；fake `_tick_loop` 抛 `RuntimeError`（+ 阻塞 serve/bus）→ `main()` 重抛 `RuntimeError`（tick 异常传播）
- [ ] 集成测试：无（真实编排不测 LLM；组合根装配正确性已由端点 + 订阅一致性覆盖）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] ripple 同步：tech-ref §4 REST 表补 `POST /api/observe`（`{presence}` → `{event_id}`）；tech-ref §4 SSE「data 统一 = event.content 展开 + event_id + correlation_id」补一句（替换「按 type 逐条 payload」的示例表表述）；tech-ref §7 `main.py` 注释补「FastAPI 端点 + 组合根 + tick 循环」
- [ ] 下游约定：前端（Tauri）采集活跃度+窗口标题 → `classify_presence` 判定 → `POST /api/observe`；`POST /api/chat` 返回 `event_id` 后通过 `GET /api/events` 收 `think`/`speak`/`ask`
