# ActivityFacade + 行为链 + 观察

> 范围：`activity/store.py`（ActivityStore，新增）+ `activity/facade.py`（ActivityFacade）+ `activity/exploration.py`（跨域行为链 LangGraph）+ `activity/observe.py`（观察用户判定）+ `activity/screen.py`（屏幕视觉，opt-in）。
> 活动系统是「欲望的消费端」（design §1.3）：把 `DesireFacade.get_pending()` 的欲望映射成日程块活动、执行、判定 goal、发布 `activity_end` 让 desire/inner_life 消费回写。13-activity-scheduler 的四个纯函数（`desire_to_activity` / `rank_desires` / `build_schedule` / `format_time_label`）是本 spec 的决策底座。
> **本文件自包含**：四个文件的完整代码内联在下文。

## 元信息

- **前置依赖**：05-event（`EventBus` / ROUTING）、06-tools（`ToolRegistry`）、11-desire（`DesireFacade.get_pending`/`get_all`）、12-inner-life（`InnerLifeFacade.get_state` + `activity_end` 的 `energy_delta` 契约）、13-activity-scheduler（四个纯函数）、02-config（`ActivityConfig` / `ExplorationConfig`）、03-llm（`LlmClient.complete`）、04-db（`activity` 表）、15-eval（`Evaluator`）

## 用户故事

> 作为 Nyx 系统的开发者，我想要活动系统的门面——`on_tick`/`on_desire_generated` 触发消费欲望、`select_activity` 选活动、后台 task 执行（读书/创作/发呆/自由探索/观察/休息）、`complete_activity` 判定 goal 并发布 `activity_end`、`interrupt` 抢占即暂停（可续活动）或废弃、同日程块内恢复 PAUSED 记录、`get_current`/`get_schedule` 供仪表盘——以便欲望「达峰→生成→被消费→满足回写」闭环，前端能看到活动时间线、打断点、进度。

## 验收标准

- [ ] `store.py` 含 `ActivityStore`（`insert` / `get` / `get_current` / `get_paused_in_block` / `get_last_exploration` / `list_schedule` / `list_results` / `update`），与「`activity/store.py`（完整）」段逐字一致
- [ ] `facade.py` 含 `ActivityFacade`：`on_tick(tick_type) -> None` / `on_desire_generated(event) -> None` / `select_activity(desires, state) -> Activity | None` / `complete_activity(activity) -> None` / `interrupt(activity_id, by_event) -> None` / `get_current() -> Activity | None` / `get_schedule() -> list[Activity]` / `get_results() -> list[Activity]` / `list_materials() -> list[Material]` / `read_material(path, filename, total_chars, correlation_id) -> None` / `list_reading_notes(limit) -> list[ReadingNote]` / `delete_reading_note(note_id) -> None` / `list_annotations(target_id) -> list[Annotation]` / `add_annotation(target_id, content) -> Annotation` / `delete_annotation(annotation_id) -> None`
- [ ] `select_activity` 纯决策：无欲望→`None`；精力不足→`REST`；否则第一个可排程欲望→映射活动，`progress` 存 `desire_id`/`goal`/`correlation_id`/`description`
- [ ] `READING` 升级 `FREE_EXPLORATION`：探索欲映射的读书在 `_maybe_start_activity` 里经 `should_explore`（频率上限）判定升级；频率上限内降级为普通读书
- [ ] 空槽默认：`select_activity` 返回 `None`（无欲望/全互动欲）时 `_maybe_start_activity` 产 `_default_activity`（精力疲惫 `< ENERGY_REST_THRESHOLD`→`IDLE_REFLECTION`、否则→`OBSERVE_USER`），`progress["desire_id"] is None`
- [ ] 活动执行在**后台 task**（不阻塞事件总线）；`activity_start`/`activity_end`/`activity_interrupted` 由 facade 自己 `publish`、`source=INTERNAL`
- [ ] `complete_activity`：goal 判定（`_goal_met` 纯函数）→ `status=COMPLETED` → 发布 `activity_end`（content 含 `activity_id`/`type`/`desire_id`/`goal_met`/`energy_delta`/`result`）
- [ ] `interrupt`：先校验目标 activity 存在且 RUNNING → cancel 执行 task 并 await 其结束 → 重读守卫 → 可续活动（`_RESUMABLE_TYPES`：READING/CREATION/FREE_EXPLORATION）置 `PAUSED`、其余置 `ABANDONED` + 发布 `activity_interrupted`（content `{activity_id, by}`）
- [ ] 同日程块内恢复：`_maybe_start_activity` 在查 running 后、欲望排序前查 `get_paused_in_block(当前块)`，命中则恢复同一记录（READING 从 `material_store.get_by_path` 刷新 `read_chars`/`total_chars` 续读；CREATION/FREE_EXPLORATION 无中间态重跑）；未命中再走欲望排序/空槽默认
- [ ] `material_store.py` 含 `MaterialStore`（`upsert` / `next_readable` / `find_by_topic` / `get_by_path` / `advance` / `append_fragment` / `get_fragments` / `list_all`），`get_by_path` 供读书恢复续读、`list_all` 供资料面板进度展示
- [ ] `exploration.py` 含 `Exploration`（LangGraph 图）+ `should_explore` 纯函数；`web_enabled=false` 时不注册 `search_web` 节点；节点内 LLM 调用带 `correlation_id` 溯源
- [ ] `observe.py` 含 `classify_presence` 纯函数（活跃度+窗口标题 → `"online"`/`"away"`/`"busy"`）与 `build_observation_summary` 纯函数（presence/窗口标题/屏幕摘要 → 观察 summary）；`screen.py` 含 `capture_screen` + `ScreenObserver`（周期抓屏 → 视觉描述 → 回调）
- [ ] 两处 LLM 产出后紧跟 `await evaluator.evaluate(output)`：`_run_llm_activity`（`output_type` "reading"/"creation"）与 `Exploration._plan_next`（`output_type="exploration_plan"`）
- [ ] `pyright` strict 零报错

## 技术方案

- **新文件**：`nyx/activity/store.py`、`nyx/activity/facade.py`、`nyx/activity/exploration.py`、`nyx/activity/observe.py`、`nyx/activity/screen.py`、`nyx/activity/material_store.py`、`nyx/activity/reading_note_store.py`（`scheduler.py` 归 13）
- **库**：`langgraph`（仅 `StateGraph` / `START` / `END` / 条件边；版本敏感契约以锁定版本为准，同 03-llm 依赖 pin 约定）
- **ActivityStore 归属**：memory/desire/inner_life 各有 `store.py`，activity 保持一致——facade 不直接写 SQL（三层：Facade → 子系统 → 内部类）。tech-ref §7 ripple：`activity/` 补一行 `store.py  # ActivityStore（activity 表单表 CRUD）`
- **依赖解环（遵守 12 §54）**：`inner_life → {activity, desire}` 已锁，故 `ActivityFacade` **不持有 `InnerLifeFacade`**，注入 `get_state: Callable[[], Awaitable[CurrentState]]` 回调（组合根用 `inner_life.get_state` 绑定）。`select_activity(desires, state)` 以参数收 `CurrentState`（纯决策，无环）；`DesireFacade` 依赖单向（activity → desire，读队列/values），不成环
- **两个事件入口都归到 `_maybe_start_activity`**：`SCHEDULE_BLOCK_START` tick（每小时一块）与 `DESIRE_GENERATED`（欲望刚生成）都「有空闲就消费」。区别是触发时机，逻辑共用；有 running 活动则忽略（等它完成或下一个触发）
- **`select_activity` 返回 `Activity | None`**：无欲望 / 全互动欲时无活动可排，返回 `None`（空槽）。tech-ref §5 原签名 `-> Activity` 需 ripple 为 `-> Activity | None`（见完成定义）
- **活动执行 = 后台 task**：05-event「顺序分发、逐个 await handler」，若 on_tick 里 await 完整个活动（LLM 秒级、探索链分钟级）会阻塞事件总线、吞掉用户消息打断。故 `_maybe_start_activity` 用 `asyncio.create_task` 启动执行后立即返回；`interrupt` 靠 `self._task.cancel()` 软打断
- **并发守卫（同一时刻仅一个活动）**：`_start_lock` 串行化「查 running → insert PENDING → 翻 RUNNING」决策；但 `_execute` 在锁外异步翻 RUNNING，仅靠 `get_current`（只匹配 running，见 store）会留 TOCTOU 窗口（PENDING 已 insert 却查不到 running）。故锁内先同步查 `self._task` 未完成即 `return` 闭合窗口；`self._task` 在锁内赋值，天然串行
- **执行失败 = INCOMPLETE + 上抛**：`_execute` 失败落 `INCOMPLETE`（`ended_at` 已记）后仍 `raise`（不吞异常）；`logger.exception` 记录详情，`add_done_callback(_harvest_task_exception)` 收割 fire-and-forget task 的异常，避免 asyncio「Task exception was never retrieved」警告静默漂着
- **欲望状态接线（11 的 `mark_active`/`mark_suppressed`，V2）**：活动真正开始消费欲望时标 ACTIVE、非满足路径退出时释放 SUPPRESSED。三处均守卫 `isinstance(desire_id, str)`：`_execute` 置 RUNNING 后 `await self._desire.mark_active(desire_id)`（PENDING → ACTIVE）；`interrupt` 置 PAUSED/ABANDONED 落库后 `await self._desire.mark_suppressed(desire_id)`（ACTIVE → SUPPRESSED）；`_execute` 异常分支落 INCOMPLETE 后 `await self._desire.mark_suppressed(desire_id)`（ACTIVE → SUPPRESSED）。满足路径走既有 `complete_activity → ACTIVITY_END → satisfy`，由 11 的 `satisfy` 里「ACTIVE → PENDING」先行释放；续做路径（`_execute(resumed)`）`mark_active` 对 SUPPRESSED 是 no-op（守卫只 PENDING→ACTIVE），完成时 `satisfy` 从 SUPPRESSED 直达 SATISFIED 合法
- **自由探索升级（design §8.6，13 已委托给 14）**：`select_activity` 保持基线映射（探索欲→`READING`），升级判定放 `_maybe_start_activity`（那里有 store/config/now，`select_activity` 保持纯决策）。「探索欲」条件由结构保证——`READING` 活动**仅**由 `DesireType.EXPLORATION` 映射而来（13 `desire_to_activity`），故调用方在 `activity.type is READING` 时才调 `should_explore`（只查频率一项）
- **读书 = 读本地书库（禁凭空编造，design §8.2 落地）**：`MaterialStore`（`material` 表）存用户喂的读物与分块进度。`read_material`（`USER_MATERIAL` 入口）先 `upsert` 注册再发起 READING 读第一块；探索欲触发的 `READING` 在 `_maybe_start_activity` 里**先按 `goal.topic` 走 `find_by_topic`**（命中读那本，C2）、否则 `next_readable()` 取**最近未读完的那本**续读，读完自动换下一本。**无书可读**（`next_readable()` 返回 None）→ 经 `should_explore` 转 `FREE_EXPLORATION`（限速中则退回默认活动）——任何路径都不让 LLM 凭空编造读书内容。三层兜底：`_maybe_start_activity` 不产无 source 的 READING、`_run_activity` 缺 source `raise`、`_run_reading_source` 只读真实文件块（空块聚合已有片段，不凭空编造）
- **六种活动执行分派（`_run_activity`）**：
  - `READING`：`_run_reading_source` 分块读真实文件（切 `[read_chars, read_chars+6000)` 一块喂 LLM 产 `{book, note}`）→ result 附 `read_chars`/`total_chars` 推进进度；缺 `source` 直接 `raise ValueError`（**禁凭空编造**）；读到最后一块/空块时聚合全部片段 → 完整笔记落盘（`_aggregate_note` 1 次 LLM）。**滚动摘要接力**：续读时把「上次已读到第 N 字 + 此前片段笔记（`get_fragments`）」拼进 `extra_context`（`书名 + 上次已读 + 本次新读（第 N~M 字）`），让本次 note 自然承接已读部分、只续写本块新内容，避免几篇之间不连贯
  - `CREATION`：1 次 LLM（`json_mode=True`、`module="activity"`、`output_type="creation"`）→ result `{title, content}`，再把标题 `_sanitize_filename` 清洗成安全文件名落盘 `workspace/creations/<safe>.md`（`file_io` write），result 附 `path`。**创作注入人格声音 + 此刻心境**：system prompt 用 `_build_creation_system(canon, state)` 拼「canon 全文（含 §说话风格）+ 此刻心境（emotion/valence/arousal/energy/active_desires）+ 正向创作指令 + JSON 约束」，补上创作路径此前缺失的 canon（canon 只进对话、不进 `_ACTIVITY_SYSTEM`）——读书仍走 `_ACTIVITY_SYSTEM` 不动
  - `IDLE_REFLECTION`：直接 `await self._reflect`（组合根注入的 reflect 回调，1 LLM 在 inner_life），不发 `REFLECTION` 事件；result 回带 `{summary}`
  - `FREE_EXPLORATION`：调 `Exploration.run()`（LangGraph 多步，seed = 欲望描述）→ result `{findings, notes, nodes}`
  - `OBSERVE_USER`：调组合根注入的 `get_observation`（0 LLM）产 `{presence, window_title, screen_summary}`，`summary` 由 `build_observation_summary` 拼装（窗口优先、屏幕次之）；`REST`：0 LLM，result `{}`
- **空槽默认（design §8.2 观察/发呆，13 §30 委托 14）**：`select_activity` 返回 `None`（无欲望/全互动欲）时 `_maybe_start_activity` 产 `_default_activity`——精力疲惫（`< ENERGY_REST_THRESHOLD`，从 12 `inner_life.emotion` 共享导入）→ `IDLE_REFLECTION`（+10 微恢复 + 反思回带 summary），否则 → `OBSERVE_USER`（-10 消耗 + 情报收集）。这是 `IDLE_REFLECTION`/`OBSERVE_USER` 的唯一触发来源（非欲望驱动、不进 13 `build_schedule`），补上后两条分支可达，不再死代码
- **`activity_end` content 契约（11 §49 + 12 §45 引用，本 spec 定义完整形状）**：`{"activity_id": str, "type": str, "desire_id": str | None, "goal_met": bool | None, "energy_delta": float, "result": dict}`。`desire_id`/`goal_met` 由 11 `satisfy_from_activity_end` 消费（缺键/错类型跳过）；`energy_delta` 由 12 `_apply_energy` 消费（缺省 0）；`type`/`result` 由 09 `remember_activity` 消费（活动记忆）；`result` 进 SSE payload（tech-ref §4）
- **`energy_delta` 取值**：`getattr(config.energy_delta, activity.type.value)`（`ActivityType.value` 与 `ActivityEnergyDelta` 字段名 1:1，`reading→-20`、`creation→-25`、`free_exploration→-30`、`observe_user→-10`、`idle_reflection→+10`、`rest→+30`），不用 if-elif（六键自然对应）
- **goal 判定（C3 精确版）**：`_goal_met(goal, result)` = goal None → `None`；否则按 `action` 判「本次是否完成一个单位」——`read` → `result.completed`（读完整本）、`write` → 有 `title`+`content`、`observe` → 有 `presence`；其余 → `False`。
- **精力门槛**：`select_activity` 用 13 的 `build_schedule(desires, state.energy, energy_delta)` 取 `[0]`（精力跌破阈值自动穿插 `REST`），不另写门槛逻辑；`schedule[0] is REST` → 无关联 desire
- **`get_schedule()` 语义**：返回「今日已产生的 Activity 记录」（`started_at >= 今日零点`，`list_schedule`），按 `started_at ASC`；`current`（running）也在 schedule 内。未来计划不持久化（design §8.1），前端按单条时间线渲染已产生记录（running 加「◀ 现在」标记），**不画未来空槽**；`_day_start` 纯函数算当日零点（MVP 用 UTC 日边界，可推翻）
- **`interrupt` 的 `by_event: EventType`**：打断原因（`USER_MESSAGE` / `INITIATE_CHAT`）。谁调 `interrupt` 归 17/18（用户消息/搭话打断活动）；14 只提供方法 + 发布 `activity_interrupted`。可续活动（`_RESUMABLE_TYPES`：READING/CREATION/FREE_EXPLORATION）打断置 `PAUSED`（保留记录 + 欲望关联），其余瞬时无进度的活动（发呆/观察/休息）仍置 `ABANDONED` 终态
- **恢复/续做（design §3.3 抢占语义落地）**：`interrupt` 对 `_RESUMABLE_TYPES` 置 `PAUSED` 而非废弃，`progress` 里的 `desire_id`/`goal`/`correlation_id` 保留。`_maybe_start_activity` 在「查 running → 查当前块 PAUSED → 恢复」——命中则复用同一 id 重跑：READING 从 `material_store.get_by_path(source)` 刷新 `read_chars`/`total_chars` 续读（书库进度是唯一持久进度）；CREATION/FREE_EXPLORATION 无中间态、整段重跑（探索不 checkpoint 中间 findings/notes）。恢复不新建记录、不重新消耗欲望；跨日程块（`get_paused_in_block` 按 `schedule_block_id` 过滤）不恢复，旧 PAUSED 留档可查
- **`observe.py` 与观察状态的分工**：`classify_presence` 是「在线/离开/忙碌」三态判定的**单一事实来源**（纯函数、单测锁定）。采集（键盘/鼠标活跃度 + 前台窗口标题）在前端 Tauri 壳（design §2 进程边界），判定结果作为 `OBSERVATION_STATE` 事件推给 Python，ROUTING 到 inner_life + desire。**`classify_presence` 的运行时调用方是前端 ingress，不在本 spec 的 backend 范围内**（前端 spec 推迟）——保留它是为了让「判定规则」在 Python 侧可展示（原则 3）+ 可溯源（原则 5）。`OBSERVE_USER` 活动本身是 0-LLM（调注入的 `get_observation` 产 `{presence, window_title, screen_summary}`），`summary` 由 `build_observation_summary` 拼装
- **明确不做**：不建「计划」表（design §8.1 临时概念）；`classify_presence` 只覆盖键盘/鼠标/窗口三输入（屏幕视觉不扩展它）
- **屏幕视觉（design §8.5 落地，opt-in）**：`vision.enabled`（config）开启时，组合根注入的 `get_observation` 返回 `screen_summary`——`ScreenObserver` 周期抓屏（Pillow ImageGrab，`asyncio.to_thread`）→ `VisionClient` 视觉描述 → `app.last_screen_summary` 折入。`OBSERVE_USER` 的 `summary` 由 `build_observation_summary` 拼装（窗口标题优先、屏幕摘要次之）。视觉**丰富观察摘要**、不扩展 `classify_presence`（在线判定仍只靠键盘/鼠标/窗口三输入）；`ScreenObserver`/`VisionClient` 失败 best-effort 返 `None`，主流程正确性不依赖其产出。

### `activity/store.py`（完整）

```python
import json

import aiosqlite

from nyx.db import Database
from nyx.enums import ActivityStatus, ActivityType
from nyx.types import Activity

_COLS = "id, type, schedule_block_id, status, progress, started_at, ended_at"


class ActivityStore:
    """activity 表单表 CRUD。

    所有读写都 `async with self._db.lock:` 串行化（同 05/07/11）。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert(self, activity: Activity) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                "INSERT INTO activity (id, type, schedule_block_id, status, progress, "
                "started_at, ended_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    activity.id,
                    activity.type.value,
                    activity.schedule_block_id,
                    activity.status.value,
                    json.dumps(activity.progress),
                    activity.started_at,
                    activity.ended_at,
                ),
            )
            await self._db.conn.commit()

    async def get(self, activity_id: str) -> Activity | None:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM activity WHERE id = ?", (activity_id,),
            )
            row = await cursor.fetchone()
        return _row_to_activity(row) if row is not None else None

    async def get_current(self) -> Activity | None:
        """当前活动（running），取最新一条。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM activity WHERE status = 'running' "
                "ORDER BY started_at DESC LIMIT 1",
            )
            row = await cursor.fetchone()
        return _row_to_activity(row) if row is not None else None

    async def get_paused_in_block(self, schedule_block_id: str) -> Activity | None:
        """当前日程块内最新一条 PAUSED 记录（供恢复）；无则 None。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM activity WHERE status = 'paused' "
                "AND schedule_block_id = ? ORDER BY started_at DESC LIMIT 1",
                (schedule_block_id,),
            )
            row = await cursor.fetchone()
        return _row_to_activity(row) if row is not None else None

    async def get_last_exploration(self) -> float:
        """最近一次自由探索活动的 started_at；从未探索返回 0.0（供频率上限判定）。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT MAX(started_at) AS t FROM activity "
                "WHERE type = 'free_exploration'"
            )
            row = await cursor.fetchone()
        return row["t"] if row is not None and row["t"] is not None else 0.0

    async def list_schedule(self, start: float) -> list[Activity]:
        """今日已产生记录（started_at >= start），按 started_at ASC。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM activity WHERE started_at >= ? "
                "ORDER BY started_at ASC",
                (start,),
            )
            rows = await cursor.fetchall()
        return [_row_to_activity(r) for r in rows]

    async def list_results(self, limit: int) -> list[Activity]:
        """已完成且带产出的三类活动（读书/探索/创作），按结束时间倒序（供「产出」面板）。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM activity "
                "WHERE status = 'completed' AND type IN "
                "('reading', 'free_exploration', 'creation') "
                "ORDER BY ended_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
        return [_row_to_activity(r) for r in rows]

    async def update(self, activity: Activity) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                "UPDATE activity SET type = ?, schedule_block_id = ?, status = ?, "
                "progress = ?, started_at = ?, ended_at = ? WHERE id = ?",
                (
                    activity.type.value,
                    activity.schedule_block_id,
                    activity.status.value,
                    json.dumps(activity.progress),
                    activity.started_at,
                    activity.ended_at,
                    activity.id,
                ),
            )
            await self._db.conn.commit()


def _row_to_activity(row: aiosqlite.Row) -> Activity:
    return Activity(
        id=row["id"],
        type=ActivityType(row["type"]),
        schedule_block_id=row["schedule_block_id"],
        status=ActivityStatus(row["status"]),
        progress=json.loads(row["progress"]),
        started_at=row["started_at"],
        ended_at=row["ended_at"],
    )
```

### `activity/material_store.py`（完整）

```python
import json

import aiosqlite

from nyx.db import Database
from nyx.types import Material

_COLS = "path, filename, total_chars, read_chars, created_at, updated_at"


class MaterialStore:
    """读物（书库）单表 CRUD：上传注册 + 分块进度 + 选最近未读完。

    与 ActivityStore 同层（store 层）；所有读写 `async with self._db.lock:`
    串行化（同 05/07/11）。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert(
        self, path: str, filename: str, total_chars: int, now: float
    ) -> None:
        """注册（或重传覆盖）一本书：重传同路径重置进度为 0、更新时间戳。"""
        async with self._db.lock:
            await self._db.conn.execute(
                "INSERT INTO material (path, filename, total_chars, read_chars, "
                "created_at, updated_at) VALUES (?, ?, ?, 0, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET filename = excluded.filename, "
                "total_chars = excluded.total_chars, read_chars = 0, "
                "note_fragments = '[]', "
                "created_at = excluded.created_at, updated_at = excluded.updated_at",
                (path, filename, total_chars, now, now),
            )
            await self._db.conn.commit()

    async def list_all(self) -> list[Material]:
        """全量读物（按 created_at 倒序，最近上传在前），供资料面板进度展示。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM material ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()
        return [_row_to_material(row) for row in rows]

    async def next_readable(self) -> Material | None:
        """最近上传、且未读完的书（read_chars < total_chars，按 created_at 倒序）；
        无则 None。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM material WHERE read_chars < total_chars "
                "ORDER BY created_at DESC LIMIT 1"
            )
            row = await cursor.fetchone()
        return _row_to_material(row) if row is not None else None

    async def find_by_topic(self, topic: str) -> Material | None:
        """按主题（filename 子串，SQLite LIKE 默认大小写不敏感）选一本未读完的书；
        无则 None。goal.topic（如「骑士团」）与「最近上传」可能不同，读书按 topic
        选料时优先走这里（C2）。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM material WHERE filename LIKE ? "
                "AND read_chars < total_chars ORDER BY created_at DESC LIMIT 1",
                (f"%{topic}%",),
            )
            row = await cursor.fetchone()
        return _row_to_material(row) if row is not None else None

    async def get_by_path(self, path: str) -> Material | None:
        """按路径取一本书（含最新 read_chars），供读书恢复续读；无则 None。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM material WHERE path = ?", (path,),
            )
            row = await cursor.fetchone()
        return _row_to_material(row) if row is not None else None

    async def advance(self, path: str, read_chars: int, now: float) -> None:
        """推进一本书的已读进度（updated_at 同步刷新）。"""
        async with self._db.lock:
            await self._db.conn.execute(
                "UPDATE material SET read_chars = ?, updated_at = ? WHERE path = ?",
                (read_chars, now, path),
            )
            await self._db.conn.commit()

    async def append_fragment(self, path: str, note: str, now: float) -> None:
        """追加一块片段笔记到 note_fragments（JSON 数组，updated_at 同步刷新）。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT note_fragments FROM material WHERE path = ?", (path,)
            )
            row = await cursor.fetchone()
            fragments: list[str] = (
                json.loads(row["note_fragments"]) if row is not None else []
            )
            fragments.append(note)
            await self._db.conn.execute(
                "UPDATE material SET note_fragments = ?, updated_at = ? WHERE path = ?",
                (json.dumps(fragments, ensure_ascii=False), now, path),
            )
            await self._db.conn.commit()

    async def get_fragments(self, path: str) -> list[str]:
        """读一本书已累积的片段笔记（无则空列表）。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT note_fragments FROM material WHERE path = ?", (path,)
            )
            row = await cursor.fetchone()
        if row is None:
            return []
        return json.loads(row["note_fragments"])


def _row_to_material(row: aiosqlite.Row) -> Material:
    return Material(
        path=row["path"],
        filename=row["filename"],
        total_chars=row["total_chars"],
        read_chars=row["read_chars"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
```

### `activity/reading_note_store.py`（完整）

```python
import aiosqlite

from nyx.db import Database
from nyx.types import Annotation, ReadingNote

_NOTE_COLS = "id, book, content, created_at, path"
_ANNOTATION_COLS = "id, target_id, author, content, created_at"


class ReadingNoteStore:
    """读书笔记 + 批注两张表 CRUD：读完一本落一条笔记，用户可删笔记、加批注。

    与 MaterialStore 同层（store 层）；所有读写 `async with self._db.lock:`
    串行化（同 05/07/11）。删除笔记级联删其批注（同一事务）。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_by_path(self, note: ReadingNote) -> None:
        """按 path 去重落笔记：命中则原地更新（保留 note id → 批注仍挂旧 id），
        未命中则插入。单锁内 SELECT + UPDATE/INSERT 原子完成，避免跨路径同名
        误删、也避免重读静默删用户批注。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT id FROM reading_note WHERE path = ?", (note.path,)
            )
            row = await cursor.fetchone()
            if row is not None:
                await self._db.conn.execute(
                    "UPDATE reading_note SET book = ?, content = ?, created_at = ? "
                    "WHERE id = ?",
                    (note.book, note.content, note.created_at, row["id"]),
                )
            else:
                await self._db.conn.execute(
                    f"INSERT INTO reading_note ({_NOTE_COLS}) VALUES (?, ?, ?, ?, ?)",
                    (note.id, note.book, note.content, note.created_at, note.path),
                )
            await self._db.conn.commit()

    async def list_notes(self, limit: int = 50) -> list[ReadingNote]:
        """全量笔记（含 annotation_count 徽标用），按创建时间倒序。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_NOTE_COLS}, "
                "(SELECT COUNT(*) FROM annotation a "
                "WHERE a.target_id = reading_note.id) AS annotation_count "
                "FROM reading_note ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
        return [_row_to_note(row) for row in rows]

    async def delete(self, note_id: str) -> None:
        """删一条笔记 + 其全部批注（同一事务；已落盘的 notes/*.md 文件不动）。"""
        async with self._db.lock:
            await self._db.conn.execute(
                "DELETE FROM annotation WHERE target_id = ?", (note_id,)
            )
            await self._db.conn.execute(
                "DELETE FROM reading_note WHERE id = ?", (note_id,)
            )
            await self._db.conn.commit()

    async def add_annotation(self, annotation: Annotation) -> None:
        """给某条笔记加一条批注。"""
        async with self._db.lock:
            await self._db.conn.execute(
                f"INSERT INTO annotation ({_ANNOTATION_COLS}) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    annotation.id,
                    annotation.target_id,
                    annotation.author,
                    annotation.content,
                    annotation.created_at,
                ),
            )
            await self._db.conn.commit()

    async def list_annotations(self, target_id: str) -> list[Annotation]:
        """某笔记的全部批注，按创建时间升序（早的在前）。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_ANNOTATION_COLS} FROM annotation "
                "WHERE target_id = ? ORDER BY created_at ASC",
                (target_id,),
            )
            rows = await cursor.fetchall()
        return [_row_to_annotation(row) for row in rows]

    async def delete_annotation(self, annotation_id: str) -> None:
        """删一条批注。"""
        async with self._db.lock:
            await self._db.conn.execute(
                "DELETE FROM annotation WHERE id = ?", (annotation_id,)
            )
            await self._db.conn.commit()


def _row_to_note(row: aiosqlite.Row) -> ReadingNote:
    return ReadingNote(
        id=row["id"],
        book=row["book"],
        content=row["content"],
        created_at=row["created_at"],
        path=row["path"],
        annotation_count=int(row["annotation_count"]),
    )


def _row_to_annotation(row: aiosqlite.Row) -> Annotation:
    return Annotation(
        id=row["id"],
        target_id=row["target_id"],
        author=row["author"],
        content=row["content"],
        created_at=row["created_at"],
    )
```

### `activity/facade.py`（完整）

```python
import asyncio
import contextlib
import hashlib
import json
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from nyx.activity.exploration import Exploration, should_explore
from nyx.activity.material_store import MaterialStore
from nyx.activity.observe import build_observation_summary
from nyx.activity.reading_note_store import ReadingNoteStore
from nyx.activity.scheduler import (
    build_schedule,
    desire_to_activity,
    format_time_label,
    rank_desires,
)
from nyx.activity.store import ActivityStore
from nyx.config import ActivityConfig, ExplorationConfig
from nyx.desire.facade import DesireFacade
from nyx.enums import ActivityStatus, ActivityType, DesireType, EventType, TickType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import SECONDS_PER_DAY, internal_event
from nyx.inner_life.emotion import ENERGY_REST_THRESHOLD
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.tools.file_io import file_io
from nyx.tools.registry import ToolRegistry
from nyx.types import (
    Activity,
    Annotation,
    CurrentState,
    Event,
    Material,
    Memory,
    ReadingNote,
    ShortTermDesire,
)

_logger = logging.getLogger(__name__)

_READ_CONTEXT_CHARS = 6000  # 读物喂 LLM 的字符预算（decision，可推翻）
_KNOWLEDGE_REF_CHARS = 80   # 创作知识参考单条截断字符数（decision，可推翻）
_KNOWLEDGE_MAX_POINTS = 5   # 每本书知识点入记忆上限（decision，可推翻）
_KNOWLEDGE_MAX_CHUNKS = 16  # 知识点提取分块上限，防一次打满全书（decision，可推翻）

# 可续活动：打断置 PAUSED、同日程块内恢复同一记录（读书续读，创作/探索重跑）。
# 发呆/观察/休息瞬时无进度，仍抢占即废弃置 ABANDONED。
_RESUMABLE_TYPES = (
    ActivityType.READING,
    ActivityType.CREATION,
    ActivityType.FREE_EXPLORATION,
)


def _day_start(now: float) -> float:
    """当日零点（UTC 日边界，MVP 可推翻为本地时区）。纯函数。"""
    return now - now % SECONDS_PER_DAY


def _schedule_block_id(now: float, grid_minutes: int) -> str:
    """当前日程块 id（网格标签）：块序号 = 当日已过分钟 // grid_minutes。

    复用 scheduler.format_time_label(block_index, grid_minutes, 0.0) 产出
    design §3.3 约定的网格标签（如 14:00），与 main._tick_loop 的 grid 边界一致。
    纯函数。
    """
    block_index = int(now % SECONDS_PER_DAY) // 60 // grid_minutes
    return format_time_label(block_index, grid_minutes, 0.0)


def _goal_met(goal: dict[str, Any] | None, result: dict[str, Any]) -> bool | None:
    """Goal 完成判定（纯函数，C3 精确版）。

    goal None → None；否则按 action 判「本次是否完成一个单位」：
    read → result.completed（读完整本）；write → 有 title+content；
    observe → 有 presence。goal None 的欲望由 desire 层按单次 goal_met 满足。
    """
    if goal is None:
        return None
    action = goal.get("action")
    if action == "read":
        return bool(result.get("completed"))
    if action == "write":
        return bool(result.get("title") and result.get("content"))
    if action == "observe":
        return bool(result.get("presence"))
    return False


def _sanitize_filename(name: str) -> str:
    """把创作标题清洗成安全文件名（去路径分隔符/控制字符，空则回退 untitled）。

    对称读书读 workspace：创作落盘进 workspace/creations/<safe>.md。
    """
    cleaned = "".join(
        c for c in name if c not in '\\/:*?"<>|' and c.isprintable()
    ).strip()
    return cleaned or "untitled"


def _path_hash_suffix(path: str) -> str:
    """读物绝对路径 → 8 位短哈希，作落盘文件名后缀（跨路径同名书不互相覆盖）。"""
    return hashlib.md5(path.encode("utf-8")).hexdigest()[:8]


def _empty_progress() -> dict[str, Any]:
    """活动 progress 初始模板（desire_id/goal/correlation_id 三键为空）。"""
    return {"desire_id": None, "goal": None, "correlation_id": None}


def _correlation_id(activity: Activity) -> str:
    """活动事件/LLM 溯源 id：优先欲望 correlation_id，退活动自身 id。"""
    return str(activity.progress.get("correlation_id") or activity.id)


def _harvest_task_exception(task: asyncio.Task[None]) -> None:
    """收割后台 task 异常，避免 asyncio 'Task exception was never retrieved' 警告。

    真正的失败详情已在 _execute 的 except 块里经 logger.exception 记录；
    这里只负责把异常标记为「已检索」，不重复记录。
    """
    if not task.cancelled():
        task.exception()


def _parse_activity_result(raw: str, output_type: str) -> dict[str, Any]:
    """LLM 活动结果解析 + 结构校验（对齐 11/12 的 _parse_* fail-fast 风格）。

    reading 需 {book, note}、creation 需 {title, content}；
    缺键 raise（非法结构不静默吞）。
    """
    data: Any = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"活动结果 JSON 应是对象，得到 {type(data).__name__}")
    parsed = cast(dict[str, Any], data)
    required = ("book", "note") if output_type == "reading" else ("title", "content")
    if not all(k in parsed for k in required):
        raise ValueError(f"活动结果 JSON 缺键：{required}")
    return parsed


_CREATION_STYLES = ("日记体", "随笔", "微型小说", "散文诗", "书信体", "观察笔记")


def _pick_creation_style() -> str:
    """创作风格随机池：6 种里随机抽一种（W1）。"""
    return random.choice(_CREATION_STYLES)


def _build_creation_context(
    activity: Activity,
    style: str,
    knowledge: list[Memory],
    observation: dict[str, str],
) -> str:
    """创作参考上下文：风格 + 主题 + 知识库参考 + 当前屏幕灵感（W1/W2/W3）。

    各段有内容才拼、空则省略；纯确定性拼接，不调 LLM。
    """
    goal = activity.progress.get("goal")
    topic = ""
    if isinstance(goal, dict):
        t = cast(dict[str, Any], goal).get("topic")
        if isinstance(t, str):
            topic = t
    if not topic:
        desc = activity.progress.get("description")
        if isinstance(desc, str):
            topic = desc
    parts = [f"风格：{style}"]
    if topic:
        parts.append(f"主题：{topic}")
    if knowledge:
        refs = "\n".join(
            f"- {m.summary}：{m.content[:_KNOWLEDGE_REF_CHARS]}"
            for m in knowledge[:3]
        )
        parts.append(f"知识库参考（可引用，勿编造）：\n{refs}")
    window = observation.get("window_title", "").strip()
    screen = observation.get("screen_summary", "").strip()
    if window or screen:
        insp = "；".join(x for x in (window, screen) if x)
        parts.append(f"当前屏幕灵感：{insp}")
    return "\n\n".join(parts)


def _build_creation_system(canon: str, state: CurrentState) -> str:
    """创作 system prompt：canon 人格全文 + 此刻心境 + 创作声音指令 + JSON 约束。

    补上创作路径此前缺失的人格声音（canon 只进对话，不进 _ACTIVITY_SYSTEM）；
    「此刻心境」让文字有情绪底色，而非任意模型平铺直叙的套话。纯函数。
    """
    desires = "、".join(d.description for d in state.active_desires) or "无"
    mood = (
        "[此刻心境]\n"
        f"情感：{state.emotion.value}"
        f"（valence={state.valence:.2f}，arousal={state.arousal:.2f}）\n"
        f"精力：{state.energy:.0f}/100\n"
        f"惦记：{desires}"
    )
    voice = (
        "[创作要求]\n"
        "以尼克斯的说话风格写：温柔克制安静真诚、带一点羞涩犹豫停顿、偶尔轻微自我修正，"
        "不要客服腔、不要堆砌华丽词藻，让文字有你的情绪底色。"
        "遵循给定风格，可引用知识库参考，但绝不编造不存在的知识；"
        "当前屏幕灵感只作启发，勿照搬。按 JSON 输出 {title, content}。"
    )
    return f"{canon}\n\n{mood}\n\n{voice}"


class ActivityFacade:
    """活动模块门面：消费欲望 → 选活动 → 后台执行 → 完成/打断 → 发布事件。

    依赖注入解环：不持有 InnerLifeFacade，注入 get_state 回调
    （组合根绑 inner_life.get_state）。
    """

    def __init__(
        self,
        store: ActivityStore,
        material_store: MaterialStore,
        bus: EventBus,
        llm: LlmClient,
        evaluator: Evaluator,
        tools: ToolRegistry,
        desire: DesireFacade,
        memory: MemoryFacade,
        reading_notes: ReadingNoteStore,
        get_state: Callable[[], Awaitable[CurrentState]],
        reflect: Callable[[str | None], Awaitable[str | None]],
        get_observation: Callable[[], Awaitable[dict[str, str]]],
        config: ActivityConfig,
        exploration_config: ExplorationConfig,
        canon: str,
    ) -> None:
        self._store = store
        self._material_store = material_store
        self._bus = bus
        self._llm = llm
        self._evaluator = evaluator
        self._desire = desire
        self._memory = memory
        self._reading_notes = reading_notes
        self._get_state = get_state
        self._reflect = reflect
        self._get_observation = get_observation
        self._config = config
        self._canon = canon
        self._exploration = Exploration(
            llm, evaluator, tools, bus, exploration_config
        )
        self._exploration_config = exploration_config
        self._start_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    # ---- 事件入口 ----

    async def on_tick(self, tick_type: TickType) -> None:
        """SCHEDULE_BLOCK_START：日程块开始，有空闲就消费欲望。"""
        if tick_type is TickType.SCHEDULE_BLOCK_START:
            await self._maybe_start_activity()

    async def on_desire_generated(self, event: Event) -> None:
        """DESIRE_GENERATED：欲望刚生成，有空闲就立即消费。"""
        await self._maybe_start_activity()

    # ---- 决策 ----

    def select_activity(
        self, desires: list[ShortTermDesire], state: CurrentState
    ) -> Activity | None:
        """选下一个活动（纯决策，无 I/O）。

        desires 已由 _maybe_start_activity 排序（rank_desires）。
        """
        if not desires:
            return None
        target = next(
            (d for d in desires if desire_to_activity(d.type) is not None), None
        )
        if target is None:
            return None
        schedule = build_schedule(desires, state.energy, self._config.energy_delta)
        if not schedule:
            return None
        activity_type = schedule[0]
        if activity_type is ActivityType.REST and target.type is not DesireType.REST:
            # 精力恢复穿插的 REST（队首非休息欲却产出 REST = 精力不足），
            # 无关联 desire
            target = None
        now = time.time()
        progress = _empty_progress()
        if target is not None:
            progress["desire_id"] = target.id
            progress["correlation_id"] = target.id
            progress["description"] = target.description
            if target.goal is not None:
                progress["goal"] = {
                    "action": target.goal.action.value,
                    "count": target.goal.count,
                    "topic": target.goal.topic,
                }
        return Activity(
            id=str(uuid.uuid4()),
            type=activity_type,
            schedule_block_id=_schedule_block_id(now, self._config.grid_minutes),
            status=ActivityStatus.PENDING,
            progress=progress,
            started_at=now,
        )

    def _default_activity(self, state: CurrentState) -> Activity:
        """空槽默认（design §8.2 观察/发呆，13 §30 委托 14）：无欲望可消费时，
        精力疲惫 → 发呆反思（+10 恢复 + 反思），否则 → 观察用户（-10 情报收集）。
        纯决策，无 I/O。
        """
        activity_type = (
            ActivityType.IDLE_REFLECTION
            if state.energy < ENERGY_REST_THRESHOLD
            else ActivityType.OBSERVE_USER
        )
        now = time.time()
        return Activity(
            id=str(uuid.uuid4()),
            type=activity_type,
            schedule_block_id=_schedule_block_id(now, self._config.grid_minutes),
            status=ActivityStatus.PENDING,
            progress=_empty_progress(),
            started_at=now,
        )

    # ---- 生命周期 ----

    async def complete_activity(self, activity: Activity) -> None:
        """完成：goal 判定 + 收尾 + 发布 activity_end（desire/inner_life 消费）。"""
        activity.status = ActivityStatus.COMPLETED
        activity.ended_at = time.time()
        await self._store.update(activity)
        goal = activity.progress.get("goal")
        result = activity.progress.get("result", {})
        await self._bus.publish(
            internal_event(
                EventType.ACTIVITY_END,
                {
                    "activity_id": activity.id,
                    "type": activity.type.value,
                    "desire_id": activity.progress.get("desire_id"),
                    "goal_met": _goal_met(goal, result),
                    "energy_delta": getattr(
                        self._config.energy_delta, activity.type.value
                    ),
                    "result": result,
                },
                _correlation_id(activity),
            )
        )

    async def interrupt(self, activity_id: str, by_event: EventType) -> None:
        """抢占即暂停：校验目标 RUNNING → cancel 执行 task 并 await 其彻底结束
        → 重读守卫（窗口内已自行完成/失败则不覆盖）→ 置终态落库
        （可续活动 PAUSED，其余 ABANDONED）+ 发布 activity_interrupted。

        执行中的 result 尚未写入，故仅落终态（不持久化部分进度）；
        读书的 read_chars 已 advance 进 material 层，恢复时从那里续读。
        """
        activity = await self._store.get(activity_id)
        if activity is None or activity.status is not ActivityStatus.RUNNING:
            return
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            # 等 _execute 收尾即可，无论取消还是异常终；失败已由 _execute
            # 记日志 + done_callback 收割，终态交给下方重读守卫，不跳过 ABANDONED
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        activity = await self._store.get(activity_id)   # 重读：已终态则不动
        if activity is None or activity.status is not ActivityStatus.RUNNING:
            return
        # 可续活动（读书/创作/探索）打断置 PAUSED 保留记录，同日程块内恢复；
        # 其余（发呆/观察/休息瞬时无进度）抢占即废弃，仍置 ABANDONED。
        activity.status = (
            ActivityStatus.PAUSED
            if activity.type in _RESUMABLE_TYPES
            else ActivityStatus.ABANDONED
        )
        activity.ended_at = time.time()
        await self._store.update(activity)
        desire_id = activity.progress.get("desire_id")
        if isinstance(desire_id, str):
            await self._desire.mark_suppressed(desire_id)  # 中断：ACTIVE → SUPPRESSED
        await self._bus.publish(
            internal_event(
                EventType.ACTIVITY_INTERRUPTED,
                {"activity_id": activity_id, "by": by_event.value},
                activity_id,
            )
        )

    # ---- 读 ----

    async def get_current(self) -> Activity | None:
        return await self._store.get_current()

    async def get_schedule(self) -> list[Activity]:
        return await self._store.list_schedule(_day_start(time.time()))

    async def get_results(self, limit: int = 100) -> list[Activity]:
        """跨天历史产出（读书笔记/探索发现/创作内容），按结束时间倒序。"""
        return await self._store.list_results(limit)

    async def list_materials(self) -> list[Material]:
        """书库全量（含已读进度），供资料面板展示「读到哪了」。"""
        return await self._material_store.list_all()

    async def list_reading_notes(self, limit: int = 50) -> list[ReadingNote]:
        """读书笔记清单（含批注数），供读书笔记面板 CRUD。"""
        return await self._reading_notes.list_notes(limit)

    async def delete_reading_note(self, note_id: str) -> None:
        """删一条读书笔记（级联删其批注；已落盘 notes/*.md 文件不动）。"""
        await self._reading_notes.delete(note_id)

    async def list_annotations(self, target_id: str) -> list[Annotation]:
        """某笔记的全部批注，按时间升序。"""
        return await self._reading_notes.list_annotations(target_id)

    async def add_annotation(self, target_id: str, content: str) -> Annotation:
        """给笔记加一条用户批注（author 固定 'user'）。"""
        annotation = Annotation(
            id=str(uuid.uuid4()),
            target_id=target_id,
            author="user",
            content=content,
            created_at=time.time(),
        )
        await self._reading_notes.add_annotation(annotation)
        return annotation

    async def delete_annotation(self, annotation_id: str) -> None:
        """删一条批注。"""
        await self._reading_notes.delete_annotation(annotation_id)

    async def read_material(
        self, path: str, filename: str, total_chars: int, correlation_id: str
    ) -> None:
        """用户投喂资料：注册进书库 → 立即发起一次 READING 活动读第一块。

        与 _maybe_start_activity 共用 _start_lock 串行守卫；忙时跳过（文件已落盘、
        且已入书库，探索欲后续会自行续读，不排队）。结果经 activity_start/activity_end
        SSE 可见。
        """
        await self._material_store.upsert(path, filename, total_chars, time.time())
        async with self._start_lock:
            if self._task is not None and not self._task.done():
                return
            now = time.time()
            activity = Activity(
                id=str(uuid.uuid4()),
                type=ActivityType.READING,
                schedule_block_id=_schedule_block_id(now, self._config.grid_minutes),
                status=ActivityStatus.PENDING,
                progress={
                    "source": path,
                    "filename": filename,
                    "description": filename,
                    "read_chars": 0,
                    "total_chars": total_chars,
                    "correlation_id": correlation_id,
                },
                started_at=now,
            )
            await self._store.insert(activity)
            self._task = asyncio.create_task(self._execute(activity))
            self._task.add_done_callback(_harvest_task_exception)

    # ---- 内部 ----

    async def _maybe_start_activity(self) -> None:
        async with self._start_lock:
            if self._task is not None and not self._task.done():
                return
            current = await self._store.get_current()
            if current is not None and current.status is ActivityStatus.RUNNING:
                return
            # 同日程块内恢复 PAUSED 记录（design §3.3）：读书从 material 层刷新
            # read_chars 续读；创作/探索无中间态、重跑。恢复同一 id，不新建。
            block_id = _schedule_block_id(time.time(), self._config.grid_minutes)
            resumed = await self._store.get_paused_in_block(block_id)
            if resumed is not None:
                if resumed.type is ActivityType.READING:
                    source = resumed.progress.get("source")
                    if isinstance(source, str):
                        material = await self._material_store.get_by_path(source)
                        if material is not None:
                            resumed.progress["read_chars"] = material.read_chars
                            resumed.progress["total_chars"] = material.total_chars
                resumed.ended_at = None
                self._task = asyncio.create_task(self._execute(resumed))
                self._task.add_done_callback(_harvest_task_exception)
                return
            desires = await self._desire.get_pending()
            values = (await self._desire.get_all()).values
            ranked = rank_desires(desires, values)
            state = await self._get_state()
            activity = self.select_activity(ranked, state)
            if activity is None:
                activity = self._default_activity(state)
            if activity.type is ActivityType.READING:
                # 先按 goal.topic 选料（C2），命中读那本；否则最近未读完；
                # 再否则转自由探索（下方 else 分支）。绝不凭空编造。
                goal = cast(dict[str, Any] | None, activity.progress.get("goal"))
                topic = goal.get("topic") if goal is not None else None
                material = None
                if isinstance(topic, str) and topic:
                    material = await self._material_store.find_by_topic(topic)
                if material is None:
                    material = await self._material_store.next_readable()
                if material is not None:
                    # 读最近未读完的那本，从它的进度续读（绝不编造）
                    activity.progress["source"] = material.path
                    activity.progress["filename"] = material.filename
                    activity.progress["description"] = material.filename
                    activity.progress["read_chars"] = material.read_chars
                    activity.progress["total_chars"] = material.total_chars
                else:
                    # 无书可读：转自由探索（沿用限速）；限速中则退回默认活动，
                    # 任何情况都不让 LLM 凭空编造读书内容
                    last = await self._store.get_last_exploration()
                    if should_explore(
                        last,
                        self._exploration_config.rate_limit_hours,
                        time.time(),
                    ):
                        activity.type = ActivityType.FREE_EXPLORATION
                    else:
                        activity = self._default_activity(state)
            await self._store.insert(activity)
            self._task = asyncio.create_task(self._execute(activity))
            self._task.add_done_callback(_harvest_task_exception)

    async def _execute(self, activity: Activity) -> None:
        activity.status = ActivityStatus.RUNNING
        await self._store.update(activity)
        desire_id = activity.progress.get("desire_id")
        if isinstance(desire_id, str):
            await self._desire.mark_active(desire_id)  # 消费开始：PENDING → ACTIVE
        await self._bus.publish(
            internal_event(
                EventType.ACTIVITY_START,
                {
                    "activity_id": activity.id,
                    "type": activity.type.value,
                    "schedule_block_id": activity.schedule_block_id,
                },
                _correlation_id(activity),
            )
        )
        try:
            result = await self._run_activity(activity)
        except Exception:
            # fail-fast：失败态落库后仍上抛（不吞异常），但活动不卡 RUNNING
            activity.status = ActivityStatus.INCOMPLETE
            activity.ended_at = time.time()
            await self._store.update(activity)
            desire_id = activity.progress.get("desire_id")
            if isinstance(desire_id, str):
                # 异常退出：ACTIVE → SUPPRESSED
                await self._desire.mark_suppressed(desire_id)
            _logger.exception(
                "活动执行失败 activity_id=%s type=%s",
                activity.id,
                activity.type.value,
            )
            raise
        activity.progress["result"] = result
        await self.complete_activity(activity)

    async def _run_activity(self, activity: Activity) -> dict[str, Any]:
        t = activity.type
        if t is ActivityType.READING:
            source = activity.progress.get("source")
            if source is None:
                # READING 必须有真实读物；缺 source 说明上游决策出错，fail-fast
                raise ValueError("读书活动缺 source：已禁止凭空编造")
            return await self._run_reading_source(activity, str(source))
        if t is ActivityType.CREATION:
            style = _pick_creation_style()
            knowledge = await self._memory.list_memories(tag="knowledge")
            obs = await self._get_observation()
            state = await self._get_state()
            context = _build_creation_context(activity, style, knowledge, obs)
            system = _build_creation_system(self._canon, state)
            result = await self._run_llm_activity(
                activity, "creation", extra_context=context,
                context_label="创作参考", system=system,
            )
            title = str(result["title"])
            path = f"creations/{_sanitize_filename(title)}.md"
            written = await file_io("write", path, str(result["content"]))
            result["path"] = written["path"]
            return result
        if t is ActivityType.IDLE_REFLECTION:
            summary = await self._reflect(_correlation_id(activity))
            return {"summary": summary}
        if t is ActivityType.FREE_EXPLORATION:
            return await self._exploration.run(
                seed=str(activity.progress.get("description") or activity.id),
                activity_id=activity.id,
                correlation_id=_correlation_id(activity),
            )
        if t is ActivityType.OBSERVE_USER:
            obs = await self._get_observation()
            presence = obs.get("presence", "")
            window_title = obs.get("window_title", "")
            screen_summary = obs.get("screen_summary", "")
            return {
                "presence": presence,
                "window_title": window_title,
                "screen_summary": screen_summary,
                "summary": build_observation_summary(
                    presence, window_title, screen_summary
                ),
            }
        if t is ActivityType.REST:
            return {}
        raise ValueError(f"未知活动类型 {t!r}")

    async def _run_reading_source(
        self, activity: Activity, source: str
    ) -> dict[str, Any]:
        """分块读真实文件：切 [read_chars, read_chars+6000) 一块喂 LLM 产 {book, note}，
        推进书库进度并把 note 追加进片段；读到最后一块时聚合全部片段 → 完整笔记
        落盘 + completed=True（一本=一次单位）。绝不凭空编造。"""
        content = await asyncio.to_thread(
            Path(source).read_text, encoding="utf-8", errors="replace"
        )
        read_chars = int(activity.progress.get("read_chars", 0))
        chunk = content[read_chars : read_chars + _READ_CONTEXT_CHARS]
        filename = str(activity.progress.get("filename") or Path(source).name)
        if chunk == "":
            # 已读到末尾（或文件比注册时短）：无新块可读，聚合已有片段（不编造）
            full = await self._finalize_reading(
                activity, source, filename, len(content)
            )
            await self._extract_knowledge(activity, filename, content)
            return full
        # 滚动摘要接力：把「上次读到哪里 + 已读片段笔记」一起喂给 LLM，让本次 note
        # 自然承接已读部分、只续写本块新内容（避免几篇之间不连贯）。
        prior = await self._material_store.get_fragments(source)
        prior_block = ""
        if prior:
            prior_block = (
                f"上次已读到第 {read_chars} 字，此前片段笔记：\n"
                + "\n---\n".join(prior)
                + "\n"
            )
        context = (
            f"书名：{filename}\n"
            f"{prior_block}"
            f"本次新读（第 {read_chars}～{read_chars + len(chunk)} 字）：\n{chunk}"
        )
        result = await self._run_llm_activity(
            activity, "reading", extra_context=context
        )
        new_read_chars = read_chars + len(chunk)
        await self._material_store.append_fragment(
            source, str(result.get("note", "")), time.time()
        )
        await self._material_store.advance(source, new_read_chars, time.time())
        result["read_chars"] = new_read_chars
        result["total_chars"] = len(content)
        if new_read_chars >= len(content):
            # 读到最后一块：聚合全部片段（含本块）→ 完整笔记落盘 + completed
            full = await self._finalize_reading(
                activity, source, filename, len(content)
            )
            await self._extract_knowledge(activity, filename, content)
            full["read_chars"] = new_read_chars
            full["total_chars"] = len(content)
            return full
        return result

    async def _finalize_reading(
        self, activity: Activity, source: str, filename: str, content_len: int
    ) -> dict[str, Any]:
        """读完整本书：聚合 note_fragments → 完整笔记落盘 workspace/notes/<safe>.md。"""
        fragments = await self._material_store.get_fragments(source)
        full_note = await self._aggregate_note(activity, filename, fragments)
        note_path = (
            f"notes/{_sanitize_filename(filename)}"
            f"-{_path_hash_suffix(source)}.md"
        )
        written = await file_io("write", note_path, full_note)
        # 同路径重读（read_material 重传会重置进度）原地更新保留批注，不累积重复
        # 笔记；不同路径同名书互不误删（path 是去重键，book 仅 filename 展示）。
        await self._reading_notes.upsert_by_path(
            ReadingNote(
                id=str(uuid.uuid4()),
                book=filename,
                content=full_note,
                created_at=time.time(),
                path=source,
            )
        )
        return {
            "book": filename,
            "note": full_note,
            "path": written["path"],
            "completed": True,
            "read_chars": content_len,
            "total_chars": content_len,
        }

    async def _aggregate_note(
        self, activity: Activity, filename: str, fragments: list[str]
    ) -> str:
        """把各块片段笔记聚合成一篇完整读书笔记（1 次 LLM，output_type=note）。"""
        joined = "\n---\n".join(fragments) if fragments else "（无片段）"
        output = await self._llm.complete(
            [
                {"role": "system", "content": _AGGREGATE_SYSTEM},
                {
                    "role": "user",
                    "content": f"书名：{filename}\n各片段笔记：\n{joined}",
                },
            ],
            module="activity",
            output_type="note",
            correlation_id=_correlation_id(activity),
            json_mode=True,
        )
        await self._evaluator.evaluate(output)
        data: Any = json.loads(output.content)
        if not isinstance(data, dict):
            raise ValueError(f"聚合笔记 JSON 应是对象，得到 {type(data).__name__}")
        note = cast(dict[str, Any], data).get("note")
        if not isinstance(note, str) or not note:
            raise ValueError("聚合笔记 JSON 缺 note 或非空字符串")
        return note

    async def _extract_knowledge(
        self, activity: Activity, filename: str, content: str
    ) -> None:
        """读完一本书后分块提取知识点入长期记忆（R1）。

        正文按 _READ_CONTEXT_CHARS 分块逐个喂 LLM（对齐阅读循环的字符预算，
        避免整本书超上下文被静默截断）；跨块累积去重，最多 _KNOWLEDGE_MAX_POINTS
        条、块数上限 _KNOWLEDGE_MAX_CHUNKS。best-effort：任一失败只记日志、
        不冒泡——读书笔记已落盘，知识点是增强旁路，主流程正确性不依赖它
        （CLAUDE.md 豁免：LLM/eval 失败吞异常）。
        """
        items: list[dict[str, str]] = []
        seen: set[str] = set()
        budget_chars = _KNOWLEDGE_MAX_CHUNKS * _READ_CONTEXT_CHARS
        for start in range(0, min(len(content), budget_chars), _READ_CONTEXT_CHARS):
            chunk = content[start : start + _READ_CONTEXT_CHARS]
            if not chunk.strip():
                continue
            for point in await self._extract_knowledge_points(
                activity, filename, chunk
            ):
                if len(items) >= _KNOWLEDGE_MAX_POINTS:
                    break
                content_pt = point.get("content", "")
                if content_pt and content_pt not in seen:
                    seen.add(content_pt)
                    items.append(point)
            if len(items) >= _KNOWLEDGE_MAX_POINTS:
                break
        if items:
            try:
                await self._memory.remember_knowledge(
                    items, _correlation_id(activity)
                )
            except Exception:
                _logger.exception("知识点入库失败 activity_id=%s", activity.id)

    async def _extract_knowledge_points(
        self, activity: Activity, filename: str, chunk: str
    ) -> list[dict[str, str]]:
        """对单个正文分块（≤_READ_CONTEXT_CHARS）提取知识点，返回 [{topic, content}]。

        best-effort：LLM/解析失败返回空列表不冒泡，调用方继续下一块。
        """
        try:
            output = await self._llm.complete(
                [
                    {"role": "system", "content": _KNOWLEDGE_SYSTEM},
                    {"role": "user", "content": f"书名：{filename}\n正文：\n{chunk}"},
                ],
                module="activity",
                output_type="knowledge",
                correlation_id=_correlation_id(activity),
                json_mode=True,
            )
            await self._evaluator.evaluate(output)
            data: Any = json.loads(output.content)
            if not isinstance(data, dict):
                return []
            raw_points = cast(dict[str, Any], data).get("points")
            if not isinstance(raw_points, list):
                return []
            items: list[dict[str, str]] = []
            for point in cast(list[Any], raw_points)[:_KNOWLEDGE_MAX_POINTS]:
                if not isinstance(point, dict):
                    continue
                point_map = cast(dict[str, Any], point)
                topic = point_map.get("topic")
                content_pt = point_map.get("content")
                if isinstance(content_pt, str) and content_pt.strip():
                    items.append(
                        {
                            "topic": topic if isinstance(topic, str) else "",
                            "content": content_pt.strip(),
                        }
                    )
            return items
        except Exception:
            _logger.exception("知识点提取失败 activity_id=%s", activity.id)
            return []

    async def _run_llm_activity(
        self,
        activity: Activity,
        output_type: str,
        extra_context: str | None = None,
        context_label: str = "读物信息",
        system: str | None = None,
    ) -> dict[str, Any]:
        user_msg = f"活动类型：{activity.type.value}"
        if extra_context:
            user_msg += f"\n{context_label}：\n{extra_context}"
        output = await self._llm.complete(
            [
                {"role": "system", "content": system or _ACTIVITY_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            module="activity",
            output_type=output_type,
            correlation_id=_correlation_id(activity),
            json_mode=True,
        )
        await self._evaluator.evaluate(output)
        return _parse_activity_result(output.content, output_type)


_ACTIVITY_SYSTEM = (
    "你是尼克斯。按 JSON 输出活动结果，键随活动类型："
    "读书 {book, note}、创作 {title, content}。"
    "读书时：note 自然承接已读片段，不重复概括已读部分、只续写本次新读内容；"
    "note 正文里不要写「上次读到第 X 字」这类位置字样。"
    "创作时：遵循给定风格，可引用知识库参考，但绝不编造不存在的知识；"
    "当前屏幕灵感只作启发，勿照搬。"
)


_KNOWLEDGE_SYSTEM = (
    "你是尼克斯，正在阅读一本书。从下面的文本中提取 1-5 个客观、可复用的知识点"
    "（事实、概念、方法）。只输出 JSON，键：points（数组，每项 {topic, content}，"
    "topic 是主题/概念名、content 是一句完整自洽的知识陈述）。"
    "没有值得提取的知识就输出 {\"points\": []}。"
)


_AGGREGATE_SYSTEM = (
    "你是尼克斯。把读书各片段笔记聚合成一篇完整读书笔记，"
    "只输出 JSON，键：note（完整笔记，非空字符串）。"
)
```

### `activity/exploration.py`（完整）

```python
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# langgraph 类型标注松散：add_node/compile/ainvoke 返回部分未知、graph.state 缺 stub
import json
from collections.abc import Hashable
from typing import Any, TypedDict, cast
from urllib.parse import urlparse

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from nyx.config import ExplorationConfig
from nyx.enums import EventType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import SECONDS_PER_HOUR, internal_event
from nyx.llm.client import LlmClient
from nyx.tools.registry import ToolRegistry

_MAX_STEPS = 8                    # 探索链最大步数（可推翻）


class ExplorationNode(TypedDict):
    name: str
    url: str
    kind: str  # "search"（搜索动作，url 空）| "web"（访问的网页）


class ExplorationState(TypedDict):
    seed: str
    focus: str
    findings: list[str]
    notes: list[str]
    nodes: list[ExplorationNode]
    step: int
    done: bool
    activity_id: str
    correlation_id: str


def should_explore(last_explored_at: float, rate_limit_hours: int, now: float) -> bool:
    """自由探索升级门槛（纯函数）：仅频率上限。

    「探索欲」条件由调用方结构保证：READING 活动仅由 DesireType.EXPLORATION 映射而来，
    故调用方在 activity.type is READING 时才调本函数。
    精力不再单独卡：探索消耗 -30，精力不足由 build_schedule 的 REST 穿插兜底。
    """
    return now - last_explored_at >= rate_limit_hours * SECONDS_PER_HOUR


class Exploration:
    """跨域行为链（LangGraph）：好奇 → 搜索 → 读 → 写笔记（design §8.6）。"""

    def __init__(
        self,
        llm: LlmClient,
        evaluator: Evaluator,
        tools: ToolRegistry,
        bus: EventBus,
        exploration_config: ExplorationConfig,
    ) -> None:
        self._llm = llm
        self._evaluator = evaluator
        self._tools = tools
        self._bus = bus
        self._web_enabled = exploration_config.web_enabled
        # 联网为主通道：web 开启时 search_web 是主搜索动作，
        # search_local 作兜底（进 _search_web 内）
        if self._web_enabled:
            self._actions = ["search_web", "read", "write_note"]
        else:
            self._actions = ["search_local", "read", "write_note"]
        self._graph = self._build_graph()

    def _build_graph(self) -> CompiledStateGraph[ExplorationState]:
        g = StateGraph(ExplorationState)
        g.add_node("plan_next", self._plan_next)
        g.add_node("search_local", self._search_local)
        g.add_node("read", self._read)
        g.add_node("write_note", self._write_note)
        g.add_node("finalize", self._finalize)
        if self._web_enabled:
            g.add_node("search_web", self._search_web)
        g.add_edge(START, "plan_next")
        path_map: dict[Hashable, str] = {}
        for a in self._actions:
            path_map[a] = a
        path_map["finalize"] = "finalize"
        g.add_conditional_edges("plan_next", self._route, path_map)
        for a in self._actions:
            g.add_edge(a, "plan_next")
        g.add_edge("finalize", END)
        return g.compile()

    async def run(
        self, seed: str, activity_id: str, correlation_id: str
    ) -> dict[str, Any]:
        initial: ExplorationState = {
            "seed": seed, "focus": seed, "findings": [], "notes": [],
            "nodes": [], "step": 0, "done": False,
            "activity_id": activity_id, "correlation_id": correlation_id,
        }
        result = await self._graph.ainvoke(initial)
        return {
            "findings": result["findings"],
            "notes": result["notes"],
            "nodes": result["nodes"],
        }

    async def _plan_next(self, state: ExplorationState) -> ExplorationState:
        # MVP：LLM 判定 focus + done（json_mode）；step 达上限强制 done
        if state["step"] >= _MAX_STEPS:
            state["done"] = True
            return state
        output = await self._llm.complete(
            [
                {"role": "system", "content": _EXPLORATION_PLAN_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"focus={state['focus']} findings={state['findings']} "
                        f"notes={state['notes']}"
                    ),
                },
            ],
            module="activity",
            output_type="exploration_plan",
            correlation_id=state["correlation_id"],
            json_mode=True,
        )
        await self._evaluator.evaluate(output)
        plan = json.loads(output.content)
        if not isinstance(plan, dict):
            raise ValueError(f"探索规划 JSON 应是对象，得到 {type(plan).__name__}")
        plan = cast(dict[str, Any], plan)
        state["focus"] = plan.get("focus", state["focus"])
        state["done"] = bool(plan.get("done", False))
        state["step"] = state["step"] + 1
        return state

    async def _search_local(self, state: ExplorationState) -> ExplorationState:
        await self._record_node(
            state, {"name": f"搜索：{state['focus']}", "url": "", "kind": "search"}
        )
        res = await self._tools.call("local_search", {"query": state["focus"]})
        state["findings"].extend(str(r) for r in res)
        return state

    async def _search_web(self, state: ExplorationState) -> ExplorationState:
        focus = state["focus"]
        await self._record_node(
            state, {"name": f"搜索：{focus}", "url": "", "kind": "search"}
        )
        res = await self._tools.call("web_search", {"query": focus})
        if not res:
            # 联网失败/无结果 → 本地兜底（web_enabled 时 search_local 不再独立轮转）
            res = await self._tools.call("local_search", {"query": focus})
        state["findings"].extend(str(r) for r in res)
        if res:
            first = res[0]
            if isinstance(first, dict) and first.get("url"):
                first = cast(dict[str, Any], first)
                url = first["url"]
                name = first.get("title") or _domain(url)
                await self._record_node(
                    state, {"name": name, "url": url, "kind": "web"}
                )
                # 顺手下第一条正文入书库；失败静默不崩探索
                try:
                    fetched = await self._tools.call("web_fetch", {"url": url})
                    state["findings"].append(f"已下载资料：{fetched}")
                except Exception:
                    pass
        return state

    async def _read(self, state: ExplorationState) -> ExplorationState:
        res = await self._tools.call(
            "file_io", {"action": "read", "path": state["focus"]}
        )
        state["findings"].append(str(res))
        return state

    async def _write_note(self, state: ExplorationState) -> ExplorationState:
        note = "\n".join(state["findings"][-3:])
        await self._tools.call(
            "file_io",
            {"action": "write", "path": "exploration_note.md", "content": note},
        )
        state["notes"].append(note)
        return state

    async def _finalize(self, state: ExplorationState) -> ExplorationState:
        return state

    async def _record_node(
        self, state: ExplorationState, node: ExplorationNode
    ) -> None:
        """记一条探索节点并广播 EXPLORATION_STEP（前端地图实时点亮）。"""
        state["nodes"].append(node)
        await self._bus.publish(internal_event(
            EventType.EXPLORATION_STEP,
            {"activity_id": state["activity_id"], "node": dict(node)},
            state["correlation_id"],
        ))

    def _route(self, state: ExplorationState) -> str:
        if state["done"]:
            return "finalize"
        # MVP：确定性轮转（与 self._actions 对齐，3 步一轮），不靠 LLM 选具体动作。
        # web 开启时 actions[0]=search_web 起始；web 关闭时 =search_local 起始。
        # step 在 _plan_next 里先 +1，故 -1 对齐到 actions[0]。
        return self._actions[(state["step"] - 1) % len(self._actions)]


_EXPLORATION_PLAN_SYSTEM = (
    "你是尼克斯的探索规划器。按 JSON 输出 {focus, done}，"
    "决定下一步聚焦对象与是否结束。"
)


def _domain(url: str) -> str:
    """网页节点名兜底：title 缺失时用域名。"""
    host = urlparse(url).hostname
    return host or url
```

### `activity/observe.py`（完整）

```python
def classify_presence(
    keyboard_active: bool, mouse_active: bool, window_title: str
) -> str:
    """观察用户：键盘/鼠标活跃度 + 前台窗口标题 → 在线/离开/忙碌
    （纯函数，design §8.5）。

    MVP 简化规则：键盘或鼠标活跃 → "online"；否则窗口标题非空 → "busy"；
    否则 "away"。运行时调用方是前端 ingress（Tauri 壳采集后判定的单一事实来源）；
    本 spec 保留为可展示/可测的规则定义。
    """
    if keyboard_active or mouse_active:
        return "online"
    if window_title:
        return "busy"
    return "away"


def build_observation_summary(
    presence: str, window_title: str, screen_summary: str
) -> str:
    """观察摘要（纯函数，design §8.5 屏幕视觉扩展）：窗口标题优先，
    视觉摘要次之逐段拼接；两者皆空则仅回 presence。"""
    if window_title:
        base = f"用户（{presence}）正在浏览 {window_title}"
    else:
        base = f"用户（{presence}）"
    if screen_summary:
        base += f"，屏幕：{screen_summary}"
    return base
```

### `activity/screen.py`（完整）

```python
import asyncio
import logging
from collections.abc import Awaitable, Callable
from io import BytesIO

from PIL import ImageGrab

_logger = logging.getLogger(__name__)


def capture_screen() -> bytes:
    """抓全屏 → PNG bytes。纯 I/O 薄封装；失败上抛由调用方 best-effort 处理。"""
    img = ImageGrab.grab()
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class ScreenObserver:
    """周期截屏 → 视觉描述 → 回调摘要。

    best-effort：单次采样失败记日志返 None，循环不中断（design §8.5 手动开启，
    主流程正确性不依赖其产出）。capture/describe 可注入（测试不碰真桌面/真模型）。
    """

    def __init__(
        self,
        capture: Callable[[], bytes],
        describe: Callable[[bytes], Awaitable[str]],
        interval_seconds: int,
    ) -> None:
        self._capture = capture
        self._describe = describe
        self._interval_seconds = interval_seconds

    async def sample_once(self) -> str | None:
        """一次采样：抓屏（to_thread）→ describe → 摘要；失败记日志返 None。"""
        try:
            image = await asyncio.to_thread(self._capture)
            return await self._describe(image)
        except Exception:
            _logger.exception("屏幕视觉采样失败")
            return None

    async def run(self, on_summary: Callable[[str], None]) -> None:
        """周期采样循环（永不抛；仅 CancelledError 上抛供取消）。"""
        while True:
            summary = await self.sample_once()
            if summary:
                on_summary(summary)
            await asyncio.sleep(self._interval_seconds)
```

## 测试要点

- [ ] 单元测试 `tests/test_activity/`（`pytest-asyncio`；`db = await connect(":memory:")`；fake `LlmClient.complete` 按 `output_type` 返回 fixture JSON；`EventBus` 真实例 + recording handler，`run()` 作 task；`get_state` 用 fake 回调返回预设 `CurrentState`——同 05/09/11/12 模式）：
  - [ ] **store**（`test_activity_store.py`）：`insert + get` 往返（`progress` JSON 往返、枚举 `.value` 往返）；`get_current` 只取 running 最新一条；`get_paused_in_block`（当前块最新 PAUSED、忽略其他块；无则 None）；`get_last_exploration`（无 free_exploration 记录 → `0.0`，有 → `MAX(started_at)`）；`list_schedule(start)` 按 `started_at >= start` 过滤 + ASC；`list_results` 只回 completed + 读书/探索/创作三类按 `ended_at DESC`；`update` 改 `status`/`progress`/`ended_at` → `get` 验证
  - [ ] **material_store**（`test_material_store.py`）：`get_by_path`（upsert+advance 后取到最新 `read_chars`；缺路径 → None）
  - [ ] **reading_note_store**（`test_reading_note_store.py`）：`upsert_by_path`（同 path 二次 upsert → 1 条、content 更新、id 不变；不同 path 同名 → 2 条互不删、`list_notes` 按 `created_at` 倒序）；`list_notes` 数批注（加两条批注 → `annotation_count=2`）；`delete` 级联（删笔记 → 笔记与批注都空）；`list_annotations` 升序；`delete_annotation`（删一条留其余）
  - [ ] **纯函数**（`test_activity_facade.py`）：`_day_start`（`now=86400*1.5 → 86400.0`）；`_schedule_block_id`（同网格块内多个 now 返回同标签、跨块返回不同标签、跨小时边界正确进位）；`_goal_met`（goal None → None；goal 非 None + result 空 → False；goal 非 None + result 非空 → True）；`_pick_creation_style`（返回 6 风格之一）；`_build_creation_context`（有风格/主题/知识/屏幕各段；无知识无屏幕 → 省略对应段）
  - [ ] **select_activity**（fake `get_state` 返回 `energy=80`）：无欲望 → `None`；`[探索欲]` → `type is READING`、`progress["desire_id"] == desire.id`、`goal` 序列化正确、`progress["description"] == desire.description`；`[互动欲]` → `None`（不占日程块）；`[休息欲]` → `type is REST`、`progress["desire_id"] == rest_desire.id`（欲望驱动的 REST 保留关联）；`energy=30` + 探索欲 → `type is REST`、`progress["desire_id"] is None`（精力恢复无关联）
  - [ ] **should_explore**（`test_exploration.py`）：`last=1000` + `now-last < 1h*3600` → False；`last=0.0` + 频率过 → True
  - [ ] **facade 生命周期**：
    - [ ] `_maybe_start_activity`：有 running 活动 → 不新起；无欲望 → 产 `_default_activity`（见空槽默认 bullet）并 insert；有欲望 → insert + 发布 `activity_start` + `activity_end`（`content["type"]`/`desire_id`/`goal_met`/`energy_delta`/`result` 正确、`source is INTERNAL`）；READING/CREATION 时 `evaluator.evaluate` 被调 1 次（收到该 `LLMOutput`）
    - [ ] 升级路径：探索欲 + 频率过 → `activity.type is FREE_EXPLORATION`；频率未过 → 降级 `READING`
    - [ ] 空槽默认：无欲望 + `energy=30` → `type is IDLE_REFLECTION`、`progress["desire_id"] is None`；无欲望 + `energy=80` → `type is OBSERVE_USER`、`progress["desire_id"] is None`
    - [ ] `complete_activity`：`status is COMPLETED`、`ended_at` 非 None、发布 `activity_end`（`energy_delta == config.energy_delta.reading` 等）
    - [ ] `interrupt`：RUNNING 活动 → cancel + 可续活动 `status is PAUSED`、非可续 `status is ABANDONED` + 发布 `activity_interrupted`（`content["by"]` 正确）；`activity_id` 不存在 → 不 cancel、不发布；执行中活动挂起在可取消 await 上时 interrupt → 终态 `PAUSED`/`ABANDONED` 而非被 complete 覆盖
    - [ ] 恢复：`_maybe_start_activity` 命中当前块 PAUSED 创作 → 复用同一 id 重跑（id 不变、COMPLETED、evaluator 再调 1 次）；命中 PAUSED 读书 → 从 material 层刷新 `read_chars` 续读；不同块旧 PAUSED → 不恢复、走新建默认活动
    - [ ] `start_exploration(topic | None)`：手动触发 FREE_EXPLORATION（返回 `activity_id`、落一条 `FREE_EXPLORATION`、`progress["description"]` 记录 topic）；`topic is None` → 调 `Exploration.pick_topic(activity.id)` 覆盖 description；已有活动在跑 → `RuntimeError`
    - [ ] `get_current` / `get_schedule` / `get_results` 委托 store
    - [ ] **读书知识点提取（R1）**：mock LLM 返回 `{"points":[{"topic","content"}...]}` → `_memory.remember_knowledge` 收到同批 items（tag 由 memory 层写 "knowledge"）；mock LLM 抛异常 → 不冒泡、读书活动仍 COMPLETED（best-effort）；`points` 非 list / 超 5 条截断到 5
    - [ ] **分块提取**：长正文（> `_READ_CONTEXT_CHARS`）切成多块逐个喂 LLM，每块 `正文` ≤ 6000 字、块数 ≤ `_KNOWLEDGE_MAX_CHUNKS`；跨块重复知识点按 content 去重，总量 ≤ `_KNOWLEDGE_MAX_POINTS`
    - [ ] **`chunk==""` 完成分支**：读到末尾（文件比注册时短）时既聚合笔记也调 `_extract_knowledge`（此前漏调）
    - [ ] **重读同书去重**：`_finalize_reading` 二次调用同 path → `upsert_by_path` 原地更新（保留 note id → 批注仍挂原 id 下），`list_reading_notes()` 只剩一条；不同 path 同名书 → 两条互不删
    - [ ] **创作上下文（W1/W2/W3）**：创作活动执行时 `list_memories(tag="knowledge")` 被调、`_get_observation` 被调、`_run_llm_activity` 收到 `context_label="创作参考"` 且 `extra_context` 含风格/知识/屏幕（`_FakeMemory`/`_FakeObservation` 桩）
    - [ ] **读书笔记 CRUD 委托**：`list_reading_notes`/`delete_reading_note`/`list_annotations`/`add_annotation`（author=="user"）/`delete_annotation` 委托 `ReadingNoteStore`；`_finalize_reading` 落一条 `ReadingNote`（book=filename、content=full_note、path=source）
  - [ ] **exploration**（`test_exploration.py`）：`Exploration` 用 fake llm/fake_evaluator/tools/bus，`web_enabled=false` 时图不含 `search_web`、`run(seed, activity_id, correlation_id)` 返回 `{findings, notes, nodes}` 且步数 ≤ `_MAX_STEPS`；`_plan_next` 的 `llm.complete` 收到 `correlation_id == 初始 correlation_id`，且每次 `complete` 后 `evaluator.evaluate` 被调（`output_type="exploration_plan"`）；规划 JSON 非对象（如数组）→ `ValueError`；`_search_web` 搜到结果记 search+web 两节点并下第一条正文、`web_search` 空则兜底 `local_search`（str 结果不崩）、每节点发布一条 `EXPLORATION_STEP`（content `{activity_id, node}`）；`pick_topic(correlation_id)`（`output_type="exploration_topic"`）返回 JSON `{topic}` 的 topic、无 `topic` 键 → 兜底「有趣的新鲜事」、JSON 非对象 → `ValueError`
  - [ ] **observe**（`test_observe.py`）：`classify_presence` 三态判定（活跃→online、窗口标题→busy、无→away）；`build_observation_summary` 四态拼接（有窗口无屏幕 / 无窗口无屏幕 / 窗口+屏幕 / 无窗口有屏幕）
  - [ ] **screen**（`test_screen.py`）：`ScreenObserver.sample_once` 抓屏+describe 各 1 次返描述文本；capture 抛异常 → 返 `None` 不崩；describe 抛异常 → 返 `None` 不崩（best-effort）
- [ ] 集成测试：无（LLM 全 mock、DB 用 `:memory:`；与 desire/inner_life 的真实编排归 18-api）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] ripple 同步：tech-ref §7 `activity/` 补 `store.py` + `reading_note_store.py`；§5 `ActivityFacade` 构造参数补 `memory` / `reading_notes`、方法补 5 个读书笔记 CRUD；§6.2 `ExplorationState` 补 `correlation_id` 字段；§5 `select_activity` 返回类型 `Activity` → `Activity | None` 且 `async def` → `def`（纯决策，与 `_default_activity` 一致）；`activity_end` content 契约（`desire_id`/`goal_met`/`energy_delta`）与 11 §49 + 12 §45 一致
- [ ] ripple 同步：`interrupt` 语义「可续置 PAUSED、其余 ABANDONED」与 design §3.3 抢占语义一致；`ActivityStore.get_paused_in_block` / `MaterialStore.get_by_path` 补进 tech-ref §7；`_maybe_start_activity` 恢复路径与 17-expression 打断入口约定（搭话/回复打断活动调 `interrupt`）一致
- [ ] 下游约定：17-expression 搭话/回复打断活动时调 `interrupt(activity_id, by_event)`；18-api 组合根注入 `get_state=inner_life.get_state`、`evaluator`（给 `ActivityFacade` 与 `Exploration` 的 LLM 产出评分）、订阅 `SCHEDULE_BLOCK_START`（on_tick）与 `DESIRE_GENERATED`（on_desire_generated）
- [ ] ripple 同步（屏幕视觉）：tech-ref §7 `activity/` 补 `screen.py`、§8 补 `vision:` 段；`OBSERVE_USER` 观察 result 契约由 `{presence, window_title, summary}` 扩展为 `{presence, window_title, screen_summary, summary}`（`result.summary` 仍由 `build_observation_summary` 拼装，09 `remember_activity` 消费不变）
