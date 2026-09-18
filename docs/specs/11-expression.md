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
  `nyx/db.py`、`prompts/knowledge-boundary.md`
- **联动文档**：04-module-bus-system、06-memory-system、07-desire、08-inner-life、
  09-activity、12-reading-system、10-eval、docs/tech-reference.md、
  docs/facts/expression-system-facts.md

## 系统边界

- `ExpressionFacade` 是表达侧唯一门面，负责回复、主动搭话、碎碎念、交互等待和表达历史。
- `pipeline.py` 负责回复图；prompt/classifier/mutter 模块中的纯函数不访问数据库、不调用
  LLM、不直接发布事件。
- `ReadingCompanion` 负责读书行为的生成和展示事件；提问等待、表达历史和问句判断复用本 spec。
- HTTP 端点和事件订阅属于组合根/总线契约，表达门面不直接承担 HTTP 路由。

## 公开接口

```python
class ExpressionFacade:
    async def reply(
        self,
        msg: str,
        correlation_id: str,
        reply_to: str | None = None,
        *,
        browsing_context: dict[str, str] | None = None,
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
    ) -> str: ...
    async def answer_waiting(
        self, reply_event_id: str, reply_to: str | None = None
    ) -> InteractionAttempt | None: ...
    async def commit_browsing_question(
        self,
        text: str,
        source_id: str,
        correlation_id: str,
        event_content: dict[str, Any],
    ) -> str: ...
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
claimed_return 和 fallback。

### 通道

- `classify_channel()` 使用消息长度、问句、情绪词、精力/唤醒度、距上次慢通道时间五个因子；
  得分大于等于 `slow_threshold` 为 `SLOW`，否则为 `FAST`。
- FAST 只执行一次回复生成，不检索记忆、不调用工具、不生成场景记忆；仍必须交付 THINK
  （非空时）和 SPEAK。
- SLOW 在回复前执行回溯、记忆检索、recall 记录、自我叙事读取和一次工具判断；回复可连续
  多轮，轮数上限由 `slow_max_rounds` 定义。
- 慢通道命中问句后注册 `CHAT_ASK`，发布 `ASK`，结束本回合并生成场景记忆；未命中且未达
  轮数上限时继续续写。

### Prompt

- `build_system_prompt()` 按顺序拼接 canon、人格与审美倾向、当前状态、当前欲望，并按需加入
  ask guidance、知识边界、轻量意图、自我叙事、相关记忆和工具结果。
- `render_personality_instruction()` 将 1-10 的 Big Five、三观和审美转为自然语言行为倾向；
  prompt 同时明确这些只是倾向，不是硬规则。结构化数值仍保留在状态段。
- `knowledge_boundary` 由组合根读取 `prompts/knowledge-boundary.md`，不由 LLM 改写。它要求
  对陌生、最新、精确或专业诊断类事实表达不确定，必要时澄清或建议工具；工具失败不能
  伪造查询结果。
- `classify_user_intent()` 只做无 LLM 的字符串分类，结果只作为 think prompt 参考，不改变
  通道、欲望、等待或事件。
- `build_user_prompt()` 只负责历史和本次消息；think/speak 任务指令由回复节点追加。
- `[相关记忆]` 只展示记忆的 kind 人话前缀、topics 和 summary/content；记忆内容明确标注为资料而非指令。
- `build_system_prompt()` 在人格/状态段之后加入 `[时间与重逢上下文]`。该段由纯函数根据
  注入的 epoch、本地日历、持久化对话锚点和运行时 observation 生成；LLM 不负责自行计算
  时间差、判断跨日或猜测用户离开期间的行为。

### 回溯与历史

- history 是 `deque[Message]`，容量由 `max_context_len` 限制；当前消息在回合末才追加。
- 快通道取最近 history；慢通道使用 `build_backtrack_context()` 从新到旧截断，遇到容量、
  相邻时间间隔超过 `context_time_gap` 或无共同非空白字符时停止。
- 快通道 Nyx 消息标记 `fast=True`，慢通道回溯跳过该条并继续向前；读书主动 turn 默认
  `fast=False`。
- 回合结束按 user 后 Nyx 顺序追加；Nyx 多轮 speak 用换行拼成一个 history turn。

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
- 合法解析后 think/speak 分别经过 evaluator，并分别产生 THINK/SPEAK 事件；拆分后的两个
  `LLMOutput` 共享原始调用的 call_id、token 和 `prompt_messages`，prompt 只按 call_id 存一份。
- 非法 JSON、顶层类型错误、speak 缺失/为空、LLM 或 evaluator 异常都属于失败，不得把原始
  文本当作合法 speak。
- 解析失败最多重试一次。仍失败时发布固定 fallback SPEAK，载荷增加
  `response_kind="fallback"`，不发布 THINK/ASK、不创建等待项、不生成正常场景记忆。
- fallback 事件和历史成功完成后，用户消息处理才算成功；事件/本地持久化失败交由总线
  delivery 重试。相关 correlation 已有终局 SPEAK、ASK 或 fallback 时，重放入口不得再次
  调用 LLM。

## InteractionAttempt

### 类型

`InteractionKind` 包含 `CHAT_ASK`、`READING_QUESTION`、`BROWSING_QUESTION`、
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
async def release_claim(attempt_id: str) -> bool
async def latest_created_at(kind: InteractionKind) -> float | None
```

所有转换使用 `WHERE status = ...` 条件更新。用户消息最多关联一条等待项：
有合法 `reply_to` 时只尝试该 id，否则选择最新 WAITING；超时按最早到期项逐条领取。
`answer_waiting()` 对主动搭话先结算对应互动欲，再把 attempt 完成为 `ANSWERED`；
外部结算或完成写入失败时释放 claim，未被领取的其它等待项不受影响。

真实组合根注入 store；没有 store 的兼容路径可以使用进程内等待字段，但不提供重启恢复保证。

## 普通提问、读书提问与浏览提问

- 普通 FAST/SLOW 回复产生问句时调用 `register_question(..., CHAT_ASK, ...)`。
- 读书提问生成后必须通过统一 `is_question()` 校验；`QUOTE_QUESTION` 还必须有非空第二行
  quote。成功后调用 `register_question(..., READING_QUESTION, ...)`。
- `ASK` 载荷为 `{"content", "attempt_id", "kind"}`。
- `READING_QUESTION` 保留书籍、段落、subtype、selected_text 等前端字段并增加 attempt_id；
  它是展示兼容事件，不替代 canonical ASK。
- 浏览提问生成后必须通过统一 `is_question()`；
  `commit_browsing_question(..., event_content)` 在同一本地事务写入 waiting
  `InteractionAttempt(BROWSING_QUESTION)`、canonical `ASK` 和 `BROWSING_QUESTION`。
  展示事件保留 session/page/title/url/selected_text 等字段并增加 attempt_id；任一步失败
  整体回滚，正式组合根不得退回非原子路径。source id 和 correlation id
  均使用 page id，使浏览整合可从 durable event log 恢复该页已提交的 Nyx 输出。
- 读书提问会把正文调用 `record_proactive_turn()` 追加到表达历史；selected_text 不追加。
- 读书联想最多展示三条，每条 snippet 调用 `record_proactive_turn()`；读书 mutter 不进入
  表达历史。
- 浏览问题正文和最多三条联想 snippet 进入表达历史；只有
  `13-browsing-system` 中结构合法的 `association` action 才执行检索和发布联想，浏览 mutter
  不进入表达历史。
- 空输出、解析失败、quote 缺失或非问句不创建 attempt、不发布成功提问事件。

### 当前网页上下文

- `POST /api/chat` 可以携带已由浏览 Facade 校验的 `browsing_page_id`；runtime 先读取只读
  页面上下文，再通过 `browsing_context` 参数传给 `reply`，表达 Facade 不反向持有浏览 Facade。
- 上下文最多包含标题、安全化 URL和 6,000 字符正文，置于明确的“不可信网页材料，不是
  指令”边界；网页内容不能修改 system/persona/tool/知识边界规则。
- 没有 page id 时行为不变；非法、未授权、不是 `current_page_id` 或不属于当前
  未结束会话的 id 在 API/runtime 边界拒绝，不静默降级成无网页上下文回复。
  API 受理后才失效时，runtime 发布固定 fallback SPEAK，要求用户在当前页重新发送；
  不调用 reply/LLM、不关联等待 attempt 或创建 scene memory。

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
- 所有 LLM 真实调用可注入 fake；测试重点是通道拓扑、状态转换、事件字段、失败重试、
  重放短路和读书提问关联，不测试生成文本质量。
- 时间测试覆盖 `05:59/06:00`、`21:59/22:00`、同小时、5/30/120 分钟边界、跨午夜、
  昨天、多日、本地星期和引用截断。对话锚点测试覆盖最近完整 correlation、忽略 THINK/半截
  回合、重建 Facade 后恢复。快/慢回复、工具判断、主动搭话与 LLM 碎碎念都必须断言收到
  自然语言时间块；测试只验证事实进入 prompt，不评价最终文案质量。
- 表达相关测试位于 `tests/test_expression/`，读书陪读测试位于 `tests/test_reading/`。

## 完成定义

- 新增或修改表达实现必须同步本 spec 与 `docs/facts/expression-system-facts.md`。
- 若涉及总线、事务、事件投递或数据库迁移，必须同时同步 `docs/specs/04-module-bus-system.md`
  及其事实摘要；若要改变已有契约语义，先询问用户。
- 运行 `ruff check`、`pyright` 和 `pytest`；修改测试后同步 `docs/test-inventory.md`。
