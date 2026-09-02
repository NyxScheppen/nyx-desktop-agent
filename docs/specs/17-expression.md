# ExpressionFacade + 回复流程 + 碎碎念/搭话

> 范围：`expression/facade.py`（`ExpressionFacade`：reply / initiate_chat / mutter）+ `expression/pipeline.py`（回复流程 LangGraph）+ `expression/mutter.py`（碎碎念模板 + 搭话触发判定纯函数）。
> Facade spec：回复流程走 LangGraph 图、每个 LLM 产出紧跟 `evaluate`、事件统一 `publish`。不含 API（`POST /api/chat` 薄封装归 18-api）。
> spec 只定义契约（签名 + 图拓扑 + 多轮语义 + 模板契约）；实现以 `nyx/expression/facade.py` / `nyx/expression/pipeline.py` / `nyx/expression/mutter.py` 源文件为准。

## 元信息

- **前置依赖**：01-types（`Event`/`EventType`/`Source`/`ContextMode`/`Message`/`CurrentState`/`ShortTermDesire`/`SelfNarrative`）、02-config（`ExpressionConfig`）、03-llm（`LlmClient`）、05-event（`EventBus`）、06-tools（`ToolRegistry`）、09-memory-facade（`MemoryFacade`）、11-desire（`DesireFacade`）、12-inner-life（`InnerLifeFacade`）、14-activity（`ActivityFacade`）、eval（`Evaluator`）、16-expression-prompt（`build_system_prompt`/`build_user_prompt`/`classify_channel`）

## 用户故事

> 作为 Nyx 系统的开发者，我想要一个 ExpressionFacade 把「回复流程（快慢通道 + 多轮 think/speak + 场景化记忆）」、「碎碎念」、「搭话」三件事串起来，以便用户消息走完整回复、空闲时 Nyx 会碎碎念、有互动欲时主动搭话，且每次 LLM 产出都过 eval、每个事件可沿 correlation_id 溯源。

## 验收标准

- [ ] `facade.py` 含 `ExpressionFacade`（`reply` / `initiate_chat` / `mutter`）；`pipeline.py` 含 `ReplyState` + `build_reply_graph`；`mutter.py` 含 `MutterCategory` + `_MUTTER_SKELETONS`（**四类各 10 条骨架**，带 `{subject}` 占位）+ `naturalize_presence` / `clean_fragment` / `activity_subject` + `pick_mutter_category` / `pick_mutter_template` + `should_initiate_chat`（实现见 `nyx/expression/mutter.py` 等源文件）
- [ ] `reply` 走 LangGraph 图：快通道 `classify → think → speak → record → end`（不检索记忆、不生成场景化记忆）；慢通道 `classify → assemble → use_tools → think → speak → should_ask`，非问句 round 循环（≤ `slow_max_rounds`，**每轮 publish 一条 SPEAK**），问句 publish ASK 后回合结束，最终都走 `scene_memory → record → end`
- [ ] **当前消息在 prompt 里只出现一次**：`reply` 入口回溯的 `context` 不含当前消息（当前消息尚未进 history），`build_user_prompt` 里它只作为「本次消息」
- [ ] **慢通道回溯截断**：慢通道 `assemble` 调 `build_backtrack_context` 重截断 context——命中「满 max_len / 相邻隔超 `context_time_gap` / 与当前消息零字符重叠」即停，快通道 Nyx 消息（`fast=True`）跳过继续往前；快通道保持入口朴素取最近 `max_context_len` 条
- [ ] **累积式 prompt**：第 N 轮 think 的 user prompt 含前 N-1 轮 think/speak；第 N 轮 speak 的 user prompt 含前 N-1 轮 think/speak + 本轮 think
- [ ] **慢通道递进续写**：首轮 think/speak 是「先想此刻的念头 / 先说出一句」，第 2 轮起的 think/speak 任务指令切换为「再往里想一层 / 再往下说一句」，不重复、不重新回答
- [ ] 每个 LLM 产出（tool / think / speak / initiate_chat / mutter_wander）后紧跟 `await evaluator.evaluate(output)`；`output_type` 分别 `tool` / `think` / `speak` / `initiate_chat` / `mutter_wander`、`module="expression"`、`correlation_id` 透传
- [ ] `initiate_chat` 返回 `bool`（发话 True / 无话 False），供 18-api 维护 `last_chat_at`；发话开场白 append 进 `_history`（`role="nyx"`），用户随后回复能回溯到这句搭话；`mutter` 返回 `None`（无状态依赖）
- [ ] 事件发布：`think` / `speak` / `ask` / `mutter` / `initiate_chat` 全部 `content={"content": 文本}`、`source=INTERNAL`、`correlation_id` 接上游
- [ ] 纯函数测全（`pick_mutter_category` / `pick_mutter_template` / `naturalize_presence` / `clean_fragment` / `activity_subject` / `should_initiate_chat` / `_is_question` / `_rounds_block`）；`pyright` strict 零报错
- [ ] `reply` 后按 `result["ask"]` 置 `_waiting_user`/`_ask_text`/`_ask_cid`；`initiate_chat` 发话记 `_pending_chat_desire_id`；`check_timeouts(now)` 问句超时调 `memory.record_no_answer`、搭话超时调 `desire.expire`；`reply` 入口清两者待回应态，且用户回复搭话时调 `desire.satisfy(desire_id, True)` 闭环消费

## 技术方案

- **新文件**：`nyx/expression/facade.py`、`nyx/expression/pipeline.py`、`nyx/expression/mutter.py`（无 API、无数据变更——会话历史 `deque` 是内存态）
- **库**：`langgraph`（`StateGraph` / `END` / `CompiledStateGraph`，与 14-activity 的 exploration 同源）
- **公开面**：`from nyx.expression.facade import ExpressionFacade`；`from nyx.expression.mutter import naturalize_presence, clean_fragment, activity_subject, pick_mutter_category, pick_mutter_template, should_initiate_chat`（不加 `__all__`）
- **Facade 依赖注入**：`__init__(bus, llm, evaluator, memory, activity, desire, inner_life, canon, ask_guidance, config, tools)`——`activity: ActivityFacade` 供碎碎念 ACTIVITY 类取最近活动；`canon: str` 由 18-api 组合根读 `prompts/canon.md`、`ask_guidance: str` 读 `prompts/ask.md` 传入（本 spec 不读文件，测试不碰文件系统）；`tools: ToolRegistry` 由组合根 `_build_tools(config)` 传入，仅慢通道 use_tools 用；`ask_guidance` 仅慢通道 think/speak 与 `initiate_chat` 注入，快通道省略
- **会话历史（内存）**：`deque[Message]`（maxlen=`config.max_context_len`）由 facade 持有，跨 reply 持久。**用户消息 + Nyx 消息（多轮拼接）都在回合末的 `record_message` 节点按序 append**（先 user 后 nyx）——`reply` 入口回溯时当前消息还没进 history，天然不重复。重启丢失（同情感，内存易变态）
- **多轮语义（慢通道）**：think → speak 循环，每轮 think 发 `THINK`、每轮 speak 发 `SPEAK`（**都交付**）；某轮 speak 是问句 → 发 `ASK` 后回合结束。`slow_max_rounds` 是「连续无 ask 的 think/speak 轮数上限」。**累积式 prompt**：后一轮的 think/speak 知道前几轮想了/说了什么（`_rounds_block` 拼前轮）。**递进续写**：首轮任务指令是「先说出一句 / 先想此刻的念头」，续写轮切换为「再往下说一句 / 再往里想一层」，避免三段生成三个并列回答
- **场景化记忆记整个回合**：`nyx_think`/`nyx_speak` = 多轮 `"\n".join(...)` 拼接（`create_scene_memory` 的 `str` 契约不变，只是内容是多轮）
- **MVP 语义**：ask 后回合结束（走 scene_memory + record）；用户回应作为下一条 `USER_MESSAGE` 触发新 reply，round 自然从 0 重算
- **V2 表达交互闭环**：facade 在 reply 后按 `result["ask"]` 置 `self._waiting_user`（问句已问出、等用户答）；`initiate_chat` 记 `self._pending_chat_desire_id`（搭话已发、等用户回）。tick 心跳（18-api 组合根）直呼 `check_timeouts(now)`：问句超时（`ask_timeout`）→ `memory.record_no_answer` 落一条「用户没回答」的 SHORT_TERM 记忆；搭话超时（`chat_ignore_timeout`）→ `desire.expire`（值立即 +0.3 回灌）。用户任一下条消息（`reply` 入口）即视为回应、清两者待回应态
- **慢通道工具调用**：`use_tools` 节点（慢通道专属）在 assemble 后问 LLM 是否需查资料，有 `tool_calls` 就逐个执行（`ToolRegistry.call`）并把结果 `json.dumps` 拼进 `tool_outputs`，think/speak 的 system prompt 追加「[工具查询结果]」段；一轮，不做 agentic 循环；单条结果超 `_TOOL_OUTPUT_MAX_CHARS` 截断（尾加 `…`）；工具执行失败降级为失败文案（best-effort，不崩回复）
- **回溯检测（V2）**：快通道入口朴素取最近 `max_context_len` 条；慢通道 `assemble` 调 `build_backtrack_context` 重截断——从新到旧累积，命中「满 max_len / 相邻隔超 `context_time_gap` / 与当前消息零字符重叠（十分不相关）」即停，快通道 Nyx 消息（`fast=True`）跳过继续往前（浅层回复不占上下文、不断深聊线程）
- **搭话 `last_chat_at` 归 18-api**：`should_initiate_chat` 是纯函数（判定触发），`initiate_chat` 返回 `bool` 作为「是否真发话」的信号；18-api 组合根据此更新 `last_chat_at`（`since_last_chat` 的来源），facade 不持有搭话状态
- **碎碎念去人机感（模板为主 + 低频 LLM 即兴 + 数据具体化）**：`mutter` 空闲命中后，`_LLM_MUTTER_RATE`（0.2）概率走 `_mutter_wander`（`output_type="mutter_wander"`、`build_system_prompt(canon, state)` + 一句自然口语，空回退模板）；否则按类填空——ACTIVITY 读活动产出 `progress["result"]` 的 `book`/`title`/`core_discovery`（`activity_subject` 转「读了《书名》」等具体指涉），MEMORY/USER 读 `content`（优先）或 `summary` 经 `clean_fragment` 清洗（「用户（presence）」观察串 → `naturalize_presence` 润色，raw 枚举不泄漏），DESIRE 读 `description`；骨架池带 `{subject}` 占位 + 内嵌停顿/自我修正/走神语气词；发前查 `_mutter_seen`（deque maxlen 8）去重，近期说过同一句不发
- **明确不做**：`POST /api/chat`（归 18-api）；观察用户在线/忙状态（归 14-activity 的 observe，本 spec 只接收 `online`/`busy` bool）

> 注：`facade.py` 与 `pipeline.py` 曾各有一份 `_make_event`（构造 `Event` 纯函数）。第五轮 review 判定为重复，已下沉到 `events/event.py` 的 `internal_text_event`（`content` 纯文本 → 包装成 `{"content": content}`）；两模块改 import 单一来源，删各自副本。

## 测试要点

- [ ] 单元测试 `tests/test_expression/`（纯函数 + `:memory:` db + fake llm/memory/desire/inner_life/evaluator/bus）：
  - [ ] **mutter 纯函数**（`test_mutter.py`，无 DB、无 async）：
    - [ ] `pick_mutter_category`：`roll<0` / `roll>=1.0` → `None`；`roll=0.0/0.25/0.5/0.75` 均匀映射到四类（ACTIVITY/MEMORY/DESIRE/USER）
    - [ ] `_MUTTER_SKELETONS`：四类齐全（`set(keys) == set(MutterCategory)`）、每类 `len == 10` 且无重复、每条含 `{subject}` 占位
    - [ ] `pick_mutter_template`：`roll<0` / `roll>=1.0` → `None`；`roll=0.0` → 该类第 0 条；`roll=0.999` → 该类最后一条；返回值 ∈ 该类骨架池
    - [ ] `naturalize_presence` / `clean_fragment` / `activity_subject`：`"away"`→`"你走开了"` 且输出不含 raw 枚举；`clean_fragment("用户（away）")` 不含 `"away"`；`activity_subject(READING, {"book":"挪威的森林"}) == "读了《挪威的森林》"`（CREATION/探索/缺数据回退 summary 逐项）
    - [ ] `should_initiate_chat`：五条件任一不满足 → `False`（含 interaction 欲望、online、busy、energy、since_last_chat 逐项置反）；全满足 → `True`
  - [ ] **pipeline 纯函数**（`test_pipeline.py`）：
    - [ ] `_is_question`：`"你今天好吗？"` → True；`"你今天怎么样"` → True（含「怎么」）；`"我很好。"` → False
    - [ ] `_rounds_block`：`([], [])` → `""`；`(["t1"], ["s1"])` → 含「第1轮内心：t1」「第1轮对外：s1」；`(["t1","t2"], ["s1","s2"])` → 含两轮且顺序正确
  - [ ] **facade 集成**（`test_expression_facade.py`，mock LLM 按 `output_type` 返回 fixture，mock bus 记录 `publish`、fake 注入不碰 db；文件名为避免与 `test_memory/test_facade.py` 同 basename 冲突而加前缀）：
    - [ ] `reply` 快通道（classify 因子令 score < threshold）：`llm.complete` 调 2 次（think + speak，各 1 次）、`memory.search` / `memory.create_scene_memory` 未被调、`evaluator.evaluate` 调 2 次、`bus.publish` 收到 `think` + `speak` 各 1 条
    - [ ] `reply` 快通道问句（classify 令 score < threshold，mock speak 返回问句）：`bus.publish` 收到 `ask`（非 `speak`）、`result["ask"]` 置 `_waiting_user`（快通道绕过 should_ask，问句信号不丢）
    - [ ] `reply` 慢通道非问句（score ≥ threshold，mock speak 恒非问句）：`memory.search` 被调、`create_scene_memory` 被调、`llm.complete` 调 `2 × slow_max_rounds + 1` 次（tool 1 次 + think+speak 各 3 次）、`bus.publish` 收到 `think` 3 条 + `speak` 3 条（**每轮交付**）、`create_scene_memory` 的 `nyx_speak`/`nyx_think` 是 3 轮 `"\n"` 拼接
    - [ ] **慢通道检索命中记 recall**：fake `memory.search` 返回 2 条命中 → `record_recall` 对每条记忆 id 各调 1 次（`recalled == ["m1", "m2"]`）；返回空 → 不调
    - [ ] `reply` 慢通道问句（第 1 轮 speak 返回 `"你还好吗？"`）：`bus.publish` 收到 `ask`（非 `speak`）且仅 1 条、`create_scene_memory` 仍被调（问句也走场景化记忆）、提前结束（tool 1 次 + think/speak 各 1 次，不循环到满）
    - [ ] **慢通道工具调用**：fake llm 返回 `tool_calls=[{"name": "local_search", "args": {...}}]` → `tools.call` 被调、结果拼进 think 的 system prompt「[工具查询结果]」段；大结果（超 `_TOOL_OUTPUT_MAX_CHARS`）→ 注入段被截断（尾带 `…`、越界 sentinel 不出现）；fake llm 返回空 `tool_calls` → 无该段；工具抛异常 → 降级「工具 X 执行失败」不崩回复
    - [ ] **累积式 prompt**：慢通道非问句多轮下，fake llm 记录的第 2 轮 think 调用 user prompt 含第 1 轮的 think 文本与 speak 文本；第 2 轮 speak 调用 user prompt 含第 2 轮 think 文本
    - [ ] **慢通道递进续写**：慢通道非问句多轮下，第 1 轮 speak 的 user prompt 含「先说出一句」、不含「再往下说一句」；第 2 轮 speak 的 user prompt 含「再往下说一句」
    - [ ] **当前消息不重复**（回归）：慢通道下，fake llm 记录的 think/speak 调用里，`[对话历史]` 段不含当前消息文本、`[本次消息]` 段含且仅含一次
    - [ ] **history 落库顺序**：连续两次 `reply` 后，facade 内部 history 的 role 序列为 `[user, nyx, user, nyx]`；两次都走快通道时第二次 prompt 仍含上一轮历史（回归：历史不因快通道丢失）
    - [ ] **快通道 nyx 落库标记**：快通道 `reply` 后，history 里 `role="nyx"` 的消息 `fast=True`、`role="user"` 的消息 `fast=False`（回溯截断据此跳过浅层回复）
    - [ ] **慢通道回溯跳过快通道 nyx**：先走一次快通道（产生 `fast=True` 的 nyx 消息）、再走慢通道 reply 相关话题，断言慢通道 think/speak 的 user prompt 含更早的相关用户消息、不含那条快通道 nyx 消息
    - [ ] `mutter`：`state.current_activity` 非 None → 不发；`random.random()` 命中（monkeypatch）+ 该类数据源有值 → 发 `mutter`（content 含具体填充文本——书名/记忆片段/画像、`correlation_id == 传入值`）；`random` 命中 `_LLM_MUTTER_RATE` → 走 `_mutter_wander`（`output_type="mutter_wander"`）；LLM 即兴空 → 回退模板；观察串「用户（away）」→ 润色不含 raw 枚举；连续两次相同文本 → 第二次去重不发；该类数据源空 → 不发；未命中 → 不发
    - [ ] `initiate_chat`：mock `llm.complete` 返回空 content → 返回 `False` 且不发；返回非空 → 返回 `True` 且发 `initiate_chat`（`output_type="initiate_chat"`、correlation_id 一致）
    - [ ] `initiate_chat` 落历史：非空发话后 facade 内部 history 含一条 `role="nyx"`、content 为开场白的消息（用户随后回复可回溯搭话内容）
- [ ] 集成测试：无（真实 LLM 不测；Facade 间的编排归 18-api 组合根）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] ripple 同步：tech-ref §6.1 `ReplyState` 的 `think`/`speak` 从 `str | None` 改 `list[str]`（多轮累积）、补 `narrative: SelfNarrative | None` 与 `correlation_id: str` 两字段、edges 补「每轮 SPEAK 交付 + ask 后回合结束走 scene_memory」；tech-ref §5 `initiate_chat` 签名 `-> bool`（发话 True/无话 False）、`mutter` 签名补 `correlation_id: str`（MUTTER_CHECK tick 恒定根）
- [ ] ripple 同步（V2 交互闭环）：tech-ref §5 `ExpressionFacade` 补 `check_timeouts(now)`；02-config `ExpressionConfig` 补 `ask_timeout`/`chat_ignore_timeout` 两字段；09-memory-facade 补 `record_no_answer`；18-api `_tick_loop` 每轮心跳 `await app.expression.check_timeouts(now)`
- [ ] 下游约定：18-api 组合根 `canon` = `prompts/canon.md`、`ask_guidance` = `prompts/ask.md` 读入后注入 `ExpressionFacade`；`POST /api/chat` → `ExpressionFacade.reply(msg, correlation_id)`；`INITIATE_CHAT_CHECK` tick 由组合根调 `should_initiate_chat` 判定、从 `DesireFacade.get_pending()` 选 interaction 欲望后 `await initiate_chat(desire, state)`，返回 `True` 才更新 `last_chat_at`；`MUTTER_CHECK` tick → `mutter(state, event.correlation_id)`；组合根构造 `ExpressionFacade` 时在 `memory` 实参后注入 `activity`（activity 先于 expression 构造）
