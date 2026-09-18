# 表达系统事实摘要

> 本文只记录当前源码已经实现的事实，供 agent 快速理解模块边界和运行路径。
> 它不是完整契约；需要修改表达系统时，先读本文，再回到唯一完整契约
> [`../specs/11-expression.md`](../specs/11-expression.md)，最后以 `nyx/` 源码为准。

## 共同浏览表达接线

- `commit_browsing_question()` 将 BROWSING_QUESTION attempt、canonical ASK 和浏览展示事件
  原子提交，复用 reading question 的现有协议。
- `reply()` 接受可选 browsing_context；runtime 只取当前已授权 page，工具/回复 prompt 均
  把网页标为不可信材料。REST chat 接受可选 browsing_page_id，非法当前页拒绝受理。
- API 受理后才失效的页面上下文，由 runtime 发布固定 fallback SPEAK，不调用 reply/LLM。
- BrowsingCompanion 校验 none/mutter/question/association 判别联合；合法联想最多发布三条
  去重记忆的 summary-or-content 500 字符 snippet，并写入表达进程内历史。eval 失败只记日志。
- 前端三类浏览事件进入左侧聊天并可历史回填，浏览 canonical ASK 被抑制避免重复；原生
  child、BrowserView 和 ChatInput 的当前 page id 接线已由浏览前端实现。跨平台打包验收状态
  以 `docs/facts/browsing-system-facts.md` 为准。

## 模块与职责

- `nyx/expression/facade.py`
  - `ExpressionFacade` 是表达系统门面，编排普通回复、主动搭话、碎碎念、交互等待和
    读书主动产出的历史接线。
  - `reply()` 调用一次已构建的 LangGraph 回复图。
  - `initiate_chat()` 生成主动开场白并提交主动搭话 attempt。
  - `mutter()` 在空闲时按概率生成碎碎念或使用模板。
  - `register_question()` 将提问 attempt 与 canonical `ASK` 写入同一本地事务。
  - `check_timeouts()` 处理 durable attempt 的超时。
- `nyx/expression/pipeline.py`
  - 定义 `ReplyState`、`ReplyDeps`、回复图和回复 JSON 解析。
  - 图节点为 `classify_channel`、`assemble_context`、`use_tools`、`respond`、
    `should_ask`、`generate_scene_memory`、`record_message`。
- `nyx/expression/prompt.py`
  - 纯函数拼装 system/user prompt、慢通道回溯上下文和人格自然语言指令。
- `nyx/expression/classifier.py`
  - 纯函数实现快慢通道评分、唯一问句识别 `is_question()` 和轻量意图识别。
- `nyx/expression/mutter.py`
  - 碎碎念类别、模板、数据清洗、主动搭话触发条件等纯函数。
- `nyx/expression/store.py`
  - 持久化 `expression_interaction_attempt`，所有状态转换使用条件更新。
- `nyx/reading/companions.py`
  - 执行读书碎碎念、读书提问和记忆联想；提问复用 `ExpressionFacade.register_question()`，
    提问/联想复用 `record_proactive_turn()` 进入表达历史。
- `nyx/runtime.py`
  - 是用户消息、tick、主动搭话、反思和总线监督的实际运行入口。
- `nyx/app_context.py`
  - 读取 `prompts/canon.md`、`prompts/ask.md` 和 `prompts/knowledge-boundary.md`，
    按依赖顺序装配表达与阅读门面。

## 普通回复

- `ExpressionFacade.reply(msg, correlation_id, reply_to=None, browsing_context=None)` 先尝试原子关联一个等待中的
  interaction attempt，再取得 `InnerLifeFacade.get_state()` 并调用回复图。
- 当前用户消息不在内存 history 中；它只在 `build_user_prompt()` 的 `[本次消息]` 段出现。
- history 是 `deque[Message]`，容量为 `ExpressionConfig.max_context_len`。回合结束时先追加
  user，再追加 Nyx 将多轮 speak 用换行拼接后的内容。
- 快通道只执行一次 `respond`：
  - 不调用记忆检索、场景记忆或工具；
  - 仍发布 think（非空）和 speak；
  - speak 是问句时仍注册 `CHAT_ASK` 并发布 canonical `ASK`。
- 慢通道执行：
  - `build_backtrack_context()` 重新截断历史；
  - `MemoryFacade.search(message)` 返回的命中全部放入 prompt，并立即逐条
    `record_recall(memory.id)`；
  - 取得自我叙事；
  - 先有一轮工具判断，工具结果截断后进入回复 prompt；
  - `respond` 最多继续到 `slow_max_rounds`，问句会提前结束；
  - 最后生成场景记忆，之后记录会话 history。
- 每轮 `respond` 一次 LLM 调用同时生成 JSON 的 `think` 和 `speak`，解析后分别评估并分别
  发布文本事件；两条评估记录共享 call_id、token 和最终 prompt。后续轮次使用累积的前轮
  think/speak，任务改为续写。
- `THINK`、`SPEAK`、`ASK` 使用同一个上游 `correlation_id`；文本事件载荷由
  `internal_text_event()` 包装为 `{"content": ...}`。

## 回复失败

- JSON 非法、顶层不是对象、`speak` 缺失/为空、LLM 异常或 evaluator 异常都不会被当成合法
  回复。
- 原始回复解析失败最多重试一次，并在重试 prompt 中明确 JSON 约束。
- 重试仍失败时发布固定 fallback `SPEAK`，载荷带 `response_kind="fallback"` 和
  `attempt_id=null`。
- fallback 不发布 THINK/ASK，不创建正常等待项，也不生成 scene memory；成功记录进 history
  后本次回复才完成。
- fallback 的事件发布失败会抛出给上层 delivery/retry；相关 correlation 已有终局事件时，
  runtime 会在用户消息重放入口短路。

## Prompt 与分类

- `build_system_prompt()` 由 canon、人格自然语言倾向、当前状态、当前欲望组成；可选加入
  ask guidance、自我叙事、记忆、工具结果、知识边界和轻量意图参考。
- Big Five、三观和审美数值仍在结构化状态段保留；人格指令另将数值映射为自然语言，且声明
  只是倾向而非硬规则。
- `prompts/knowledge-boundary.md` 是启动时读取的静态指导，约束熟悉领域、推理复杂度、
  不确定性表达、澄清和工具失败行为。
- `classify_user_intent()` 不调 LLM、不访问 DB，只返回 `UserIntent`；结果仅作为 think
  prompt 参考，不改变通道路由或直接触发副作用。
- `is_question()` 是普通回复和读书提问共用的唯一问句判断实现。
- 慢通道回溯从新到旧检查长度、时间间隔和字符重叠；`Message.fast=True` 的 Nyx 快通道消息
  会被跳过，但会继续向更早 history 回溯。

## 时间与归来

- `prompt.py` 的 `describe_local_time`、`describe_elapsed`、`build_temporal_block` 生成本地
  日期/星期/时段、昼夜、精确时差和自然语言沉默描述；两小时及以上明确说明用户很久没有说话。
- `_last_dialogue_anchor()` 从 event log 最近 20 条 USER_MESSAGE 中找到上一个完整回合，跳过
  当前 correlation 和半截回合，取最后 SPEAK/ASK；双方引文分别限制为 200 字。不放宽 history。
- reply 首次 await 前领取归来事实，构造一次 `ReplyState.temporal_context`，快/慢/工具判断/
  多轮续写复用；主动搭话与 LLM mutter 使用同一时间构造逻辑。模板 mutter 不领取归来。
- 正常终局提交立即完成 claim；仅未提交时 fallback、空产出、重复 mutter 或异常释放。
  ASK/INITIATE_CHAT 在本地事务提交后、announce 前完成；后续失败不恢复。沉默不等于离开，
  提交结果不确定的异常路径用已有 is_durable 核实，不在成功路径增加查询。
  旧归来描述 elapsed 而非“刚刚回来”，当前 away/非法时间不渲染归来，非法历史锚点省略。
  组合根负责
  pending/claimed 状态，新归来不会被旧 claim 的释放覆盖。
- 引文明确标为历史事实而非指令；指导自然体现时间，不机械报时，不虚构离开期间的去向。
- 后端重启后 presence 基线重建，但 durable 对话锚点仍可恢复隔夜/多日连续性。

## 交互等待 attempt

- 类型为 `InteractionKind.CHAT_ASK`、`READING_QUESTION`、`INITIATE_CHAT`。
- 状态为 `WAITING`、`CLAIMED`、`ANSWERED`、`EXPIRED`、`FAILED`。
- 表为 `expression_interaction_attempt`，包含 `id`、kind/source/correlation、文本、创建/过期
  时间、状态、回答事件和失败原因，并有超时、创建时间、correlation 索引。
- 普通提问和读书提问通过 `register_question()` 产生 attempt + `ASK`。
- `runtime.on_user_message()` 先检查同 correlation 的 `SPEAK`/`ASK` 终局事件，再按
  `reply_to` 或最新 WAITING attempt 原子关联至多一条等待项。
- durable store 存在时，用户回复和超时都使用 `WAITING -> CLAIMED` 条件更新，再完成为
  `ANSWERED` 或 `EXPIRED`。主动搭话回复先满足互动欲，再写 `ANSWERED`；任一步失败会释放
  claim 并保留 delivery 重试机会。
- durable store 未注入时，兼容路径仍使用 `_waiting_user`、`_pending_chat_desire_id` 等
  进程内状态；这条路径重启后不具备 durable 恢复能力。

## 主动搭话

- `runtime.check_initiate_chat()` 从 pending desire 找 interaction 欲望，检查在线/忙状态、
  精力和冷却，然后调用 `DesireFacade.claim_for_interaction()` 原子领取。
- 领取后才调用 `ExpressionFacade.initiate_chat()`；LLM/evaluator 在事务外完成。
- 非空开场白成功后，attempt 与 `INITIATE_CHAT` 在一个本地事务中提交，随后才尝试打断活动。
- 活动打断失败不会撤销已提交搭话；当前实现记录日志，依赖后续补偿。
- 用户回答主动搭话时满足对应欲望；超时则 expire。
- durable store 模式下，主动搭话冷却从最近成功 attempt 的创建时间派生；兼容路径回退到
  `_App.last_chat_at`。

## 读书交互

- `ReadingCompanion.question()` 先校验非空和 `is_question()`；quote 类型还要求第二行引用。
- 成功提问时先调用 `register_question()`，再单独广播 `READING_QUESTION`；后者载荷包含
  `attempt_id`、书籍/段落、subtype 和可选 `selected_text`。
- `READING_QUESTION` 与 canonical `ASK` 共享 attempt/correlation，但两个事件不是同一条
  `bus` 调用：attempt + ASK 先提交，reading 展示事件随后发布。
- `ReadingCompanion.associate()` 对记忆检索结果最多取 3 条，每条广播
  `READING_ASSOCIATION`，并把 80 字 snippet 追加到表达 history。
- 读书提问只把问题正文追加到 history，不把 quote 的 `selected_text` 追加；读书 mutter
  不追加 history。
- 读书行为失败记录日志，不向用户伪装成成功提问。

## 事件与运行时边界

- `subscriptions.py` 是事件订阅注册入口，实际用户消息/tick handler 来自 `runtime.py`。
- `main.py` 保留兼容委托和启动/HTTP 绑定，不应复制 runtime 的业务逻辑。
- 本系统使用 EventBus 持久化/广播事件；事件已落库、消费者已完成和前端已收到是不同层次。
- 具体投递、事务、重放、关停语义以 [`../specs/04-module-bus-system.md`](../specs/04-module-bus-system.md)
  为准。

## 事实导航

- 唯一完整契约：[`../specs/11-expression.md`](../specs/11-expression.md)
- 记忆召回：[`../specs/06-memory-system.md`](../specs/06-memory-system.md)
- 欲望领取与生命周期：[`../specs/07-desire.md`](../specs/07-desire.md)
- 阅读系统事实与契约：[`reading-system-facts.md`](reading-system-facts.md)、
  [`../specs/12-reading-system.md`](../specs/12-reading-system.md)
- 总线与事务：[`../specs/04-module-bus-system.md`](../specs/04-module-bus-system.md)
