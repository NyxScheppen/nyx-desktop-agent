# 读书反应并进对话（后端）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让读书时的提问/记忆联想追加进 `ExpressionFacade._history`，使后续 `reply()` 能回溯引用（用户能顺着她的话回，她也记得自己刚问过什么）。

**Architecture:** 在 `ExpressionFacade` 加一个同步方法 `record_proactive_turn(text)` 把 Nyx 主动产出 append 进 `_history`（复用 `initiate_chat` 的「进历史」半机制，不碰欲望/待回应生命周期）；`ReadingFacade` 构造注入 `expression`，在 `_question_reading`/`_associate_reading` 两个叶子方法里 `publish` 之后调它。事件不变（仍发 `READING_QUESTION/ASSOCIATION/MUTTER`），前端 08 负责重路由。

**Tech Stack:** Python 3.11+ / FastAPI / asyncio；pytest（duck-typed fake + `cast`，不用 `unittest.mock`）；ruff + pyright。

**Spec:** `docs/specs/24-reading-chat-turn.md`（本计划的唯一契约来源；执行前先读它）。

## Global Constraints

- Python 3.11+，所有函数签名完整类型标注；Facade 方法与 I/O 用 `async def`，纯内存/纯计算保持同步（`record_proactive_turn` 无 I/O → **同步 `def`**）。
- 命名 snake_case；枚举用 `Enum`；公开方法 Google style docstring（解释 why）。
- 导入顺序：标准库 → 第三方 → 本地；本地按字母序。
- 禁止 `*` 导入、吞异常不重抛。本改动在 `try` 块内 `deque.append`（不抛），best-effort 语义不受影响。
- 测试用 duck-typed fake + `cast`（本项目约定，非 `unittest.mock`）；LLM 不真调。
- 质量门：`ruff check` 零报错、`pyright` 零报错、`pytest` 全绿；改测试后同步 `docs/test-inventory.md`（快照）。
- 测试目录 `tests/test_{system}/`；每个 Facade 方法测试 ≤5 断言。

---

### Task 1: `ExpressionFacade.record_proactive_turn`（TDD）

**Files:**
- Modify: `nyx/expression/facade.py`（`initiate_chat` 之后、`mutter` 之前，约 157-159 行之间加方法）
- Test: `tests/test_expression/test_expression_facade.py`（`test_record_message_marks_fast` 之后，约 526 行之后加测试）

**Interfaces:**
- Consumes: 已有 `Message`（`from nyx.types import ... Message`，已 import）、`time`（已 import）、`self._history: deque[Message]`（`__init__` 已建）。
- Produces: `ExpressionFacade.record_proactive_turn(text: str) -> None`——Task 3 的 `_question_reading`/`_associate_reading` 依赖它；Task 2 的慢通道测试也依赖它。

- [ ] **Step 1: 写失败测试**

在 `test_record_message_marks_fast`（`tests/test_expression/test_expression_facade.py:518-526`）之后追加：

```python
def test_record_proactive_turn_appends_to_history() -> None:
    facade, *_ = _new_facade()
    facade.record_proactive_turn("她在读书时问了个问题")
    assert facade._history[-1].role == "nyx"
    assert facade._history[-1].content == "她在读书时问了个问题"
    assert facade._history[-1].fast is False
```

（`_new_facade()` 是同步 `def`，返回 6 元组，`facade, *_` 解构；该文件已 `# pyright: reportPrivateUsage=false`，`facade._history` 可直接读。）

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_expression/test_expression_facade.py::test_record_proactive_turn_appends_to_history -v`
Expected: FAIL — `AttributeError: 'ExpressionFacade' object has no attribute 'record_proactive_turn'`（方法缺失，非拼写错）。

- [ ] **Step 3: 写最小实现**

在 `nyx/expression/facade.py` 的 `initiate_chat` 方法结束（`return True` 之后、`async def mutter` 之前）插入：

```python
    def record_proactive_turn(self, text: str) -> None:
        """把 Nyx 主动产出（读书提问/联想）追加进会话历史，供后续 reply() 引用。"""
        self._history.append(Message(role="nyx", content=text, timestamp=time.time()))
```

（`Message`、`time` 均已在该文件顶部 import，无需新增 import；`Message.fast` 缺省 `False`，故读书 turn 不被慢通道回溯跳过。）

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_expression/test_expression_facade.py -v`
Expected: PASS，且该文件其余测试仍绿。

- [ ] **Step 5: 提交**

```bash
git add nyx/expression/facade.py tests/test_expression/test_expression_facade.py
git commit -m "feat(expression): 加 record_proactive_turn 把主动产出落历史

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: `test_reading_turn_slow_backtrack_preserved`（回归钉桩，非红绿）

**Files:**
- Test: `tests/test_expression/test_expression_facade.py`（`test_reply_slow_backtrack_skips_fast_nyx` 之后，约 543 行之后）

**Interfaces:**
- Consumes: Task 1 的 `record_proactive_turn`；`_new_facade(energy=100.0, arousal=0.0)`（强制慢通道）；`_user_content` helper（`:231`）；`test_reply_slow_backtrack_skips_fast_nyx`（`:529`）的断言风格。

> **为什么不是红→绿**：本条不新增生产代码——它钉住「`record_proactive_turn` 产出的 `fast=False` 消息在慢通道回溯里保留」这一**既有正确行为**（`build_backtrack_context` 只跳 `fast=True`）。写完后它应**直接 PASS**。目的：与既有的 `test_reply_slow_backtrack_skips_fast_nyx`（证 `fast=True` 跳）各证一半，合起来闭合「`fast` 唯一消费方是慢通道」的契约。运行若失败，说明 `record_proactive_turn` 的 `fast` 缺省被改坏了——这正是它要防的回归。

- [ ] **Step 1: 写测试**

```python
async def test_reading_turn_slow_backtrack_preserved() -> None:
    # 读书 turn（fast=False）在慢通道回溯里保留——与 test_reply_slow_backtrack_skips_fast_nyx
    # 各证一半（fast=True 跳 / fast=False 留），合起来闭合 fast 标志的唯一消费方契约。
    facade, llm, _evaluator, _memory, _inner_life, _bus = _new_facade(
        energy=100.0, arousal=0.0
    )
    facade.record_proactive_turn("她刚才问：你觉得自由是什么")
    await facade.reply("自由很虚无", "corr-bt")
    think_user = _user_content([m for t, m, _c in llm.calls if t == "think"][0])
    assert "Nyx：她刚才问：你觉得自由是什么" in think_user
```

- [ ] **Step 2: 跑测试确认通过（回归钉桩应直接绿）**

Run: `python -m pytest tests/test_expression/test_expression_facade.py::test_reading_turn_slow_backtrack_preserved -v`
Expected: PASS（若 FAIL，检查 `record_proactive_turn` 是否误设了 `fast=True`）。

- [ ] **Step 3: 提交**

```bash
git add tests/test_expression/test_expression_facade.py
git commit -m "test(expression): 补读书 turn 慢通道保留（与 fast-skip 各证一半）

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: `ReadingFacade` 接线（构造第 9 参 + 调用点 + 夹具）

**Files:**
- Modify: `nyx/reading/facade.py`（import、`__init__`、`_question_reading`、`_associate_reading`、模块 docstring）
- Modify: `tests/test_reading/test_reading_facade.py`（`_FakeExpression` 类、import、`_build_impulse_facade`、`_note_facade`、3 条接线测试）

**Interfaces:**
- Consumes: Task 1 的 `ExpressionFacade.record_proactive_turn`；`ReadingBehavior.QUESTION_KNOWLEDGE`/`QUOTE_QUESTION`（已 import）；`EventType.READING_QUESTION/READING_ASSOCIATION/READING_MUTTER`（已 import）。
- Produces: `ReadingFacade.__init__` 第 9 参 `expression: ExpressionFacade`；`self._expression`；调用点 `self._expression.record_proactive_turn(...)`。后续无任务依赖它（main.py 装配是 Task 4）。

> **夹具是硬前置**：`ReadingFacade` 加第 9 必需参后，全仓 3 处真实构造（`main.py:729`、本文件 `_build_impulse_facade`/`_note_facade`）必须同步加参，否则全套 reading 测试 `TypeError`。4 个文件里的 `cast(ReadingFacade, …)` 类型桩（`test_tick_loop.py:65/308`、`test_subscription.py:86`、`test_endpoints.py:130`、`test_reading_api.py:161`）是类型桩不是真实构造，**不动**。

- [ ] **Step 1: 测试侧加 `_FakeExpression` + import**

在 `tests/test_reading/test_reading_facade.py` 模块级 `_Fake*` 类旁（`_FakeEvaluator` 之后，约 184 行之后）加：

```python
class _FakeExpression:
    def __init__(self) -> None:
        self.recorded: list[str] = []

    def record_proactive_turn(self, text: str) -> None:
        self.recorded.append(text)
```

import 块（`:14-37`）加一行 `from nyx.expression.facade import ExpressionFacade`（按字母序插在 `from nyx.events.bus import EventBus` 之后、`from nyx.inner_life.facade import InnerLifeFacade` 之前；`cast` 已 import，无需再加）。

- [ ] **Step 2: 改 `_build_impulse_facade`（`:264-281`）加第 7 可选参**

```python
def _build_impulse_facade(
    database: db.Database,
    bus: _FakeBus,
    llm: _FakeLlm,
    memory: _FakeMemory,
    inner_life: _FakeInnerLife,
    desire: _FakeDesire,
    expression: _FakeExpression | None = None,
) -> ReadingFacade:
    return ReadingFacade(
        ReadingStore(database),
        cast(InnerLifeFacade, inner_life),
        cast(DesireFacade, desire),
        cast(MemoryFacade, memory),
        cast(LlmClient, llm),
        cast(Evaluator, _FakeEvaluator()),
        cast(EventBus, bus),
        "canon",
        cast(ExpressionFacade, expression if expression is not None else _FakeExpression()),
    )
```

- [ ] **Step 3: 改 `_note_facade`（`:749-758`）加第 9 实参**

```python
    facade = ReadingFacade(
        ReadingStore(database),
        cast(InnerLifeFacade, fake_inner),
        cast(DesireFacade, _FakeDesire(_desire_values())),
        cast(MemoryFacade, fake_memory),
        cast(LlmClient, fake_llm),
        cast(Evaluator, fake_eval),
        cast(EventBus, bus),
        "canon",
        cast(ExpressionFacade, _FakeExpression()),
    )
```

- [ ] **Step 4: 写 3 条接线测试（叶子方法级，确定性）**

在 `test_associate_reading_none_search_skips_without_raise`（`:705`）之后追加。**用叶子方法直调而非 `evaluate_paragraph`**：`evaluate_paragraph` 的触发集由 `check_triggers` 阈值决定（富文本同时触 mutter+question+association，`recorded` 顺序不可控），而 `record_proactive_turn` 的调用点就在叶子方法里——直调叶子确定性钉住接线，`evaluate_paragraph` 整条管线已由 `test_evaluate_paragraph_forward_dispatches_events` 覆盖（spec 24 行 5 的「落点指令」也指向叶子方法）。

```python
async def test_question_reading_records_proactive_turn() -> None:
    # 提问触发：record_proactive_turn 记问题正文（quote_question 的 selected_text 不进历史）
    llm = _FakeLlm({"quote_question": "这段为什么重要？\n因为生命的意义。"})
    bus = _FakeBus()
    expr = _FakeExpression()
    database = await db.connect(":memory:")
    facade = _build_impulse_facade(
        database, bus, llm, _FakeMemory([]),
        _FakeInnerLife(_mk_state()), _FakeDesire(_desire_values()), expr,
    )
    try:
        await facade._question_reading(
            "b1", 1, _RICH_TEXT, ReadingBehavior.QUOTE_QUESTION, _mk_state()
        )
    finally:
        await database.conn.close()
    question = [e for e in bus.published if e.type is EventType.READING_QUESTION][0]
    assert question.content["content"] == "这段为什么重要？"
    assert expr.recorded == ["这段为什么重要？"]  # 只记正文，不含 selected_text


async def test_associate_reading_records_proactive_turn_per_memory() -> None:
    # 联想触发：每条命中记忆记一条 turn（snippet）
    bus = _FakeBus()
    expr = _FakeExpression()
    database = await db.connect(":memory:")
    memory = _FakeMemory([_memory("m1", "第一条记忆"), _memory("m2", "第二条记忆")])
    facade = _build_impulse_facade(
        database, bus, _FakeLlm(), memory,
        _FakeInnerLife(_mk_state()), _FakeDesire(_desire_values()), expr,
    )
    try:
        await facade._associate_reading("b1", 1, _RICH_TEXT)
    finally:
        await database.conn.close()
    assoc = [e for e in bus.published if e.type is EventType.READING_ASSOCIATION]
    assert [a.content["memory_id"] for a in assoc] == ["m1", "m2"]
    assert expr.recorded == ["第一条记忆", "第二条记忆"]


async def test_mutter_reading_does_not_record_proactive_turn() -> None:
    # mutter 触发：只广播，不调 record_proactive_turn
    bus = _FakeBus()
    expr = _FakeExpression()
    database = await db.connect(":memory:")
    facade = _build_impulse_facade(
        database, bus, _FakeLlm({"reading_mutter": "这句真美。"}), _FakeMemory([]),
        _FakeInnerLife(_mk_state()), _FakeDesire(_desire_values()), expr,
    )
    try:
        await facade._mutter_reading("b1", 1, _RICH_TEXT, _mk_state())
    finally:
        await database.conn.close()
    assert any(e.type is EventType.READING_MUTTER for e in bus.published)
    assert expr.recorded == []
```

- [ ] **Step 5: 跑测试确认失败**

Run: `python -m pytest tests/test_reading/test_reading_facade.py -v`
Expected: 全文件 FAIL — `TypeError: ReadingFacade.__init__() missing 1 required positional argument: 'expression'`（构造缺参，正是要补的 feature；接线测试还会因 `_expression` 不存在继续失败，正常）。

- [ ] **Step 6: 改 `nyx/reading/facade.py`（import + 第 9 参 + 存参 + 调用点 + docstring）**

(1) import 块（`:20` 的 `from nyx.expression.prompt import build_system_prompt` 之前）加：

```python
from nyx.expression.facade import ExpressionFacade
```

(2) `__init__` 签名（`:142` 的 `canon: str,` 之后）加 `expression: ExpressionFacade,`，体（`:151` 的 `self._canon = canon` 之后）加 `self._expression = expression`。

(3) 模块 docstring 第 5 行 `构造注入 8 依赖` → `构造注入 9 依赖`。

(4) `_question_reading`（`:378-390` 的 `await self._bus.publish(...)` 之后、`except` 之前）加：

```python
            self._expression.record_proactive_turn(content)
```

(5) `_associate_reading`（`:404-418` 循环内 `await self._bus.publish(...)` 之后）加：

```python
                self._expression.record_proactive_turn(snippet)
```

（`_mutter_reading` 不动。）

- [ ] **Step 7: 跑测试确认通过**

Run: `python -m pytest tests/test_reading/test_reading_facade.py -v`
Expected: PASS，3 条新接线测试 + 既有全套 reading 测试全绿。

- [ ] **Step 8: 提交**

```bash
git add nyx/reading/facade.py tests/test_reading/test_reading_facade.py
git commit -m "feat(reading): 提问/联想进表达历史（构造第 9 参 expression）

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: `main.py` 装配顺序（expression 先于 reading）

**Files:**
- Modify: `nyx/main.py`（`:729-736` 两块对调 + reading 第 9 参）

**Interfaces:**
- Consumes: Task 3 的 `ReadingFacade.__init__` 第 9 参。
- Produces: 装配后的 `app`（无下游依赖）。

- [ ] **Step 1: 对调构造块**

把当前（`nyx/main.py:729-736`）：

```python
    reading = ReadingFacade(
        ReadingStore(db), inner_life, desire, memory, llm, evaluator, bus, canon
    )

    expression = ExpressionFacade(
        bus, llm, evaluator, memory, activity, desire, inner_life, canon, ask,
        config.expression, tools,
    )
```

改为：

```python
    expression = ExpressionFacade(
        bus, llm, evaluator, memory, activity, desire, inner_life, canon, ask,
        config.expression, tools,
    )

    reading = ReadingFacade(
        ReadingStore(db), inner_life, desire, memory, llm, evaluator, bus, canon,
        expression,
    )
```

（`expression` 的 11 个依赖 `bus/llm/evaluator/memory/activity/desire/inner_life/canon/ask/config.expression/tools` 在 `:729` 之前已全部就绪；`expression` 不依赖 `reading`，无环。）

- [ ] **Step 2: 验证**

Run: `python -m pyright nyx/main.py`（零报错——顺序错了会在 `reading` 处报 `expression` 未定义）；再 `python -m pytest -q`（全绿——任何 import `nyx.main` 的测试若顺序错会 NameError）。

- [ ] **Step 3: 提交**

```bash
git add nyx/main.py
git commit -m "fix(main): expression 先于 reading 装配，reading 注入 expression

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: 文档 ripple + 测试清单快照

**Files:**
- Modify: `docs/tech-reference.md`（ReadingFacade 构造注释、ExpressionFacade 方法表）
- Modify: `docs/specs/18-api.md`（装配顺序描述，若提及）
- Modify: `docs/test-inventory.md`（快照，5 条新测试 + 夹具说明）

- [ ] **Step 1: tech-reference.md**

`docs/tech-reference.md:129` 改为：

```
# 构造：ReadingFacade(store, inner_life, desire, memory, llm, evaluator, bus, canon, expression)  # 9 依赖注入
```

`docs/tech-reference.md:89-96` 的 `### ExpressionFacade` 代码块，在 `check_timeouts` 行之后加：

```python
def record_proactive_turn(text: str) -> None                     # 把 Nyx 主动产出（读书提问/联想）追加进 _history，供 reply() 回溯引用（同步，纯内存 append）
```

- [ ] **Step 2: specs/18-api.md（grep 定位）**

Run `grep -n "ReadingFacade\|expression\|装配" docs/specs/18-api.md`；若其装配/构造小节列出 `ReadingFacade` 构造，更新为「`expression` 先于 `reading` 装配，`ReadingFacade` 第 9 参 `expression`」。若无相关小节则跳过（本改动对 18-api 无契约影响）。

- [ ] **Step 3: test-inventory.md 快照**

在 `docs/test-inventory.md` 的 `## 17-expression` 段，`test_reply_slow_backtrack_skips_fast_nyx` 行（`:585`）之后追加两行：

```
| `test_record_proactive_turn_appends_to_history` | 功能正确 | `record_proactive_turn("…")` → `_history` 尾多一条 `role="nyx"`、`content="…"`、`fast is False` |
| `test_reading_turn_slow_backtrack_preserved` | 功能正确 | 读书 turn（fast=False）后慢通道 reply → 回溯 prompt 含 `Nyx：她刚才问：…`（与 fast-skip 各证一半） |
```

在 `## 21-reading-impulse` 段，`test_associate_reading_none_search_skips_without_raise` 行（`:742`）之后追加三行：

```
| `test_question_reading_records_proactive_turn` | 功能正确 | `_question_reading`（quote_question）→ 广播 `reading_question` 且 `fake_expression.recorded == [问题正文]`（不含 selected_text） |
| `test_associate_reading_records_proactive_turn_per_memory` | 功能正确 | 2 条记忆 → 2 条 `reading_association` 且 `recorded == [snippet1, snippet2]`（每条一次） |
| `test_mutter_reading_does_not_record_proactive_turn` | 功能正确 | `_mutter_reading` → 广播 `reading_mutter` 且 `recorded == []`（未调 record_proactive_turn） |
```

- [ ] **Step 4: 全量质量门**

Run: `python -m ruff check`（零）→ `python -m pyright`（零）→ `python -m pytest -q`（全绿）。

- [ ] **Step 5: 提交**

```bash
git add docs/tech-reference.md docs/specs/18-api.md docs/test-inventory.md
git commit -m "docs: 24-reading-chat-turn ripple + 测试清单快照

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## 自检（writing-plans skill）

- **Spec 覆盖**：验收标准 7 条全落——`record_proactive_turn`（Task 1）、第 9 参（Task 3）、`main.py` 顺序（Task 4）、提问/联想/mutter 三处调用点（Task 3 测试 3 条 + 实现）、`pyright`（各任务验证）。测试要点 5 条：历史尾部（Task 1）、question/association/mutter（Task 3）、慢通道保留（Task 2）。夹具 recipe（`_build_impulse_facade`/`_note_facade`/`_FakeExpression`）全在 Task 3。
- **占位符扫描**：无 TBD/TODO；所有代码步骤给了完整代码。
- **类型一致**：`record_proactive_turn(text: str) -> None` 三处调用点一致；`_FakeExpression.recorded: list[str]` 与断言一致；`ExpressionFacade` import 在 reading facade 与 test 文件都补了。
