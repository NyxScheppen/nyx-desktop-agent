# 欲望系统（desire）：store + 全周期 + 门面

> 范围：`desire/store.py`（`DesireStore` 三表 CRUD + 序列化）、`desire/lifecycle.py`（`DesireLifecycle` 全周期编排）、`desire/facade.py`（`DesireFacade` 门面）。
> 值机制的纯函数/常量在 10（`desire/value.py`），本 spec 只**调**它们不重写。生命周期（加压/衰减/达峰生成/满足/淘汰）都在 `lifecycle.py`；纯 CRUD 在 `store.py`；`facade.py` 是薄门面（事件入口 + 读委托）。
> **本文件自包含**：三个文件的完整代码内联在下文。

## 元信息

- **前置依赖**：01-types（`DesireType` / `DesireStatus` / `DesireValue` / `ShortTermDesire` / `LongTermDesire` / `Goal` / `DesireState` / `GoalAction` / `Event` / `EventType` / `Source`）、02-config（`DesireConfig`：`peak_threshold` / `retry_limit` / `value_decay`）、03-llm（`LlmClient.complete`）、04-db（`Database`（conn+lock）+ `short_term_desire` / `desire_value` / `long_term_desire` 三表）、05-event（`EventBus.publish`）、10-desire-value（`decay_value` / `apply_pressure` / `reinforce_weight` / `raise_suppression` / `at_peak` / `is_expressible` / `default_value` / `REFUND_DELTA`）、15-eval（`Evaluator`）
- **本 spec 带来的连锁改动（ripple，已同步）**：01-types 给 `LongTermDesire` 加 `type` 字段、`DesireValue` 加 `updated_at` 字段；04-db 给 `long_term_desire` 加 `type` 列、`desire_value` 加 `updated_at` 列；10 的 `default_value` 补 `updated_at=0.0`；tech-ref §7 补 `desire/store.py`。

## 用户故事

> 作为 Nyx 系统的开发者，我想要 `DesireFacade` 把欲望全周期（观察加压、达峰生成、满足/淘汰回写）统一成一个门面，以便 `activity` 只调 `get_pending` 消费、`inner_life`/`activity` 只靠事件回写满足、仪表盘只调 `get_all` 快照；值机制纯函数复用 10 的 `value.py`，三表 CRUD 收口在 `store.py`，全周期编排在 `lifecycle.py`，所有 LLM 调用和事件发布走可注入的 `llm` / `bus`。

## 验收标准

- [ ] `store.py` 含 `DesireStore`（`add_desire` / `get_desire` / `list_pending` / `list_suppressed` / `list_short_term` / `update_desire` / `get_value` / `list_values` / `upsert_value` / `insert_long_term` / `list_long_term` / `update_long_term`）+ 序列化 helper，与「`desire/store.py`（完整）」段代码逐字一致
- [ ] `lifecycle.py` 含 `DesireLifecycle`（`pressure_from_observation` / `satisfy_from_activity_end` / `run_eval` / `satisfy` / `expire` / `mark_active` / `mark_suppressed`）+ `_parse_desire` / `_subtopics_for` / `_subtopic_freshness` / `_pick_topic_seed` / `_most_relevant_long_term` / `_build_desire_prompt`，与「`desire/lifecycle.py`（完整）」段代码逐字一致
- [ ] `facade.py` 含 `DesireFacade`，九个公开方法签名与 tech-ref §5 逐字一致：`add_value(source: Event) -> None` / `evaluate() -> list[ShortTermDesire]` / `get_pending() -> list[ShortTermDesire]` / `get_all() -> DesireState` / `satisfy(desire_id: str, goal_met: bool) -> None` / `expire(desire_id: str) -> None` / `mark_active(desire_id: str) -> None` / `mark_suppressed(desire_id: str) -> None` / `add_long_term(desire: LongTermDesire) -> None`
- [ ] `add_value` 是**事件入口**（对 tech-ref「加压」注释的精确化）：`OBSERVATION_STATE` → 互动欲加压，`ACTIVITY_END` → 解析满足信号回写；其余类型忽略
- [ ] `run_eval`：先四类型衰减（`elapsed_days` 来自 `updated_at`）→ 长期欲望周期加压 → 达峰判定（`at_peak and is_expressible`）→ **只生成最迫切的 1 个**（value 最高）→ LLM 生成 → 重置该类型 value → 入队 → 发布 `desire_generated`；无达峰返回 `[]`，非选中类型**保留压力**（不重置）
- [ ] `satisfy(goal_met=True, goal=None)`：出队（`SATISFIED`）+ 表达权重正强化 + 长期进度回写 + 发布 `desire_satisfied`
- [ ] `satisfy(goal_met=True, goal 非 None)`：`goal_progress+1` 累计；`>= goal.count` 才满足（出队 + 强化 + 回写 + 发布），否则保持 `PENDING`（累计进度，不重复满足）
- [ ] `satisfy(goal_met=False)`：`retry_count+1`；`> retry_limit` → 放弃（`EXPIRED` + 值回增 + 抑制阈值上浮 + 发布 `desire_expired`）；否则保持 `PENDING`（`created_at` 不变，`list_pending` 的 `created_at ASC` FIFO 天然靠前，无显式插队动作）
- [ ] `mark_active`：`PENDING → ACTIVE`（活动开始消费），仅 PENDING 可转、其余幂等 no-op
- [ ] `mark_suppressed`：`ACTIVE → SUPPRESSED`（活动中断/异常停车，不立即重试），仅 ACTIVE 可转、其余幂等 no-op
- [ ] `run_eval` 释放：`SUPPRESSED` 欲望其类型仍可表达（`is_expressible`）→ `PENDING` 放回队列；不可表达保持 `SUPPRESSED`
- [ ] `expire`：`EXPIRED` + 值回增 + 抑制阈值上浮 + 发布 `desire_expired`
- [ ] `add_long_term(desire)`：直接委托 `store.insert_long_term`（无容量逻辑；容量检查归 12 反思）
- [ ] 事件发布遵守「Facade 自己 publish、绝不返回 Event」；事件 `source=INTERNAL`；`desire_satisfied` / `desire_expired` 的 `correlation_id` = `desire.id`
- [ ] `run_eval` 的 LLM 产出（`output_type="desire"`）后紧跟 `await evaluator.evaluate(output)`（15-eval 原则 4，漏记由测试断言兜底）
- [ ] `pyright` strict 零报错

## 技术方案

- **新文件**：`nyx/desire/store.py`、`nyx/desire/lifecycle.py`、`nyx/desire/facade.py`（无 API、无数据变更——表结构是 04-db 的活）
- **库**：无新库（标准库 `json` / `time` / `uuid` / `typing`；`aiosqlite` 已由 04-db 引入）
- **公开面**：`from nyx.desire.store import DesireStore`；`from nyx.desire.lifecycle import DesireLifecycle`；`from nyx.desire.facade import DesireFacade`（不加 `__all__`；序列化/纯函数 helper 私有）
- **三层**：`DesireFacade`（Facade）→ `DesireLifecycle`（内部类，全周期编排）→ `DesireStore`（子系统，CRUD）。`lifecycle` 由 `facade` 内部构造（共享 store），不是额外抽象层——它承载"加压/衰减/生成/满足/淘汰"的编排，让 `facade` 只做事件入口 + 读委托
- **store 锁约定（同 07）**：每个方法一个 `async with self._db.lock` 的 SQL 块；store 方法之间不互相调用对方的持锁方法（`asyncio.Lock` 不可重入）
- **两个读路径（`get_pending` vs `get_all`）**：tech-ref §5 把它们分开——`get_pending` = 待消费队列（`list_pending`，`status IN pending/active`、`created_at ASC` FIFO），供活动排期/拼 prompt；`get_all` = 全量快照（`short_term` 用 `list_short_term`，含 satisfied/expired 历史、`created_at DESC`），供 `/api/desires` 仪表盘。故 store 要两个 list 方法，`DesireState.short_term` 是「全部」而非「待消费」
- **可空 JSON 列（同 07 的 `embedding`）**：`short_term_desire.goal` 是 `Goal | None` ⟺ `goal TEXT` 可空，`None ↔ SQL NULL`（非 `"null"` 字符串）
- **`add_value` 是事件入口（决策，对 tech-ref 注释的精确化）**：tech-ref 写「活动/对话/长期欲望 加压」，但 ROUTING 里 desire 订阅了 `OBSERVATION_STATE` 和 `ACTIVITY_END` 两个事件——`OBSERVATION_STATE` 是加压、`ACTIVITY_END` 是满足回写（design §3.2「满足信号」、ROUTING 注释「满足」）。故 `add_value` 按 `source.type` 派发；18-api 组合根用 `bus.subscribe(EventType.OBSERVATION_STATE, facade.add_value)` + `bus.subscribe(EventType.ACTIVITY_END, facade.add_value)` 绑定
- **`evaluate()` 由 tick 触发**：TICK_ROUTING 的 `DESIRE_EVAL → desire`。`evaluate()` 不接受 Event（tech-ref 签名），由 18-api 的 CLOCK_TICK 分发器按 `tick_type == DESIRE_EVAL` 调 `facade.evaluate()`。`desire_generated` 因此无上游 tick 溯源——`desire_generated` 的 `correlation_id = desire.id`（溯源到欲望自身，断链局限，同 09 的 `record_recall`）
- **加压增量（默认值，标注可推翻）**：`_OBSERVATION_PRESSURE_DELTA=0.15`（观察状态→互动欲 +0.15）、`_LONG_TERM_PRESSURE_DELTA=0.2`（每个长期欲望周期→对应类型 +0.2）。加压复用 10 的 `apply_pressure`
- **衰减时机（决策：加 `updated_at` 列，已与用户确认）**：`elapsed_days = (now - updated_at) / 86400`，`decay_value(value, elapsed_days, config.value_decay)`。`updated_at` 记录"最后一次 value 变化"，每次 evaluate 先衰减结算再写回 `updated_at = now`；衰减是单调的，两次 evaluate 之间 value 不实时下降（同 09 的 `decay_freshness` 局限），相对顺序不破坏
- **达峰生成（决策：只生成最迫切 1 个，已与用户确认）**：达峰判据 = `at_peak(value, peak) and is_expressible(value, suppression)`（10 的门控组合）；多个达峰类型时 `max(..., key=value)` 取最高者生成 1 个，**只重置选中类型**，其余达峰类型保留压力下次 evaluate 再生成——每次 evaluate 最多 1 次 LLM 调用（原则 1）
- **主题种子（decision，可推翻）**：`_pick_topic_seed` 按「没做过 / 新鲜度最低」从对应类型长期欲望的子主题池取——先查记忆（注入的 `list_memories` 回调，组合根接 `memory.list_memories`）做 substring 匹配，无命中记忆（= 没做过）最优先，都做过取新鲜度最低者；空池返回 `None`。种子拼进 `_build_desire_prompt` 给 LLM 作生成上下文
- **`strength` 语义**：`ShortTermDesire.strength` = 达峰时的 `value`（生成前保存，值重置后仍保留），供展示/排序
- **长期进度回写（decision，可推翻）**：满足时回写**最相关**的长期欲望 `progress += 0.1`（夹 `[0,1]`）、`strength -= 0.02`（夹 `[0,1]`）。`_most_relevant_long_term` 按 `goal.topic` 双向 substring 命中 `subtopics` 者优先，无 topic 或都不命中退回第一个 `type` 匹配；无 `type` 匹配返回 `None`（不回写）
- **长期欲望初始化（seed）**：3 个初始集来自 canon §4（硬编码），归 **18-api 组合根**启动时 `insert_long_term`（表空才 seed）；四类型 `desire_value` 同样由 18-api 用 `default_value(t)` 初始化并覆盖 `updated_at=now`。11 只提供 store 原语，不提供 seed 方法；`long_term_capacity` 不被 11 消费——长期欲望运行时新增/淘汰的唯一入口是 12-inner-life 反思，容量淘汰编排归 12
- **五态流转（V2，`ACTIVE`/`SUPPRESSED` 纳入）**：`PENDING → ACTIVE`（`mark_active`，活动 `_execute` 置 RUNNING 时）；`ACTIVE → SATISFIED | EXPIRED`（满足/淘汰，`satisfy` 里先 `ACTIVE → PENDING` 释放再走原逻辑）；`ACTIVE → SUPPRESSED`（`mark_suppressed`，活动中断/异常停车，不立即重试）；`SUPPRESSED → PENDING`（`run_eval` 里类型仍可表达即释放回队列）。`SUPPRESSED` 可逆、非终态——`list_pending` 过滤 `status IN ('pending','active')` 天然排除 suppressed/终态，无需改过滤；续做路径（14 恢复同一记录）里 `mark_active` 对 SUPPRESSED 是 no-op，完成时 `satisfy` 从 SUPPRESSED 直达 SATISFIED 合法
- **`activity_end` 的满足信号契约（14 引用）**：`event.content` 含 `desire_id`（`str | None`）与 `goal_met`（`bool | None`）。`satisfy_from_activity_end` 缺任一键或非预期类型即跳过（不抛），因为观察用户/发呆等活动无欲望可满足
- **新增 `output_type="desire"`**：落 `TokenUsage.purpose`（01-types 该字段是自由字符串，design 注释已含 `desire`，无冲突）

### `desire/store.py`（完整）

```python
import json

import aiosqlite

from nyx.db import Database
from nyx.enums import DesireStatus, DesireType, GoalAction
from nyx.types import DesireValue, Goal, LongTermDesire, ShortTermDesire

_STD_COLS = (
    "id, created_at, type, strength, description, goal, retry_count, "
    "status, goal_progress"
)
_VALUE_COLS = "type, value, expression_weight, suppression_threshold, updated_at"
_LT_COLS = (
    "id, created_at, type, name, description, strength, progress, "
    "subtopics, linked_values"
)


class DesireStore:
    """short_term_desire / desire_value / long_term_desire 三表 CRUD
    + 行↔dataclass 序列化。

    db 由组合根注入（同所有 store 共享一个 conn+lock）。每个方法一个
    `async with db.lock` 的 SQL 块，不跨方法嵌套（asyncio.Lock 不可重入）。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    # —— short_term_desire ——

    async def add_desire(self, desire: ShortTermDesire) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                f"INSERT INTO short_term_desire ({_STD_COLS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _std_row(desire),
            )
            await self._db.conn.commit()

    async def get_desire(self, desire_id: str) -> ShortTermDesire | None:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_STD_COLS} FROM short_term_desire WHERE id = ?", (desire_id,),
            )
            row = await cursor.fetchone()
        return _row_to_std(row) if row is not None else None

    async def list_pending(self) -> list[ShortTermDesire]:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_STD_COLS} FROM short_term_desire "
                "WHERE status IN (?, ?) ORDER BY created_at ASC",
                (DesireStatus.PENDING.value, DesireStatus.ACTIVE.value),
            )
            rows = await cursor.fetchall()
        return [_row_to_std(r) for r in rows]

    async def list_suppressed(self) -> list[ShortTermDesire]:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_STD_COLS} FROM short_term_desire "
                "WHERE status = ? ORDER BY created_at ASC",
                (DesireStatus.SUPPRESSED.value,),
            )
            rows = await cursor.fetchall()
        return [_row_to_std(r) for r in rows]

    async def list_short_term(self) -> list[ShortTermDesire]:
        """全部短期欲望（含 satisfied/expired 历史），供 /api/desires
        全量快照；最新在前。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_STD_COLS} FROM short_term_desire ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()
        return [_row_to_std(r) for r in rows]

    async def update_desire(self, desire: ShortTermDesire) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                "UPDATE short_term_desire SET type = ?, strength = ?, description = ?, "
                "goal = ?, retry_count = ?, status = ?, goal_progress = ? WHERE id = ?",
                (
                    desire.type.value, desire.strength, desire.description,
                    _goal_json(desire.goal), desire.retry_count, desire.status.value,
                    desire.goal_progress, desire.id,
                ),
            )
            await self._db.conn.commit()

    # —— desire_value ——

    async def get_value(self, type_: DesireType) -> DesireValue | None:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_VALUE_COLS} FROM desire_value WHERE type = ?",
                (type_.value,),
            )
            row = await cursor.fetchone()
        return _row_to_value(row) if row is not None else None

    async def list_values(self) -> list[DesireValue]:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_VALUE_COLS} FROM desire_value"
            )
            rows = await cursor.fetchall()
        return [_row_to_value(r) for r in rows]

    async def upsert_value(self, dv: DesireValue) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                f"INSERT INTO desire_value ({_VALUE_COLS}) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(type) DO UPDATE SET value = excluded.value, "
                "expression_weight = excluded.expression_weight, "
                "suppression_threshold = excluded.suppression_threshold, "
                "updated_at = excluded.updated_at",
                (
                    dv.type.value, dv.value, dv.expression_weight,
                    dv.suppression_threshold, dv.updated_at,
                ),
            )
            await self._db.conn.commit()

    # —— long_term_desire ——

    async def insert_long_term(self, desire: LongTermDesire) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                f"INSERT INTO long_term_desire ({_LT_COLS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _lt_row(desire),
            )
            await self._db.conn.commit()

    async def list_long_term(self) -> list[LongTermDesire]:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_LT_COLS} FROM long_term_desire"
            )
            rows = await cursor.fetchall()
        return [_row_to_lt(r) for r in rows]

    async def update_long_term(self, desire: LongTermDesire) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                "UPDATE long_term_desire SET type = ?, name = ?, description = ?, "
                "strength = ?, progress = ?, subtopics = ?, "
                "linked_values = ? WHERE id = ?",
                (
                    desire.type.value, desire.name, desire.description,
                    desire.strength, desire.progress, json.dumps(desire.subtopics),
                    json.dumps(desire.linked_values), desire.id,
                ),
            )
            await self._db.conn.commit()


def _std_row(
    d: ShortTermDesire
) -> tuple[str, float, str, float, str, str | None, int, str, int]:
    return (
        d.id, d.created_at, d.type.value, d.strength, d.description,
        _goal_json(d.goal), d.retry_count, d.status.value, d.goal_progress,
    )


def _goal_json(g: Goal | None) -> str | None:
    if g is None:
        return None
    return json.dumps({"action": g.action.value, "count": g.count, "topic": g.topic})


def _row_to_std(row: aiosqlite.Row) -> ShortTermDesire:
    return ShortTermDesire(
        id=row["id"],
        created_at=row["created_at"],
        type=DesireType(row["type"]),
        strength=row["strength"],
        description=row["description"],
        goal=_parse_goal(row["goal"]),
        retry_count=row["retry_count"],
        status=DesireStatus(row["status"]),
        goal_progress=row["goal_progress"],
    )


def _parse_goal(raw: str | None) -> Goal | None:
    if raw is None:
        return None
    data = json.loads(raw)
    return Goal(
        action=GoalAction(data["action"]),
        count=data["count"],
        topic=data.get("topic"),
    )


def _row_to_value(row: aiosqlite.Row) -> DesireValue:
    return DesireValue(
        type=DesireType(row["type"]),
        value=row["value"],
        expression_weight=row["expression_weight"],
        suppression_threshold=row["suppression_threshold"],
        updated_at=row["updated_at"],
    )


def _lt_row(
    d: LongTermDesire
) -> tuple[str, float, str, str, str, float, float, str, str]:
    return (
        d.id, d.created_at, d.type.value, d.name, d.description,
        d.strength, d.progress, json.dumps(d.subtopics), json.dumps(d.linked_values),
    )


def _row_to_lt(row: aiosqlite.Row) -> LongTermDesire:
    return LongTermDesire(
        id=row["id"],
        created_at=row["created_at"],
        type=DesireType(row["type"]),
        name=row["name"],
        description=row["description"],
        strength=row["strength"],
        progress=row["progress"],
        subtopics=json.loads(row["subtopics"]),
        linked_values=json.loads(row["linked_values"]),
    )
```

### `desire/lifecycle.py`（完整）

```python
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast
from uuid import uuid4

from nyx.config import DesireConfig
from nyx.desire.store import DesireStore
from nyx.desire.value import (
    REFUND_DELTA,
    apply_pressure,
    at_peak,
    decay_value,
    default_value,
    is_expressible,
    raise_suppression,
    reinforce_weight,
)
from nyx.enums import DesireStatus, DesireType, EventType, GoalAction
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import SECONDS_PER_DAY, internal_event
from nyx.llm.client import LlmClient
from nyx.types import (
    DesireValue,
    Event,
    Goal,
    LongTermDesire,
    Memory,
    ShortTermDesire,
)

_OBSERVATION_PRESSURE_DELTA = 0.15    # 观察状态 → 互动欲 +0.15
_LONG_TERM_PRESSURE_DELTA = 0.2       # 每个长期欲望周期 → 对应类型 +0.2
_LONG_TERM_PROGRESS_DELTA = 0.1       # 满足一次长期进度 +0.1
_LONG_TERM_STRENGTH_DECAY = 0.02      # 满足一次长期迫切度 -0.02
_GOAL_ACTIONS = frozenset(g.value for g in GoalAction)

_DESIRE_SYSTEM = (
    "你是尼克斯，一个住在用户电脑里的 AI 同伴，明确知道自己是 AI 并希望成为人类。"
    "基于当前最迫切的欲望类型和主题种子，生成一条具体的短期欲望"
    "（想做什么）+ 一个可量化的完成目标。"
    "只输出 JSON，键：description（具体描述，非空字符串）、"
    "goal（对象 {action, count, topic} 或 null）。"
    "goal.action 只能是 read / write / observe 之一，"
    "count 是正整数，topic 是可选字符串。"
)


def _build_desire_prompt(type_: DesireType, seed: str | None) -> str:
    return f"欲望类型：{type_.value}\n主题种子：{seed or '（无）'}"


def _parse_desire(raw: str) -> tuple[str, Goal | None]:
    """解析欲望 LLM 的 JSON 产出 → (description, goal)；结构非法抛 ValueError。"""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"欲望 JSON 应是对象，得到 {type(data).__name__}")
    parsed = cast(dict[str, Any], data)
    description = parsed.get("description")
    if not isinstance(description, str) or not description:
        raise ValueError("欲望 JSON 缺 description 或非空字符串")
    goal_raw = parsed.get("goal")
    if goal_raw is None:
        return description, None
    if not isinstance(goal_raw, dict):
        raise ValueError("欲望 JSON 的 goal 应是对象或 null")
    goal = cast(dict[str, Any], goal_raw)
    action = goal.get("action")
    if not isinstance(action, str) or action not in _GOAL_ACTIONS:
        raise ValueError(
            f"欲望 JSON 的 goal.action 应是 {'/'.join(g.value for g in GoalAction)}"
        )
    count = goal.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        raise ValueError("欲望 JSON 的 goal.count 应是正整数")
    topic = goal.get("topic")
    if topic is not None and not isinstance(topic, str):
        raise ValueError("欲望 JSON 的 goal.topic 应是字符串或 null")
    return description, Goal(action=GoalAction(action), count=count, topic=topic)


ListMemories = Callable[[], Awaitable[list[Memory]]]


def _subtopics_for(type_: DesireType, long_term: list[LongTermDesire]) -> list[str]:
    """对应类型长期欲望的子主题池；无匹配或空池返回 []。纯函数。

    过滤空白子主题：空串在 _subtopic_freshness 的 substring 匹配里是通配符。
    """
    for lt in long_term:
        if lt.type is type_ and lt.subtopics:
            return [s for s in lt.subtopics if s.strip()]
    return []


def _subtopic_freshness(subtopic: str, memories: list[Memory]) -> float | None:
    """子主题最新新鲜度：命中摘要/正文的最新记忆 freshness；无命中为 None。纯函数。"""
    if not subtopic.strip():
        return None   # 空串是 substring 通配符，不做匹配
    hits = [
        m.freshness
        for m in memories
        if subtopic in m.summary or subtopic in m.content
    ]
    return max(hits) if hits else None


def _pick_topic_seed(subtopics: list[str], memories: list[Memory]) -> str | None:
    """按「没做过 / 新鲜度最低」取种子：没做过最优先，都做过取新鲜度最低者。纯函数。"""
    if not subtopics:
        return None
    best = subtopics[0]
    best_freshness = _subtopic_freshness(best, memories)
    for subtopic in subtopics[1:]:
        freshness = _subtopic_freshness(subtopic, memories)
        if freshness is None and best_freshness is not None:
            best, best_freshness = subtopic, freshness
        elif (
            freshness is not None
            and best_freshness is not None
            and freshness < best_freshness
        ):
            best, best_freshness = subtopic, freshness
    return best


def _most_relevant_long_term(
    type_: DesireType,
    topic: str | None,
    long_term: list[LongTermDesire],
) -> LongTermDesire | None:
    """满足回写的长期欲望：goal.topic 双向 substring 命中 subtopics 者优先，
    否则第一个 type 匹配；无 type 匹配返回 None。纯函数。"""
    matching = [lt for lt in long_term if lt.type is type_]
    if not matching:
        return None
    if topic:
        for lt in matching:
            if any(topic in s or s in topic for s in lt.subtopics):
                return lt
    return matching[0]


class DesireLifecycle:
    """欲望全周期编排：观察加压、达峰生成、满足/淘汰回写。

    值机制纯函数在 value.py（10）；三表 CRUD 在 DesireStore；本类只编排。
    """

    def __init__(
        self,
        store: DesireStore,
        bus: EventBus,
        llm: LlmClient,
        evaluator: Evaluator,
        config: DesireConfig,
        list_memories: ListMemories,
    ) -> None:
        self._store = store
        self._bus = bus
        self._llm = llm
        self._evaluator = evaluator
        self._config = config
        self._list_memories = list_memories
        self._logger = logging.getLogger(__name__)

    async def pressure_from_observation(self, event: Event) -> None:
        """OBSERVATION_STATE → 互动欲加压（增量固定 +0.15，不解析 event.content）。"""
        dv = await self._store.get_value(DesireType.INTERACTION)
        if dv is None:
            dv = default_value(DesireType.INTERACTION)
        dv.value = apply_pressure(dv.value, _OBSERVATION_PRESSURE_DELTA)
        dv.updated_at = time.time()
        await self._store.upsert_value(dv)

    async def satisfy_from_activity_end(self, event: Event) -> None:
        """ACTIVITY_END → 解析满足信号（desire_id + goal_met），调 satisfy。"""
        desire_id = event.content.get("desire_id")
        goal_met = event.content.get("goal_met")
        if isinstance(desire_id, str) and isinstance(goal_met, bool):
            await self.satisfy(desire_id, goal_met)

    async def run_eval(self) -> list[ShortTermDesire]:
        """DESIRE_EVAL：衰减 → 长期加压 → 达峰判定 → 只生成最迫切的 1 个。"""
        now = time.time()
        long_term = await self._store.list_long_term()
        values: dict[DesireType, DesireValue] = {
            v.type: v for v in await self._store.list_values()
        }
        for t in DesireType:
            if t not in values:
                dv = default_value(t)
                dv.updated_at = now
                values[t] = dv

        # 1. 四类型衰减（结算时间流逝）
        for t in DesireType:
            dv = values[t]
            elapsed_days = max(0.0, now - dv.updated_at) / SECONDS_PER_DAY
            dv.value = decay_value(dv.value, elapsed_days, self._config.value_decay)
            dv.updated_at = now

        # 2. 长期欲望周期加压
        for lt in long_term:
            values[lt.type].value = apply_pressure(
                values[lt.type].value, _LONG_TERM_PRESSURE_DELTA
            )

        # 2.5 SUPPRESSED 释放：类型仍可表达（值越过抑制阈值）→ 放回队列
        for d in await self._store.list_suppressed():
            dv = values.get(d.type)
            if dv is not None and is_expressible(dv.value, dv.suppression_threshold):
                d.status = DesireStatus.PENDING
                await self._store.update_desire(d)

        # 3. 达峰判定（可表达 = 达峰且未被抑制压住）
        expressible = [
            dv for dv in values.values()
            if at_peak(dv.value, self._config.peak_threshold)
            and is_expressible(dv.value, dv.suppression_threshold)
        ]
        if not expressible:
            for t in DesireType:
                await self._store.upsert_value(values[t])
            return []

        # 4. 取最迫切的 1 个
        target = max(expressible, key=lambda dv: dv.value)
        peak_value = target.value
        subtopics = _subtopics_for(target.type, long_term)
        seed = (
            _pick_topic_seed(subtopics, await self._list_memories())
            if subtopics
            else None
        )

        # 5. 写回非选中类型（保留压力）；选中类型生成后重置写 0（step 7）
        for t in DesireType:
            if t is not target.type:
                await self._store.upsert_value(values[t])

        # 6. LLM 生成 + 解析（best-effort：LLM 返回非法 JSON → 漏报优于误报，跳过
        #    本次 eval；传输异常 / evaluator 真 bug 不吞，上抛给 supervisor 处理）
        desire_id = str(uuid4())
        output = await self._llm.complete(
            [
                {"role": "system", "content": _DESIRE_SYSTEM},
                {
                    "role": "user",
                    "content": _build_desire_prompt(target.type, seed),
                },
            ],
            module="desire",
            output_type="desire",
            correlation_id=desire_id,
            json_mode=True,
        )
        await self._evaluator.evaluate(output)
        try:
            description, goal = _parse_desire(output.content)
        except ValueError:
            self._logger.exception(
                "欲望 JSON 解析失败 type=%s correlation_id=%s",
                target.type.value,
                desire_id,
            )
            return []

        # 7. 重置选中类型 value（其余达峰类型保留压力）
        target.value = 0.0
        target.updated_at = now
        await self._store.upsert_value(target)

        # 8. 入队
        desire = ShortTermDesire(
            id=desire_id,
            created_at=now,
            type=target.type,
            strength=peak_value,
            description=description,
            goal=goal,
            retry_count=0,
            status=DesireStatus.PENDING,
        )
        await self._store.add_desire(desire)

        # 9. 发布
        await self._bus.publish(
            internal_event(
                EventType.DESIRE_GENERATED, {"desire_id": desire.id}, desire.id
            )
        )
        return [desire]

    async def satisfy(self, desire_id: str, goal_met: bool) -> None:
        """达成/未达成回写。goal 非 None 时按 count 累计 goal_progress，达标才满足；
        goal None 沿用单次满足。终态（SATISFIED/EXPIRED）幂等：重复投递 no-op。"""
        desire = await self._store.get_desire(desire_id)
        if desire is None:
            return
        if desire.status in (DesireStatus.SATISFIED, DesireStatus.EXPIRED):
            return
        if desire.status is DesireStatus.ACTIVE:
            desire.status = DesireStatus.PENDING  # 消费中先释放，未达标分支不卡 ACTIVE
        if goal_met and desire.goal is not None:
            desire.goal_progress += 1
            if desire.goal_progress >= desire.goal.count:
                await self._satisfy(desire)
            else:
                await self._store.update_desire(desire)  # 保持 PENDING，累计进度
            return
        if goal_met:
            await self._satisfy(desire)
        else:
            desire.retry_count += 1
            if desire.retry_count > self._config.retry_limit:
                await self._expire(desire)
            else:
                await self._store.update_desire(desire)  # 保持 PENDING，retry+1

    async def expire(self, desire_id: str) -> None:
        """淘汰：出队 + 值回增 + 抑制阈值上浮。终态幂等：重复投递 no-op。"""
        desire = await self._store.get_desire(desire_id)
        if desire is None:
            return
        if desire.status in (DesireStatus.SATISFIED, DesireStatus.EXPIRED):
            return
        await self._expire(desire)

    async def mark_active(self, desire_id: str) -> None:
        """PENDING → ACTIVE：活动开始消费。仅 PENDING 可转，其余幂等 no-op。"""
        desire = await self._store.get_desire(desire_id)
        if desire is None or desire.status is not DesireStatus.PENDING:
            return
        desire.status = DesireStatus.ACTIVE
        await self._store.update_desire(desire)

    async def mark_suppressed(self, desire_id: str) -> None:
        """ACTIVE → SUPPRESSED：活动中断/异常停车，不立即重试。仅 ACTIVE 可转。"""
        desire = await self._store.get_desire(desire_id)
        if desire is None or desire.status is not DesireStatus.ACTIVE:
            return
        desire.status = DesireStatus.SUPPRESSED
        await self._store.update_desire(desire)

    async def _satisfy(self, desire: ShortTermDesire) -> None:
        desire.status = DesireStatus.SATISFIED
        await self._store.update_desire(desire)
        await self._reinforce(desire)
        await self._bus.publish(
            internal_event(
                EventType.DESIRE_SATISFIED,
                {"desire_id": desire.id},
                desire.id,
            )
        )

    async def _expire(self, desire: ShortTermDesire) -> None:
        desire.status = DesireStatus.EXPIRED
        await self._store.update_desire(desire)
        await self._suppress(desire.type)
        await self._bus.publish(
            internal_event(
                EventType.DESIRE_EXPIRED,
                {"desire_id": desire.id},
                desire.id,
            )
        )

    async def _reinforce(self, desire: ShortTermDesire) -> None:
        """满足后：表达权重正强化 + 长期进度回写（最相关长期欲望）。"""
        dv = await self._store.get_value(desire.type)
        if dv is not None:
            dv.expression_weight = reinforce_weight(dv.expression_weight)
            await self._store.upsert_value(dv)
        topic = desire.goal.topic if desire.goal is not None else None
        lt = _most_relevant_long_term(
            desire.type, topic, await self._store.list_long_term()
        )
        if lt is not None:
            lt.progress = min(1.0, lt.progress + _LONG_TERM_PROGRESS_DELTA)
            lt.strength = max(0.0, lt.strength - _LONG_TERM_STRENGTH_DECAY)
            await self._store.update_long_term(lt)

    async def _suppress(self, type_: DesireType) -> None:
        """失败/淘汰后：值回增（压力回灌）+ 抑制阈值上浮（习得性抑制）。"""
        dv = await self._store.get_value(type_)
        if dv is None:
            return
        dv.value = apply_pressure(dv.value, REFUND_DELTA)
        dv.suppression_threshold = raise_suppression(dv.suppression_threshold)
        dv.updated_at = time.time()
        await self._store.upsert_value(dv)
```

### `desire/facade.py`（完整）

```python
from nyx.config import DesireConfig
from nyx.desire.lifecycle import DesireLifecycle, ListMemories
from nyx.desire.store import DesireStore
from nyx.enums import EventType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.llm.client import LlmClient
from nyx.types import DesireState, Event, LongTermDesire, ShortTermDesire


class DesireFacade:
    """欲望模块门面：事件入口 + 达峰生成 + 队列/快照读 + 满足/淘汰回写。

    全周期编排在 DesireLifecycle（内部构造，共享 store）；纯 CRUD 在 DesireStore。
    """

    def __init__(
        self,
        store: DesireStore,
        bus: EventBus,
        llm: LlmClient,
        evaluator: Evaluator,
        config: DesireConfig,
        list_memories: ListMemories,
    ) -> None:
        self._store = store
        self._lifecycle = DesireLifecycle(
            store, bus, llm, evaluator, config, list_memories
        )

    async def add_value(self, source: Event) -> None:
        """事件入口：OBSERVATION_STATE 加压互动欲，ACTIVITY_END 满足回写。"""
        if source.type is EventType.OBSERVATION_STATE:
            await self._lifecycle.pressure_from_observation(source)
        elif source.type is EventType.ACTIVITY_END:
            await self._lifecycle.satisfy_from_activity_end(source)

    async def evaluate(self) -> list[ShortTermDesire]:
        return await self._lifecycle.run_eval()

    async def get_pending(self) -> list[ShortTermDesire]:
        return await self._store.list_pending()

    async def get_all(self) -> DesireState:
        return DesireState(
            values=await self._store.list_values(),
            short_term=await self._store.list_short_term(),
            long_term=await self._store.list_long_term(),
        )

    async def satisfy(self, desire_id: str, goal_met: bool) -> None:
        await self._lifecycle.satisfy(desire_id, goal_met)

    async def expire(self, desire_id: str) -> None:
        await self._lifecycle.expire(desire_id)

    async def mark_active(self, desire_id: str) -> None:
        await self._lifecycle.mark_active(desire_id)

    async def mark_suppressed(self, desire_id: str) -> None:
        await self._lifecycle.mark_suppressed(desire_id)

    async def add_long_term(self, desire: LongTermDesire) -> None:
        """反思新增/强化长期欲望入口：直接插入（容量检查归 12 反思）。"""
        await self._store.insert_long_term(desire)
```

## 测试要点

- [ ] 单元测试 `tests/test_desire/`（`pytest-asyncio`；`db = await connect(":memory:")`；`store = DesireStore(db)`；`lifecycle = DesireLifecycle(store, bus, fake_llm, fake_evaluator, config, fake_list_memories)`；fake `LlmClient.complete` 按 `output_type == "desire"` 返回 fixture JSON 并记录调用、fake `Evaluator.evaluate` 记录调用；`EventBus` 用真实例 + 订阅 recording handler，`run()` 作 task 驱动——同 05/09 模式）：
  - [ ] **store**（`test_desire_store.py`）：
    - [ ] `add_desire + get_desire` 往返：含 `goal=Goal(READ, 3, "骑士团")`、非默认 `retry_count`/`status` → 各字段全等（`goal` JSON 往返、枚举往返）
    - [ ] `goal=None` 往返 → `get_desire().goal is None`（SQL NULL 非 `"null"` 字符串）
    - [ ] `list_pending`：造 pending/active/satisfied/expired 各一条 → 只返回 pending+active，按 `created_at ASC` 排序
    - [ ] `list_suppressed`：造 suppressed 两条（`created_at` 乱序）+ pending/active 各一条 → 只返回 suppressed，按 `created_at ASC` 排序
    - [ ] `list_short_term`：同上四条 → 返回全部（含 satisfied/expired），按 `created_at DESC` 排序（区别于 `list_pending` 的过滤 + ASC）
    - [ ] `update_desire`：改 `status`/`retry_count` → `get_desire` 验证
    - [ ] `goal_progress` 往返：`add_desire` 带 `goal_progress=2` → `get` 往返；`update_desire` 改 `goal_progress=3` → 再 `get` 验证（goal 精确计数存储层）
    - [ ] `list_values + upsert_value`：`upsert_value` 新建 → `list_values` 返回；同 `type` 再 `upsert_value` 改 `value`/`updated_at`（ON CONFLICT 更新不重复建行）
    - [ ] `insert_long_term + list_long_term + update_long_term`：`subtopics`/`linked_values` JSON 数组往返、`type` 枚举往返；`update_long_term` 改 `progress`/`strength`
  - [ ] **lifecycle 纯函数**：
    - [ ] `_parse_desire`：合法 JSON（含 goal）→ `(description, Goal)`；`goal: null` → `(description, None)`；缺 `description` / 空串 → `ValueError`；`goal.action` 非法 → `ValueError`；`count` 非正/非 int → `ValueError`；`topic` 非 str → `ValueError`；JSON 是数组 → `ValueError`
    - [ ] `_subtopics_for`：有 `type` 匹配且 `subtopics` 非空的长期欲望 → 返回该 `subtopics`（过滤 `""`/空白子主题）；无匹配/空池 → `[]`
    - [ ] `_subtopic_freshness`：空串/纯空白 → `None`（通配符不做匹配）；非空命中 → 最新 freshness
    - [ ] `_pick_topic_seed`：空池 → `None`；全没做过（无命中记忆）→ 第一个；部分做过 → 取没做过的；都做过 → 取新鲜度最低者
    - [ ] `_most_relevant_long_term`：无 `type` 匹配 → `None`；`topic` 双向 substring 命中第二条 → 返回第二条；`topic` 轻微漂移仍命中；`topic=None` → 第一个；同类型都不命中 → 第一个
    - [ ] `_build_desire_prompt`：含类型 `.value` 与种子；`seed=None` → 含「（无）」
  - [ ] **pressure_from_observation**：互动欲 `value` 由 `x` → `min(1.0, x + 0.15)`；`updated_at` 更新
  - [ ] **run_eval**：
    - [ ] 四类型都低于 `peak_threshold` → `[]`，无 LLM 调用
    - [ ] 互动欲达峰（造 `value=0.9`）→ 1 次 LLM 调用（`output_type="desire"`）、`evaluator.evaluate` 被调 1 次（收到该 `LLMOutput`）、返回 1 个 `ShortTermDesire`（`type` 正确、`status is PENDING`、`strength == 0.9`、`description`/`goal` 来自 fixture）、该类型 `value` 重置为 0、发布 `desire_generated`（`content["desire_id"] == desire.id`）
    - [ ] **只生成最迫切的 1 个**：互动欲 0.9 + 探索欲 0.85 都达峰 → 只生成互动欲；探索欲 `value` 保留 0.85 不重置
    - [ ] **长期加压**：seed 一个 `type=EXPLORATION` 的长期欲望 → 探索欲 `value` 额外 +0.2
    - [ ] **衰减**：`updated_at` 设为 1 天前 → `value` 衰减 `value_decay × 1`
    - [ ] **抑制门控**：`suppression_threshold=0.9 > value=0.85`（达峰但被抑制）→ 不生成，返回 `[]`
    - [ ] **SUPPRESSED 释放**：SUPPRESSED 欲望其类型 `value=0.6 >= suppression=0.5` → `run_eval` 后该欲望 `status is PENDING`（不新生成）；`value=0.4 < 0.5` → 保持 SUPPRESSED
    - [ ] **主题种子**：seed 探索型长期欲望（`subtopics=["骑士团", "大学朋友"]`）+ 记忆命中「骑士团」→ LLM 收到的 prompt 含「大学朋友」、不含「骑士团」（没做过优先）
  - [ ] **satisfy**：
    - [ ] `goal_met=True` → `status is SATISFIED`、表达权重 +0.05、长期进度 +0.1、发布 `desire_satisfied`
    - [ ] **goal 精确计数**：`goal.count=3` → 前两次 `goal_met=True` 累计 `goal_progress` 保持 PENDING、不发布；第三次 → SATISFIED + 发布 `desire_satisfied`
    - [ ] **最相关回写**：同类型两条长期欲望（subtopics 各不同）+ `goal.topic` 命中第二条 → 只回写第二条 progress、第一条不动
    - [ ] `goal_met=False` 且 `retry_count <= retry_limit` → `retry_count+1`、`status` 仍 `PENDING`、无事件
    - [ ] `goal_met=False` 且 `retry_count > retry_limit` → `status is EXPIRED`、值回增 `+REFUND_DELTA`、抑制阈值 +0.1、发布 `desire_expired`
  - [ ] **expire**：`status is EXPIRED` + 值回增 + 抑制阈值上浮 + 发布 `desire_expired`
  - [ ] **satisfy/expire 未命中**：`desire_id` 不存在 → 无事件、不抛
  - [ ] **mark_active / mark_suppressed**：
    - [ ] `mark_active`：PENDING → ACTIVE；SUPPRESSED/SATISFIED/EXPIRED/缺失 → 不变（no-op）
    - [ ] `mark_suppressed`：ACTIVE → SUPPRESSED；PENDING/SATISFIED/EXPIRED/缺失 → 不变（no-op）
    - [ ] `satisfy` 释放：ACTIVE 欲望 `satisfy` 未达标 → `status is PENDING`（不卡 ACTIVE）；达标 → SATISFIED
  - [ ] **facade**（`test_desire_facade.py`）：
    - [ ] `add_value(OBSERVATION_STATE)` → 互动欲加压；`add_value(ACTIVITY_END)`（content 含 `desire_id`+`goal_met`）→ 满足回写；`add_value(ACTIVITY_END)`（缺键/错类型）→ 无操作
    - [ ] `evaluate` / `get_pending` / `get_all` / `satisfy` / `expire` 委托（`get_all` 返回 `DesireState` 三字段非空；`short_term` 含 satisfied 历史、`long_term` 含 seed 的长期欲望）
    - [ ] `add_long_term(desire)` → `list_long_term` 多一条、字段全等（委托 `insert_long_term`）
    - [ ] `mark_active` / `mark_suppressed` 委托 → `status` 依次 ACTIVE / SUPPRESSED
- [ ] 集成测试：无（LLM 全 mock、DB 用 `:memory:`；与 activity/expression 的真实编排归 13/14/17）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] 18-api 组合根：`DesireStore(db)` → `DesireFacade(store, bus, llm, evaluator, config.desire, lambda: memory.list_memories())`；启动时 seed 四类型 `desire_value`（`default_value(t)` + `updated_at=now`）与 3 个初始长期欲望（canon §4，表空才 seed）；订阅 `OBSERVATION_STATE`/`ACTIVITY_END` 到 `facade.add_value`，CLOCK_TICK 的 `DESIRE_EVAL` 分发到 `facade.evaluate()`
- [ ] 13-activity 消费欲望走 `get_pending()`；14-activity 的 `activity_end` content 契约（`desire_id`/`goal_met`）与本 spec §技术方案一致；17-expression 搭话：MVP 不调 `satisfy`（欲望保持 pending，靠搭话间隔缓解重复）
