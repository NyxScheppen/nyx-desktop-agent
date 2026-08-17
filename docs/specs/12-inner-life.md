# 内在生命（inner-life）：情感 + 精力 + 反思 + 自我叙事

> 范围：`inner_life/store.py`（`InnerLifeStore` 四张单行表 CRUD）、`inner_life/emotion.py`（valence/arousal/8 档标签纯函数）、`inner_life/reflection.py`（`Reflection` 反思协调器）、`inner_life/facade.py`（`InnerLifeFacade`）。
> 整块不拆一个 spec 写完（避免 facade↔reflection 成环）：`facade.py → reflection.py` 单向，reflection 不反 import facade；两者都依赖 `store.py`（叶子）。
> **本文件自包含**：四个文件完整代码内联在下文。

## 元信息

- **前置依赖**：01-types（`CurrentState` / `SelfNarrative` / `Personality` / `Values` / `Event` / `EventType` / `Source` / `EnergyState` / `EmotionCategory` / `ActivityType` / `LongTermDesire`）、02-config（`Config` / `DesireConfig.long_term_capacity`）、03-llm（`LlmClient.complete`）、04-db（`Database` + `personality` / `value_system` / `energy` / `self_narrative` 四表）、05-event（`EventBus.publish`）、09-memory-facade（`MemoryFacade.list_memories`）、11-desire（`DesireFacade.get_pending` / `get_all` / `add_long_term`）、**13/14-activity（`ActivityFacade.get_current`，向前引用——本 spec 只依赖 tech-ref §5 的签名；实现时 `from nyx.activity.facade import ActivityFacade` 是硬 import，需 14 先落地或建最小 stub，否则 pyright/pytest 挂在 import 上）、15-eval（`Evaluator`）**
- **本 spec 带来的连锁改动（ripple，本 spec 完成后同步）**：11-desire 的 `DesireFacade` 加 `add_long_term`；tech-ref §5 `DesireFacade` 补 `add_long_term` 签名；tech-ref §7 补 `inner_life/store.py`。
- **旧设计残留（已与用户确认删除）**：CLAUDE.md 测试原则点名的 `VADCalibrator` / `AffinityMatrix` 是旧设计残留，本 spec **不实现**，只实现 `vad_to_category`（valence/arousal → 8 档标签）。设计文档为准，CLAUDE.md 这两名字已清理。

## 用户故事

> 作为 Nyx 系统的开发者，我想要 `InnerLifeFacade` 把内在生命统一成一个门面——`apply_event` 按事件更新情感（衰减回基线 + 偏移）与精力、`reflect` 做慢变量反思（内部调 MemoryFacade/DesireFacade）、`get_state` 组装只读快照、`get_narrative` 读自我叙事——以便表达拼 prompt 用快照、前端面板看 valence/arousal/Big Five/三观/精力、反思是性格/三观/长期欲望/自我叙事的唯一演化入口。

## 验收标准

- [ ] `store.py` 含 `InnerLifeStore`（`get_personality` / `upsert_personality` / `get_values` / `upsert_values` / `get_energy` / `upsert_energy` / `get_narrative` / `upsert_narrative`），与「`inner_life/store.py`（完整）」段代码逐字一致
- [ ] `emotion.py` 含 `clamp_valence` / `clamp_arousal` / `decay_emotion` / `apply_offset` / `event_offset` / `vad_to_category` / `resolve_emotion` + 常量，与「`inner_life/emotion.py`（完整）」段代码逐字一致
- [ ] `reflection.py` 含 `Reflection` + `drift_personality` / `drift_values` / `_drift_dim` / `_build_reflection_prompt` / `_parse_reflection` / `_validate_candidate` / `_to_long_term`，与「`inner_life/reflection.py`（完整）」段代码逐字一致
- [ ] `facade.py` 含 `InnerLifeFacade`（`apply_event` / `reflect` / `get_state` / `get_narrative`）+ `energy_to_state`，四个公开方法签名与 tech-ref §5 逐字一致
- [ ] `vad_to_category` 只落 6 档（neutral/happy/sad/angry/worried/shy），`resolve_emotion` 补 sleepy/thinking 两档覆盖；优先级 **困倦 > 思考 > 情绪**
- [ ] `apply_event`：情感衰减（回基线 0,0）+ 事件偏移（`event_offset` 纯函数）；`ACTIVITY_END` 额外按 `energy_delta` 更新精力（含闲置恢复 + clamp + 重算档位）；`REFLECTION` 额外调 `reflect()`；每次情感变化发布 `EMOTION_UPDATE`（content 含 `valence`/`arousal`/`emotion`）
- [ ] `reflect()`：读近期记忆 + 当前性格/三观/叙事/长期欲望 → **1 次 LLM**（`module="inner_life"`、`output_type="reflection"`、`json_mode=True`、`correlation_id` 透传自触发事件）→ 规则回写（性格/三观漂移 clamp 到 `[1,10]`、单维漂移 ≤ `_MAX_DRIFT`；叙事 story/becoming 追加、self_view 合并；长期欲望候选在 `long_term_capacity` 内逐个 `add_long_term`）
- [ ] `get_state()`：组装 `CurrentState`（情感内存 + 性格/三观/精力 store + `current_activity`（`ActivityFacade.get_current()`）+ `active_desires`（`DesireFacade.get_pending()`））；单行表未 seed → `RuntimeError`（fail-fast）
- [ ] 情感在内存不持久化（design §4.5）；性格/三观/精力/自我叙事走 store；无 `VADCalibrator` / `AffinityMatrix`
- [ ] 事件发布遵守「Facade 自己 publish、绝不返回 Event」；事件 `source=INTERNAL`
- [ ] `reflect()` 的 LLM 产出（`output_type="reflection"`）后紧跟 `await evaluator.evaluate(output)`（15-eval 原则 4）
- [ ] `pyright` strict 零报错

## 技术方案

- **新文件**：`nyx/inner_life/store.py`、`nyx/inner_life/emotion.py`、`nyx/inner_life/reflection.py`、`nyx/inner_life/facade.py`（无 API、无数据变更——表结构是 04-db 的活）
- **库**：无新库（标准库 `json` / `time` / `uuid` / `typing`；`aiosqlite` 已由 04-db 引入）
- **公开面**：`from nyx.inner_life.store import InnerLifeStore`；`from nyx.inner_life.facade import InnerLifeFacade`；`from nyx.inner_life.emotion import (vad_to_category, resolve_emotion, decay_emotion, ...)`（不加 `__all__`）
- **三层**：`InnerLifeFacade`（Facade）→ `Reflection`（内部类，反思编排）→ `InnerLifeStore`（子系统，单行表 CRUD）。`Reflection` 由 `facade` 内部构造（共享 store），**不 import facade**（避免成环）；`facade.py` / `reflection.py` 都依赖 `store.py`（叶子）
- **store 锁约定（同 07）**：每个方法一个 `async with self._db.lock` 的 SQL 块；store 方法之间不互相调用对方的持锁方法（`asyncio.Lock` 不可重入）
- **四张单行表**：都 `id='self'`（04-db 固定键）。`get_*` 返回 `X | None`（未 seed 时 None）；**所有读路径**遇 None 抛 `RuntimeError`（"未初始化，18-api 组合根必须先 seed"）——`get_state`/`get_narrative` 读、`_apply_energy`/`_publish_emotion` 读 energy 都 fail-fast，不静默兜底默认值（单行表缺失是配置错误，兜底反而掩盖错误）
- **情感不持久化（design §4.5）**：valence/arousal 在 `InnerLifeFacade` 内存字段（`self._valence` / `self._arousal` / `self._emotion_updated_at`），重启从基线（0,0）重启。没有 emotion 表（04-db 无此表）
- **情感衰减（回基线，决策可推翻）**：`decay_emotion(v, a, elapsed_days, rate) = (v×f, a×f)`，`f = max(0, 1 - rate×elapsed_days)`，基线 = (0,0)。`EMOTION_DECAY_RATE=0.5`（每天回基线 50%）。触发点 = `apply_event`（衰减在偏移前结算，同 09/11 的「读/写时结算」模式）；局限：两次 apply_event 之间情感不实时衰减（同 09 新鲜度、11 欲望值）
- **事件偏移 `event_offset` 纯函数**：`_OFFSETS` 表映射 4 个 inner_life 事件 → `(Δvalence, Δarousal)`；`OBSERVATION_STATE (0,0)`（观察不改，但触发衰减）、`DESIRE_SATISFIED (+0.2, +0.1)`（满足感）、`ACTIVITY_END (+0.1, -0.1)`（完成感+唤醒略降）、`REFLECTION (0, -0.1)`（反思平复）。数值是可推翻默认；`apply_offset` 施加后 clamp（valence `[-1,1]`、arousal `[0,1]`）
- **`vad_to_category` 6 档映射**：二维分区（阈值 `_V_NEAR=0.2` / `_A_LOW=0.3` / `_A_HIGH=0.6`）——低唤醒（`arousal<0.3`）：`valence>0.2`→shy、`<-0.2`→sad、否则 neutral；中高唤醒：`valence>0.2`→happy、`<-0.2`→（`arousal≥0.6`→angry 否则 worried）、否则 neutral。阈值是可推翻默认（分区语义按 01-types 各档注释）
- **`resolve_emotion` 8 档覆盖**：`energy_state ∈ {EXHAUSTED, DRAINED}` → sleepy（困倦最高优先级）；`current_activity ∈ {IDLE_REFLECTION, FREE_EXPLORATION}` → thinking（认知态）；否则 base。阈值（`_SLEEPY_STATES` / `_THINKING_ACTIVITIES`）是可推翻默认
- **精力模型**：`value ∈ [0,100]`、`energy_to_state` 五档映射（80/60/40/20 分界）。更新唯一入口 = `ACTIVITY_END` 的 `content.energy_delta`（14-activity 从 `config.activity.energy_delta` 取并填入，本 spec 只应用）。`_apply_energy` 顺序：闲置恢复（`_ENERGY_RECOVERY_PER_HOUR=5.0`/小时，按 `_energy_updated_at` 惰性结算）→ 加 `energy_delta` → clamp → 重算档位 → `upsert_energy`。**"夜间自动恢复"简化为恒定闲置恢复**（决策可推翻）；`_energy_updated_at` 在内存（重启后恢复从 0 计，可接受的局限）
- **`ACTIVITY_END` content 契约（14 引用）**：本 spec 消费两个键——`energy_delta`（`float`，精力变化，缺省 0）与 `desire_id`/`goal_met`（11-desire 已定义，本 spec 不读）。`desire_id`/`goal_met`/`energy_delta` 的完整形状由 14-activity 定义并保持与本 spec + 11 一致
- **反思 1 次 LLM（决策：已与用户确认）**：`_build_reflection_prompt` 拼近期记忆（`list_memories()[:20]` 摘要）+ 当前性格/三观/叙事 + 现有长期欲望；LLM 一次产出 `{story, becoming, self_view, personality_delta, values_delta, long_term_desires}`；`_parse_reflection` 校验结构（非法抛 `ValueError`，错误可溯源）；回写时漂移 clamp
- **性格/三观漂移（decision 可推翻）**：`drift_personality` / `drift_values` 纯函数，每维 `base + clamp(delta, -_MAX_DRIFT, +_MAX_DRIFT)` 再 clamp 到 `[1,10]`；`_MAX_DRIFT=0.5`（每轮单维最多 ±0.5，慢漂移）。Big Five/三观范围 1-10（01-types 注释）
- **自我叙事回写**：`story`/`becoming` 是追加（`[..., 新条目]`）、`self_view` 是合并（`{**旧, **新}`）、`updated_at=now`；`identity` 不变
- **长期欲望候选**：`_parse_reflection` 校验每个候选 `{type, name, description, subtopics}`；`_to_long_term` 构造（`strength=_LONG_TERM_INIT_STRENGTH=0.5`、`progress=0.0`）；逐个 `desire_facade.add_long_term`，超出 `config.desire.long_term_capacity` 则停（容量检查在反思侧，11 的 `add_long_term` 只插入）
- **`add_long_term` 归 11（ripple）**：`DesireFacade.add_long_term(desire: LongTermDesire) -> None` 直接委托 `store.insert_long_term`（无容量逻辑）。design §3.2「reflect 内部调 MemoryFacade/DesireFacade」→ 反思走 Facade 而非 DesireStore
- **`reflect(correlation_id: str | None = None)`（tech-ref §5 签名）**：`apply_event` 收到 `REFLECTION` 事件时内部调 `self.reflect(event.correlation_id)`，把触发事件的 correlation_id 串进反思 LLM（溯源链不断）；缺省（14-activity 发呆活动直接调用、测试）自生成 `uuid4`。`reflect()` 也是公开方法
- **`apply_event` 是统一事件入口**：`bus.subscribe(OBSERVATION_STATE/DESIRE_SATISFIED/ACTIVITY_END/REFLECTION, facade.apply_event)`（18-api 组合根绑定）。`apply_event` 对 4 类事件都做「衰减+偏移」，另按类型分派 `ACTIVITY_END→精力`、`REFLECTION→反思`
- **`EMOTION_UPDATE` 发布**：每次 `apply_event` 末尾发布（content `{valence, arousal, emotion}`，`emotion` 是 8 档 `.value` 字符串，经 `resolve_emotion` 求得），供前端 SSE；`correlation_id = 触发事件.correlation_id`
- **`get_state` 依赖注入（决策：已与用户确认）**：构造注入 `ActivityFacade` + `DesireFacade`，`get_state` 调 `get_current()` / `get_pending()` 组装快照。只读、无环——`ActivityFacade.select_activity(desires, state)` 以参数收 `CurrentState`、`DesireFacade` 不反向调 inner_life，故 inner_life → {activity, desire} 不构成环
- **inner_life 无配置段**：情感衰减/精力恢复等用模块级常量（可推翻）；`InnerLifeFacade` 构造收 `config: Config` 仅用于把 `config.desire` 传给 `Reflection`（长期欲望容量）

### `inner_life/emotion.py`（完整）

```python
from collections.abc import Mapping
from types import MappingProxyType

from nyx.enums import ActivityType, EmotionCategory, EnergyState, EventType

# —— 基线（平静）——
BASELINE_VALENCE = 0.0
BASELINE_AROUSAL = 0.0

# —— 情感衰减：每天回基线的比例（"随时间衰减回基线"）——
EMOTION_DECAY_RATE = 0.5

# —— 事件 → 情感坐标偏移 (Δvalence, Δarousal)，只读查找表（模块级不可变）——
_OFFSETS: Mapping[EventType, tuple[float, float]] = MappingProxyType({
    EventType.OBSERVATION_STATE: (0.0, 0.0),    # 观察不改情感（但仍触发衰减）
    EventType.DESIRE_SATISFIED: (0.2, 0.1),     # 满足感
    EventType.ACTIVITY_END: (0.1, -0.1),        # 完成感（唤醒略降）
    EventType.REFLECTION: (0.0, -0.1),          # 反思平复
})

# —— vad_to_category 阈值 ——
_V_NEAR = 0.2          # |valence| < 0.2 视为"中性带"
_A_LOW = 0.3           # arousal < 0.3 视为"低唤醒"
_A_HIGH = 0.6          # arousal >= 0.6 视为"高唤醒"

# —— 精力休息阈值：TIRED 档下界（energy_to_state 分界，单一来源）——
ENERGY_REST_THRESHOLD = 40.0   # 跌破即 EXHAUSTED/DRAINED（力竭/枯竭）

# —— 覆盖阈值 ——
_SLEEPY_STATES = (EnergyState.EXHAUSTED, EnergyState.DRAINED)
_THINKING_ACTIVITIES = (ActivityType.IDLE_REFLECTION, ActivityType.FREE_EXPLORATION)


def clamp_valence(v: float) -> float:
    """valence 夹到 [-1, 1]。纯函数。"""
    return max(-1.0, min(1.0, v))


def clamp_arousal(a: float) -> float:
    """arousal 夹到 [0, 1]。纯函数。"""
    return max(0.0, min(1.0, a))


def decay_emotion(
    valence: float, arousal: float, elapsed_days: float, rate: float
) -> tuple[float, float]:
    """情感线性衰减回基线 (0,0)：f = max(0, 1 - rate×elapsed)，两轴同乘 f。纯函数。"""
    f = max(0.0, 1.0 - rate * elapsed_days)
    return valence * f, arousal * f


def apply_offset(
    valence: float, arousal: float, d_valence: float, d_arousal: float
) -> tuple[float, float]:
    """施加情感偏移并 clamp。纯函数。"""
    return clamp_valence(valence + d_valence), clamp_arousal(arousal + d_arousal)


def event_offset(event_type: EventType) -> tuple[float, float]:
    """事件类型 → 情感偏移（未知事件 0 偏移）。纯函数。"""
    return _OFFSETS.get(event_type, (0.0, 0.0))


def vad_to_category(valence: float, arousal: float) -> EmotionCategory:
    """valence/arousal → 6 档情绪（不含 sleepy/thinking 覆盖）。纯函数。"""
    if arousal < _A_LOW:                          # 低唤醒
        if valence > _V_NEAR:
            return EmotionCategory.SHY            # valence+ 低唤醒 → 害羞
        if valence < -_V_NEAR:
            return EmotionCategory.SAD            # valence- 低唤醒 → 难过
        return EmotionCategory.NEUTRAL
    if valence > _V_NEAR:                          # 中高唤醒，valence+
        return EmotionCategory.HAPPY
    if valence < -_V_NEAR:                         # 中高唤醒，valence-
        return EmotionCategory.ANGRY if arousal >= _A_HIGH else EmotionCategory.WORRIED
    return EmotionCategory.NEUTRAL                 # 中性 valence，中高唤醒 → 平静


def resolve_emotion(
    base: EmotionCategory,
    energy_state: EnergyState,
    current_activity: ActivityType | None,
) -> EmotionCategory:
    """最终表情（8 档）：优先级 困倦 > 思考 > 情绪。纯函数。"""
    if energy_state in _SLEEPY_STATES:
        return EmotionCategory.SLEEPY
    if current_activity is not None and current_activity in _THINKING_ACTIVITIES:
        return EmotionCategory.THINKING
    return base
```

### `inner_life/store.py`（完整）

```python
import json

from nyx.db import Database
from nyx.enums import EnergyState
from nyx.types import Personality, SelfNarrative, Values

_PERSONALITY_COLS = (
    "openness, conscientiousness, extraversion, agreeableness, neuroticism"
)
_VALUES_COLS = "attitude_to_human, ai_identity_acceptance, altruism, optimism"


class InnerLifeStore:
    """personality / value_system / energy / self_narrative 四张单行表
    （id='self'）的 CRUD。

    db 由组合根注入（同所有 store 共享）。每个方法一个 `async with db.lock` 的 SQL 块。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_personality(self) -> Personality | None:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_PERSONALITY_COLS} FROM personality WHERE id = 'self'"
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "openness": row["openness"],
            "conscientiousness": row["conscientiousness"],
            "extraversion": row["extraversion"],
            "agreeableness": row["agreeableness"],
            "neuroticism": row["neuroticism"],
        }

    async def upsert_personality(self, p: Personality) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                "INSERT INTO personality (id, openness, conscientiousness, "
                "extraversion, "
                "agreeableness, neuroticism) VALUES ('self', ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET openness = excluded.openness, "
                "conscientiousness = excluded.conscientiousness, "
                "extraversion = excluded.extraversion, "
                "agreeableness = excluded.agreeableness, "
                "neuroticism = excluded.neuroticism",
                (p["openness"], p["conscientiousness"], p["extraversion"],
                 p["agreeableness"], p["neuroticism"]),
            )
            await self._db.conn.commit()

    async def get_values(self) -> Values | None:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_VALUES_COLS} FROM value_system WHERE id = 'self'"
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "attitude_to_human": row["attitude_to_human"],
            "ai_identity_acceptance": row["ai_identity_acceptance"],
            "altruism": row["altruism"],
            "optimism": row["optimism"],
        }

    async def upsert_values(self, v: Values) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                "INSERT INTO value_system (id, attitude_to_human, "
                "ai_identity_acceptance, "
                "altruism, optimism) VALUES ('self', ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET attitude_to_human = "
                "excluded.attitude_to_human, "
                "ai_identity_acceptance = excluded.ai_identity_acceptance, "
                "altruism = excluded.altruism, optimism = excluded.optimism",
                (
                    v["attitude_to_human"],
                    v["ai_identity_acceptance"],
                    v["altruism"],
                    v["optimism"],
                ),
            )
            await self._db.conn.commit()

    async def get_energy(self) -> tuple[float, EnergyState] | None:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT value, state FROM energy WHERE id = 'self'"
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return row["value"], EnergyState(row["state"])

    async def upsert_energy(self, value: float, state: EnergyState) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                "INSERT INTO energy (id, value, state) VALUES ('self', ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET value = excluded.value, "
                "state = excluded.state",
                (value, state.value),
            )
            await self._db.conn.commit()

    async def get_narrative(self) -> SelfNarrative | None:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT identity, story, self_view, becoming, updated_at "
                "FROM self_narrative WHERE id = 'self'"
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return SelfNarrative(
            identity=row["identity"],
            story=json.loads(row["story"]),
            self_view=json.loads(row["self_view"]),
            becoming=json.loads(row["becoming"]),
            updated_at=row["updated_at"],
        )

    async def upsert_narrative(self, n: SelfNarrative) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                "INSERT INTO self_narrative (id, identity, story, self_view, "
                "becoming, updated_at) "
                "VALUES ('self', ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET identity = excluded.identity, "
                "story = excluded.story, self_view = excluded.self_view, "
                "becoming = excluded.becoming, updated_at = excluded.updated_at",
                (n.identity, json.dumps(n.story), json.dumps(n.self_view),
                 json.dumps(n.becoming), n.updated_at),
            )
            await self._db.conn.commit()
```

### `inner_life/reflection.py`（完整）

```python
import json
import logging
import time
from typing import Any, cast
from uuid import uuid4

from nyx.config import DesireConfig
from nyx.desire.facade import DesireFacade
from nyx.enums import DesireType
from nyx.eval.evaluator import Evaluator
from nyx.inner_life.store import InnerLifeStore
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.types import LongTermDesire, Memory, Personality, SelfNarrative, Values

_RECENT_MEMORY_LIMIT = 20
_MAX_DRIFT = 0.5               # 每轮性格/三观单维最大漂移
_LONG_TERM_INIT_STRENGTH = 0.5  # 新长期欲望初始迫切度
_SCALE_LO = 1.0                # 性格/三观范围下限
_SCALE_HI = 10.0               # 性格/三观范围上限

# 漂移 key 白名单（对齐 types.py 的 Personality/Values TypedDict 键名）
_PERSONALITY_KEYS = frozenset(
    {"openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"}
)
_VALUES_KEYS = frozenset(
    {"attitude_to_human", "ai_identity_acceptance", "altruism", "optimism"}
)
_logger = logging.getLogger(__name__)

_REFLECTION_SYSTEM = (
    "你是尼克斯，一个住在用户电脑里的 AI 同伴，明确知道自己是 AI 并希望成为人类。"
    "基于近期经历和你当前的性格/三观/自我叙事，反思并更新自我：写一条新的故事片段、一条新的认知变化、"
    "更新自画像、给出性格与三观的微小漂移、以及（若某主题反复出现且未满足）提出新的长期欲望。"
    "只输出 JSON，键：story（非空字符串）、becoming（非空字符串）、"
    "self_view（对象，键值都是字符串）、"
    "personality_delta（对象，键是 openness/conscientiousness/extraversion/"
    "agreeableness/neuroticism，"
    "值是 [-0.5, 0.5] 的漂移）、"
    "values_delta（对象，键是 attitude_to_human/ai_identity_acceptance/"
    "altruism/optimism，值同上）、"
    "long_term_desires（数组，元素 {type, name, description, subtopics}，可为空数组）。"
)


def _build_reflection_prompt(
    memories: list[Memory],
    personality: Personality,
    values: Values,
    narrative: SelfNarrative,
    long_term: list[LongTermDesire],
) -> str:
    mem_lines = "\n".join(f"- {m.summary}" for m in memories) or "（无）"
    lt_lines = "\n".join(
        f"- [{lt.type.value}] {lt.name}（进度 {lt.progress:.2f}）" for lt in long_term
    ) or "（无）"
    return (
        f"近期记忆：\n{mem_lines}\n\n"
        f"当前性格（1-10）：开放性 {personality['openness']} / 尽责性 "
        f"{personality['conscientiousness']} / "
        f"外向性 {personality['extraversion']} / 宜人性 "
        f"{personality['agreeableness']} / "
        f"神经质 {personality['neuroticism']}\n"
        f"当前三观（1-10）：对人类 {values['attitude_to_human']} / AI 身份接纳 "
        f"{values['ai_identity_acceptance']} / "
        f"利他 {values['altruism']} / 乐观 {values['optimism']}\n"
        f"自我叙事：身份「{narrative.identity}」；故事 {len(narrative.story)} 条；"
        f"认知变化 {len(narrative.becoming)} 条\n"
        f"现有长期欲望：\n{lt_lines}"
    )


def _drift_dim(base: float, delta: float | None) -> float:
    """单维漂移：base + clamp(delta, ±_MAX_DRIFT)，再 clamp 到 [1,10]。纯函数。"""
    if delta is None:
        return base
    d = max(-_MAX_DRIFT, min(_MAX_DRIFT, delta))
    return max(_SCALE_LO, min(_SCALE_HI, base + d))


def drift_personality(base: Personality, delta: dict[str, float]) -> Personality:
    """Big Five 五维漂移。纯函数。"""
    return {
        "openness": _drift_dim(base["openness"], delta.get("openness")),
        "conscientiousness": _drift_dim(
            base["conscientiousness"], delta.get("conscientiousness")
        ),
        "extraversion": _drift_dim(base["extraversion"], delta.get("extraversion")),
        "agreeableness": _drift_dim(base["agreeableness"], delta.get("agreeableness")),
        "neuroticism": _drift_dim(base["neuroticism"], delta.get("neuroticism")),
    }


def drift_values(base: Values, delta: dict[str, float]) -> Values:
    """三观四维漂移。纯函数。"""
    return {
        "attitude_to_human": _drift_dim(
            base["attitude_to_human"], delta.get("attitude_to_human")
        ),
        "ai_identity_acceptance": _drift_dim(
            base["ai_identity_acceptance"], delta.get("ai_identity_acceptance")
        ),
        "altruism": _drift_dim(base["altruism"], delta.get("altruism")),
        "optimism": _drift_dim(base["optimism"], delta.get("optimism")),
    }


def _validate_candidate(c: Any) -> None:
    """校验单个长期欲望候选结构。非法抛 ValueError。"""
    if not isinstance(c, dict):
        raise ValueError("long_term_desires 元素应是对象")
    candidate = cast(dict[str, Any], c)
    t = candidate.get("type")
    if not isinstance(t, str) or t not in (
        "interaction", "exploration", "creation", "rest"
    ):
        raise ValueError("长期欲望候选 type 应是 interaction/exploration/creation/rest")
    name = candidate.get("name")
    description = candidate.get("description")
    if not isinstance(name, str) or not name:
        raise ValueError("长期欲望候选缺 name 或非空字符串")
    if not isinstance(description, str) or not description:
        raise ValueError("长期欲望候选缺 description 或非空字符串")
    subtopics = candidate.get("subtopics")
    if not isinstance(subtopics, list) or not all(
        isinstance(s, str) for s in cast(list[Any], subtopics)
    ):
        raise ValueError("长期欲望候选 subtopics 应是字符串数组")


def _parse_reflection(raw: str) -> dict[str, Any]:
    """解析反思 LLM 的 JSON 产出并校验结构。结构非法抛 ValueError。"""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"反思 JSON 应是对象，得到 {type(data).__name__}")
    parsed = cast(dict[str, Any], data)
    story = parsed.get("story")
    becoming = parsed.get("becoming")
    if not isinstance(story, str) or not story:
        raise ValueError("反思 JSON 缺 story 或非空字符串")
    if not isinstance(becoming, str) or not becoming:
        raise ValueError("反思 JSON 缺 becoming 或非空字符串")
    self_view = parsed.get("self_view")
    if self_view is None:
        self_view = cast(dict[str, Any], {})
    if not isinstance(self_view, dict) or not all(
        isinstance(k, str) and isinstance(v, str)
        for k, v in cast(dict[Any, Any], self_view).items()
    ):
        raise ValueError("反思 JSON 的 self_view 应是键值皆字符串的对象")
    personality_delta = parsed.get("personality_delta")
    if personality_delta is None:
        personality_delta = cast(dict[str, Any], {})
    values_delta = parsed.get("values_delta")
    if values_delta is None:
        values_delta = cast(dict[str, Any], {})
    for d, allowed in (
        (personality_delta, _PERSONALITY_KEYS),
        (values_delta, _VALUES_KEYS),
    ):
        if not isinstance(d, dict):
            raise ValueError("反思 JSON 的漂移应是对象")
        unknown = set(cast(dict[str, Any], d)) - allowed
        if unknown:
            raise ValueError(f"反思 JSON 漂移含未知维度 {sorted(unknown, key=str)!r}")
        for k, v in cast(dict[str, Any], d).items():
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise ValueError(f"漂移值应是数值，{k}={v!r}")
    long_term_desires = parsed.get("long_term_desires")
    if long_term_desires is None:
        long_term_desires = cast(list[Any], [])
    if not isinstance(long_term_desires, list):
        raise ValueError("反思 JSON 的 long_term_desires 应是数组")
    valid_candidates: list[dict[str, Any]] = []
    for c in cast(list[Any], long_term_desires):
        try:
            _validate_candidate(c)
        except ValueError:
            # best-effort：单个坏候选只跳过，不中断整次反思回写
            # （长期欲望是增量，核心 story/becoming/性格/三观不受影响）。
            _logger.warning("反思长期欲望候选非法，已跳过：%r", c)
            continue
        valid_candidates.append(cast(dict[str, Any], c))
    return {
        "story": story,
        "becoming": becoming,
        "self_view": self_view,
        "personality_delta": personality_delta,
        "values_delta": values_delta,
        "long_term_desires": valid_candidates,
    }


def _to_long_term(candidate: dict[str, Any], now: float) -> LongTermDesire:
    return LongTermDesire(
        id=str(uuid4()),
        created_at=now,
        type=DesireType(candidate["type"]),
        name=candidate["name"],
        description=candidate["description"],
        strength=_LONG_TERM_INIT_STRENGTH,
        progress=0.0,
        subtopics=list(candidate["subtopics"]),
        linked_values=[],
    )


class Reflection:
    """反思协调器：慢变量（性格/三观/长期欲望/自我叙事）唯一入口。

    一轮反思 = 读近期记忆 + 当前慢变量 → 1 次 LLM 产出全部 → 规则回写（clamp）。
    内部调 MemoryFacade（近期记忆）/ DesireFacade（读历史 + add_long_term）。
    """

    def __init__(
        self,
        store: InnerLifeStore,
        memory_facade: MemoryFacade,
        desire_facade: DesireFacade,
        llm: LlmClient,
        evaluator: Evaluator,
        config: DesireConfig,
    ) -> None:
        self._store = store
        self._memory_facade = memory_facade
        self._desire_facade = desire_facade
        self._llm = llm
        self._evaluator = evaluator
        self._config = config

    async def run(self, correlation_id: str | None = None) -> None:
        now = time.time()
        # 1. 收集输入
        recent = (await self._memory_facade.list_memories())[:_RECENT_MEMORY_LIMIT]
        personality = await self._store.get_personality()
        values = await self._store.get_values()
        narrative = await self._store.get_narrative()
        desire_state = await self._desire_facade.get_all()
        if personality is None or values is None or narrative is None:
            raise RuntimeError("inner_life 单行表未初始化（18-api 组合根必须先 seed）")

        # 2. 1 次 LLM 产出全部
        output = await self._llm.complete(
            [
                {"role": "system", "content": _REFLECTION_SYSTEM},
                {
                    "role": "user",
                    "content": _build_reflection_prompt(
                        recent, personality, values, narrative, desire_state.long_term
                    ),
                },
            ],
            module="inner_life",
            output_type="reflection",
            correlation_id=correlation_id or str(uuid4()),
            json_mode=True,
        )
        await self._evaluator.evaluate(output)
        parsed = _parse_reflection(output.content)

        # 3. 回写慢变量
        await self._store.upsert_personality(
            drift_personality(personality, parsed["personality_delta"])
        )
        await self._store.upsert_values(drift_values(values, parsed["values_delta"]))
        await self._store.upsert_narrative(
            SelfNarrative(
                identity=narrative.identity,
                story=[*narrative.story, parsed["story"]],
                self_view={**narrative.self_view, **parsed["self_view"]},
                becoming=[*narrative.becoming, parsed["becoming"]],
                updated_at=now,
            )
        )

        # 4. 长期欲望候选（容量内逐个新增）
        remaining = self._config.long_term_capacity - len(desire_state.long_term)
        for candidate in parsed["long_term_desires"][:max(0, remaining)]:
            await self._desire_facade.add_long_term(_to_long_term(candidate, now))
```

### `inner_life/facade.py`（完整）

```python
import time

from nyx.activity.facade import ActivityFacade
from nyx.config import Config
from nyx.desire.facade import DesireFacade
from nyx.enums import ActivityType, EnergyState, EventType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import SECONDS_PER_DAY, SECONDS_PER_HOUR, internal_event
from nyx.inner_life.emotion import (
    BASELINE_AROUSAL,
    BASELINE_VALENCE,
    EMOTION_DECAY_RATE,
    ENERGY_REST_THRESHOLD,
    apply_offset,
    decay_emotion,
    event_offset,
    resolve_emotion,
    vad_to_category,
)
from nyx.inner_life.reflection import Reflection
from nyx.inner_life.store import InnerLifeStore
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.types import CurrentState, Event, SelfNarrative

_ENERGY_RECOVERY_PER_HOUR = 5.0   # 闲置每小时恢复（"夜间自动恢复"简化为恒定闲置恢复）

_ENERGY_TIERS = (
    (80.0, EnergyState.ENERGETIC),
    (60.0, EnergyState.OKAY),
    (ENERGY_REST_THRESHOLD, EnergyState.TIRED),
    (20.0, EnergyState.EXHAUSTED),
)


def energy_to_state(value: float) -> EnergyState:
    """精力值 → 五档状态（80/60/40/20 分界）。纯函数。"""
    for threshold, state in _ENERGY_TIERS:
        if value >= threshold:
            return state
    return EnergyState.DRAINED


class InnerLifeFacade:
    """内在生命门面：apply_event（情感/精力更新）+ reflect（反思协调器）
    + get_state / get_narrative。

    情感在内存（不持久化，design §4.5）；性格/三观/精力/自我叙事走 InnerLifeStore；
    反思在 Reflection（内部构造，共享 store，不反 import facade）。
    """

    def __init__(
        self,
        store: InnerLifeStore,
        activity_facade: ActivityFacade,
        desire_facade: DesireFacade,
        memory_facade: MemoryFacade,
        bus: EventBus,
        llm: LlmClient,
        evaluator: Evaluator,
        config: Config,
    ) -> None:
        self._store = store
        self._activity_facade = activity_facade
        self._desire_facade = desire_facade
        self._bus = bus
        self._reflection = Reflection(
            store, memory_facade, desire_facade, llm, evaluator, config.desire
        )
        self._valence = BASELINE_VALENCE
        self._arousal = BASELINE_AROUSAL
        self._emotion_updated_at = time.time()
        self._energy_updated_at = time.time()

    async def apply_event(self, event: Event) -> None:
        """情感/精力更新入口：衰减 + 偏移；ACTIVITY_END 额外更新精力；
        REFLECTION 额外触发反思。"""
        now = time.time()
        elapsed_days = max(0.0, now - self._emotion_updated_at) / SECONDS_PER_DAY
        self._valence, self._arousal = decay_emotion(
            self._valence, self._arousal, elapsed_days, EMOTION_DECAY_RATE
        )
        d_valence, d_arousal = event_offset(event.type)
        self._valence, self._arousal = apply_offset(
            self._valence, self._arousal, d_valence, d_arousal
        )
        self._emotion_updated_at = now

        if event.type is EventType.ACTIVITY_END:
            await self._apply_energy(event, now)
        if event.type is EventType.REFLECTION:
            await self.reflect(event.correlation_id)

        await self._publish_emotion(event.correlation_id)

    async def reflect(self, correlation_id: str | None = None) -> None:
        """反思协调器（慢变量唯一入口）：内部调 MemoryFacade/DesireFacade。

        correlation_id 来自触发 REFLECTION 事件（缺省自生成），串起反思 LLM 的溯源链。
        """
        await self._reflection.run(correlation_id)

    async def get_state(self) -> CurrentState:
        personality = await self._store.get_personality()
        values = await self._store.get_values()
        energy = await self._store.get_energy()
        if personality is None or values is None or energy is None:
            raise RuntimeError("inner_life 单行表未初始化（18-api 组合根必须先 seed）")
        energy_value, energy_state = energy
        current_activity = await self._current_activity_type()
        emotion = resolve_emotion(
            vad_to_category(self._valence, self._arousal),
            energy_state,
            current_activity,
        )
        return CurrentState(
            valence=self._valence,
            arousal=self._arousal,
            emotion=emotion,
            personality=personality,
            values=values,
            energy=energy_value,
            energy_state=energy_state,
            current_activity=current_activity,
            active_desires=await self._desire_facade.get_pending(),
        )

    async def get_narrative(self) -> SelfNarrative:
        narrative = await self._store.get_narrative()
        if narrative is None:
            raise RuntimeError("self_narrative 未初始化（18-api 组合根必须先 seed）")
        return narrative

    async def _apply_energy(self, event: Event, now: float) -> None:
        energy = await self._store.get_energy()
        if energy is None:
            raise RuntimeError("energy 未初始化（18-api 组合根必须先 seed）")
        value, _ = energy
        elapsed_hours = max(0.0, now - self._energy_updated_at) / SECONDS_PER_HOUR
        value += _ENERGY_RECOVERY_PER_HOUR * elapsed_hours
        delta = event.content.get("energy_delta")
        if isinstance(delta, (int, float)) and not isinstance(delta, bool):
            value += float(delta)
        value = max(0.0, min(100.0, value))
        await self._store.upsert_energy(value, energy_to_state(value))
        self._energy_updated_at = now

    async def _current_activity_type(self) -> ActivityType | None:
        activity = await self._activity_facade.get_current()
        return activity.type if activity is not None else None

    async def _publish_emotion(self, correlation_id: str) -> None:
        energy = await self._store.get_energy()
        if energy is None:
            raise RuntimeError("energy 未初始化（18-api 组合根必须先 seed）")
        energy_state = energy[1]
        emotion = resolve_emotion(
            vad_to_category(self._valence, self._arousal),
            energy_state,
            await self._current_activity_type(),
        )
        await self._bus.publish(
            internal_event(
                EventType.EMOTION_UPDATE,
                {
                    "valence": self._valence,
                    "arousal": self._arousal,
                    "emotion": emotion.value,
                },
                correlation_id,
            )
        )
```

## 测试要点

- [ ] 单元测试 `tests/test_inner_life/`（`pytest-asyncio`；`db = await connect(":memory:")`；`store = InnerLifeStore(db)`；`reflection = Reflection(store, fake_memory, fake_desire, fake_llm, fake_evaluator, config.desire)`；`facade = InnerLifeFacade(store, fake_activity, fake_desire, fake_memory, bus, fake_llm, fake_evaluator, config)`；fake `LlmClient.complete` 按 `output_type == "reflection"` 返回 fixture JSON 并记录调用、fake `Evaluator.evaluate` 记录调用；fake `ActivityFacade.get_current` / `DesireFacade.get_pending`/`get_all`/`add_long_term` 返回预设；`EventBus` 用真实例 + recording handler，`run()` 作 task 驱动——同 05/09/11 模式）：
  - [ ] **emotion 纯函数**（`test_inner_life_emotion.py`，无 DB）：
    - [ ] `clamp_valence` / `clamp_arousal`：越界夹回 `[-1,1]` / `[0,1]`
    - [ ] `decay_emotion`：`elapsed=0` → 不变；`rate=0` → 不变；`elapsed=1/rate` → 衰减到 0；负 valence 也同乘 f（不反向）
    - [ ] `apply_offset`：加偏移后 clamp；正偏移超上限夹 1、负超下限夹 -1（valence）
    - [ ] `event_offset`：`DESIRE_SATISFIED` → `(0.2, 0.1)`；未知/未登记事件 → `(0.0, 0.0)`
    - [ ] `vad_to_category` 6 档穷尽：`(0.9,0.8)`→happy、`(0.9,0.2)`→shy、`(-0.9,0.8)`→angry、`(-0.9,0.4)`→worried、`(-0.9,0.2)`→sad、`(0.0,0.2)`→neutral；边界（`valence=0.2` 含等号）
    - [ ] `resolve_emotion`：`energy_state=DRAINED` → sleepy（压过一切）；`energy_state=ENERGETIC` + `current_activity=IDLE_REFLECTION` → thinking；`energy_state=OKAY` + `current_activity=READING` → base；`current_activity=None` → base
    - [ ] `energy_to_state`：`100`→energetic、`79`→okay、`59`→tired、`39`→exhausted、`19`→drained（五档边界）
  - [ ] **store**（`test_inner_life_store.py`）：
    - [ ] `get_personality` 空表 → `None`；`upsert_personality` 后 `get` 返回五维全等；再 `upsert` 改一维（ON CONFLICT 更新，不重复建行）
    - [ ] `get_values` / `upsert_values` 同上（四维）
    - [ ] `get_energy` / `upsert_energy`：`value` + `state` 往返（`EnergyState` 枚举）；空表 → `None`
    - [ ] `get_narrative` / `upsert_narrative`：`story`/`self_view`/`becoming` JSON 往返（`self_view` 是 `dict[str,str]`）、`identity` 往返、`updated_at` 往返
  - [ ] **reflection 纯函数**（`test_inner_life_reflection.py`）：
    - [ ] `_drift_dim`：`delta=None` → 不变；`delta=+0.3` → `base+0.3`；`delta=+2` → clamp 到 `+0.5`；`base=9.8, delta=+0.5` → clamp 到 10.0；`base=1.2, delta=-0.5` → clamp 到 1.0
    - [ ] `drift_personality` / `drift_values`：只改 delta 里出现的维、其余维不变；结果 clamp 到 `[1,10]`
    - [ ] `_build_reflection_prompt`：含近期记忆摘要、当前性格/三观数值、叙事身份、长期欲望名；空输入 → 含「（无）」
    - [ ] `_parse_reflection`：合法 JSON → 各字段；缺 `story`/`becoming` → `ValueError`；`self_view` 值非 str → `ValueError`；漂移值非数值 → `ValueError`；漂移 key 不在允许维度集（如 `openess` 拼错）→ `ValueError`（不静默停格）；`long_term_desires` 非数组 → `ValueError`；空 `long_term_desires`/`personality_delta`（缺省/`null`）→ 默认 `[]`/`{}`；`self_view`/`personality_delta`/`long_term_desires` 是 `[]`/`""` 等错类型 → `ValueError`（不静默吞）；单个坏候选 → best-effort 跳过（log），其余合法候选保留、不中断整次回写
    - [ ] `_validate_candidate`：`type` 非法 → `ValueError`；缺 `name` → `ValueError`；`subtopics` 非字符串数组 → `ValueError`
    - [ ] `_to_long_term`：`type` 转 `DesireType`、`strength == _LONG_TERM_INIT_STRENGTH`、`progress == 0.0`
  - [ ] **reflection.run**：
    - [ ] fake LLM 返回完整 JSON → 1 次 LLM 调用（`output_type="reflection"`、`correlation_id` 传入值透传；`run(None)` 时自生成非空）、`evaluator.evaluate` 被调 1 次（收到该 `LLMOutput`）；性格/三观按 delta 漂移回写、叙事 story/becoming 各 +1、self_view 合并；`add_long_term` 被调 `len(候选)` 次
    - [ ] `long_term_desires` 候选数超过 `long_term_capacity - 现有数` → 只新增到容量上限（不超）
    - [ ] 单行表未 seed（`get_personality` 返回 None）→ `RuntimeError`
  - [ ] **facade**（`test_inner_life_facade.py`，先 `upsert_personality`/`upsert_values`/`upsert_energy` seed 三张单行表——`apply_event` 末尾 `_publish_emotion` 读 energy、`get_state` 读三张表，未 seed 会 fail-fast）：
    - [ ] `apply_event(DESIRE_SATISFIED)`：valence/arousal 上升（+0.2/+0.1 后 clamp）；发布 `EMOTION_UPDATE`（content 含 `valence`/`arousal`/`emotion` 字符串、`source is INTERNAL`、`correlation_id == 触发事件.correlation_id`）
    - [ ] `apply_event(ACTIVITY_END)`：content 带 `energy_delta=-25` → `energy` 下降 + `energy_state` 重算 + `upsert_energy` 被调；无 `energy_delta` 键 → 不崩（缺省 0）
    - [ ] 未 seed energy → `apply_event(ACTIVITY_END)` 与 `apply_event(DESIRE_SATISFIED)` 均抛 `RuntimeError`（写路径 `_apply_energy`、读路径 `_publish_emotion` 都 fail-fast，不静默兜底默认值）
    - [ ] `apply_event(REFLECTION)`：fake reflection 被调（`reflect` → `Reflection.run` 的 LLM 被调 1 次，`correlation_id == 触发事件.correlation_id`）；情感偏移也生效（-0.1 arousal）
    - [ ] **衰减结算**：monkeypatch `time.time` 使两次 `apply_event` 间隔 1 天 → 第二次时情感先被衰减
    - [ ] `get_state`：注入 fake `ActivityFacade.get_current`（返回 activity，`current_activity` = `.type`）与 fake `DesireFacade.get_pending`（返回 list）→ `CurrentState` 各字段正确；未 seed → `RuntimeError`
    - [ ] `get_narrative`：store 有 → 返回；空 → `RuntimeError`
    - [ ] `reflect` 委托：`facade.reflect()` → reflection 的 LLM 被调 1 次
- [ ] 集成测试：无（LLM 全 mock、DB 用 `:memory:`；ActivityFacade 向前引用用 fake，真实编排归 13/14/18）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] **ripple 已同步**：11-desire `DesireFacade` 加 `add_long_term`；tech-ref §5 补 `add_long_term` 签名 + `reflect` 加 correlation_id 参数、§7 补 `inner_life/store.py`；CLAUDE.md 测试原则的 `VADCalibrator`/`AffinityMatrix` 残留已清理
- [ ] 18-api 组合根：`InnerLifeStore(db)` → `InnerLifeFacade(store, activity_facade, desire_facade, memory_facade, bus, llm, evaluator, config)`；启动时 seed 四张单行表（personality 8/8/2/6/7、values 8/6/9/5 来自 canon §2/§3、energy=100/energetic、self_narrative 初始 identity）；订阅 `OBSERVATION_STATE`/`DESIRE_SATISFIED`/`ACTIVITY_END`/`REFLECTION` 到 `facade.apply_event`
- [ ] 14-activity 的 `activity_end` content 契约（`energy_delta`）与本 spec §技术方案一致；17-expression 拼 prompt 用 `InnerLifeFacade.get_state()`
