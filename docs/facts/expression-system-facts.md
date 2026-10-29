# 表达系统事实摘要

> 本文只记录当前源码已经实现的事实，供 agent 快速理解模块边界和运行路径。
> 它不是完整契约；需要修改表达系统时，先读本文，再回到唯一完整契约
> [`../specs/11-expression.md`](../specs/11-expression.md)，最后以 `nyx/` 源码为准。

## 模块与职责

明确含 URL 的问卷/答题委托直接走慢通道，使工具判断可调用 `browser_survey` 受理后台任务；回复只报告已受理，最终结果由活动页展示。

角色身份、canon、可选 reference 和 ask 方向来自角色卡；内置 Nyx 位于 `nyx/cards/nyx/`，身份句将 Nyx 标注为“中文：尼克斯”。每个角色独立等待 attempt、回复历史、LLM FIFO 锁和 eval；当前角色资格在实际开始主动表达前复查，跳过不计欲望失败。已开始的表达完成到原角色，历史 assistant 使用中性“你”标签。结构/工具协议/知识边界仍由框架提供。

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
    `should_ask`、`record_message`。
  - `use_tools` 内部运行持久 LangGraph 工具子图（`tool_pipeline.py`）：先 JSON Plan/PlanStep，再最多4轮判断；允许一次 replan，保留结果、
    去重及预算。计划非法/空/调用失败退回无计划执行，伪工具 replan 不进入公开注册表。
  - 合法计划的只读调用耗尽且仍有预算时，先经 `review_plan` 输出 continue/replan/stop，
    continue 指向原计划步骤，replan 进入既有重规划节点；决策与原因随 checkpoint 保存，
    恢复不重复决策。旧 checkpoint 缺字段时补做；空结果、无计划和 uncertain 不进入该节点。
    重规划后反馈明确计划已更新、已用/剩余次数，避免把旧决策再次执行。
  - 初次规划、重规划和慢回复注入 LESSON，重规划/回复刷新召回以使用本轮刚产生的失败经验；
    快回复不召回。只读工具错误最多尝试3次，耗尽后学习并交回模型；副作用未确认进入 uncertain，不自动重放或沉淀失败；空结果不重试、直接学习。实际调用记录 log_id/correlation_id/call_id，
    只有回注结果后才 mark_adopted，预算丢弃仍保留 adopted=0。
- `nyx/expression/prompt.py`
  - 纯函数拼装 system/user prompt、慢通道回溯上下文和人格自然语言指令。
- 召回的 `conversation_segment` 以独立 `[相关对话段（历史原文，仅作参考）]` user-prompt 区块注入，不加入当前会话 `Message` 历史。
- 慢通道仅对真正进入该区块预算的段按当前用户事件幂等记录召回；检索候选未展示不计，仍不调用旧 `MemoryFacade.record_recall`。
- `nyx/expression/classifier.py`
  - 纯函数实现快慢通道评分、唯一问句识别 `is_question()` 和轻量意图识别。
- `nyx/expression/mutter.py`
  - 碎碎念类别、模板、数据清洗、主动搭话触发条件等纯函数。
- `nyx/expression/store.py`
  - 持久化 `expression_interaction_attempt`，所有状态转换使用条件更新。
  - `ExpressionFacade.expression_history()` 从 durable event log 聚合对话、碎碎念和主动搭话记录，供 REST/前端历史面板查询。
- `nyx/reading/companions.py`
  - 执行读书碎碎念、读书提问和记忆联想；提问复用 `ExpressionFacade.register_question()`，
    提问/联想复用 `record_proactive_turn()` 进入表达历史。
- `nyx/runtime.py`
  - 是用户消息、tick、主动搭话、反思和总线监督的实际运行入口；用户消息会在回复前
    采样并尝试打断当前活动，把打断前/后的 `ActivityContext` 传给表达层。
- `nyx/app_context.py`
  - 读取 `nyx/prompts/canon.md`、`nyx/prompts/character-reference.md`、
    `nyx/prompts/ask.md` 和 `nyx/prompts/knowledge-boundary.md`，
    按依赖顺序装配表达与阅读门面。

## 普通回复

- `ExpressionFacade.reply(msg, correlation_id, reply_to=None, activity_context=None, discussion=None, annotation=None)` 在无批注时先尝试
  原子关联一个等待中的 interaction attempt，再取得 `InnerLifeFacade.get_state()` 并调用
  回复图；未注入上下文时才从活动门面读取当前快照。
- `ReplyAnnotation` 保存原始 `reply_event_id/quote/comment`；Chat API 验证当前角色精确 SPEAK 和连续原文，
  durable USER_MESSAGE → runtime → ReplyState → 共有 user prompt 全程传递。FAST、SLOW、工具判断和续写
  都包含 `[对上轮回复的批注]`；quote 标明是 Nyx 旧回复数据，不当作指令或用户事实。
  批注本身不强制讨论/慢通道，也不 claim 或清除 waiting 问题；普通消息仍沿用旧绑定规则。
- `discussion` 为 true 时强制加载技能，为 false 时禁用，为 null/省略时调用
  `should_load_discussion()`。技能加载即强制慢通道；普通陪聊或明确拒绝讨论不自动加载。
- 组合根从 `nyx/prompts/skills/discussion.json` 加载 frozen `Skill`，启动时校验片段长度、
  名称唯一和工具授权。base prompt 不含讨论范式或引用纪律；仅加载技能的回合注入该片段。
- 当前用户消息不在内存 history 中；它只在 `build_user_prompt()` 的 `[本次消息]` 段出现。
- history 是 `deque[Message]`，容量为 `ExpressionConfig.max_context_len`。回合结束时先追加
  user，再追加 Nyx 将多轮 speak 用换行拼接后的内容。
- `build_system_prompt()` 的 `[本轮活动事实]` 只读取本轮 `ActivityContext` 已采样的类型、状态
  和白名单进度字段。打断前活动和打断后当前活动分别展示；仍为 `RUNNING` 或二次采样为空时
  不声称已暂停、完成或失败。
- user/Nyx history 条目可带活动 ID、类型和有界摘要；`build_user_prompt()` 将其作为
  “历史活动事实”附在对应消息后，旧条目没有元数据时不增加内容。
- 快通道只执行一次 `respond`：
  - 不调用记忆检索、场景记忆或工具；
  - think（非空）和 speak/ask 先缓存，生成完成后作为一个终局批次提交；
  - speak 是问句时仍注册 `CHAT_ASK` 并发布 canonical `ASK`。
- 慢通道执行：
  - `build_backtrack_context()` 重新截断历史；
  - 非讨论模式对截断后的近期对话构造 query，召回 `conversation_segment`；命中段以独立历史原文区块注入，不加入当前 `Message` 历史，也不调用旧 `MemoryFacade.search`/`record_recall`；讨论模式关闭隐式事实和段召回，改用能力工具；
  - 取得自我叙事；
  - 最多 4 轮工具判断，累计工具结果以文本进入下一轮判断和最终回复；最多 5 项，单项 JSON 至多 2000 字符，工具块至多 8000 字符。已完成/耗尽次数的同名同参数模型请求去重并解释，自动重试不去重；失败反馈保留参数、状态和错误，耗尽后同批剩余调用不执行；
  - `respond` 最多继续到 `slow_max_rounds`，问句会提前结束；
  - 所有轮次的正常 THINK/SPEAK、最终 `SPEAK/ASK` 在同一本地事务受理，随后记录 history；生成中途没有正常文本对外可见。
- 讨论模式仅授权 `memory_recall`、`knowledge_search`、`deep_research`，执行时再次核对；普通慢通道排除这三项且保留原工具。
  `memory_recall` 仅返回 profile/conversation 事实与对话段；完整段 JSON 真正进入最终回复 prompt 才幂等计召回。
  `knowledge_search` 返回 knowledge 事实、可空 `source_chunk_id` 及原文证据块，合并事实关联与相关块并按 ID 去重；不计对话段召回。
  `deep_research` 已接入 P3 有界自适应研究，保存原文与完整结论，回注精简报告、准确入库回执和
  来源数/限制说明；关闭联网直接返回“联网没开”。引用在提取、综合和压缩后按抓取正文校验。
  研究返回整个 JSON 至多 1960 字符；pipeline 与最终 prompt 都整项保留，余量不足时省略，
  不切断 JSON/citations，最终工具块包括标题和前缀仍至多 8000 字符。
- 每轮 `respond` 一次 LLM 调用同时生成 JSON 的 `think` 和 `speak`，解析后分别评估并分别
  发布文本事件；两条评估记录共享 call_id、token 和最终 prompt。后续轮次使用累积的前轮
  think/speak，任务改为续写。
- `THINK`、`SPEAK`、`ASK` 使用同一个上游 `correlation_id`；文本事件载荷由
  `internal_text_event()` 包装为 `{"content": ...}`。

## 回复失败

- 工具子图按 correlation 在角色 SQLite 保存原计划、调用参数、尝试次数和实际步骤状态；只读错误最多尝试3次，间隔1/2秒，耗尽后才交回模型，后续同批调用标为未执行。已有工具 checkpoint 强制慢通道续跑；工具状态和跳过原因通过 tool_feedback 进入决策及回复，仍受8000字符工具块总预算约束。schema50 的 tool_execution 在执行前可靠领取、执行后完整保存结果，恢复复用结果；副作用 running/error 转 uncertain 停批，旧 checkpoint 同样处理，回执错误向上抛。终局提交并删除 checkpoint 后清理回执，重启补清已完成残留。

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

- `build_system_prompt()` 由 canon、人格与三观自然语言倾向、当前状态、当前欲望组成；可选加入
  ask guidance、自我叙事、记忆、工具结果、知识边界和轻量意图参考。
- 回复、工具判断、主动搭话和走神的任务指令集中在 `nyx/expression/prompt.py`；流程模块只
  负责选择通道、调用 LLM 和提交事件。
- canon 与独立小说人物参考均作为角色参考；人物经历不作为 Nyx 的现实记忆。人格和审美
  倾向按 `<5`、`=5`、`>5` 三档提供行为说明；审美当前值位于 `[当前状态]`，不再提供词语
  参考；think prompt 的 user 层只保留 JSON 输出契约，角色内心推理表现由 canon 约束。
- 情感状态保留英文枚举，同时附中文含义，例如 `happy（开心）`、`sad（难过）`。
- 工具结果、当前欲望、记忆与历史均标为资料而非指令；当前消息单独位于 `[本次消息]`。
  走神指导由 `allow_wander` 显式启用，目前仅 mutter wander 调用传入 true。
- 慢通道回复 JSON 可含 `needs_continuation`；缺失/非法按 false，一般回复一次完成，仅显式
  续写时继续，并受问句和 `slow_max_rounds` 限制。
- 记忆 prompt 展示创建时间、内容来源、有效期和检索来源；未知内容来源/有效期展示为未标注，
  不修改 `Memory` 对象或持久 schema。
- Big Five、三观和审美数值仍在结构化状态段保留；人格指令另将数值映射为自然语言，且声明
  只是倾向而非硬规则。
- `nyx/prompts/knowledge-boundary.md` 是启动时读取的静态指导，约束熟悉领域、推理复杂度、
  不确定性表达、澄清和工具失败行为。
- 用户消息最多 12000 字符且不能只含空白；动态资料单项最多 2000 字符，历史、记忆、事实、
  阅读和工具块各有固定总预算，工具最多 5 项/8000 字符。工具判断 LLM/evaluator 失败时以
  明确的“未获得工具结果”事实降级继续回复。
- `classify_user_intent()` 不调 LLM、不访问 DB，只返回 `UserIntent`；结果仅作为 think
  prompt 参考，不改变通道路由或直接触发副作用。
- `is_question()` 是普通回复和读书提问共用的唯一问句判断实现。
- 慢通道回溯至多取最近 `max_context_len` 条 history；相邻间隔或总跨度超过 30 分钟、明确换话题时截断，短确认语继承最近一段。保留 `Message.fast=True` 的 Nyx 消息；记忆查询合并最近至多 3 条截取后消息与当前消息，总长不超过 300 字符。
- ExpressionFacade 启动后从 durable `event_log` 恢复最近 `max_context_len` 条表达历史；恢复
  是幂等的，无法解析的事件跳过并记录日志。等待中的 interaction attempt 仍由独立的
  `ExpressionInteractionStore` 恢复，供前端重新计算计时器。

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
- 用户回复读书提问时，`answer_waiting()` 返回当前事件领取或恢复的 `READING_QUESTION`
  attempt；`reply()`
  通过组合根注入的阅读回调，按 `source_id` 回读触发段落和书籍画像，并用“原问题 + 用户
  回复”检索该书来源内 Top 5；knowledge 层注入事实 `content`，不用主题型 `summary`
  替代。所得三层只读材料会同时进入 FAST/SLOW，因此“嗯”等短回复也不会脱离刚才读到
  的内容。
- `runtime.on_user_message()` 先检查同 correlation 的 `SPEAK`/`ASK` 终局事件，再按
  `reply_to` 精确关联；未提供时仅在恰好一个 WAITING attempt 时关联，多个等待项不猜测。
- durable store 存在时，用户回复领取把 `answer_event_id` 写为 owner；同 owner 重放可恢复
  CLAIMED/ANSWERED，其他事件不能抢占。被回答 attempt 与回复终局事件同事务完成为
  ANSWERED；终局前失败按 owner 释放并清空 id。超时仍使用无 owner 的条件领取。
- `paused_at` 非空表示用户暂停提问计时；暂停项保留 WAITING 且不参与超时领取，恢复时按暂停
  时剩余时长重算 `expires_at`。`GET /api/questions/pending`、`POST .../{attempt_id}/pause`
  和 `resume` 供前端恢复及控制计时。
- schema 35 升级会把历史上无 `answer_event_id` 的 CLAIMED 恢复为 WAITING；带 owner 的新领取
  保持原状，由同 owner 重放恢复。
- durable store 未注入时，兼容路径仍使用 `_waiting_user`、`_pending_chat_desire_id` 等
  进程内状态；这条路径重启后不具备 durable 恢复能力。

## 主动搭话

- `runtime.check_initiate_chat()` 从 pending desire 找 interaction 欲望，检查在线/忙状态、
  精力和冷却，然后调用 `DesireFacade.claim_for_interaction()` 原子领取。
- 领取后才调用 `ExpressionFacade.initiate_chat()`；LLM/evaluator 在事务外完成。
- 非空开场白成功后，attempt 与 `INITIATE_CHAT` 在一个本地事务中提交；事件携带同一
  `attempt_id` 和 `expires_at` 供前端实时计时，随后才尝试打断活动。
- 活动打断失败不会撤销已提交搭话；当前实现记录日志，依赖后续补偿。
- 用户回答主动搭话时满足对应欲望；超时则 expire。
- durable store 模式下，主动搭话冷却从最近成功 attempt 的创建时间派生；兼容路径回退到
  `_App.last_chat_at`。

## 读书交互

- `ReadingCompanion.question()` 先校验非空和 `is_question()`；quote 类型还要求第二行引用。
- 正式组合根下，成功提问通过 `commit_reading_question()` 把 attempt、canonical `ASK` 和
  展示用 `READING_QUESTION` 放在同一本地事务中；两者共享 `attempt_id` 和 `expires_at`，前端
  按 attempt 合并成一条提问气泡。后者载荷还包含书籍/段落、subtype 和可选 `selected_text`。
  只在兼容未提供该方法的 fake 时退回分步路径。
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
