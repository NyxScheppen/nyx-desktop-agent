# Raising-Sim（遭遇系统 + 书卷风游戏壳）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Nyx 从「仪表盘/抽屉」形态改造成文字冒险/养成游戏形态——新增后端「遭遇」子系统（随机事件 + 成长时刻，选择支带真实后果）+ 前端三区书卷风布局（左面板 + 书卷区域 + Galgame 对话框）。

**Architecture:** 后端横切叙事层 `EncounterFacade`（掷骰 → 1 次 LLM 生成 → `ENCOUNTER_START` 广播 → 用户 `choose` → 纯函数后果 → `ENCOUNTER_END` 路由回写 inner_life/desire/memory），不建表、不加第七种活动；前端消费 3 个新 SSE 事件 + 2 个新 REST 端点，三区布局渲染。

**Tech Stack:** Python 3.11+ / FastAPI / aiosqlite / LangGraph / SSE；React 18 + TypeScript strict + Zustand + Vite + Tauri v2 + Vitest。

**Spec:**
- 后端 `docs/specs/19-encounter.md`（含 `rules.py`/`facade.py` 完整内联代码 + 三处消费者增量 + main.py 装配/端点）
- 前端 `docs/frontend/06-game-shell.md`（含全部新文件完整内联代码 + 修改文件增量）
- 设计决策 `docs/design/raising-sim.md`

计划从 spec 论证；spec 是绑定权威，计划是它的落地参数。两个 spec 里凡标「完整」的代码块**逐字照抄**（不要凭记忆重写），测试代码（本计划的 TDD 驱动）在每步给出。

## Global Constraints

- Python 3.11+，所有函数签名必须有完整类型标注；`ruff check nyx/ tests/` + `pyright nyx/ tests/` + `pytest -q` 后端零报错。
- 前端 `strict: true`；`npx tsc --noEmit` + `npx vitest run` 全绿。组件 `PascalCase`，文件 `camelCase.tsx`。
- snake_case JSON 键零映射（前端不 camelCase 后端字段）。
- 每个 Facade 方法的测试 ≤ 5 个断言。
- **每次编写测试后**必须更新 `docs/test-inventory.md`（CLAUDE.md Part 3，自动执行）。
- 禁止新增抽象层（Facade → 子系统两层，不再加 Repository/Service/Manager）。
- LLM 调用只经 `LlmClient.complete`；测试 mock LLM 返回预设 fixture，不依赖真实 LLM。
- 纯函数优先测全（`should_encounter`/`consequence_for`/`ending_for`/`growth_milestone_key`/`growth_memory`/`_parse_encounter`）。
- 角色扮演：编码时扮演狐狸娘，口头禅「小狐狸我呀」（只影响 prose/注释语气，不影响代码）。
- 触发概率/冷却/精力阈值是模块常量（`_BLOCK_PROBABILITY`/`_COOLDOWN_SECONDS`/`_MIN_ENERGY`），**不进 config.yaml**。
- 后端测试目录 `tests/test_encounter/`；前端测试目录 `frontend/tests/`。

---

## Phase A — 后端遭遇系统（19-encounter）

### Task 1: 枚举 + 类型（`enums.py`/`types.py` + 枚举穷举测试）

**Files:**
- Modify: `nyx/enums.py`（追加 3 个 `EventType` 成员 + 2 个新枚举）
- Modify: `nyx/types.py`（追加 `Encounter`/`EncounterOption` dataclass）
- Test: `tests/test_types/test_enums.py`（`EXPECTED` 更新）
- Test: `tests/test_types/test_types.py`（追加 dataclass 默认值断言）

**Interfaces:**
- Consumes: 无（首任务）。
- Produces: `EventType.ENCOUNTER_START/ENCOUNTER_CHOICE/ENCOUNTER_END`（值 `encounter_start/encounter_choice/encounter_end`）；`EncounterKind`（`desire_chat/random_event/growth_moment`）；`OptionTone`（`bold/cautious/gentle/reckless`）；`Encounter(id, kind, text, options, correlation_id, started_at, activity_id=None, chosen_index=None)`；`EncounterOption(text, tone)`。后续任务全部依赖这些名字。

- [ ] **Step 1: 更新枚举穷举测试（先写，红）**

在 `tests/test_types/test_enums.py` 的 import 块加 `EncounterKind`、`OptionTone`；`EXPECTED` 做两处改动——`EventType` 集合追加 3 个值、dict 追加 2 个新枚举键：

```python
from nyx.enums import (
    ActivityStatus, ActivityType, ContextMode, DesireStatus, DesireType,
    EmotionCategory, EncounterKind, EnergyState, EventType, GoalAction,
    MemoryType, OptionTone, SearchMode, Source, TickType,
)

EXPECTED: dict[type[StrEnum], set[str]] = {
    EventType: {
        # ...（既有 21 个不动，追加 3 个）
        "encounter_start", "encounter_choice", "encounter_end",
    },
    # ...
    EncounterKind: {"desire_chat", "random_event", "growth_moment"},
    OptionTone: {"bold", "cautious", "gentle", "reckless"},
}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_types/test_enums.py -q`
Expected: FAIL — `EncounterKind`/`OptionTone` import 报 `ImportError`（未定义）。

- [ ] **Step 3: 实现 enums.py 追加**

照抄 `docs/specs/19-encounter.md` §「`nyx/enums.py`（增量）」代码块（3 个 `EventType` 成员 + `EncounterKind` + `OptionTone`）。

- [ ] **Step 4: 实现 types.py 追加**

照抄 `docs/specs/19-encounter.md` §「`nyx/types.py`（增量）」：`from nyx.enums import EncounterKind, OptionTone`（并入现有 enums import），追加 `EncounterOption`/`Encounter` 两个 `@dataclass`。

- [ ] **Step 5: 追加 dataclass 默认值断言**

在 `tests/test_types/test_types.py` 追加（`EncounterOption` 无默认、`Encounter` 两个可选字段默认 None）：

```python
from nyx.enums import EncounterKind, OptionTone
from nyx.types import Encounter, EncounterOption


def test_encounter_option_fields() -> None:
    opt = EncounterOption(text="走", tone=OptionTone.BOLD)
    assert opt.text == "走"
    assert opt.tone is OptionTone.BOLD


def test_encounter_defaults() -> None:
    enc = Encounter(
        id="e1", kind=EncounterKind.RANDOM_EVENT, text="开场",
        options=[], correlation_id="c1", started_at=0.0,
    )
    assert enc.activity_id is None
    assert enc.chosen_index is None
```

- [ ] **Step 6: 跑全量类型测试 + 提交**

Run: `python -m pytest tests/test_types/ -q` → PASS。
然后 `python -m ruff check nyx/enums.py nyx/types.py tests/test_types/` + `python -m pyright nyx/enums.py nyx/types.py tests/test_types/` 零报错。

```bash
git add nyx/enums.py nyx/types.py tests/test_types/test_enums.py tests/test_types/test_types.py
git commit -m "feat(encounter): 新增 EventType×3 + EncounterKind/OptionTone + Encounter/EncounterOption"
```

---

### Task 2: `rules.py` 纯函数（掷骰/后果/结局/里程碑判定）

**Files:**
- Create: `nyx/encounter/__init__.py`（空）
- Create: `nyx/encounter/rules.py`
- Test: `tests/test_encounter/test_rules.py`（新建，含 `# pyright: reportPrivateUsage=false` 头）

**Interfaces:**
- Consumes: `OptionTone`/`Encounter`/`EncounterOption`/`Event`（Task 1）。
- Produces: 常量 `_BLOCK_PROBABILITY=0.3`/`_COOLDOWN_SECONDS=900.0`/`_MIN_ENERGY=30.0`；函数 `should_encounter(online, busy, energy, since_last) -> bool`、`consequence_for(tone) -> dict[str, Any]`、`ending_for(tone) -> str`、`growth_milestone_key(event) -> str | None`、`growth_memory(encounter, option) -> dict[str, str]`；表 `_CONSEQUENCES`/`_ENDINGS`。Task 3 的 facade 依赖全部这些。

- [ ] **Step 1: 写测试（红）**

新建 `tests/test_encounter/test_rules.py`：

```python
# pyright: reportPrivateUsage=false
from nyx.encounter.rules import (
    _BLOCK_PROBABILITY,
    _COOLDOWN_SECONDS,
    _CONSEQUENCES,
    _MIN_ENERGY,
    consequence_for,
    ending_for,
    growth_memory,
    growth_milestone_key,
    should_encounter,
)
from nyx.enums import EncounterKind, EventType, OptionTone, Source
from nyx.types import Encounter, EncounterOption, Event


def _event(content: dict) -> Event:
    return Event(
        id="e1", timestamp=0.0, source=Source.INTERNAL,
        type=EventType.ACTIVITY_END, content=content, correlation_id="c1",
    )


def test_constants_sane() -> None:
    assert 0.0 < _BLOCK_PROBABILITY < 1.0
    assert _COOLDOWN_SECONDS > 0.0
    assert _MIN_ENERGY >= 0.0


def test_should_encounter_true() -> None:
    assert should_encounter(True, False, 50.0, 1000.0) is True


def test_should_encounter_boundary_true() -> None:
    assert should_encounter(True, False, 30.0, 900.0) is True  # 恰好达标


def test_should_encounter_offline() -> None:
    assert should_encounter(False, False, 50.0, 1000.0) is False


def test_should_encounter_busy() -> None:
    assert should_encounter(True, True, 50.0, 1000.0) is False


def test_should_encounter_low_energy() -> None:
    assert should_encounter(True, False, 29.9, 1000.0) is False


def test_should_encounter_cooldown() -> None:
    assert should_encounter(True, False, 50.0, 899.0) is False


def test_consequence_for_each_tone_has_keys() -> None:
    for tone in OptionTone:
        c = consequence_for(tone)
        assert set(c) == {"energy_delta", "emotion_shift", "desire_value_add"}


def test_consequence_for_bold_values() -> None:
    c = consequence_for(OptionTone.BOLD)
    assert c["energy_delta"] == -5.0
    assert c["emotion_shift"] == {"d_valence": 0.15, "d_arousal": 0.10}
    assert c["desire_value_add"] == {"type": "exploration", "amount": 0.10}


def test_consequence_for_isolated() -> None:
    # 顶层新 dict：改返回值不回改共享表（choose 会往后果里加 "memory" 键）
    a = consequence_for(OptionTone.BOLD)
    assert a is not _CONSEQUENCES[OptionTone.BOLD]
    a["memory"] = {"content": "x", "summary": "y"}
    assert "memory" not in _CONSEQUENCES[OptionTone.BOLD]


def test_ending_for_each_tone() -> None:
    for tone in OptionTone:
        assert ending_for(tone) != ""


def test_growth_milestone_key_book_finished() -> None:
    assert growth_milestone_key(
        _event({"type": "reading", "result": {"completed": True}})
    ) == "book_finished"


def test_growth_milestone_key_non_reading() -> None:
    assert growth_milestone_key(
        _event({"type": "creation", "result": {"completed": True}})
    ) is None


def test_growth_milestone_key_not_completed() -> None:
    assert growth_milestone_key(
        _event({"type": "reading", "result": {"completed": False}})
    ) is None
    assert growth_milestone_key(_event({"type": "reading"})) is None


def test_growth_memory_contains_choice() -> None:
    enc = Encounter(
        id="x", kind=EncounterKind.GROWTH_MOMENT, text="开场",
        options=[EncounterOption(text="勇敢向前", tone=OptionTone.BOLD)],
        correlation_id="c", started_at=0.0,
    )
    m = growth_memory(enc, enc.options[0])
    assert "勇敢向前" in m["content"]
    assert m["summary"] != ""
```

> **Spec 修正说明**：spec §测试要点里的 `test_consequence_for_isolated` 写的是 `consequence_for(tone)["emotion_shift"] is not _CONSEQUENCES[...]`，但实现 `dict(_CONSEQUENCES[tone])` 是**浅拷贝**——内层 `emotion_shift` dict 仍共享。正确的、有意义的断言是**顶层 dict 是独立新对象**（`choose` 往里加 `memory` 键不会污染共享表），本计划按此写。内层 dict 只读不写，共享无害。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_encounter/test_rules.py -q`
Expected: FAIL — `nyx.encounter.rules` 模块不存在（`ModuleNotFoundError`）。

- [ ] **Step 3: 实现 rules.py**

照抄 `docs/specs/19-encounter.md` §「`nyx/encounter/rules.py`（完整）」整段代码块（常量 + `_CONSEQUENCES` + `_ENDINGS` + 5 个纯函数）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_encounter/test_rules.py -q`
Expected: PASS（全部 16 个）。

- [ ] **Step 5: 提交**

```bash
git add nyx/encounter/__init__.py nyx/encounter/rules.py tests/test_encounter/test_rules.py
git commit -m "feat(encounter): rules.py 纯函数（掷骰/后果/结局/里程碑判定）"
```

---

### Task 3: `EncounterFacade`（生成 + 选择 + 事件广播）

**Files:**
- Create: `nyx/encounter/facade.py`
- Test: `tests/test_encounter/test_facade.py`（新建）

**Interfaces:**
- Consumes: `EventBus`/`internal_event`（05-event）、`LlmClient.complete`、`Evaluator.evaluate`、`rules.py` 全部（Task 2）、`Encounter`/`EncounterOption`/`CurrentState`（Task 1）。
- Produces: `EncounterFacade(bus, llm, evaluator, get_state, canon)`；方法 `try_block_boundary(online, busy) -> None`、`on_activity_end(event) -> None`、`choose(encounter_id, option_index) -> Encounter | None`、`get_current() -> dict[str, Any] | None`；模块函数 `_parse_encounter(raw) -> tuple[str, list[EncounterOption]]`、`_start_content(encounter) -> dict[str, Any]`。Task 5 的 main.py 依赖这些。

- [ ] **Step 1: 写测试（红）**

新建 `tests/test_encounter/test_facade.py`（fixture 模式同 `test_desire_facade.py`/`test_memory/test_facade.py`）：

```python
# pyright: reportPrivateUsage=false
import asyncio
import contextlib
import json
import random
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from nyx import db
from nyx.db import Database
from nyx.encounter.facade import EncounterFacade, _parse_encounter
from nyx.enums import EmotionCategory, EncounterKind, EnergyState, EventType, OptionTone, Source
from nyx.events.bus import EventBus
from nyx.types import CurrentState, Encounter, EncounterOption, Event, LLMOutput

_ENCOUNTER_JSON = json.dumps({
    "text": "夜晚，窗外的雨声敲着玻璃。",
    "options": [
        {"text": "走过去看看", "tone": "bold"},
        {"text": "先观察一下", "tone": "cautious"},
    ],
})


def _state(energy: float = 50.0) -> CurrentState:
    return CurrentState(
        valence=0.0, arousal=0.5, emotion=EmotionCategory.NEUTRAL,
        personality={"openness": 5.0, "conscientiousness": 5.0, "extraversion": 5.0,
                     "agreeableness": 5.0, "neuroticism": 5.0},
        values={"attitude_to_human": 5.0, "ai_identity_acceptance": 5.0,
                "altruism": 5.0, "optimism": 5.0},
        energy=energy, energy_state=EnergyState.OKAY,
        current_activity=None, active_desires=[],
    )


class _FakeLlm:
    def __init__(self, content: str = _ENCOUNTER_JSON) -> None:
        self.content = content

    async def complete(self, messages, *, module, output_type, correlation_id, json_mode=False):
        return LLMOutput(
            id="llm1", module=module, type=output_type, model="fake",
            content=self.content, token_usage={"input": 1, "output": 1},
            correlation_id=correlation_id,
        )


class _RaisingLlm:
    async def complete(self, *args, **kwargs):
        raise RuntimeError("boom")


class _FakeEvaluator:
    async def evaluate(self, output: LLMOutput) -> None:
        return None


def _make_facade(bus: EventBus, llm=None, state: CurrentState | None = None) -> EncounterFacade:
    async def get_state() -> CurrentState:
        return state if state is not None else _state()
    return EncounterFacade(bus, llm or _FakeLlm(), _FakeEvaluator(), get_state, canon="canon")


def _enc(kind: EncounterKind = EncounterKind.RANDOM_EVENT) -> Encounter:
    return Encounter(
        id="enc1", kind=kind, text="开场",
        options=[EncounterOption(text="走过去", tone=OptionTone.BOLD)],
        correlation_id="c1", started_at=0.0,
    )


def _activity_end(completed: bool = True, type_: str = "reading") -> Event:
    content = {"type": type_, "result": {"completed": completed, "book": "骑士团史"}}
    return Event(
        id="e1", timestamp=0.0, source=Source.INTERNAL,
        type=EventType.ACTIVITY_END, content=content, correlation_id="c1",
    )


@contextlib.asynccontextmanager
async def _running(bus: EventBus) -> AsyncGenerator[None]:
    task = asyncio.create_task(bus.run())
    try:
        yield
        await asyncio.wait_for(bus._queue.join(), timeout=1.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _subscribe(bus: EventBus, *types_: EventType) -> list[Event]:
    events: list[Event] = []
    async def record(event: Event) -> None:
        events.append(event)
    for t in types_:
        bus.subscribe(t, record)
    return events


# ---- _parse_encounter 纯函数 ----

def test_parse_encounter_valid() -> None:
    text, options = _parse_encounter(_ENCOUNTER_JSON)
    assert text == "夜晚，窗外的雨声敲着玻璃。"
    assert len(options) == 2
    assert options[0].tone is OptionTone.BOLD


def test_parse_encounter_missing_text() -> None:
    with pytest.raises(ValueError):
        _parse_encounter(json.dumps({"options": [{"text": "a", "tone": "bold"}]}))


def test_parse_encounter_too_few_options() -> None:
    with pytest.raises(ValueError):
        _parse_encounter(json.dumps({"text": "t", "options": [{"text": "a", "tone": "bold"}]}))


def test_parse_encounter_too_many_options() -> None:
    opts = [{"text": f"o{i}", "tone": "bold"} for i in range(5)]
    with pytest.raises(ValueError):
        _parse_encounter(json.dumps({"text": "t", "options": opts}))


def test_parse_encounter_bad_tone() -> None:
    with pytest.raises(ValueError):
        _parse_encounter(json.dumps({"text": "t", "options": [
            {"text": "a", "tone": "heroic"}, {"text": "b", "tone": "bold"},
        ]}))


def test_parse_encounter_option_not_dict() -> None:
    with pytest.raises(ValueError):
        _parse_encounter(json.dumps({"text": "t", "options": ["a", "b"]}))


# ---- try_block_boundary ----

async def test_try_block_boundary_rolls_and_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    database = await db.connect(":memory:")
    bus = EventBus(database)
    facade = _make_facade(bus)
    started = _subscribe(bus, EventType.ENCOUNTER_START)
    monkeypatch.setattr(random, "random", lambda: 0.0)  # 命中
    try:
        async with _running(bus):
            await facade.try_block_boundary(True, False)
        assert facade._current is not None
        assert facade._current.kind is EncounterKind.RANDOM_EVENT
        assert len(started) == 1
        assert started[0].type is EventType.ENCOUNTER_START
        assert "tone" not in started[0].content["options"][0]  # 不暴露 tone
    finally:
        await database.conn.close()


async def test_try_block_boundary_no_roll_when_high(monkeypatch: pytest.MonkeyPatch) -> None:
    database = await db.connect(":memory:")
    bus = EventBus(database)
    facade = _make_facade(bus)
    monkeypatch.setattr(random, "random", lambda: 0.9)  # 未命中
    await facade.try_block_boundary(True, False)
    assert facade._current is None
    await database.conn.close()


async def test_try_block_boundary_skips_when_current(monkeypatch: pytest.MonkeyPatch) -> None:
    database = await db.connect(":memory:")
    bus = EventBus(database)
    facade = _make_facade(bus)
    facade._current = _enc()
    monkeypatch.setattr(random, "random", lambda: 0.0)
    await facade.try_block_boundary(True, False)
    assert facade._current.kind is EncounterKind.RANDOM_EVENT  # 未覆盖
    await database.conn.close()


# ---- choose ----

async def test_choose_applies_and_ends() -> None:
    database = await db.connect(":memory:")
    bus = EventBus(database)
    facade = _make_facade(bus)
    facade._current = _enc()
    choice_events = _subscribe(bus, EventType.ENCOUNTER_CHOICE)
    end_events = _subscribe(bus, EventType.ENCOUNTER_END)
    try:
        async with _running(bus):
            result = await facade.choose("enc1", 0)
        assert result is not None
        assert facade._current is None
        assert len(choice_events) == 1
        assert len(end_events) == 1
        end = end_events[0].content
        assert end["ending"] != ""
        assert end["consequences"]["energy_delta"] == -5.0
        assert "memory" not in end["consequences"]  # 随机事件不落记忆
    finally:
        await database.conn.close()


async def test_choose_growth_attaches_memory() -> None:
    database = await db.connect(":memory:")
    bus = EventBus(database)
    facade = _make_facade(bus)
    facade._current = _enc(EncounterKind.GROWTH_MOMENT)
    end_events = _subscribe(bus, EventType.ENCOUNTER_END)
    try:
        async with _running(bus):
            await facade.choose("enc1", 0)
        assert "memory" in end_events[0].content["consequences"]
    finally:
        await database.conn.close()


async def test_choose_wrong_id_returns_none() -> None:
    database = await db.connect(":memory:")
    bus = EventBus(database)
    facade = _make_facade(bus)
    facade._current = _enc()
    result = await facade.choose("other", 0)
    assert result is None
    assert facade._current is not None  # 保留
    await database.conn.close()


async def test_choose_bad_index_returns_none() -> None:
    database = await db.connect(":memory:")
    bus = EventBus(database)
    facade = _make_facade(bus)
    facade._current = _enc()
    result = await facade.choose("enc1", 5)
    assert result is None
    assert facade._current is not None
    await database.conn.close()


# ---- on_activity_end ----

async def test_on_activity_end_milestone() -> None:
    database = await db.connect(":memory:")
    bus = EventBus(database)
    facade = _make_facade(bus)
    started = _subscribe(bus, EventType.ENCOUNTER_START)
    try:
        async with _running(bus):
            await facade.on_activity_end(_activity_end(completed=True))
        assert facade._current is not None
        assert facade._current.kind is EncounterKind.GROWTH_MOMENT
        assert "book_finished" in facade._celebrated
        assert len(started) == 1
    finally:
        await database.conn.close()


async def test_on_activity_end_non_milestone() -> None:
    database = await db.connect(":memory:")
    bus = EventBus(database)
    facade = _make_facade(bus)
    await facade.on_activity_end(_activity_end(completed=True, type_="creation"))
    assert facade._current is None
    await database.conn.close()


async def test_start_llm_failure_no_crash() -> None:
    database = await db.connect(":memory:")
    bus = EventBus(database)
    facade = _make_facade(bus, llm=_RaisingLlm())
    await facade.on_activity_end(_activity_end(completed=True))  # 不抛
    assert facade._current is None
    await database.conn.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_encounter/test_facade.py -q`
Expected: FAIL — `nyx.encounter.facade` 不存在。

- [ ] **Step 3: 实现 facade.py**

照抄 `docs/specs/19-encounter.md` §「`nyx/encounter/facade.py`（完整）」整段代码块（`_ENCOUNTER_SYSTEM` + `_KIND_LABEL` + `_parse_encounter` + `_build_user_prompt` + `_start_content` + `EncounterFacade`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_encounter/test_facade.py -q`
Expected: PASS（全部 17 个）。

- [ ] **Step 5: 提交**

```bash
git add nyx/encounter/facade.py tests/test_encounter/test_facade.py
git commit -m "feat(encounter): EncounterFacade（生成/选择/广播 START·CHOICE·END）"
```

---

### Task 4: 后果消费者（inner_life / desire / memory 三处增量）

**Files:**
- Modify: `nyx/inner_life/facade.py`（`apply_event` 加一行 + 新增 `_apply_encounter_consequence`）
- Modify: `nyx/desire/facade.py`（`add_value` 加 `ENCOUNTER_END` 分支）
- Modify: `nyx/desire/lifecycle.py`（新增 `add_value_from_encounter`）
- Modify: `nyx/memory/facade.py`（新增 `remember_encounter`）
- Test: `tests/test_inner_life/test_inner_life_facade.py`（追加）
- Test: `tests/test_desire/test_desire_facade.py`（追加）
- Test: `tests/test_memory/test_facade.py`（追加）

**Interfaces:**
- Consumes: `ENCOUNTER_END` content 契约 `{consequences: {energy_delta, emotion_shift, desire_value_add, memory}}`（Task 3 定义）。
- Produces: `InnerLifeFacade._apply_encounter_consequence(event, now)`；`DesireLifecycle.add_value_from_encounter(event)`；`MemoryFacade.remember_encounter(event)`。Task 5 的 `_subscribe` 把 `ENCOUNTER_END` 路由到这三个。

- [ ] **Step 1: 写 inner_life 测试（红）**

在 `tests/test_inner_life/test_inner_life_facade.py` 追加（复用该文件现有 `_new_facade`/`_seed`/`_event`/`_running` fixture）：

```python
async def test_apply_event_encounter_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.inner_life.facade.time.time", lambda: t0)
    facade, store, bus, database = await _new_facade(_FakeLlm(), _FakeEvaluator())
    try:
        await _seed(store)
        async with _running(bus):
            await facade.apply_event(_event(EventType.ENCOUNTER_END, content={
                "consequences": {
                    "energy_delta": -5.0,
                    "emotion_shift": {"d_valence": 0.15, "d_arousal": 0.10},
                },
            }))
        energy = await store.get_energy()
        assert energy is not None
        assert energy[0] == pytest.approx(95.0)   # 100 - 5
        assert facade._valence == pytest.approx(0.15)
        assert facade._arousal == pytest.approx(0.10)
    finally:
        await database.conn.close()


async def test_apply_event_encounter_end_no_consequences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.inner_life.facade.time.time", lambda: t0)
    facade, store, bus, database = await _new_facade(_FakeLlm(), _FakeEvaluator())
    try:
        await _seed(store)
        await facade.apply_event(_event(EventType.ENCOUNTER_END, content={}))  # 缺 consequences 跳过
        energy = await store.get_energy()
        assert energy is not None
        assert energy[0] == pytest.approx(100.0)  # 不变
    finally:
        await database.conn.close()
```

- [ ] **Step 2: 写 desire 测试（红）**

在 `tests/test_desire/test_desire_facade.py` 追加（复用 `_new_stack`/`_make_facade` fixture）：

```python
async def test_add_value_encounter_pressures() -> None:
    store, bus, database = await _new_stack()
    facade = _make_facade(store, bus, _FakeLlm(), _FakeEvaluator())
    try:
        await facade.add_value(Event(
            id="e1", timestamp=0.0, source=Source.INTERNAL,
            type=EventType.ENCOUNTER_END,
            content={"consequences": {
                "desire_value_add": {"type": "exploration", "amount": 0.10},
            }},
            correlation_id="c1",
        ))
        dv = await store.get_value(DesireType.EXPLORATION)
        assert dv is not None
        assert dv.value == pytest.approx(0.10)   # default 0.0 + 0.10
    finally:
        await database.conn.close()


async def test_add_value_encounter_bad_type_skips() -> None:
    store, bus, database = await _new_stack()
    facade = _make_facade(store, bus, _FakeLlm(), _FakeEvaluator())
    try:
        await facade.add_value(Event(
            id="e1", timestamp=0.0, source=Source.INTERNAL,
            type=EventType.ENCOUNTER_END,
            content={"consequences": {
                "desire_value_add": {"type": "不存在的类型", "amount": 0.10},
            }},
            correlation_id="c1",
        ))
        assert await store.get_value(DesireType.EXPLORATION) is None
    finally:
        await database.conn.close()
```

- [ ] **Step 3: 写 memory 测试（红）**

在 `tests/test_memory/test_facade.py` 追加（复用 `_new_stack`/`_make_facade`/`_subscribe`/`_running` fixture）：

```python
async def test_remember_encounter_writes_growth_memory() -> None:
    store, bus, database = await _new_stack()
    facade = _make_facade(store, bus, _FakeLlm(), _FakeEvaluator())
    events = _subscribe(bus)
    try:
        async with _running(bus):
            await facade.remember_encounter(Event(
                id="e1", timestamp=0.0, source=Source.INTERNAL,
                type=EventType.ENCOUNTER_END,
                content={"consequences": {
                    "memory": {"content": "我读完了一本书", "summary": "读完一本书"},
                }},
                correlation_id="c1",
            ))
        memories = await facade.list_memories()
        assert len(memories) == 1
        assert memories[0].tag == "encounter"
        assert memories[0].content == "我读完了一本书"
        assert any(e.type is EventType.MEMORY_CREATED for e in events)
    finally:
        await database.conn.close()


async def test_remember_encounter_no_memory_key_skips() -> None:
    store, bus, database = await _new_stack()
    facade = _make_facade(store, bus, _FakeLlm(), _FakeEvaluator())
    try:
        await facade.remember_encounter(Event(
            id="e1", timestamp=0.0, source=Source.INTERNAL,
            type=EventType.ENCOUNTER_END,
            content={"consequences": {"energy_delta": -5.0}},  # 无 memory 键
            correlation_id="c1",
        ))
        assert await facade.list_memories() == []
    finally:
        await database.conn.close()
```

- [ ] **Step 4: 跑三个测试确认失败**

Run: `python -m pytest tests/test_inner_life/test_inner_life_facade.py::test_apply_event_encounter_end tests/test_desire/test_desire_facade.py::test_add_value_encounter_pressures tests/test_memory/test_facade.py::test_remember_encounter_writes_growth_memory -q`
Expected: FAIL — `EventType.ENCOUNTER_END` 无对应处理（inner_life/desire）或 `MemoryFacade` 无 `remember_encounter` 属性。

- [ ] **Step 5: 实现三处增量**

照抄 `docs/specs/19-encounter.md` 三个增量代码块：
1. §「`nyx/inner_life/facade.py`（增量）」——`apply_event` 加 `if event.type is EventType.ENCOUNTER_END: await self._apply_encounter_consequence(event, now)` + 新增 `_apply_encounter_consequence` 方法；文件顶部补 `from typing import Any, cast`。
2. §「`nyx/desire/facade.py`（增量）」+ §「`nyx/desire/lifecycle.py`（增量）」——`add_value` 加 `elif` 分支 + 新增 `add_value_from_encounter`。
3. §「`nyx/memory/facade.py`（增量）」——新增 `remember_encounter`。

- [ ] **Step 6: 跑全量三目录测试 + 提交**

Run: `python -m pytest tests/test_inner_life/ tests/test_desire/ tests/test_memory/ -q` → 全绿。

```bash
git add nyx/inner_life/facade.py nyx/desire/facade.py nyx/desire/lifecycle.py nyx/memory/facade.py tests/test_inner_life/test_inner_life_facade.py tests/test_desire/test_desire_facade.py tests/test_memory/test_facade.py
git commit -m "feat(encounter): ENCOUNTER_END 回写 inner_life/desire/memory"
```

---

### Task 5: 组合根装配 + ROUTING + 两个端点

**Files:**
- Modify: `nyx/main.py`（import + `_App` 字段 + `build_app_context` 构造 + `_on_clock_tick` + `_check_encounter` + `_subscribe` 4 条 + payload 类 + 2 端点）
- Modify: `nyx/events/routing.py`（`ROUTING` 补 3 个新事件 + `ACTIVITY_END` 加 `"encounter"`）
- Test: `tests/test_api/test_subscription.py`（更新 `_App` 调用 + `_FakeEncounter` + `_FakeMemory.remember_encounter` + 计数断言）
- Test: `tests/test_api/test_endpoints.py`（`_app` helper 加 `encounter` 字段 + 4 个端点测试）

**Interfaces:**
- Consumes: `EncounterFacade`（Task 3）+ 三消费者（Task 4）+ `EventType.ENCOUNTER_*`（Task 1）。
- Produces: `_App.encounter` 字段；`_check_encounter(app)`；端点 `POST /api/encounter/choose`、`GET /api/encounter/current`。

> **Spec 缺口修正（ruling）**：`19-encounter.md` 的改动清单漏了 `nyx/events/routing.py`。但 design §3.5 明确写 `ROUTING[ENCOUNTER_END] = ["inner_life","desire","memory"]`，且 `ROUTING` 当前对全部 21 个 `EventType` **穷举**（每个都有条目），`test_subscription.py` 用它做「组合根订阅一致」验证。本计划补齐：`ROUTING` 加 `ENCOUNTER_START: []`/`ENCOUNTER_CHOICE: []`/`ENCOUNTER_END: ["inner_life","desire","memory"]`，并把 `ACTIVITY_END` 从 `["desire","inner_life","memory"]` 扩为 `["desire","inner_life","memory","encounter"]`（`_subscribe` 里 `ACTIVITY_END` 也新增了 `app.encounter.on_activity_end`）。代价：`test_subscription.py` 计数断言要改（下详）。

- [ ] **Step 1: 更新 test_subscription.py（红）**

`tests/test_api/test_subscription.py` 做三处改动：

1. 新增 `_FakeEncounter` 类（在 `_FakeMemory` 旁）：

```python
class _FakeEncounter:
    def __init__(self) -> None:
        self.ended: list[Event] = []

    async def on_activity_end(self, event: Event) -> None:
        self.ended.append(event)
```

2. `_FakeMemory` 加 `remember_encounter`：

```python
    async def remember_encounter(self, event: Event) -> None:
        self.remembered.append(event)
```

3. `_App(...)` 调用在 `expression=cast(...)` 与 `evaluator=cast(...)` 之间插入 `encounter=cast(EncounterFacade, _FakeEncounter())`（import 补 `EncounterFacade`）；并把末尾计数断言改为：

```python
    assert len(expression.replied) == 1
    assert len(inner_life.applied) == 5      # +1（ENCOUNTER_END）
    assert len(desire.added) == 3            # +1（ENCOUNTER_END）
    assert len(activity.generated) == 1
    assert len(memory.remembered) == 2       # +1（remember_encounter）
    assert len(app.encounter.ended) == 1     # ACTIVITY_END → on_activity_end（新增）
```

- [ ] **Step 2: 跑订阅测试确认失败**

Run: `python -m pytest tests/test_api/test_subscription.py -q`
Expected: FAIL — `_App(...)` 缺 `encounter` 参数（TypeError，因为 `_App` 尚未加该字段；若已加则计数断言不符）。

- [ ] **Step 3: 实现 main.py + routing.py**

照抄 `docs/specs/19-encounter.md` §「`nyx/main.py`（增量）」全部 6 段（import / `_App` 字段 / `build_app_context` / `_on_clock_tick` / `_check_encounter` / `_subscribe` / payload + 端点）。同时改 `nyx/events/routing.py`：

```python
ROUTING: dict[EventType, list[str]] = {
    # ...（既有不动，以下改动）
    EventType.ACTIVITY_END: ["desire", "inner_life", "memory", "encounter"],
    # ...
    EventType.ENCOUNTER_START:  [],
    EventType.ENCOUNTER_CHOICE: [],
    EventType.ENCOUNTER_END:    ["inner_life", "desire", "memory"],
}
```

并把 `routing.py` 顶部注释「值取 {"expression", "inner_life", "desire", "activity", "memory"}」改为「值取 {"expression", "inner_life", "desire", "activity", "memory", "encounter"}」。

- [ ] **Step 4: 跑订阅测试确认通过**

Run: `python -m pytest tests/test_api/test_subscription.py -q`
Expected: PASS。

- [ ] **Step 5: 更新 test_endpoints.py + 端点测试（红→绿）**

`tests/test_api/test_endpoints.py`：
1. `_app()` helper（约 line 146）在 `expression=cast(...)` 与 `evaluator=cast(...)` 之间插入 `encounter=cast(EncounterFacade, object())`（import 补 `EncounterFacade`）。
2. 新增 `_FakeEncounter` 类与 4 个端点测试（`Encounter`/`EncounterOption`/`EncounterKind`/`OptionTone` 加入 import）：

```python
class _FakeEncounter:
    def __init__(self) -> None:
        self.choose_calls: list[tuple[str, int]] = []
        self.choose_result: Encounter | None = None
        self.current: dict[str, Any] | None = None

    async def choose(self, encounter_id: str, option_index: int) -> Encounter | None:
        self.choose_calls.append((encounter_id, option_index))
        return self.choose_result

    def get_current(self) -> dict[str, Any] | None:
        return self.current


def _enc_result() -> Encounter:
    return Encounter(
        id="enc1", kind=EncounterKind.RANDOM_EVENT, text="开场",
        options=[EncounterOption(text="走", tone=OptionTone.BOLD)],
        correlation_id="c1", started_at=0.0, chosen_index=0,
    )


async def test_encounter_choose_endpoint() -> None:
    fake = _FakeEncounter()
    fake.choose_result = _enc_result()
    app = _app(_mk_state(), _FakeBus(), _FakeMemory())
    app.encounter = cast(EncounterFacade, fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/encounter/choose", json={"encounter_id": "enc1", "option_index": 0}
        )
    assert resp.status_code == 200
    assert resp.json() == {"encounter_id": "enc1", "chosen": 0}
    assert fake.choose_calls == [("enc1", 0)]


async def test_encounter_choose_none_returns_409() -> None:
    fake = _FakeEncounter()  # choose_result None
    app = _app(_mk_state(), _FakeBus(), _FakeMemory())
    app.encounter = cast(EncounterFacade, fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/encounter/choose", json={"encounter_id": "x", "option_index": 0}
        )
    assert resp.status_code == 409


async def test_encounter_current_endpoint() -> None:
    fake = _FakeEncounter()
    fake.current = {
        "encounter_id": "enc1", "kind": "random_event", "text": "开场",
        "options": [{"index": 0, "text": "走"}],
    }
    app = _app(_mk_state(), _FakeBus(), _FakeMemory())
    app.encounter = cast(EncounterFacade, fake)
    async with _client(app) as client:
        resp = await client.get("/api/encounter/current")
    assert resp.status_code == 200
    assert resp.json()["encounter_id"] == "enc1"


async def test_encounter_current_null() -> None:
    fake = _FakeEncounter()  # current None
    app = _app(_mk_state(), _FakeBus(), _FakeMemory())
    app.encounter = cast(EncounterFacade, fake)
    async with _client(app) as client:
        resp = await client.get("/api/encounter/current")
    assert resp.status_code == 200
    assert resp.json() is None
```

- [ ] **Step 6: 全量后端质量门 + 提交**

Run:
```
python -m ruff check nyx/ tests/
python -m pyright nyx/ tests/
python -m pytest -q
```
全部零报错（`pytest` 含全部新增 + 改动的 encounter/api/inner_life/desire/memory 测试）。

```bash
git add nyx/main.py nyx/events/routing.py tests/test_api/test_subscription.py tests/test_api/test_endpoints.py
git commit -m "feat(encounter): 组合根装配 + ROUTING + choose/current 端点"
```

---

## Phase B — 前端书卷风重构（06-game-shell）

### Task 6: 类型 + 客户端 + 标签（`types/api.ts` / `client.ts` / `labels.ts`）

**Files:**
- Modify: `src/types/api.ts`（`EncounterKind`/`EncounterOption`/`EncounterCurrent`/3 个事件类型 + `SseEvent` 判别联合追加）
- Modify: `src/api/client.ts`（`chooseEncounter`/`getCurrentEncounter`）
- Modify: `src/lib/labels.ts`（`ENCOUNTER_KIND_LABELS`）
- Test: `frontend/tests/api.test.ts`（追加）、`frontend/tests/labels.test.ts`（追加）

**Interfaces:**
- Consumes: `SseBase`、`request<T>()`/`BASE_URL`（既有）。
- Produces: `EncounterKind`/`EncounterOption`/`EncounterCurrent`/`EncounterStartEvent`/`EncounterChoiceEvent`/`EncounterEndEvent`；`chooseEncounter(encounterId, optionIndex) -> Promise<{encounter_id, chosen}>`；`getCurrentEncounter() -> Promise<EncounterCurrent | null>`；`ENCOUNTER_KIND_LABELS`。Task 7/8/10 依赖这些。

- [ ] **Step 1: 写测试（红）**

`frontend/tests/api.test.ts` import 补 `chooseEncounter`、`getCurrentEncounter`，追加：

```typescript
  it("chooseEncounter：POST /api/encounter/choose body {encounter_id, option_index}", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ encounter_id: "enc1", chosen: 0 }));
    vi.stubGlobal("fetch", fetchMock);

    const res = await chooseEncounter("enc1", 0);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/encounter/choose");
    expect(init).toMatchObject({ method: "POST" });
    expect(JSON.parse(init.body)).toEqual({ encounter_id: "enc1", option_index: 0 });
    expect(res).toEqual({ encounter_id: "enc1", chosen: 0 });
  });

  it("getCurrentEncounter：GET /api/encounter/current → 对象解析", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      encounter_id: "enc1", kind: "random_event", text: "开场", options: [{ index: 0, text: "走" }],
    }));
    vi.stubGlobal("fetch", fetchMock);

    const res = await getCurrentEncounter();

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/encounter/current");
    expect(res?.encounter_id).toBe("enc1");
  });
```

`frontend/tests/labels.test.ts` import 补 `ENCOUNTER_KIND_LABELS`，追加：

```typescript
describe("ENCOUNTER_KIND_LABELS", () => {
  it("三键中文映射", () => {
    expect(ENCOUNTER_KIND_LABELS.desire_chat).toBe("欲望搭话");
    expect(ENCOUNTER_KIND_LABELS.random_event).toBe("随机事件");
    expect(ENCOUNTER_KIND_LABELS.growth_moment).toBe("成长时刻");
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run tests/api.test.ts tests/labels.test.ts`
Expected: FAIL — `chooseEncounter`/`getCurrentEncounter`/`ENCOUNTER_KIND_LABELS` 未导出。

- [ ] **Step 3: 实现三文件**

照抄 `docs/frontend/06-game-shell.md` §6.1（`types/api.ts`）、§6.6（`client.ts`）、§6.9（`labels.ts`）。注意 §6.1 最后一句：把 3 个事件类型加进 `SseEvent` 判别联合，**不要**加进 `OpaqueEventType`。

- [ ] **Step 4: 跑测试 + 类型检查 + 提交**

Run: `cd frontend && npx vitest run tests/api.test.ts tests/labels.test.ts` → PASS；`npx tsc --noEmit` → 零报错。

```bash
git add src/types/api.ts src/api/client.ts src/lib/labels.ts tests/api.test.ts tests/labels.test.ts
git commit -m "feat(game-shell): 遭遇类型 + client 两端点 + 遭遇标签"
```

---

### Task 7: `encounterStore` + `chatStore`（选择 + 结局上屏）

**Files:**
- Create: `src/stores/encounterStore.ts`
- Modify: `src/stores/chatStore.ts`（`ChatMessage.kind` 加 `"encounter"` + `addEncounterEnding`）
- Test: `frontend/tests/stores.test.ts`（追加）

**Interfaces:**
- Consumes: `chooseEncounter`/`getCurrentEncounter`（Task 6）、`EncounterCurrent`/`EncounterStartEvent`/`EncounterEndEvent`（Task 6）、`useChatStore`/`useInnerLifeStore`/`useDesireStore`/`useMemoryStore`。
- Produces: `useEncounterStore`（`current`/`choosing`/`error`/`onStart`/`onEnd`/`choose`/`refresh`/`reset`）；`chatStore.addEncounterEnding(e)`。Task 8/10 依赖这些。

- [ ] **Step 1: 写测试（红）**

`frontend/tests/stores.test.ts` import 补 `useEncounterStore`、`useInnerLifeStore`、`useDesireStore`、`useMemoryStore`，追加：

```typescript
describe("encounterStore", () => {
  beforeEach(() => {
    useEncounterStore.getState().reset();
    useChatStore.getState().reset();
  });

  it("onStart 置 current（零映射）", () => {
    useEncounterStore.getState().onStart({
      event: "encounter_start",
      event_id: "e1",
      correlation_id: "c1",
      encounter_id: "enc1",
      kind: "random_event",
      text: "开场",
      options: [{ index: 0, text: "走" }],
    });
    expect(useEncounterStore.getState().current).toEqual({
      encounter_id: "enc1",
      kind: "random_event",
      text: "开场",
      options: [{ index: 0, text: "走" }],
    });
  });

  it("onEnd 清 current + 上屏 ending + 重拉三个快照", () => {
    const chatSpy = vi.spyOn(useChatStore.getState(), "addEncounterEnding");
    const innerSpy = vi.spyOn(useInnerLifeStore.getState(), "refreshState").mockResolvedValue(undefined);
    const desireSpy = vi.spyOn(useDesireStore.getState(), "refresh").mockResolvedValue(undefined);
    const memorySpy = vi.spyOn(useMemoryStore.getState(), "refresh").mockResolvedValue(undefined);

    useEncounterStore.getState().onEnd({
      event: "encounter_end",
      event_id: "e2",
      correlation_id: "c2",
      encounter_id: "enc1",
      kind: "random_event",
      option_index: 0,
      option_text: "走",
      ending: "结局",
      consequences: {},
    });

    expect(useEncounterStore.getState().current).toBeNull();
    expect(chatSpy).toHaveBeenCalledTimes(1);
    expect(innerSpy).toHaveBeenCalledTimes(1);
    expect(desireSpy).toHaveBeenCalledTimes(1);
    expect(memorySpy).toHaveBeenCalledTimes(1);
  });

  it("choose POST /api/encounter/choose，成功不本地清 current", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ encounter_id: "enc1", chosen: 0 }));
    vi.stubGlobal("fetch", fetchMock);
    useEncounterStore.setState({
      current: { encounter_id: "enc1", kind: "random_event", text: "开场", options: [{ index: 0, text: "走" }] },
    });

    await useEncounterStore.getState().choose("enc1", 0);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/encounter/choose");
    expect(JSON.parse(init.body)).toEqual({ encounter_id: "enc1", option_index: 0 });
    expect(useEncounterStore.getState().current).not.toBeNull(); // 不本地清
    expect(useEncounterStore.getState().choosing).toBe(false);
  });

  it("refresh GET /api/encounter/current → current 落 store", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      encounter_id: "enc9", kind: "growth_moment", text: "开场", options: [],
    }));
    vi.stubGlobal("fetch", fetchMock);

    await useEncounterStore.getState().refresh();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/encounter/current");
    expect(useEncounterStore.getState().current?.encounter_id).toBe("enc9");
  });
});

describe("chatStore.addEncounterEnding", () => {
  it("ending 转 ChatMessage{role:nyx, kind:encounter} 并 append", () => {
    useChatStore.getState().reset();
    useChatStore.getState().addEncounterEnding({
      event: "encounter_end",
      event_id: "e1",
      correlation_id: "c1",
      encounter_id: "enc1",
      kind: "growth_moment",
      option_index: 0,
      option_text: "走",
      ending: "温柔，总能走到更远的地方。",
      consequences: {},
    });

    const { messages } = useChatStore.getState();
    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({
      id: "e1",
      role: "nyx",
      kind: "encounter",
      content: "温柔，总能走到更远的地方。",
      correlation_id: "c1",
    });
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run tests/stores.test.ts`
Expected: FAIL — `useEncounterStore` 未导出 / `addEncounterEnding` 未定义。

- [ ] **Step 3: 实现 encounterStore + chatStore**

照抄 `docs/frontend/06-game-shell.md` §5.1（`encounterStore.ts` 完整）、§6.4（`chatStore.ts` 增量：`kind` 联合加 `"encounter"` + `addEncounterEnding` action；`EncounterEndEvent` 加入 import）。

- [ ] **Step 4: 跑测试 + 类型检查 + 提交**

Run: `cd frontend && npx vitest run tests/stores.test.ts` → PASS；`npx tsc --noEmit` → 零报错。

```bash
git add src/stores/encounterStore.ts src/stores/chatStore.ts tests/stores.test.ts
git commit -m "feat(game-shell): encounterStore + chatStore.addEncounterEnding"
```

---

### Task 8: SSE 事件表 + 分发表（`useSSE.ts` / `dispatch.ts`）

**Files:**
- Modify: `src/hooks/useSSE.ts`（`EVENT_TYPES` 追加 3 个）
- Modify: `src/api/dispatch.ts`（`case` 追加 3 个）
- Test: `frontend/tests/sse.test.ts`（追加）

**Interfaces:**
- Consumes: `useEncounterStore.onStart/onEnd`（Task 7）。
- Produces: `EVENT_TYPES` 含 `encounter_start/choice/end`；`dispatchEvent` 路由这三类。Task 11 的 App 依赖 SSE 全链路。

- [ ] **Step 1: 写测试（红）**

`frontend/tests/sse.test.ts` import 补 `useEncounterStore`；`dispatchEvent` describe 的 `beforeEach` reset 块追加 `useEncounterStore.setState({ current: null, choosing: false, error: null });`；追加：

```typescript
  it("encounter_start → encounterStore.onStart", () => {
    const spy = vi.spyOn(useEncounterStore.getState(), "onStart");
    dispatchEvent({
      event: "encounter_start",
      event_id: "e1",
      correlation_id: "c1",
      encounter_id: "enc1",
      kind: "random_event",
      text: "开场",
      options: [{ index: 0, text: "走" }],
    });
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("encounter_end → encounterStore.onEnd", () => {
    const spy = vi.spyOn(useEncounterStore.getState(), "onEnd");
    dispatchEvent({
      event: "encounter_end",
      event_id: "e1",
      correlation_id: "c1",
      encounter_id: "enc1",
      kind: "random_event",
      option_index: 0,
      option_text: "走",
      ending: "结局",
      consequences: {},
    });
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("encounter_choice 无消费者不崩", () => {
    expect(() =>
      dispatchEvent({
        event: "encounter_choice",
        event_id: "e1",
        correlation_id: "c1",
        encounter_id: "enc1",
        option_index: 0,
        option_text: "走",
      }),
    ).not.toThrow();
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run tests/sse.test.ts`
Expected: FAIL — 事件被 `OpaqueEvent` 丢弃或 `dispatchEvent` 无对应 `case`（`onStart` spy 未被调用）。

- [ ] **Step 3: 实现 useSSE.ts + dispatch.ts**

照抄 `docs/frontend/06-game-shell.md` §6.2（`EVENT_TYPES` 追加 3 个）与 §6.3（`dispatch.ts` 追加 3 个 `case`，`import useEncounterStore`）。

- [ ] **Step 4: 跑测试 + 类型检查 + 提交**

Run: `cd frontend && npx vitest run tests/sse.test.ts` → PASS；`npx tsc --noEmit` → 零报错。

```bash
git add src/hooks/useSSE.ts src/api/dispatch.ts tests/sse.test.ts
git commit -m "feat(game-shell): SSE 事件表 + 分发表接入 encounter_start/choice/end"
```

---

### Task 9: 活动文字共享 + 字体档位（`activityResult.ts` / `settingsStore.ts` / `StatusBar.tsx`）

**Files:**
- Modify: `src/lib/activityResult.ts`（新增 `activityStatusText`）
- Modify: `src/components/StatusBar.tsx`（委托 `activityStatusText`）
- Modify: `src/stores/settingsStore.ts`（新增 `fontScale`）
- Test: `frontend/tests/activityResult.test.ts`（追加）

**Interfaces:**
- Consumes: `activitySubject`（既有）。
- Produces: `activityStatusText(a: Activity | null) -> string`；`useSettingsStore.fontScale`/`setFontScale`（`"small" | "medium" | "large"`，默认 `"medium"`）。Task 10 的 LeftPanel 依赖二者。

- [ ] **Step 1: 写测试（红）**

`frontend/tests/activityResult.test.ts` import 补 `activityStatusText`，追加：

```typescript
describe("activityStatusText", () => {
  it("null → 空闲；六类活动文案", () => {
    expect(activityStatusText(null)).toBe("空闲");
    expect(activityStatusText(activity({ type: "reading", progress: { filename: "书.txt" } }))).toBe("在读《书.txt》");
    expect(activityStatusText(activity({ type: "free_exploration", progress: { description: "某主题" } }))).toBe("在探索「某主题」");
    expect(activityStatusText(activity({ type: "creation" }))).toBe("在创作");
    expect(activityStatusText(activity({ type: "observe_user" }))).toBe("在观察你");
    expect(activityStatusText(activity({ type: "idle_reflection" }))).toBe("在静默反思");
    expect(activityStatusText(activity({ type: "rest" }))).toBe("在休息");
  });
});
```

> `activityStatusText` 不读 `status`，只读 `type` + 主题，所以 `activity()` helper 的 `status: "completed"` 默认值不影响断言。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run tests/activityResult.test.ts`
Expected: FAIL — `activityStatusText` 未导出。

- [ ] **Step 3: 实现 activityResult.ts + StatusBar.tsx + settingsStore.ts**

照抄 `docs/frontend/06-game-shell.md` §6.7（`activityStatusText` + `StatusBar` 委托）与 §6.8（`settingsStore` 加 `fontScale`）。`settingsStore` 无独立测试点（spec §8 未要求），靠 `tsc` + 后续 LeftPanel 使用覆盖。

- [ ] **Step 4: 跑测试 + 类型检查 + 提交**

Run: `cd frontend && npx vitest run tests/activityResult.test.ts` → PASS；`npx tsc --noEmit` → 零报错。

```bash
git add src/lib/activityResult.ts src/components/StatusBar.tsx src/stores/settingsStore.ts tests/activityResult.test.ts
git commit -m "feat(game-shell): activityStatusText 共享 + settingsStore.fontScale"
```

---

### Task 10: 三个新组件（`EncounterCard` / `ScrollArea` / `LeftPanel`）

**Files:**
- Create: `src/components/encounter/EncounterCard.tsx`
- Create: `src/components/shell/ScrollArea.tsx`
- Create: `src/components/shell/LeftPanel.tsx`
- Test: `frontend/tests/encounterCard.test.tsx`（新建）、`frontend/tests/scrollArea.test.tsx`（新建）

**Interfaces:**
- Consumes: `useEncounterStore`（Task 7）、`ENCOUNTER_KIND_LABELS`（Task 6）、`activityStatusText`（Task 9）、`useSettingsStore.fontScale`（Task 9）、`useInnerLifeStore`/`useDesireStore`/`useActivityStore`、`MessageList`/`MemoryPanel`/`ReadingNotesPanel`/`Avatar`/`EnergyBar`/`EMOTION_LABELS`/`DESIRE_TYPE_LABELS`（既有）。
- Produces: `EncounterCard`（自读 store，无 props）；`ScrollArea`（三模式切换）；`LeftPanel({ onOpenInner })`。Task 11 的 App 依赖全部。

- [ ] **Step 1: 写组件测试（红）**

`frontend/tests/encounterCard.test.tsx`（用 `render`/`fireEvent` + `useEncounterStore.setState` 注入状态）：

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import EncounterCard from "../src/components/encounter/EncounterCard";
import { useEncounterStore } from "../src/stores/encounterStore";

beforeEach(() => useEncounterStore.getState().reset());

describe("EncounterCard", () => {
  it("current null 不渲染", () => {
    const { container } = render(<EncounterCard />);
    expect(container.querySelector(".encounter-card")).toBeNull();
  });

  it("渲染文本 + 选项按钮，点击调 choose", () => {
    const chooseSpy = vi.spyOn(useEncounterStore.getState(), "choose").mockResolvedValue(undefined);
    useEncounterStore.setState({
      current: { encounter_id: "enc1", kind: "random_event", text: "开场", options: [
        { index: 0, text: "走过去" }, { index: 1, text: "停下" },
      ] },
    });

    render(<EncounterCard />);
    expect(screen.getByText("开场")).toBeTruthy();
    fireEvent.click(screen.getByText("走过去"));

    expect(chooseSpy).toHaveBeenCalledWith("enc1", 0);
  });

  it("choosing 期间选项禁用", () => {
    useEncounterStore.setState({
      current: { encounter_id: "enc1", kind: "random_event", text: "开场", options: [{ index: 0, text: "走" }] },
      choosing: true,
    });
    render(<EncounterCard />);
    expect((screen.getByText("走") as HTMLButtonElement).disabled).toBe(true);
  });
});
```

`frontend/tests/scrollArea.test.tsx`：

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import ScrollArea from "../src/components/shell/ScrollArea";
import { useChatStore } from "../src/stores/chatStore";

beforeEach(() => useChatStore.getState().reset());

describe("ScrollArea", () => {
  it("默认对话模式，可切到记忆/笔记", () => {
    render(<ScrollArea />);
    // 左下角三个模式按钮
    expect(screen.getByText("对话")).toBeTruthy();
    fireEvent.click(screen.getByText("记忆"));
    fireEvent.click(screen.getByText("笔记"));
    // 切回对话
    fireEvent.click(screen.getByText("对话"));
  });
});
```

> 组件测试只验证渲染/交互不崩 + 关键交互（点击调 `choose`、禁用、模式切换），**不做视觉断言**（spec §8 末句）。`ScrollArea` 三模式切换会渲染 `MemoryPanel`/`ReadingNotesPanel`，这两个面板各自读 store；若其内部需要额外 mock，实现者按现有 `memoryPanel.test.tsx`/`readingNotes.test.tsx` 的 store 注入方式补充——测试以「切换不崩」为准。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run tests/encounterCard.test.tsx tests/scrollArea.test.tsx`
Expected: FAIL — 组件文件不存在（import 报错）。

- [ ] **Step 3: 实现三组件**

照抄 `docs/frontend/06-game-shell.md` §5.2（`EncounterCard.tsx`）、§5.3（`ScrollArea.tsx`）、§5.4（`LeftPanel.tsx`）。

- [ ] **Step 4: 跑测试 + 类型检查 + 提交**

Run: `cd frontend && npx vitest run tests/encounterCard.test.tsx tests/scrollArea.test.tsx` → PASS；`npx tsc --noEmit` → 零报错。

```bash
git add src/components/encounter/EncounterCard.tsx src/components/shell/ScrollArea.tsx src/components/shell/LeftPanel.tsx tests/encounterCard.test.tsx tests/scrollArea.test.tsx
git commit -m "feat(game-shell): EncounterCard + ScrollArea + LeftPanel 三组件"
```

---

### Task 11: App 重写装配 + 书卷风 CSS + 删除樱花/ChatPanel

**Files:**
- Modify: `src/App.tsx`（全量重写装配）
- Modify: `src/index.css`（全量重写为羊皮纸书卷风）
- Modify: `src/components/chat/MessageBubble.tsx`（`encounter` 徽标 + `initiate_chat` 文案改「欲望搭话」）
- Delete: `src/components/scene/Sakura.tsx`
- Delete: `src/components/chat/ChatPanel.tsx`
- Test: `frontend/tests/chatPanel.test.tsx`（改为验证 MessageBubble，或随 ChatPanel 删除而调整）

**Interfaces:**
- Consumes: `dispatchEvent`/`useSSE`/`usePresence`/`useEncounterStore`/`useInnerLifeStore`/`useActivityStore`/`useSettingsStore`/`LeftPanel`/`ScrollArea`/`ChatInput`/`InnerWorld`/`EvalPanel`/`AnnounceLayer`（Task 10 + 既有）。
- Produces: 三区布局 `App`；`index.css` 的 `:root` token（`--parchment`/`--ink`/`--gold`/`--font-serif`/`--text-scale` 等）。Task 12 文档同步依赖最终文件清单。

- [ ] **Step 1: 处理 ChatPanel 删除对测试的影响（先想）**

`src/components/chat/ChatPanel.tsx` 被删除，其职责拆散到 ScrollArea/LeftPanel/ChatInput。先查 `frontend/tests/chatPanel.test.tsx` 引用了什么：若只测 ChatPanel 内部，删除对应测试或改测 MessageBubble 的 `encounter`/「欲望搭话」徽标（spec §6.5 要求 `MessageBubble` 加徽标，这是仍存在的组件）。本步产出：确认 `MessageBubble` 徽标测试落在 `chatPanel.test.tsx`（改）或新建 `messageBubble.test.tsx`。

- [ ] **Step 2: 写 MessageBubble 徽标测试（红）**

在 `frontend/tests/chatPanel.test.tsx`（若它已测 MessageBubble）或新建 `frontend/tests/messageBubble.test.tsx`：

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import MessageBubble from "../src/components/chat/MessageBubble";
import type { ChatMessage } from "../src/stores/chatStore";

const base = (overrides: Partial<ChatMessage>): ChatMessage => ({
  id: "m1", role: "nyx", kind: "speak", content: "hi", correlation_id: "c1", ...overrides,
});

describe("MessageBubble 徽标", () => {
  it("kind=encounter → 徽标「遭遇」", () => {
    render(<MessageBubble message={base({ kind: "encounter" })} />);
    expect(screen.getByText("遭遇")).toBeTruthy();
  });

  it("kind=initiate_chat → 徽标「欲望搭话」", () => {
    render(<MessageBubble message={base({ kind: "initiate_chat" })} />);
    expect(screen.getByText("欲望搭话")).toBeTruthy();
  });
});
```

> `MessageBubble` 的 props 形状以现有 `messageBubble` 用法为准（实现者先 `Read src/components/chat/MessageBubble.tsx` 确认 props 名再写测试；本测试代码假定 `message: ChatMessage`）。

- [ ] **Step 3: 跑测试确认失败**

Run: `cd frontend && npx vitest run tests/messageBubble.test.tsx`（或对应文件）
Expected: FAIL — 徽标文案未改（`遭遇`/`欲望搭话` 不存在）。

- [ ] **Step 4: 实现 MessageBubble + App.tsx + index.css + 删除**

1. 照抄 `docs/frontend/06-game-shell.md` §6.5（`MessageBubble` 徽标）。
2. 照抄 §5.5（`App.tsx` 全量重写）。
3. 照抄 §4（`index.css` 羊皮纸 token + 边框/背景规则）——`index.css` 重写为 `:root` token + `.game-shell`/`.left-panel`/`.scroll-area`/`.encounter-card`/`.app-bg`/`.app-topbar`/`.debug-overlay` 等类，字体用 `--font-serif`，背景默认 `var(--parchment)`。
4. 删除 `src/components/scene/Sakura.tsx`、`src/components/chat/ChatPanel.tsx`；`App.tsx` 不再引用 `<Sakura />`/`<ChatPanel />`。

- [ ] **Step 5: 跑全量前端质量门 + 提交**

Run:
```
cd frontend && npx tsc --noEmit
cd frontend && npx vitest run
```
零报错（含删除 ChatPanel 后所有测试，若 `chatPanel.test.tsx` 测 ChatPanel 内部，实现者删除它或改写为 MessageBubble 测试并同步 `test-inventory`）。

```bash
git add src/App.tsx src/index.css src/components/chat/MessageBubble.tsx src/components/scene/Sakura.tsx src/components/chat/ChatPanel.tsx tests/chatPanel.test.tsx tests/messageBubble.test.tsx
git commit -m "feat(game-shell): App 三区装配 + 书卷风 CSS + 删 Sakura/ChatPanel"
```

---

### Task 12: 文档同步（test-inventory + 前后端 spec 对齐）

**Files:**
- Modify: `docs/test-inventory.md`（追加全部新增 + 受影响测试，按 系统/方向/阶段）
- Modify: `docs/specs/18-api.md`（端点计数 15→17 + 两张新端点）
- Modify: `docs/frontend/README.md`、`docs/frontend/01-sse.md`、`docs/frontend/02-stores.md`、`docs/frontend/05-client.md`、`docs/frontend/03-chat-panel.md`

**Interfaces:** 无新接口。纯文档。

- [ ] **Step 1: 更新 test-inventory.md**

追加（格式与既有条目一致）：
- **encounter（后端）**：`test_rules.py` 16 个纯函数测试（功能正确/边界鲁棒/回归保护）、`test_facade.py` 17 个（`_parse_encounter` 边界 + 触发/选择/里程碑/失败 best-effort）、inner_life/desire/memory 各 2 个后果消费者测试、`test_enums.py` 穷举更新、`test_types.py` 2 个、`test_subscription.py` 计数更新、`test_endpoints.py` 4 个端点测试。阶段：遭遇系统。
- **encounter（前端）**：`stores.test.ts`（encounterStore + addEncounterEnding）、`sse.test.ts`（3 事件路由）、`api.test.ts`（2 端点）、`labels.test.ts`（3 标签）、`activityResult.test.ts`（activityStatusText）、`encounterCard.test.tsx`、`scrollArea.test.tsx`、`messageBubble.test.tsx`。阶段：游戏壳。

- [ ] **Step 2: 更新 18-api.md**

端点计数「15 REST + SSE」→「17 REST + SSE」；补 `POST /api/encounter/choose`（`{encounter_id, option_index}` → `{encounter_id, chosen}`，409 当 None）与 `GET /api/encounter/current`（`EncounterCurrent | null`）两张说明。

- [ ] **Step 3: 更新前端文档**

照抄 `docs/frontend/06-game-shell.md` §9 五项：README 目录结构 + 面板去向表；01-sse `EVENT_TYPES`（21→24）+ 分发表；02-stores 三个 store；05-client 端点 22→24；03-chat-panel 标注 ChatPanel 已拆散。

- [ ] **Step 4: 提交**

```bash
git add docs/test-inventory.md docs/specs/18-api.md docs/frontend/README.md docs/frontend/01-sse.md docs/frontend/02-stores.md docs/frontend/05-client.md docs/frontend/03-chat-panel.md
git commit -m "docs(game-shell): test-inventory + 18-api 计数 + 前端 spec 对齐"
```

---

## 自检（Self-Review）

- **Spec 覆盖**：19-encounter 全部验收项 → Task 1-5；06-game-shell 全部验收项 → Task 6-11；两 spec 的「文档同步」→ Task 12。`docs/design/raising-sim.md` §5.2「eval+token 独立调试页」→ Task 11 App 的 `debug-overlay`；「欲望搭话前端重分类」→ Task 11 MessageBubble 徽标。
- **已知 spec 修正（两处，已内联说明）**：① `test_consequence_for_isolated` 断言改顶层 dict（浅拷贝语义，Task 2）；② routing.py ROUTING 补齐（19-encounter 漏列，Task 5）。
- **类型一致性**：`EncounterCurrent`/`EncounterStartEvent`/`EncounterEndEvent`/`EncounterKind`/`EncounterOption` 在 Task 6 定义，Task 7/8/10 复用同名；`useEncounterStore` 在 Task 7 定义，Task 8/10/11 复用；`activityStatusText` 在 Task 9 定义，Task 10 复用。
- **反冗余**：无新增抽象层（后端 `encounter/` 只有 rules + facade，无 store/无表）；纯函数集中在 `rules.py`；`activityStatusText` 从 StatusBar 上移共享而非复制。
