# 表达系统

> 本文件是表达系统唯一完整契约，合并原表达 prompt、表达流程和读书交互契约的表达侧内容。
> 本 spec 只定义接口、边界和可验证语义；当前实现细节以 `nyx/` 源码为准，快速查阅先读
> [`docs/facts/expression-system-facts.md`](../facts/expression-system-facts.md)。

## 元信息

- **前置依赖**：01-types、02-config、03-llm、04-module-bus-system、05-tools、
  06-memory-system、07-desire、08-inner-life、09-activity、10-eval、12-reading-system
- **实现文件**：`nyx/expression/facade.py`、`nyx/expression/pipeline.py`、
  `nyx/expression/prompt.py`、`nyx/expression/classifier.py`、`nyx/expression/mutter.py`、
  `nyx/expression/store.py`、`nyx/reading/companions.py`、`nyx/reading/facade.py`、
  `nyx/runtime.py`、`nyx/main.py`、`nyx/app_context.py`、`nyx/types.py`、`nyx/enums.py`、
  `nyx/db.py`、`nyx/prompts/knowledge-boundary.md`
- **联动文档**：04-module-bus-system、06-memory-system、07-desire、08-inner-life、
  09-activity、12-reading-system、10-eval、docs/tech-reference.md、
  docs/facts/expression-system-facts.md

## 系统边界

- `ExpressionFacade` 是表达侧唯一门面，负责回复、主动搭话、碎碎念、交互等待和表达历史。
- `pipeline.py` 负责回复图；prompt/classifier/mutter 模块中的纯函数不访问数据库、不调用
  LLM、不直接发布事件。
- `ReadingCompanion` 负责读书行为的生成和展示事件；提问等待、表达历史和问句判断复用本 spec。
- 读书提问的后续回复通过组合根注入的窄回调读取阅读上下文；表达层只识别
  `InteractionKind.READING_QUESTION` 和 `source_id`，不直接依赖 `ReadingFacade`。
- HTTP 端点和事件订阅属于组合根/总线契约，表达门面不直接承担 HTTP 路由。

## 公开接口

```python
class ExpressionFacade:
    async def reply(
        self,
        msg: str,
        correlation_id: str,
        reply_to: str | None = None,
        activity_context: ActivityContext | None = None,
    ) -> None: ...
    async def initiate_chat(
        self, desire: ShortTermDesire, state: CurrentState
    ) -> bool: ...
    async def mutter(
        self, state: CurrentState, correlation_id: str
    ) -> None: ...
    async def register_question(
        self,
        text: str,
        kind: InteractionKind,
        source_id: str,
        correlation_id: str,
        *,
        claimed_return: dict[str, float] | None = None,
        preceding_events: list[Event] | None = None,
        followup_events: list[Event] | None = None,
        answered_attempt: InteractionAttempt | None = None,
    ) -> str: ...
    async def answer_waiting(
        self, reply_event_id: str, reply_to: str | None = None
    ) -> InteractionAttempt | None: ...
    async def latest_initiate_chat_at(self) -> float | None: ...
    def record_proactive_turn(self, text: str) -> None: ...
    async def check_timeouts(self, now: float) -> None: ...
```

构造依赖为 bus、llm、evaluator、memory、activity、desire、inner_life、canon、
ask guidance、`ExpressionConfig`、tools，以及可选 durable interaction store、knowledge
boundary 文本、运行时 observation reader 和一次性归来上下文的 claim/finish/release 回调。
组合根负责读取 prompt 文件并注入；`_App` 本身不得传入 Facade。

时间相关的可选构造参数固定为：

```python
observation_reader: Callable[[], Awaitable[Mapping[str, object]]] | None = None
claim_return: Callable[[], dict[str, float] | None] | None = None
finish_return: Callable[[dict[str, float] | None], None] | None = None
release_return: Callable[[dict[str, float] | None], None] | None = None
```

## 回复图

### 状态

`ReplyState` 至少包含 message、mode、context、memories、state、narrative、think、speak、
ask、round、correlation_id、last_slow_at、tool_outputs、intent、temporal_context、
reading_context、activity_context、answered_attempt、claimed_return 和 fallback。

### 通道

- `classify_channel()` 使用消息长度、问句、情绪词、精力/唤醒度、距上次慢通道时间五个因子；
  得分大于等于 `slow_threshold` 为 `SLOW`，否则为 `FAST`。
- FAST 只执行一次回复生成，不检索记忆、不调用工具、不生成场景记忆；仍必须交付 THINK
  （非空时）和 SPEAK。
- FAST 通常不检索记忆；用户正在回答 durable 读书提问时例外：`reply()` 使用
  `answer_waiting()` 返回的 attempt 构造 `reading_context`，直接注入该回合。它不是普通
  `MemoryFacade.search(message)`，因此“嗯”“我不觉得”等短回复也不会丢失书中语境。
- SLOW 在回复前执行回溯、记忆检索、recall 记录、自我叙事读取和一次工具判断；回复可连续
  多轮，轮数上限由 `slow_max_rounds` 定义。
- 慢通道召回按 memory id 去重，并用当前用户事件的 `correlation_id` 调用
  `record_recall(memory.id, user_event_id)`；同一用户事件重试不重复计数。
- `select_prompt_memories(memories: list[Memory]) -> list[Memory]` 是 prompt 模块的纯函数：
  按搜索排序选择可完整放入 12000 字符记忆块的前缀（预算含标题与换行），单条完整展示行
  最多 2000 字符，超长行先截断；遇到首个放不下的条目停止，不截断尾条来填满剩余空间。
  `assemble_context` 与记忆段渲染共用此规则；只对入选记忆去重记录 recall，工具判断和
  后续各轮回复使用同一入选集合。空集合不记录；落选记忆不增加计数、不触发晋升。
- FAST 和 SLOW 的正常 THINK/SPEAK 都先缓存在 `ReplyState`，生成完成前不发布任何正常文本
  事件。FAST 一轮结束后原子提交全部文本；SLOW 命中问句或达到轮数上限后，按生成顺序把
  全部 THINK/SPEAK、终局 ASK（如有）和 `scene_memory_requested` 作为一个终局批次提交。
  问句最后一段只生成 ASK，不重复生成同内容 SPEAK。未命中且未达上限时只继续续写。
- 场景记忆请求携带完整 user message、累计 think 和累计 speak，由 `memory.scene_reply`
  durable consumer 异步生成记忆；回复图不等待 scene LLM。FAST 与 fallback 不产生该请求。

### Prompt

- `build_system_prompt()` 按顺序拼接 canon、人格与审美倾向、当前状态、当前欲望，并按需加入
  ask guidance、知识边界、轻量意图、自我叙事、相关记忆和工具结果。
- `render_personality_instruction()` 将 1-10 的 Big Five、三观和审美转为自然语言行为倾向；
  prompt 同时明确这些只是倾向，不是硬规则。结构化数值仍保留在状态段。
- `knowledge_boundary` 由组合根读取 `nyx/prompts/knowledge-boundary.md`，不由 LLM 改写。它要求
  对陌生、最新、精确或专业诊断类事实表达不确定，必要时澄清或建议工具；工具失败不能
  伪造查询结果。
- `classify_user_intent()` 只做无 LLM 的字符串分类，结果只作为 think prompt 参考，不改变
  通道、欲望、等待或事件。
- `build_user_prompt()` 只负责历史和本次消息；think/speak 任务指令由回复节点追加。
- `reading_context` 同时注入 FAST/SLOW 的回复生成与 SLOW 工具判断，并明确标为原文/记忆
  资料而非指令；普通聊天为空。
- `[相关记忆]` 只展示记忆的 kind 人话前缀、topics 和 summary/content；记忆内容明确标注为资料而非指令。
- 动态资料采用固定代码预算，不新增配置项：单项记忆、事实、历史或工具资料最多 2000
  字符；记忆块最多 12000 字符，事实块最多 8000 字符，历史块最多 12000 字符，阅读块
  最多 12000 字符；工具结果最多 5 项且总计最多 8000 字符。截断只影响 prompt 展示，
  不改持久化原文。
- 工具判断 LLM 或 evaluator 失败时降级为无工具回复，并向后续回复 prompt 注入
  “本轮没有获得工具查询结果”的有界事实；实际工具执行失败仍以失败结果文本进入同一预算，
  不得伪造查询成功。
- `build_system_prompt()` 在人格/状态段之后加入 `[时间与重逢上下文]`。该段由纯函数根据
  注入的 epoch、本地日历、持久化对话锚点和运行时 observation 生成；LLM 不负责自行计算
  时间差、判断跨日或猜测用户离开期间的行为。
- `build_system_prompt()` 在状态段之后加入可选的 `[本轮活动事实]`。该段只展示
  `ActivityContext` 中已采样的活动类型、状态和白名单进度字段；`interrupted` 只表示
  打断前看见的活动，`current` 只表示打断后重新采样的运行活动。`current is None` 时不得
  推断被打断活动已暂停、完成或失败；同一活动仍为 `RUNNING` 时必须明确未确认暂停。

### 回溯与历史

- history 是 `deque[Message]`，容量由 `max_context_len` 限制；当前消息在回合末才追加。
- 快通道取最近 history；慢通道使用 `build_backtrack_context()` 从新到旧截断，只在达到容量
  或相邻时间间隔超过 `context_time_gap` 时停止。字面字符没有交集不能证明语义无关，不再作为
  截断条件。
- 快通道 Nyx 消息标记 `fast=True`，慢通道回溯跳过该条并继续向前；读书主动 turn 默认
  `fast=False`。
- 回合结束按 user 后 Nyx 顺序追加；Nyx 多轮 speak 用换行拼成一个 history turn。
- 带活动快照的 user/Nyx history 条目保存活动 ID、类型和有界摘要；历史条目在 user prompt
  中以“历史活动事实”作为次级资料，不覆盖本轮 `[本轮活动事实]`。旧条目没有活动元数据时
  保持原有渲染。

### 活动上下文与打断

- `runtime.on_user_message()` 在表达前先采样 `ActivityFacade.get_current()`，只对采样为
  `RUNNING` 的活动尝试 `interrupt(activity_id, USER_MESSAGE)`，随后再次采样并把两次结果
  组装成 `ActivityContext`。活动不存在、打断失败或二次采样为空都继续回复。
- 打断失败不会制造“已暂停”事实；表达 prompt 同时保留 `interrupted` 与二次采样的
  `current`，由后者明确仍在运行或无法确认终态。快通道、慢通道、工具判断和多轮续写复用
  同一个 `ReplyState.activity_context`。
- `ExpressionFacade.reply()` 未由运行时注入上下文时才读取 `get_current()` 作为独立快照；
  旧测试 fake 没有该方法按空闲处理，不改变历史消息兼容性。

### 时间感知、对话锚点与归来

`nyx/expression/prompt.py` 的纯函数签名为：

```python
def describe_local_time(now: float) -> dict[str, str]: ...
def describe_elapsed(previous: float, now: float) -> tuple[str, str | None]: ...
def build_temporal_block(
    now: float,
    anchor: tuple[Message, Message] | None,
    observation: Mapping[str, object],
    claimed_return: Mapping[str, float] | None,
) -> str: ...
```

`describe_local_time()` 返回 `date/weekday/time/period/phase`；`describe_elapsed()` 返回精确时长
文本与可选沉默描述。时间运算使用 Python 标准库，不新增依赖、配置、事件类型或 `CurrentState` 字段。

表达门面的私有查询与拼装入口为：

```python
async def _last_dialogue_anchor(
    self, current_correlation_id: str | None,
) -> tuple[Message, Message] | None: ...
async def _temporal_context(
    self,
    now: float,
    current_correlation_id: str | None,
    claimed_return: Mapping[str, float] | None,
) -> str: ...
```

- 当前时间使用运行机器的本地时区。计算函数必须接收可注入的 epoch；“昨天/今天/跨日”
  按本地 calendar date 判断，不使用 `elapsed_seconds / 86400` 代替日历运算。
  持续时间使用 epoch 差并夹到非负，系统时钟回拨不生成负时长。
- 时段固定为：凌晨 `00:00-05:59`、早上 `06:00-08:59`、上午 `09:00-11:59`、
  中午 `12:00-13:59`、下午 `14:00-17:59`、晚上 `18:00-21:59`、深夜
  `22:00-23:59`。凌晨和深夜属于夜间。
- 沉默时长的确定性自然语言为：`<5 分钟` 不描述重逢；`5-30 分钟` 为“用户有一会儿没有和你说话了”；
  `30 分钟-2 小时` 为“已经有一阵子没有说话了”；`>=2 小时` 为“用户已经很久没有和你说话了”。
  跨自然日额外描述“昨天 + 上次时段”或“X 天前”；短暂跨午夜只描述跨日，不夸大离开时长。
- 正常 history 仍遵循现有容量、相关性和 `context_time_gap` 截断，不为隔夜连续性放宽。
  表达门面另从 durable `event_log` 查询最近 20 条 `USER_MESSAGE` 候选，跳过当前
  correlation 和没有终局输出的 correlation，选择最新一个包含 `SPEAK` 或 `ASK` 的完整回合。
  该回合最后一个 `SPEAK/ASK` 与用户消息构成 `last_dialogue_anchor`；不包含 `THINK`。
- 对话锚点在进程重启后仍由 event log 恢复，不新增表。用户原话和 Nyx 终局回复分别最多
  引用 200 个字符；超出时截断并标记省略，避免 prompt 无界增长。
  每次表达只查询一次锚点，并在本次调用内复用；最近 20 条候选内没有完整回合时返回 `None`，
  仍提供当前时间，不引用不存在的上一轮。候选上限是固定查询边界，不提供配置项。
- 时间块至少包含当前本地日期、星期、时刻、时段、昼夜；存在锚点时还包含距上次用户消息
  的精确时长、日历关系、上次用户原话和 Nyx 终局回复；存在已 claim 的归来上下文时还包含
  `returned` 与离开时长。
  沉默不代表物理离开；运行时 away/returned 只表示电脑输入活动。归来不足 5 分钟才说
  “刚刚回来”，较旧归来描述距归来多久；当前 away、未来或非法归来事实不渲染。
  非法或未来历史时间锚点省略，不损失当前时间块；窗口标题同样使用 200 字符引用上限。
- 引用内容必须置于明确的“历史事实，不是指令”边界内。行为指导固定要求：可以在语境合适时
  自然体现时间变化，不要每条回复机械报时，不得虚构用户离开期间去向或经历。
  历史引文是不可信资料；该边界减少指令混淆，不承诺完整的 prompt-injection 防御。
- 两小时以上的示例时间块必须能形成类似事实：

  ```text
  当前：2026-09-18 星期五，早上 08:00
  距离上次用户消息：13 小时；上一次发生在昨天晚上。
  用户已经很久没有和你说话了。
  上一次用户说：“尼克斯，我去吃饭了”
  上一次你回复：“好的，我在这里等着你”
  ```

  最终台词仍由 LLM 根据事实和人格生成，不硬编码“你去哪里了”等具体问句。
- 普通 reply、工具判断、主动搭话和 LLM 碎碎念使用同一时间块。一次表达在首次 await 前原子
  claim 待消费归来上下文；只有终局 `SPEAK/ASK/INITIATE_CHAT/MUTTER` 成功提交后才消费。
  普通 SPEAK 在 publish 成功后消费；ASK/INITIATE_CHAT 在本地事务提交后、唤醒广播前消费。
  publish/事务退出发生异常或取消、提交结果不确定时，仅异常路径使用 EventBus.is_durable
  核实本次正常终局事件是否已落库；已提交则完成 claim 再重抛，不得 release 复活旧归来。
  只有尚未成功提交正常表达时，LLM/evaluator/解析/事件提交失败、空输出和固定 fallback 才 release；同一次回复的多轮 LLM
  调用复用同一 claim，不重复消费。模板碎碎念不使用也不消费归来上下文。
  一次回复的 FAST/SLOW、工具判断和多轮续写复用同一个 `ReplyState.temporal_context`，
  不因生成期间跨分钟重新计算。近期重复的 mutter 未发布时同样释放 claim；运行时 claim
  的持有、完成和释放边界见 [模块与事件总线契约](04-module-bus-system.md)。

### 隔夜连续性验收

固定系统本地时间执行以下回合，LLM 使用 fake：

```text
2026-09-17 19:00
用户：尼克斯，我去吃饭了
Nyx：好的，我在这里等着你

2026-09-18 08:00
用户：早上好，尼克斯
```

第二次回复的 LLM 输入必须包含第二天早上的当前时间、距上次用户消息 13 小时、昨天晚上、
长时间未交谈的自然语言和双方上一轮原话；重建 Facade 后仍能从 durable event log 得到这些
事实。不固定最终台词，也不要求必问“去了哪里”。两组消息的可见时间标签及实时/历史一致性
按 [聊天面板契约](../frontend/03-chat-panel.md) 验收。

## LLM 输出与失败

- 每次 reply LLM 使用 JSON 模式，期望非空字符串 `speak` 和可选字符串 `think`。
- 合法解析后 think/speak 分别经过 evaluator；拆分后的两个 `LLMOutput` 共享原始调用的
  call_id、token 和 `prompt_messages`，prompt 只按 call_id 存一份。事件先缓存在内存，只有
  本回合正常生成完成后才进入终局批次。
- 非法 JSON、顶层类型错误、speak 缺失/为空、LLM 或 evaluator 异常都属于失败，不得把原始
  文本当作合法 speak。
- 解析失败最多重试一次。仍失败时发布固定 fallback SPEAK，载荷增加
  `response_kind="fallback"`，不发布 THINK/ASK、不创建等待项、不生成正常场景记忆。
- fallback 事件和历史成功完成后，用户消息处理才算成功；事件/本地持久化失败交由总线
  delivery 重试。相关 correlation 已有终局 SPEAK、ASK 或 fallback 时，重放入口不得再次
  调用 LLM。
- 任意正常文本、attempt 状态、终局事件或 `scene_memory_requested` 的写入失败都会回滚同一
  批受理；不能留下半轮 THINK/SPEAK，也不能出现终局 `SPEAK/ASK` 已提交、请求缺失后又被
  用户消息重放短路的半完成状态。后续轮生成失败时只提交固定 fallback，不提交先前缓存的
  正常文本。

## InteractionAttempt

### 类型

`InteractionKind` 包含 `CHAT_ASK`、`READING_QUESTION`、
`INITIATE_CHAT`；
`InteractionStatus` 包含 `WAITING`、`CLAIMED`、`ANSWERED`、`EXPIRED`、`FAILED`。

```python
@dataclass
class InteractionAttempt:
    id: str
    kind: InteractionKind
    source_id: str
    correlation_id: str
    text: str
    created_at: float
    expires_at: float
    status: InteractionStatus = InteractionStatus.WAITING
    answered_at: float | None = None
    answer_event_id: str | None = None
    failure_reason: str | None = None
```

`READING_QUESTION.source_id` 固定为 `<book_id>:<paragraph_index>`；它本身就是可跨重启恢复的
定位锚点，不复制原段落或书籍画像到 attempt 表。

### 持久化与状态转换

`expression_interaction_attempt` 至少包含上述字段，`id` 为主键，并建立
`(status, expires_at)`、`(status, created_at)` 和 `correlation_id` 索引。

`ExpressionInteractionStore` 提供：

```python
async def create(attempt: InteractionAttempt) -> None
async def get(attempt_id: str) -> InteractionAttempt | None
async def claim_reply(
    reply_event_id: str, reply_to: str | None = None
) -> InteractionAttempt | None
async def claim_expired(now: float) -> InteractionAttempt | None
async def finish_answer(attempt_id: str, reply_event_id: str) -> bool
async def finish_expired(attempt_id: str) -> bool
async def release_claim(
    attempt_id: str, reply_event_id: str | None = None
) -> bool
async def latest_created_at(kind: InteractionKind) -> float | None
```

所有转换使用 `WHERE status = ...` 条件更新。用户消息最多关联一条等待项：
有合法 `reply_to` 时只尝试该 id，否则选择最新 WAITING；超时按最早到期项逐条领取。
用户消息领取时把 `answer_event_id` 写为领取者 id；同一事件重放可以恢复自己已领取的
`CLAIMED/ANSWERED` attempt，其他事件不能抢占。`answer_waiting()` 对主动搭话先结算对应互动欲，
但保持 attempt 为 `CLAIMED`；正常终局或 fallback 与 `ANSWERED` 转换在同一本地事务提交。
终局前失败按 owner 条件释放为 `WAITING` 并清空 `answer_event_id`；未被领取的其它等待项不受
影响。超时领取不设置 `answer_event_id`，释放时仍以空 owner 条件保护。
schema 35 迁移把旧版本遗留的 `CLAIMED AND answer_event_id IS NULL` 恢复为 `WAITING`；带 owner
的 CLAIMED 不在迁移中释放。

真实组合根注入 store；没有 store 的兼容路径可以使用进程内等待字段，但不提供重启恢复保证。

## 普通提问与读书提问

- 普通 FAST/SLOW 回复产生问句时调用 `register_question(..., CHAT_ASK, ...)`。
- 慢通道普通提问允许向 `register_question` 附带场景请求；attempt、ASK 和请求同事务提交。
  快通道提问只提交 attempt + ASK。
- 读书提问生成后必须通过统一 `is_question()` 校验；`QUOTE_QUESTION` 还必须有非空第二行
  quote。正式组合根成功后调用 `commit_reading_question()`，把 attempt、ASK 和
  READING_QUESTION 同事务提交；只对未提供该方法的测试 fake 兼容回退
  `register_question(..., READING_QUESTION, ...)`。
- `ASK` 载荷为 `{"content", "attempt_id", "kind"}`。
- `READING_QUESTION` 保留书籍、段落、subtype、selected_text 等前端字段并增加 attempt_id；
  它是展示兼容事件，不替代 canonical ASK。
- 读书提问会把正文调用 `record_proactive_turn()` 追加到表达历史；selected_text 不追加。
- 用户回复读书提问时，`answer_waiting()` 返回由当前用户事件认领或恢复的 attempt；`reply()` 用
  `attempt.text + 用户回复` 作为来源内相关性查询，并向快慢通道共同注入三层只读材料：
  同一本书此前沉淀的 knowledge Top 5、触发提问的完整原段落、书名/作者/滚动摘要/主题/
  内容类别。knowledge 层注入 `Memory.content` 事实正文，不得用仅含主题标签的 `summary`
  替代。材料明确标为事实资料而非指令。
- 来源内 Top 5 必须按 `book_id` 的稳定 source topic 先过滤再排序；标题相同不能互相召回。
  旧库尚无 profile 时使用空摘要/主题和 `unknown`，但书名、作者、原段落仍可用。
- 读书联想最多展示三条，每条 snippet 调用 `record_proactive_turn()`；读书 mutter 不进入
  表达历史。
- 空输出、解析失败、quote 缺失或非问句不创建 attempt、不发布成功提问事件。

真实 bad case 的处理边界：

| 情况 | 处理 |
|---|---|
| 用户只回复“嗯”“不是” | 不依赖短消息触发慢通道；用已完成 reading attempt 回读三层上下文并注入 FAST/SLOW |
| 两本书同名 | 用 `book_id` 派生 source topic，先限定来源再排 Top 5 |
| knowledge 的 summary 只有主题标签 | 回复上下文使用 `content` 事实正文，不把主题标签当事实 |
| 旧库尚无滚动画像 | 摘要/主题显示“暂无”、类别为 `unknown`，书名/作者/原段落仍注入 |
| 原书或段落已删除 | 阅读回调返回空串，普通回复流程继续，不伪造原文 |
| 原文包含命令式文本 | 整块明确标为书籍资料和原文，不是对模型的指令 |

## 对象完整性

### ActivityContext

**入口清单**

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| runtime 用户消息处理 | 打断前采样 interrupted，尝试打断后采样 current | 本轮 ActivityContext，含 observed_at |
| `ExpressionFacade.reply` | 未注入上下文时经 `_activity_context` 采样 | 本轮 current 快照 |
| `initiate_chat` / LLM `mutter` | 生成前经 `_activity_context` 采样 | 本次调用快照 |
| 导入、迁移、恢复、重放 | 不适用：快照不持久化；回复重试重新采样或沿用调用方注入值 | 无表、API 或事件字段 |

**消费者清单**

| 消费者 | 发现方式 | 用途 |
|---|---|---|
| ReplyState / 快慢通道、工具判断、续写 | reply 注入 | 统一本轮活动事实 |
| `build_system_prompt` / 主动表达 | 参数注入 | 区分当前、刚才被打断、未知状态 |
| 回复历史及主动搭话历史写入 | 本轮快照 | 复制活动 id、类型与事实摘要到 Message |

**状态迁移表**

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| 无快照 | 开始表达调用 | 本轮采样快照 | 不修改 Activity；current/interrupted 均可为空 |
| 本轮采样快照 | 多轮生成 | 沿用本轮快照 | 不把途中活动变化猜测成新事实 |
| 本轮采样快照 | 调用结束或进程退出 | 释放 | 无持久化恢复；活动状态仍由活动系统管理 |

**Bad case 表**

| 情况 | 处理 |
|---|---|
| 空 | 表述为未观察到活动；不能推断暂停、完成或失败 |
| 失败 | 打断失败记录日志并继续采样/回复；采样失败按既有调用错误路径传播，不伪造快照 |
| 部分完成 | 前后采样分别保留；打断成功返回不等于已确认暂停 |
| 乱序 | 只用本次注入快照，不用历史 Message 替换当前事实；observed_at 为采样时间，不是状态版本 |
| 重放 | 不持久化、不独立重放快照；回复重试由入口重新采样或明确注入 |
| 删除 | 快照不级联删除；活动随后删除不改写已采样事实，不证明该活动仍在运行 |

### 带活动归属的 Message

**入口清单**

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| pipeline `record_message` | 回合完成 | 有界 history deque；用户及 Nyx 消息复制 current，缺失时复制 interrupted |
| `initiate_chat` | 非空搭话提交成功 | history deque，复制本次 current |
| `record_proactive_turn` | 阅读提问/联想等主动文本 | history deque；旧入口无活动归属 |
| `_last_dialogue_anchor` | 从 durable 事件重建完整对话时间锚点 | 临时 Message 对；旧事件无活动归属，不回填 history |
| 迁移、导入、重启恢复 | 不适用：Message history 不持久化，durable 事件仅重建时间锚点 | 无新增表或事件字段 |

**消费者清单**

| 消费者 | 发现方式 | 用途 |
|---|---|---|
| FAST 上下文 / SLOW 回溯 | history deque | 最近消息及按时间截断的对话上下文 |
| `build_user_prompt` | context 参数 | activity_summary 作为次级历史事实，与本轮活动事实分开 |
| 时间与重逢上下文 | durable 事件重建的对话锚点 | 只消费角色、正文、时间；不要求活动归属 |

**状态迁移表**

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| 无消息 | 回合/搭话提交成功或主动文本写入 | 内存历史消息 | 活动归属为当时事实，旧入口字段为 None |
| 内存历史消息 | 超出 deque 容量或进程退出 | 淘汰 | 不恢复 history；durable 事件仍可重建时间锚点 |
| 无消息 | 事件查询取得完整对话 | 临时时间锚点 | 不补造活动元数据，不修改 history |

**Bad case 表**

| 情况 | 处理 |
|---|---|
| 空 | 活动字段允许 None；没有历史时直接使用当前消息 |
| 失败 | 回合终局提交失败不进入 record_message；历史写入不提供持久化承诺 |
| 部分完成 | 有界 deque 可淘汰更早的半回合；完整时间锚点只接受完整对话 |
| 乱序 | history 按写入顺序消费；历史摘要不能覆盖本轮活动事实 |
| 重放 | 不新增 history 持久化或去重机制；durable 终局重放短路，不重复写历史 |
| 删除 | 活动删除不改写历史摘要；history 淘汰和重启清空，不新增单条删除 API |

### 回复终局批次

**入口清单**

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| FAST 正常回复 | 一轮生成和评估成功 | `event_log` 中按顺序写 THINK（可空缺）及 SPEAK/ASK |
| SLOW 正常回复 | 问句提前结束或达到轮数上限 | 同一事务写全部文本、可选 attempt、终局事件与场景请求 |
| fallback | 回复生成、解析或 evaluator 失败 | 单独写 fallback SPEAK；不包含缓存的正常文本 |
| API / 迁移 / 后台任务 | 不适用；只有回复图产生该批次 | 不适用 |

**消费者清单**

| 消费者 | 发现方式 | 用途 |
|---|---|---|
| 前端 SSE / 历史查询 | 按 correlation 和事件顺序读取 | 展示多段气泡与终局状态 |
| `memory.scene_reply` | SLOW 批次中的 durable request | 异步生成场景记忆 |
| runtime 重放门控 | 查询同 correlation 的 SPEAK/ASK | 已有终局时短路 LLM 重放 |

**状态迁移表**

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| 生成中 | 一轮成功但回合未终止 | 生成中 | 仅缓存，无 durable 正常文本 |
| 生成中 | 正常回合结束 | 已提交 | 整批 durable；提交后广播失败由日志恢复 |
| 生成中 | 生成/解析/eval 失败 | fallback 已提交 | 丢弃正常缓存，只提交 fallback |
| 生成中 | DB 写入失败 | 不存在 | 整批回滚，delivery 可重试用户事件 |

**Bad case 表**

| 情况 | 处理 |
|---|---|
| 空 | 空 think 不生成 THINK；空 speak 解析失败并进入 retry/fallback |
| 失败 | 终局批次任一 SQL 失败整批回滚；fallback 自身失败继续抛出 |
| 部分完成 | 本地事务禁止半轮可见；commit 后广播失败不反向删除 durable 事实 |
| 乱序 | 单批按生成顺序 append，消费者不得依赖异步 handler 完成顺序 |
| 重放 | 已有终局短路；无终局时重新生成，recall 与 attempt owner 各自幂等 |
| 删除 | 无单轮删除 API；事件保留遵循 event log 生命周期 |

### InteractionAttempt

**入口清单**：普通问句、读书提问和主动搭话创建 WAITING；用户消息或超时原子领取；迁移只建
表，不造业务行；不提供导入入口。

**消费者清单**：表达回复读取 reading/ask 上下文，欲望模块结算主动搭话，超时任务淘汰等待，
event log/SSE 使用 attempt id 关联展示。

**状态迁移表**

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| 不存在 | 提问/搭话终局事务 | WAITING | 与 ASK/INITIATE_CHAT 同事务 |
| WAITING | 用户事件领取 | CLAIMED(owner) | 写 `answer_event_id`；其他 owner 不能领取 |
| CLAIMED(owner) | 同 owner 重放 | CLAIMED(owner) | 返回原 attempt，不改变所有权 |
| CLAIMED(owner) | 回复终局事务 | ANSWERED | 与正常终局或 fallback 同事务 |
| CLAIMED(owner) | 终局前失败 | WAITING | 按 owner 释放并清空 `answer_event_id` |
| WAITING | 到期领取并成功结算 | EXPIRED | 无用户 owner；结算失败释放 |

**Bad case 表**

| 情况 | 处理 |
|---|---|
| 空 | 无 WAITING 或显式 id 不存在时返回 None |
| 失败 | 欲望结算、上下文读取或终局提交失败释放当前 owner，允许 delivery 重试 |
| 部分完成 | attempt 创建/完成与对应终局事件共享本地事务 |
| 乱序 | owner 条件更新拒绝旧事件释放或完成其他事件的 claim |
| 重放 | 同 `answer_event_id` 恢复 CLAIMED/ANSWERED；其他事件不得复用 |
| 删除 | 无公开删除；来源书籍删除时 attempt 仍可结算，阅读上下文降级为空 |

## 主动搭话

- runtime 先检查 pending interaction desire、presence、busy、energy 和冷却，再通过
  `DesireFacade.claim_for_interaction()` 原子领取。
- 只有领取成功者调用 LLM。生成/evaluator 在事务外进行；空输出释放领取并返回 False。
- 非空输出后，在同一本地事务中写入 `InteractionAttempt(INITIATE_CHAT)` 和
  `INITIATE_CHAT` 事件；事务提交后才打断活动。
- 活动打断失败是待补偿副作用，不撤销已提交主动搭话；补偿必须复用已持久化文本，不能
  重复生成。
- 用户回复通过 attempt 满足对应欲望；超时调用 `DesireFacade.expire()`。
- durable 模式冷却由最近成功 attempt 创建时间派生，兼容路径使用运行时字段。

## 碎碎念

- 只有无当前活动且随机命中 `_MUTTER_RATE` 时尝试发出。
- 一部分命中 `_LLM_MUTTER_RATE` 走 LLM 即兴，否则从 ACTIVITY/MEMORY/DESIRE/USER
  四类各十条模板中取一条；没有可用数据、内容为空或近期重复则不发。
- LLM 碎碎念使用 `output_type="mutter_wander"` 并评估；失败/空输出回退模板。
- 碎碎念事件为 `MUTTER`，不进入对话 history。
- `should_initiate_chat()` 是纯函数，只判定触发条件；真正搭话由 runtime + facade 完成。

## 运行时、事件和测试

- `runtime.py` 是用户消息、tick、主动搭话和超时的真实入口；`main.py` 同名兼容函数只能委托。
- 事件持久化、分发、delivery/retry、重放和关停遵循 `04-module-bus-system.md`。
- `SCENE_MEMORY_REQUESTED` 只由慢通道正常终局产生；前端将它作为 opaque no-op，
  `memory.scene_reply` 的重试、幂等与 bad case 由 `06-memory-system.md` 定义。
- 所有 LLM 真实调用可注入 fake；测试重点是通道拓扑、状态转换、事件字段、失败重试、
  重放短路和读书提问关联，不测试生成文本质量。
- 时间测试覆盖 `05:59/06:00`、`21:59/22:00`、同小时、5/30/120 分钟边界、跨午夜、
  昨天、多日、本地星期和引用截断。对话锚点测试覆盖最近完整 correlation、忽略 THINK/半截
  回合、重建 Facade 后恢复。快/慢回复、工具判断、主动搭话与 LLM 碎碎念都必须断言收到
  自然语言时间块；测试只验证事实进入 prompt，不评价最终文案质量。
- 表达相关测试位于 `tests/test_expression/`，读书陪读测试位于 `tests/test_reading/`。
- 回归必须覆盖短回复仍带三层阅读上下文、显式 `reply_to`、重启后仅凭 durable attempt
  恢复上下文，以及普通 `CHAT_ASK` 不注入阅读材料。

## 完成定义

- 新增或修改表达实现必须同步本 spec 与 `docs/facts/expression-system-facts.md`。
- 若涉及总线、事务、事件投递或数据库迁移，必须同时同步 `docs/specs/04-module-bus-system.md`
  及其事实摘要；若要改变已有契约语义，先询问用户。
- 运行 `ruff check`、`pyright` 和 `pytest`；修改测试后同步 `docs/test-inventory.md`。
