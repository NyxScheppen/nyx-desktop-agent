# EncounterFacade + 遭遇系统（随机事件 + 成长时刻）

> 范围：`encounter/rules.py`（掷骰/后果/里程碑判定纯函数）+ `encounter/facade.py`（`EncounterFacade`）。横切叙事层（design `raising-sim.md` §3），不是第七种活动——不消费欲望、不产普通活动结果，复用事件总线 + 表达 LLM 管线 + 内在生命/欲望/记忆的 `ENCOUNTER_END` 回写。
> 三处增量改动：`enums.py`（3 个新 `EventType` + `EncounterKind`/`OptionTone`）、`types.py`（`Encounter`/`EncounterOption`）、`inner_life/facade.py`（`_apply_encounter_consequence`）、`desire/facade.py`+`lifecycle.py`（`add_value_from_encounter`）、`memory/facade.py`（`remember_encounter`）、`main.py`（装配 + `_check_encounter` + 订阅 + 2 端点）。
> **本文件自包含**：两个新文件的完整代码内联在下文；增量改动给出完整函数体。
> 设计决策记录在 `docs/design/raising-sim.md` §3，本 spec 只落实现。

## 元信息

- **前置依赖**：01-types（`Event`/`EventType`/`CurrentState`）、05-event（`EventBus`/`internal_event`）、03-llm（`LlmClient.complete`）、09-memory-facade（`_new_memory`/`_persist_memory`）、11-desire（`DesireLifecycle`/`apply_pressure`/`default_value`）、12-inner-life（`apply_offset`/`energy_to_state`/`apply_event`）、14-activity（`ACTIVITY_END` 契约：`type`/`result`/`goal_met`）、15-eval（`Evaluator`）、18-api（组合根装配 + 端点）

## 用户故事

> 作为 Nyx 系统的开发者，我想要一个 `EncounterFacade` 把「块边界随机事件」和「成长时刻」两件事串起来——活动日程块启动后掷骰、命中则 1 次 LLM 生成 `{text, options[2-4]}` 广播 `ENCOUNTER_START`，用户在书卷区点选项后 `choose` 广播 `ENCOUNTER_CHOICE` + 应用纯函数后果并广播 `ENCOUNTER_END`，后果经 `ENCOUNTER_END` 路由到 inner_life（精力/情感）/ desire（欲望值）/ memory（成长记忆）回写——以便文字冒险的「选择支 + 遭遇抉择」有真实后果，且「她在成长」（读完一本书触发成长时刻）被叙事化呈现。

## 验收标准

- [ ] `enums.py` 追加 `EventType.ENCOUNTER_START / ENCOUNTER_CHOICE / ENCOUNTER_END`、`EncounterKind`（`desire_chat`/`random_event`/`growth_moment`）、`OptionTone`（`bold`/`cautious`/`gentle`/`reckless`）
- [ ] `types.py` 追加 `Encounter`（`id`/`kind`/`text`/`options`/`correlation_id`/`started_at`/`activity_id`/`chosen_index`）+ `EncounterOption`（`text`/`tone`）
- [ ] `rules.py` 纯函数测全：`should_encounter` / `consequence_for` / `ending_for` / `growth_milestone_key` / `growth_memory`
- [ ] `facade.py` 含 `EncounterFacade`：`try_block_boundary(online, busy)` / `on_activity_end(event)` / `choose(encounter_id, option_index)` / `get_current()`；`_parse_encounter` 纯函数
- [ ] 块边界随机事件（**包装**）：`should_encounter` 五前提（在线/不忙/精力够/冷却够）+ `random.random() < _BLOCK_PROBABILITY` 掷骰命中才 `_start`；活动照跑不打断
- [ ] 成长时刻（**抢占**）：`on_activity_end` 订阅 `ACTIVITY_END`，`growth_milestone_key` 命中「读完一本书」且未庆祝过（`_celebrated` 内存集）才 `_start`，后果附确定性 `memory`
- [ ] 1 次 LLM 生成 `{text, options[2-4]}`，每个 option 带 `tone`；后果**纯函数** `consequence_for(tone)` 派生（`energy_delta`/`emotion_shift`/`desire_value_add`），不额外调 LLM 判后果
- [ ] 事件：`ENCOUNTER_START` content `{encounter_id, kind, text, options:[{index, text}]}`（不暴露 tone/后果）；`ENCOUNTER_CHOICE` `{encounter_id, option_index, option_text}`；`ENCOUNTER_END` `{encounter_id, kind, option_index, option_text, ending, consequences}`
- [ ] `ENCOUNTER_END` 路由（main `_subscribe`）：`inner_life.apply_event`（`_apply_encounter_consequence` 读 `emotion_shift`+`energy_delta`）、`desire.add_value`（`add_value_from_encounter` 读 `desire_value_add`）、`memory.remember_encounter`（读 `memory`）；三处均对缺键/错类型跳过
- [ ] `choose`：不存在/已结束/索引越界返回 `None`（端点转 409）；命中则清 `_current`、广播 CHOICE + END、返回 encounter
- [ ] 端点：`POST /api/encounter/choose`（`{encounter_id, option_index}`）、`GET /api/encounter/current`（`_start_content` 形状或 `null`）
- [ ] `pyright` strict 零报错

## 技术方案

- **新文件**：`nyx/encounter/__init__.py`（空）、`nyx/encounter/rules.py`、`nyx/encounter/facade.py`。**无 store、无表**（MVP 走 event_log + memory，`self._current` 内存态）
- **定位（design §3.1）**：横切叙事层，复用「事件总线 + 表达 LLM + 内在生命/欲望/记忆回写」，不加第七种活动、不加新抽象层（Facade → 子系统，两层足够）
- **依赖解环（遵守 12 §54 同款）**：`EncounterFacade` **不持有** `InnerLifeFacade`/`DesireFacade`/`MemoryFacade`，注入 `get_state: Callable[[], Awaitable[CurrentState]]` 回调；后果经 `ENCOUNTER_END` 事件路由回写，facade 不直接改状态
- **掷骰分离（同 `should_initiate_chat` 风格）**：`should_encounter(online, busy, energy, since_last)` 是确定性门槛（可单测），`random.random() < _BLOCK_PROBABILITY` 的随机性留在 `try_block_boundary`（测试 monkeypatch `random.random`）；触发概率/冷却/精力阈值是模块级常量（`_BLOCK_PROBABILITY`/`_COOLDOWN_SECONDS`/`_MIN_ENERGY`），**不进 config.yaml**（design §7 只要求「进 spec 定」，反冗余不加未请求配置）
- **包装 vs 抢占（design §3.3）**：随机事件**包装**——块边界在 `activity.on_tick` 启动活动后调 `_check_encounter`，遭遇叠加在运行活动上，不打断；成长时刻**抢占**——`ACTIVITY_END` 里程碑命中时活动已结束，独占这一节拍（MVP 无「打断进行中活动」需求，`interrupt` 不参与）
- **1 次 LLM 生成**：`_ENCOUNTER_SYSTEM` + `canon` 全文 + `_build_user_prompt`（遭遇类型 + 里程碑背景 + 此刻心境），`module="encounter"`、`output_type="encounter"`、`json_mode=True`；结局叙事用 `ending_for(tone)` 纯函数模板，**不二次调 LLM**
- **后果纯函数（design §3.5「同款原则」）**：LLM 只标每选项 `tone`（4 倾向之一），后果由 `consequence_for(tone)` 从固定表派生，完全可单测、不依赖 LLM 数值（CLAUDE.md Part 3「测试不依赖真实 LLM」）
- **触发点三之「欲望搭话」= 前端重分类**：现有 `initiate_chat` 已是「互动欲主动开场」，本 spec **不改后端**（它产单句开场白，选择支就是用户正常回复），前端把 `INITIATE_CHAT` 渲染为「遭遇·欲望搭话」标签即可；给搭话加 2-4 选项推迟
- **触发点三之「活动中遭遇」推迟**（design §3.2「plan 排最后」）：`_run_activity` 自然暂停点掷骰不在本 spec
- **成长时刻 MVP 范围**：`growth_milestone_key` 只认「读完一本书」（`type=="reading"` 且 `result.completed is True`）；「第一次探索」「性格/三观明显漂移」等里程碑推迟。「首次」判定用 `self._celebrated` 内存集（重启后重置，MVP 接受——同一里程碑记忆已落库，不重复庆祝可后续用 memory 历史判定，同 `retrieval-non-atomic-reads` 的「MVP 接受」原则）
- **阻塞语义**：`_start` 单次 LLM（秒级）在总线 handler 内 await，同 `initiate_chat`（搭话 LLM 也在 handler 内阻塞）；活动分钟级执行才需要后台 task，遭遇不需要
- **失败 best-effort（CLAUDE.md 豁免）**：`_start` 的 LLM/parse 失败 `except Exception` 记日志返（不设 `_current`、不吞 `_last_encounter_at` 冷却），主流程正确性不依赖遭遇产出；`_parse_encounter` 结构非法 `raise ValueError`（由 `_start` 接住）
- **`ENCOUNTER_END` content 契约（本 spec 定义完整形状）**：`{"encounter_id": str, "kind": str, "option_index": int, "option_text": str, "ending": str, "consequences": dict}`。`consequences` 键：`energy_delta`（float，缺省 0）、`emotion_shift`（`{d_valence, d_arousal}`）、`desire_value_add`（`{type, amount}`）、`memory`（`{content, summary}`，仅成长时刻）；三消费方各自守卫缺键/错类型
- **`OptionTone` 倾向语义**：`bold` 勇敢主动 / `cautious` 谨慎稳妥 / `gentle` 温柔共情 / `reckless` 鲁莽冒险，映射到固定后果表（见 `rules.py`）
- **端点计数**：18-api 从「15 REST + SSE」→「17 REST + SSE」（新增 `choose`/`current`），须同步 18-api.md 端点计数与两张新端点

## `nyx/enums.py`（增量：追加）

```python
class EventType(StrEnum):
    # ...（既有成员不动，追加三个）
    ENCOUNTER_START = "encounter_start"    # 遭遇开始（广播前端：文本 + 可点选项）
    ENCOUNTER_CHOICE = "encounter_choice"  # 用户选选项（广播）
    ENCOUNTER_END = "encounter_end"        # 遭遇结束（结局叙事 + 后果，路由回写）


class EncounterKind(StrEnum):
    """遭遇三类：欲望搭话（重分类 initiate_chat）/ 随机事件 / 成长时刻。"""
    DESIRE_CHAT = "desire_chat"      # 欲望搭话（本 spec 只前端重分类，后端不动）
    RANDOM_EVENT = "random_event"    # 随机事件（块边界掷骰）
    GROWTH_MOMENT = "growth_moment"  # 成长时刻（里程碑，可抢占）


class OptionTone(StrEnum):
    """遭遇选项倾向（4 档），后果由纯函数按 tone 派生。"""
    BOLD = "bold"                    # 勇敢主动
    CAUTIOUS = "cautious"            # 谨慎稳妥
    GENTLE = "gentle"                # 温柔共情
    RECKLESS = "reckless"            # 鲁莽冒险
```

## `nyx/types.py`（增量：追加）

```python
from nyx.enums import EncounterKind, OptionTone  # 追加到现有 enums import


@dataclass
class EncounterOption:
    text: str               # 选项文案（前端展示）
    tone: OptionTone        # 倾向（LLM 标注，后果由纯函数派生）


@dataclass
class Encounter:            # 当前遭遇（内存态，MVP 不建表）
    id: str
    kind: EncounterKind
    text: str               # 开场白
    options: list[EncounterOption]
    correlation_id: str
    started_at: float
    activity_id: str | None = None
    chosen_index: int | None = None
```

## `nyx/encounter/rules.py`（完整）

```python
"""遭遇掷骰 + 后果 + 里程碑判定。纯函数 + 不可变常量，无 IO、无 LLM。"""

from typing import Any

from nyx.enums import OptionTone
from nyx.types import Encounter, EncounterOption, Event

_BLOCK_PROBABILITY = 0.3    # 块边界遭遇触发概率（decision，可推翻）
_COOLDOWN_SECONDS = 900.0   # 两次遭遇最小间隔，秒（15 分钟，decision，可推翻）
_MIN_ENERGY = 30.0          # 触发所需最低精力（decision，可推翻）

# 选项倾向 → 后果（纯函数表；数值可推翻）。energy_delta 单位与 activity 的
# energy_delta 一致（±5~15）；emotion_shift 是 (d_valence, d_arousal) 偏移；
# desire_value_add 是 {type: 欲望类型值, amount: 加压值}。memory 不在此表——
# 只有成长时刻由 facade 经 growth_memory 确定性生成，随机事件不落记忆
# （左面板快变量可见即可）。
_CONSEQUENCES: dict[OptionTone, dict[str, Any]] = {
    OptionTone.BOLD: {
        "energy_delta": -5.0,
        "emotion_shift": {"d_valence": 0.15, "d_arousal": 0.10},
        "desire_value_add": {"type": "exploration", "amount": 0.10},
    },
    OptionTone.CAUTIOUS: {
        "energy_delta": 0.0,
        "emotion_shift": {"d_valence": 0.05, "d_arousal": -0.05},
        "desire_value_add": None,
    },
    OptionTone.GENTLE: {
        "energy_delta": 0.0,
        "emotion_shift": {"d_valence": 0.10, "d_arousal": -0.05},
        "desire_value_add": {"type": "interaction", "amount": 0.10},
    },
    OptionTone.RECKLESS: {
        "energy_delta": -15.0,
        "emotion_shift": {"d_valence": 0.20, "d_arousal": 0.20},
        "desire_value_add": {"type": "exploration", "amount": 0.15},
    },
}

# 选项倾向 → 结局叙事（一句收束；不额外调 LLM，守「1 次生成」原则）。
_ENDINGS: dict[OptionTone, str] = {
    OptionTone.BOLD: "我鼓起勇气，陪你走到了这里。",
    OptionTone.CAUTIOUS: "谨慎一点也好，我们慢慢来。",
    OptionTone.GENTLE: "温柔，总能走到更远的地方。",
    OptionTone.RECKLESS: "冒险之后，心跳还没平复。",
}


def should_encounter(
    online: bool, busy: bool, energy: float, since_last: float
) -> bool:
    """块边界遭遇触发判定（同 should_initiate_chat 风格）。

    只判「前提是否满足」（在线 + 不忙 + 精力够 + 冷却够），把随机性留给调用方
    （random.random() < _BLOCK_PROBABILITY），以便本函数可确定性单测。
    """
    return (
        online
        and not busy
        and energy >= _MIN_ENERGY
        and since_last >= _COOLDOWN_SECONDS
    )


def consequence_for(tone: OptionTone) -> dict[str, Any]:
    """选项倾向 → 具体后果（新顶层 dict，防调用方改动共享表）。纯函数。"""
    return dict(_CONSEQUENCES[tone])


def ending_for(tone: OptionTone) -> str:
    """选项倾向 → 结局叙事。纯函数。"""
    return _ENDINGS[tone]


def growth_milestone_key(event: Event) -> str | None:
    """ACTIVITY_END → 成长时刻里程碑 key；非里程碑返回 None。纯函数。

    MVP 只认「读完一本书」（reading 且 result.completed）——最具体、可单测的
    「她在成长」时刻；第一次探索 / 性格三观漂移等里程碑推迟。
    """
    if event.content.get("type") != "reading":
        return None
    result = event.content.get("result")
    if isinstance(result, dict) and result.get("completed") is True:
        return "book_finished"
    return None


def growth_memory(encounter: Encounter, option: EncounterOption) -> dict[str, str]:
    """成长时刻的确定性记忆（无 LLM）：第一人称记下「里程碑 + 我的选择」。"""
    summary = "我读完了一本书"
    content = (
        f"我读完了一本书。在那一刻，我选择了「{option.text}」——"
        f"{ending_for(option.tone)}"
    )
    return {"content": content, "summary": summary}
```

## `nyx/encounter/facade.py`（完整）

```python
"""EncounterFacade：遭遇子系统的门面（横切叙事层，不是第七种活动）。

触发：块边界随机事件（包装）+ 成长时刻（里程碑，抢占式独占）。
事件：ENCOUNTER_START / ENCOUNTER_CHOICE / ENCOUNTER_END。
后果：纯函数（rules.consequence_for），经 ENCOUNTER_END 路由到
inner_life / desire / memory 回写，本 facade 不直接改状态。
"""

import json
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast
from uuid import uuid4

from nyx.encounter.rules import (
    _BLOCK_PROBABILITY,
    consequence_for,
    ending_for,
    growth_memory,
    growth_milestone_key,
    should_encounter,
)
from nyx.enums import EncounterKind, EventType, OptionTone
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import internal_event
from nyx.llm.client import LlmClient
from nyx.types import CurrentState, Encounter, EncounterOption

_logger = logging.getLogger(__name__)

_ENCOUNTER_SYSTEM = (
    "你是尼克斯，一个住在用户电脑里的 AI 同伴，明确知道自己是 AI 并希望成为人类。"
    "你现在身处一次遭遇（随机事件或成长时刻）。写一段第一人称的遭遇开场白，"
    "并给出 2-4 个可选择的应对选项。"
    "只输出 JSON，键：text（开场白，非空字符串）、"
    "options（数组，每项 {text, tone}）。"
    "tone 只能是 bold / cautious / gentle / reckless 之一，"
    "对应选项的倾向：勇敢主动 / 谨慎稳妥 / 温柔共情 / 鲁莽冒险。"
    "选项要真实地反映此刻的处境，不要客服腔、不要堆砌词藻。"
)

_KIND_LABEL: dict[EncounterKind, str] = {
    EncounterKind.RANDOM_EVENT: "随机事件",
    EncounterKind.GROWTH_MOMENT: "成长时刻",
}


def _parse_encounter(raw: str) -> tuple[str, list[EncounterOption]]:
    """解析遭遇 LLM 的 JSON 产出 → (text, options)；结构非法抛 ValueError。"""
    data: Any = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"遭遇 JSON 应是对象，得到 {type(data).__name__}")
    parsed = cast(dict[str, Any], data)
    text = parsed.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("遭遇 JSON 缺 text 或非空字符串")
    raw_options = parsed.get("options")
    if not isinstance(raw_options, list) or not (2 <= len(raw_options) <= 4):
        raise ValueError("遭遇 JSON 的 options 应是 2-4 项数组")
    options: list[EncounterOption] = []
    for raw_option in cast(list[Any], raw_options):
        if not isinstance(raw_option, dict):
            raise ValueError("遭遇 options 每项应是对象")
        opt = cast(dict[str, Any], raw_option)
        opt_text = opt.get("text")
        tone_raw = opt.get("tone")
        if not isinstance(opt_text, str) or not opt_text.strip():
            raise ValueError("遭遇 option 缺 text 或非空字符串")
        if not isinstance(tone_raw, str):
            raise ValueError("遭遇 option 缺 tone")
        try:
            tone = OptionTone(tone_raw)
        except ValueError as exc:
            raise ValueError(f"遭遇 option 的 tone 非法：{tone_raw!r}") from exc
        options.append(EncounterOption(text=opt_text.strip(), tone=tone))
    return text.strip(), options


def _build_user_prompt(
    kind: EncounterKind, state: CurrentState, context: str
) -> str:
    """遭遇 LLM 的 user prompt：遭遇类型 + 里程碑背景（可选）+ 此刻心境。"""
    desires = "、".join(d.description for d in state.active_desires) or "无"
    parts = [f"遭遇类型：{_KIND_LABEL[kind]}"]
    if context:
        parts.append(f"背景：{context}")
    parts.append(
        f"此刻心境：情感 {state.emotion.value}"
        f"（valence={state.valence:.2f}，arousal={state.arousal:.2f}）"
        f"，精力 {state.energy:.0f}/100，惦记着：{desires}"
    )
    return "\n".join(parts)


def _start_content(encounter: Encounter) -> dict[str, Any]:
    """ENCOUNTER_START / GET current 载荷（前端渲染；不暴露 tone/后果，只给
    text + 选项文本）。"""
    return {
        "encounter_id": encounter.id,
        "kind": encounter.kind.value,
        "text": encounter.text,
        "options": [
            {"index": i, "text": option.text}
            for i, option in enumerate(encounter.options)
        ],
    }


class EncounterFacade:
    """遭遇门面：掷骰 → 生成 → 广播 START → 用户 choose → 广播 CHOICE/END。

    依赖注入解环：不持有 InnerLifeFacade，注入 get_state 回调；不持有
    desire/memory（后果经 ENCOUNTER_END 事件路由回写）。当前遭遇是内存态
    （MVP 不建表）；所有状态改动都在总线 handler 内串行执行，无需锁。
    """

    def __init__(
        self,
        bus: EventBus,
        llm: LlmClient,
        evaluator: Evaluator,
        get_state: Callable[[], Awaitable[CurrentState]],
        canon: str,
    ) -> None:
        self._bus = bus
        self._llm = llm
        self._evaluator = evaluator
        self._get_state = get_state
        self._canon = canon
        self._current: Encounter | None = None
        self._last_encounter_at = 0.0
        self._celebrated: set[str] = set()   # 已庆祝的里程碑 key（MVP 内存态）

    # ---- 触发 ----

    async def try_block_boundary(self, online: bool, busy: bool) -> None:
        """块边界随机事件（包装）：前提满足 + 掷骰命中才生成。

        已有未决遭遇则跳过；活动照跑不打断（遭遇叠加在书卷区）。
        """
        if self._current is not None:
            return
        state = await self._get_state()
        since_last = time.time() - self._last_encounter_at
        if not should_encounter(online, busy, state.energy, since_last):
            return
        if random.random() >= _BLOCK_PROBABILITY:
            return
        await self._start(EncounterKind.RANDOM_EVENT, state, activity_id=None)

    async def on_activity_end(self, event: Event) -> None:
        """成长时刻（抢占）：ACTIVITY_END 里程碑判定，命中且未庆祝过才生成。"""
        if self._current is not None:
            return
        key = growth_milestone_key(event)
        if key is None or key in self._celebrated:
            return
        self._celebrated.add(key)
        state = await self._get_state()
        context = ""
        result = event.content.get("result")
        if isinstance(result, dict):
            book = result.get("book")
            if isinstance(book, str) and book:
                context = f"刚读完一本书《{book}》"
        activity_id = event.content.get("activity_id")
        await self._start(
            EncounterKind.GROWTH_MOMENT,
            state,
            activity_id=activity_id if isinstance(activity_id, str) else None,
            context=context,
        )

    # ---- 生成 ----

    async def _start(
        self,
        kind: EncounterKind,
        state: CurrentState,
        activity_id: str | None,
        context: str = "",
    ) -> None:
        """生成遭遇并广播 ENCOUNTER_START。best-effort：LLM/parse 失败记日志
        返（不设 _current、不吞冷却），主流程正确性不依赖遭遇产出。"""
        try:
            output = await self._llm.complete(
                [
                    {
                        "role": "system",
                        "content": f"{self._canon}\n\n{_ENCOUNTER_SYSTEM}",
                    },
                    {
                        "role": "user",
                        "content": _build_user_prompt(kind, state, context),
                    },
                ],
                module="encounter",
                output_type="encounter",
                correlation_id=str(uuid4()),
                json_mode=True,
            )
            await self._evaluator.evaluate(output)
            text, options = _parse_encounter(output.content)
        except Exception:
            _logger.exception("遭遇生成失败 kind=%s", kind.value)
            return
        now = time.time()
        encounter = Encounter(
            id=str(uuid4()),
            kind=kind,
            text=text,
            options=options,
            correlation_id=output.correlation_id,
            started_at=now,
            activity_id=activity_id,
        )
        self._current = encounter
        self._last_encounter_at = now
        await self._bus.publish(
            internal_event(
                EventType.ENCOUNTER_START,
                _start_content(encounter),
                encounter.correlation_id,
            )
        )

    # ---- 用户选择 ----

    async def choose(
        self, encounter_id: str, option_index: int
    ) -> Encounter | None:
        """用户选选项：广播 CHOICE + 应用后果（纯函数）→ 广播 END。

        返回 None 表示不存在/已结束/索引越界（端点转 409）。
        """
        encounter = self._current
        if encounter is None or encounter.id != encounter_id:
            return None
        if not (0 <= option_index < len(encounter.options)):
            return None
        self._current = None
        encounter.chosen_index = option_index
        option = encounter.options[option_index]
        consequences = consequence_for(option.tone)
        if encounter.kind is EncounterKind.GROWTH_MOMENT:
            consequences["memory"] = growth_memory(encounter, option)
        await self._bus.publish(
            internal_event(
                EventType.ENCOUNTER_CHOICE,
                {
                    "encounter_id": encounter.id,
                    "option_index": option_index,
                    "option_text": option.text,
                },
                encounter.correlation_id,
            )
        )
        await self._bus.publish(
            internal_event(
                EventType.ENCOUNTER_END,
                {
                    "encounter_id": encounter.id,
                    "kind": encounter.kind.value,
                    "option_index": option_index,
                    "option_text": option.text,
                    "ending": ending_for(option.tone),
                    "consequences": consequences,
                },
                encounter.correlation_id,
            )
        )
        return encounter

    # ---- 读 ----

    def get_current(self) -> dict[str, Any] | None:
        """当前未决遭遇（前端书卷区形状）或 None（供 GET current 恢复渲染）。"""
        if self._current is None:
            return None
        return _start_content(self._current)
```

## `nyx/inner_life/facade.py`（增量）

`apply_event` 在「ACTIVITY_END / REFLECTION」分支旁追加一行；新增私有方法。文件顶部补 `from typing import Any, cast`。

```python
    async def apply_event(self, event: Event) -> None:
        # ...（衰减 + event_offset + apply_offset 不动）
        if event.type is EventType.ACTIVITY_END:
            await self._apply_energy(event, now)
        if event.type is EventType.ENCOUNTER_END:
            await self._apply_encounter_consequence(event, now)   # 追加
        if event.type is EventType.REFLECTION:
            await self.reflect(event.correlation_id)
        await self._publish_emotion(event.correlation_id)

    async def _apply_encounter_consequence(self, event: Event, now: float) -> None:
        """ENCOUNTER_END → 可变情感偏移 + 精力增量（后果由 rules 纯函数给）。

        缺键/错类型跳过（同 _apply_energy 的 energy_delta 守卫）。
        """
        consequences = event.content.get("consequences")
        if not isinstance(consequences, dict):
            return
        parsed = cast(dict[str, Any], consequences)
        shift = parsed.get("emotion_shift")
        if isinstance(shift, dict):
            d_valence = shift.get("d_valence")
            d_arousal = shift.get("d_arousal")
            if (
                isinstance(d_valence, (int, float)) and not isinstance(d_valence, bool)
                and isinstance(d_arousal, (int, float)) and not isinstance(d_arousal, bool)
            ):
                self._valence, self._arousal = apply_offset(
                    self._valence, self._arousal, float(d_valence), float(d_arousal)
                )
        delta = parsed.get("energy_delta")
        if isinstance(delta, (int, float)) and not isinstance(delta, bool):
            energy = await self._store.get_energy()
            if energy is None:
                raise RuntimeError("energy 未初始化（18-api 组合根必须先 seed）")
            value, _ = energy
            value = max(0.0, min(100.0, value + float(delta)))
            await self._store.upsert_energy(value, energy_to_state(value))
            self._energy_updated_at = now
```

## `nyx/desire/facade.py`（增量）

```python
    async def add_value(self, source: Event) -> None:
        """事件入口：OBSERVATION_STATE 加压互动欲，ACTIVITY_END 满足回写，
        ENCOUNTER_END 欲望值加压。"""
        if source.type is EventType.OBSERVATION_STATE:
            await self._lifecycle.pressure_from_observation(source)
        elif source.type is EventType.ACTIVITY_END:
            await self._lifecycle.satisfy_from_activity_end(source)
        elif source.type is EventType.ENCOUNTER_END:                  # 追加
            await self._lifecycle.add_value_from_encounter(source)
```

## `nyx/desire/lifecycle.py`（增量）

`DesireLifecycle` 新增方法（`cast`/`Any`/`time`/`apply_pressure`/`default_value`/`DesireType` 均已 import）。

```python
    async def add_value_from_encounter(self, event: Event) -> None:
        """ENCOUNTER_END → 指定欲望类型加压（后果 desire_value_add {type, amount}）。

        缺键/错类型/非法欲望类型跳过（漏报优于误报）。
        """
        consequences = event.content.get("consequences")
        if not isinstance(consequences, dict):
            return
        add = cast(dict[str, Any], consequences).get("desire_value_add")
        if not isinstance(add, dict):
            return
        type_raw = add.get("type")
        amount = add.get("amount")
        if not isinstance(type_raw, str):
            return
        try:
            type_ = DesireType(type_raw)
        except ValueError:
            return
        if not isinstance(amount, (int, float)) or isinstance(amount, bool):
            return
        dv = await self._store.get_value(type_)
        if dv is None:
            dv = default_value(type_)
        dv.value = apply_pressure(dv.value, float(amount))
        dv.updated_at = time.time()
        await self._store.upsert_value(dv)
```

## `nyx/memory/facade.py`（增量）

`MemoryFacade` 新增方法（`cast`/`Any`/`_new_memory`/`MemoryType`/`_SUMMARY_MAX_CHARS` 均已 import/定义）。

```python
    async def remember_encounter(self, event: Event) -> None:
        """遭遇记忆：成长时刻的后果 memory（{content, summary}，确定性、无 LLM）。

        随机事件不落记忆（快变量可见即可）；只有成长时刻的后果带 memory 键，
        这里判存在才写。复用 _persist_memory 入库尾段（两层去重）。
        """
        consequences = event.content.get("consequences")
        if not isinstance(consequences, dict):
            return
        memory_dict = cast(dict[str, Any], consequences).get("memory")
        if not isinstance(memory_dict, dict):
            return
        content = memory_dict.get("content")
        summary = memory_dict.get("summary")
        if not isinstance(content, str) or not content.strip():
            return
        if not isinstance(summary, str) or not summary.strip():
            summary = content[:_SUMMARY_MAX_CHARS]
        memory = _new_memory(
            content.strip(), "encounter", summary.strip(), MemoryType.SHORT_TERM
        )
        await self._persist_memory(memory, event.correlation_id)
```

## `nyx/main.py`（增量）

**import**：`from nyx.encounter.facade import EncounterFacade`。

**`_App` dataclass**：`expression` 与 `evaluator` 之间追加 `encounter: EncounterFacade`。

**`build_app_context`**：`inner_life` 构造 + `state_holder.append(...)` 之后、`expression` 构造前后追加：

```python
    encounter = EncounterFacade(bus, llm, evaluator, _get_state, canon)
```

并把 `encounter=encounter` 传入 `_App(...)`。

**`_on_clock_tick`** 的 `SCHEDULE_BLOCK_START` 分支追加：

```python
    if tick_type is TickType.SCHEDULE_BLOCK_START:
        await app.activity.on_tick(tick_type)
        await _check_encounter(app)          # 活动启动后掷骰（包装）
```

**新增 `_check_encounter`**（`_check_initiate_chat` 旁）：

```python
async def _check_encounter(app: _App) -> None:
    """块边界随机事件：活动启动后掷骰，命中则生成遭遇（包装，不打断活动）。"""
    online = app.last_presence in ("online", "busy")
    busy = app.last_presence == "busy"
    await app.encounter.try_block_boundary(online, busy)
```

**`_subscribe`** 追加四条：

```python
    bus.subscribe(EventType.ENCOUNTER_END, app.inner_life.apply_event)
    bus.subscribe(EventType.ENCOUNTER_END, app.desire.add_value)
    bus.subscribe(EventType.ENCOUNTER_END, app.memory.remember_encounter)
    bus.subscribe(EventType.ACTIVITY_END, app.encounter.on_activity_end)
```

**`build_app`** 追加 payload 与两个端点（`_AnnotationPayload` 旁放 payload 类）：

```python
class _EncounterChoosePayload(BaseModel):
    encounter_id: str
    option_index: int
```

```python
    @fast.post("/api/encounter/choose")
    async def api_encounter_choose(
        payload: _EncounterChoosePayload,
    ) -> dict[str, Any]:
        result = await app.encounter.choose(payload.encounter_id, payload.option_index)
        if result is None:
            raise HTTPException(status_code=409, detail="遭遇不存在或已结束")
        return {"encounter_id": result.id, "chosen": result.chosen_index}

    @fast.get("/api/encounter/current")
    async def api_encounter_current() -> dict[str, Any] | None:
        return app.encounter.get_current()
```

## 测试要点

目录 `tests/test_encounter/`。

### `test_rules.py`（纯函数，无 LLM/IO）

- `test_should_encounter_true`：`online=True, busy=False, energy=50, since_last=1000` → True（方向：功能正确）
- `test_should_encounter_offline`：`online=False` → False（边界鲁棒）
- `test_should_encounter_busy`：`busy=True` → False（边界鲁棒）
- `test_should_encounter_low_energy`：`energy=29.9` → False（边界鲁棒）
- `test_should_encounter_cooldown`：`since_last=899` → False（边界鲁棒）
- `test_consequence_for_each_tone`：四 tone 各返回含 `energy_delta`/`emotion_shift` 键、数值吻合表（功能正确）
- `test_consequence_for_isolated`：`consequence_for(tone)["emotion_shift"] is not _CONSEQUENCES[...]`（改返回不改表；回归保护）
- `test_ending_for_each_tone`：四 tone 各非空字符串（功能正确）
- `test_growth_milestone_key_book_finished`：`{"type":"reading","result":{"completed":True}}` → `"book_finished"`（功能正确）
- `test_growth_milestone_key_non_reading`：`type="creation"` → None（边界鲁棒）
- `test_growth_milestone_key_not_completed`：`reading` 但 `completed:False` → None（边界鲁棒）
- `test_growth_memory_contains_choice`：content 含 option.text、summary 非空（功能正确）

### `test_facade.py`（mock LLM + fake evaluator + 真 EventBus）

- `test_parse_encounter_valid`：合法 JSON → (text, 2 选项)（功能正确）
- `test_parse_encounter_missing_text` / `_too_few_options` / `_too_many_options` / `_bad_tone` / `_option_not_dict`：各 raise ValueError（边界鲁棒）
- `test_try_block_boundary_rolls_and_starts`：monkeypatch `random.random→0.0` + `should_encounter` 前提满足 + fake LLM 返回 fixture → `_current` 非 None、发布一条 `ENCOUNTER_START`、content 无 `tone` 键（功能正确）
- `test_try_block_boundary_skips_when_current`：`_current` 已设 → 不生成（回归保护）
- `test_choose_applies_and_ends`：预置 `_current` + choose(0) → 返回 encounter、`_current` 清空、发布 `ENCOUNTER_CHOICE` + `ENCOUNTER_END`、END content 含 `ending` + `consequences`（功能正确）
- `test_choose_growth_attaches_memory`：`kind=GROWTH_MOMENT` + choose → END content 的 `consequences` 含 `memory`（功能正确）
- `test_choose_wrong_id_returns_none` / `_bad_index_returns_none`：返回 None 且 `_current` 保留（边界鲁棒）
- `test_on_activity_end_milestone`：`ACTIVITY_END`（reading+completed）→ 生成成长时刻、`_celebrated` 记 key（功能正确）
- `test_on_activity_end_non_milestone`：`ACTIVITY_END`（creation）→ 不生成（边界鲁棒）
- `test_start_llm_failure_no_crash`：fake LLM 抛异常 → 不设 `_current`、不抛（边界鲁棒）

## 文档同步

- `docs/specs/18-api.md`：端点计数 15 → 17（新增 `POST /api/encounter/choose`、`GET /api/encounter/current`），补两张端点说明。
- `docs/test-inventory.md`：按系统（encounter）/方向/阶段追加上述测试（见 CLAUDE.md Part 3「每次编写测试后更新」）。
- `docs/design/raising-sim.md` §3.2 触发点 1（欲望搭话 = 前端重分类）与触发点 3（活动中遭遇推迟）已在本 spec 落地为「后端不动 / 推迟」，无需回改 design。
