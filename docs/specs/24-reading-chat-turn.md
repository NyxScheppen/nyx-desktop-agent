# 24 读书反应并进对话（提问/联想 → 对话 turn）

> spec 只定义**契约**（签名 + 语义 + 决策），不内联完整代码。代码唯一事实来源是 `nyx/` 源文件。

> **行号是定位锚，不是指令**：本文行号（如 `151-153`、`729/733`）只用于在长文件里快速定位；落点指令以**符号名 + 变量名**为准——在叶子方法 `_question_reading`/`_associate_reading` 的 `await self._bus.publish(...)` 之后，用局部 `content`/`snippet` 调 `self._expression.record_proactive_turn(...)`。源码行号会随后续 spec 漂移，别盲信。

## 元信息

- **前置依赖**：17-expression（`ExpressionFacade._history` / `initiate_chat`）、21-reading-impulse（`_dispatch` / `_question_reading` / `_associate_reading` / `_mutter_reading`）
- **反向修订 18-api**：`ReadingFacade` 构造扩为 9 参（+`expression`）+ `main.py` 装配顺序（`expression` 先于 `reading`）
- **实现文件**：`nyx/expression/facade.py`、`nyx/reading/facade.py`、`nyx/main.py`

## 用户故事

> 作为用户，我在读书时 Nyx 提的问题 / 冒出的记忆联想，应该直接进聊天对话——我能顺着她的话回，她也记得自己刚问了什么（而不是一转身就忘）。

> **端到端跨 spec**：本 spec 只交付后端「进历史」这半程；前端把 `READING_QUESTION/ASSOCIATION` 重路由进对话（`chatStore`）见 `frontend/08-reading-chat-layout.md` §2。两 spec 合起来用户才看得到「顺着她的话回」。

## 验收标准

- [ ] `ExpressionFacade` 含 `record_proactive_turn(text: str) -> None`：`self._history.append(Message(role="nyx", content=text, timestamp=time.time()))`
- [ ] `ReadingFacade.__init__` 第 9 参 `expression: ExpressionFacade`
- [ ] `main.py`：`expression` 在 `reading` 之前构造（否则 NameError）
- [ ] 提问触发 → 广播 `READING_QUESTION` **且** `record_proactive_turn(content)` 已调（`content` = 问题正文，不含 `selected_text`）
- [ ] 联想触发 → 每条命中记忆广播 `READING_ASSOCIATION` **且** `record_proactive_turn(snippet)` 已调（循环内每条一次）
- [ ] mutter 触发 → 只广播 `READING_MUTTER`，**不**调 `record_proactive_turn`
- [ ] `pyright` strict 零报错

## 技术方案

### ExpressionFacade.record_proactive_turn

```python
def record_proactive_turn(self, text: str) -> None:
    """把 Nyx 主动产出（读书提问/联想）追加进会话历史，供后续 reply() 引用。"""
    self._history.append(Message(role="nyx", content=text, timestamp=time.time()))
```

- **同步**：纯内存 append，无 I/O，不 `async`（CLAUDE.md「纯计算/无 I/O 保持同步」）。
- **timestamp 必填**：`Message.timestamp` 无默认值（`types.py:252`），照抄 `initiate_chat`（`expression/facade.py:151-153`）的 `Message(role="nyx", content=…, timestamp=time.time())`；`time` 已在该文件导入。
- **fast 默认 False（有意，非碰运气）**：`Message.fast` 的消费方是**慢通道** `build_backtrack_context`（`prompt.py:58`，第 79 行 `if m.role == "nyx" and m.fast:` 跳过 fast=True 的 Nyx 消息），由 `pipeline.py:127` 调用；快通道的原始切片（`facade.py:104`）**不过滤 fast**。读书 turn 不是快通道回复，`fast=False` 保证它**不被慢通道回溯截断跳过**——用户之后进慢通道回复时，她刚问/联想到的仍留在回溯上下文里。缺省 False 正确。
- **无 correlation_id 参数**：`Message` 无 correlation 字段，`reply()` 上下文也只读文本（`list(_history)[-max_context_len:]`），传了也用不上——不造无消费方的参数（反冗余）。

### ReadingFacade 接线（import + 存参）

- **import**：`reading/facade.py` 顶部 import 块加 `from nyx.expression.facade import ExpressionFacade`（按字母序插在 `from nyx.expression.prompt import build_system_prompt` 之前）。现文件已 import `expression.prompt` 但**无** `expression.facade`——不加则第 9 参类型注解 `NameError`。无环（`expression.facade` 不 import `reading`）。
- **存参**：`__init__` 第 9 参 `expression: ExpressionFacade`（紧跟 `canon: str` 之后），体里 `self._expression = expression`（紧跟 `self._canon = canon` 之后）。缺这行 → 调用点 `self._expression.record_proactive_turn(...)` 抛 `AttributeError`。
- **docstring**：模块 docstring 第 5 行「构造注入 8 依赖」改「9 依赖」。

### ReadingFacade 调用点（叶子方法，非 `_dispatch`）

`_dispatch` 只做分发循环（`reading/facade.py:269-287`），不持有 `content`/`snippet` 变量，故调用点落在叶子方法里：

- **`_question_reading`**：在 `await self._bus.publish(...)` 之后 `self._expression.record_proactive_turn(content)`。`content` 是问题正文（`QUOTE_QUESTION` 拆 `raw.partition("\n")` 后的首行）；**`selected_text`（第二行划线引用）不进历史**——历史只喂 `reply()` 上下文，问题正文已含语义，划线是前端渲染徽标的视觉元素（YAGNI，未来要「回复引用划线那句」再加）。
- **`_associate_reading`**：`for memory in memories[:3]` 循环内、`await self._bus.publish(...)` 之后 `self._expression.record_proactive_turn(snippet)`。**每条命中记忆记一条 turn**——`_associate_reading` 没有单一 `content`，取 `snippet`（`_ASSOCIATION_SNIPPET_CHARS` 截断后的 `source`）作历史内容，与事件 payload、前端渲染三者一致。3 条记忆 → 3 条 turn；**不**拼成一条、**不**记全文 `source`（回复上下文只需「她联想到了什么」）。
- **`_mutter_reading`**：**不改**（瞬时轻声自语，不进历史）。
- 三处都在各自 `try` 块内；`deque.append` 不抛，best-effort 语义不受影响。

### 装配顺序（`main.py`）

现在 `reading`（729）先于 `expression`（733）构造。`reading` 要吃 `expression`，必须**把 `expression` 构造块（733-736）挪到 `reading`（729）之前**，`reading` 第 9 参传 `expression`。

`ExpressionFacade.__init__` 全依赖 **11 个**：`bus`/`llm`/`evaluator`/`memory`/`activity`/`desire`/`inner_life`/`canon`/`ask_guidance`（= 变量 `ask`）/`config.expression`/`tools`。逐一确认在 729 前已就绪：`canon`（712）、`ask`（713）、`bus`/`llm`/`evaluator`/`tools`/`desire`/`memory`（`activity` 715 之前已构造）、`activity`（715）、`inner_life`（720）。全部 OK，无环（`expression` 不依赖 `reading`）。

### 关键决策

- **只追加历史、不字面复用 initiate_chat**：initiate_chat 的「互动欲 → 待回应 → 超时 expire」是欲望系统专属生命周期，读书反应没有可 expire 的 `ShortTermDesire`，只复用其「进历史 + 可回复」这一半机制（用户已确认「后端统一」方向）。
- **不设 `_waiting_user`、不触发 `record_no_answer`**：ExpressionFacade 有两套「待回应」——initiate_chat 的 `_pending_chat_desire_id`（欲望超时）与 `reply()` ASK 的 `_waiting_user`/`_ask_text`/`_ask_cid`（问句超时 → `memory.record_no_answer`）。读书提问**两套都不碰**：它没有可 expire 的欲望（不设前者），也不是 `reply()` 产出的 ASK、没有回复侧 correlation_id（不设后者）——自动给低强度的读书闲聊记「用户没答」会污染记忆。读书 turn 只追加历史（用户愿意回就能回、她能记得），「超时记没答」是 reply-ASK 流程的专属，不扩到读书提问。
- **association 进历史不加包装、memory_id 不进历史**：`_history` 是扁平对话转录，`role="nyx"` 语义 =「这是 Nyx 的回合」——提问 = 她问了一句，联想 = 她浮现了一段记忆（`snippet` 是记忆摘要，非她逐字说出的话）。回复 LLM 只需这段文本进上下文即可接话；加一句合成的「我想起…」反而造出事件与前端都没有的内容。`memory_id` 不落历史：`Message` 无此字段、`reply()` 上下文只读文本，前端「记忆链接」由 SSE 事件的 `memory_id` 驱动（事件字段不变），与 `_history` 无关。这是刻意的简化，不是缺口。
- **mutter 不进历史**：瞬时轻声自语，走前端悬浮气泡（frontend 08 §3），不占对话。
- **事件不变**：仍发 `READING_QUESTION`/`READING_ASSOCIATION`/`READING_MUTTER`（21 既有契约）；前端把 question/association 重路由到 `chatStore`（frontend 08 §2）。

### 数据变更

- 无表变更（`_history` 是进程内 `deque`；`reply()` 上下文已 `[-max_context_len:]` 截断，append 不需手工裁剪）。

### 测试要点

> **fake expression 造法**（沿用本文件 duck-typed fake + `cast` 风格，非 `unittest.mock`）：新类 `_FakeExpression`，`record_proactive_turn(self, text: str) -> None` 把 `text` 追加到 `self.recorded: list[str]`（供接线断言）。放本文件其他 `_Fake*` 类旁（模块级、`_build_impulse_facade` 之前）；`cast` 已导入（`test_reading_facade.py:10`），要补的只有 `ExpressionFacade` import（下条「夹具」块已列）。

> **既有夹具必须一起改，否则全套 reading 测试 `TypeError: missing 1 required positional argument: 'expression'`**：`ReadingFacade(` 全仓 **3 处真实构造**——`nyx/main.py:729`、`test_reading_facade.py:272`（`_build_impulse_facade`）、`:749`（`_note_facade`），后两个是 8 个位置实参硬编码；`_facade`/`_impulse_facade` 及绝大多数 reading 测试经 `_build_impulse_facade` 构造，加第 9 必需参当场炸。另有 4 个文件里的 `cast(ReadingFacade, …)` 类型桩（`test_tick_loop.py:65/308`、`test_subscription.py:86`、`test_endpoints.py:130`、`test_reading_api.py:161`）——是类型桩不是真实构造，**不需**加第 9 参，别去动。
> - `_build_impulse_facade` 加第 7 个**可选**参 `expression: _FakeExpression | None = None`（缺省新 `_FakeExpression()`），`ReadingFacade` 第 9 实参传 `cast(ExpressionFacade, expression)`；既有 6 参调用点不动。新增接线测试自己持有 `_FakeExpression()` 传入、断言 `.recorded`。
> - `_note_facade`（749）第 9 实参传 `cast(ExpressionFacade, _FakeExpression())`（22-notes 测试不关心接线，无需暴露）。
> - 测试文件 import 块加 `ExpressionFacade`（供 `cast`）。

> **expression 侧两条测试的夹具 recipe（复用，不重造）**：`record_proactive_turn` 本体 + `test_reading_turn_slow_backtrack_preserved` 都落在 `test_expression_facade.py`，复用其 `_new_facade(...)`（`:235`）——已搭好「真 `ExpressionFacade` + 10 依赖 duck-typed fake + `cast`」；`ExpressionFacade.__init__` 一进来就 `build_reply_graph(ReplyDeps(...))`（`facade.py:75-88`），10 个 fake 必须结构上满足 `ReplyDeps`，`_new_facade` 已满足，别自己另造。**强制 slow**：`_new_facade(energy=100.0, arousal=0.0)` 走慢通道（`energy=20.0, arousal=0.9` 走快），照抄既有 `test_reply_slow_backtrack_skips_fast_nyx`（`:529`）。该文件已 `# pyright: reportPrivateUsage=false`（`:1`），`facade._history` 可直接读；`cast` 已导入（`:3`）。

- [ ] 集成：`record_proactive_turn("…")` → `_history` 尾部多一条 `Message(role="nyx", content=…, timestamp≈now)` 且 `fast is False`
- [ ] 集成：`evaluate_paragraph` 前翻触发 question → `READING_QUESTION` 已广播 + `fake_expression.recorded == [问题正文]`（不含 `selected_text`）
- [ ] 集成：触发 association（fake memory 回 2 条命中）→ 2 条 `READING_ASSOCIATION` + `fake_expression.recorded == [snippet1, snippet2]`
- [ ] 集成：触发 mutter → `fake_expression.recorded == []`（未调）
- [ ] 集成（expression）：`record_proactive_turn("…")` 后慢通道 `reply()` 回溯上下文**含**该 turn——锚定既有 `test_reply_slow_backtrack_skips_fast_nyx`（慢通道跳过 fast=True nyx），本条补「读书 turn（fast=False）保留」这另一半；落 `tests/test_expression/test_expression_facade.py`，命名 `test_reading_turn_slow_backtrack_preserved`

> **快通道不测（弱测试）**：`reply()` 快通道上下文是原始切片（`facade.py:104` `list(self._history)[-max_context_len:]`），**不过滤 `fast`**——读书 turn 即使 `fast=True` 也会进快通道上下文，验不到 `fast=False` 的意义，是弱测试，不写。

> **`fast` 唯一消费方是慢通道**（`prompt.py:58/79` `build_backtrack_context`），故 `fast=False` 正确性只在慢通道验得到：上条 `test_reading_turn_slow_backtrack_preserved` 与既有 `test_reply_slow_backtrack_skips_fast_nyx` 各证一半（fast=True 跳 / fast=False 留），合起来闭合；prompt 层 `test_backtrack_fast_nyx_skipped_continues` 不必再引（facade 层已覆盖）。

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新（快照）
- [ ] ripple 同步：tech-ref §5 `ReadingFacade` 构造 9 参、18-api 装配顺序、17-expression 新方法、`test_reading_facade.py` 两个夹具（`_build_impulse_facade`/`_note_facade`）加第 9 参
