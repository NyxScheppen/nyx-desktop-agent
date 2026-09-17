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
        self, msg: str, correlation_id: str, reply_to: str | None = None
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
    ) -> str: ...
    async def answer_waiting(
        self, reply_event_id: str, reply_to: str | None = None
    ) -> InteractionAttempt | None: ...
    async def latest_initiate_chat_at(self) -> float | None: ...
    def record_proactive_turn(self, text: str) -> None: ...
    async def check_timeouts(self, now: float) -> None: ...
```

构造依赖为 bus、llm、evaluator、memory、activity、desire、inner_life、canon、
ask guidance、`ExpressionConfig`、tools，以及可选 durable interaction store 和 knowledge
boundary 文本。组合根负责读取 prompt 文件并注入。

## 回复图

### 状态

`ReplyState` 至少包含 message、mode、context、memories、state、narrative、think、speak、
ask、round、correlation_id、last_slow_at、tool_outputs、intent 和 fallback。

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

### 回溯与历史

- history 是 `deque[Message]`，容量由 `max_context_len` 限制；当前消息在回合末才追加。
- 快通道取最近 history；慢通道使用 `build_backtrack_context()` 从新到旧截断，遇到容量、
  相邻时间间隔超过 `context_time_gap` 或无共同非空白字符时停止。
- 快通道 Nyx 消息标记 `fast=True`，慢通道回溯跳过该条并继续向前；读书主动 turn 默认
  `fast=False`。
- 回合结束按 user 后 Nyx 顺序追加；Nyx 多轮 speak 用换行拼成一个 history turn。

## LLM 输出与失败

- 每次 reply LLM 使用 JSON 模式，期望非空字符串 `speak` 和可选字符串 `think`。
- 合法解析后 think/speak 分别经过 evaluator，并分别产生 THINK/SPEAK 事件。
- 非法 JSON、顶层类型错误、speak 缺失/为空、LLM 或 evaluator 异常都属于失败，不得把原始
  文本当作合法 speak。
- 解析失败最多重试一次。仍失败时发布固定 fallback SPEAK，载荷增加
  `response_kind="fallback"`，不发布 THINK/ASK、不创建等待项、不生成正常场景记忆。
- fallback 事件和历史成功完成后，用户消息处理才算成功；事件/本地持久化失败交由总线
  delivery 重试。相关 correlation 已有终局 SPEAK、ASK 或 fallback 时，重放入口不得再次
  调用 LLM。

## InteractionAttempt

### 类型

`InteractionKind` 包含 `CHAT_ASK`、`READING_QUESTION`、`INITIATE_CHAT`；
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
外部结算失败释放 claim，未被领取的其它等待项不受影响。

真实组合根注入 store；没有 store 的兼容路径可以使用进程内等待字段，但不提供重启恢复保证。

## 普通提问与读书提问

- 普通 FAST/SLOW 回复产生问句时调用 `register_question(..., CHAT_ASK, ...)`。
- 读书提问生成后必须通过统一 `is_question()` 校验；`QUOTE_QUESTION` 还必须有非空第二行
  quote。成功后调用 `register_question(..., READING_QUESTION, ...)`。
- `ASK` 载荷为 `{"content", "attempt_id", "kind"}`。
- `READING_QUESTION` 保留书籍、段落、subtype、selected_text 等前端字段并增加 attempt_id；
  它是展示兼容事件，不替代 canonical ASK。
- 读书提问会把正文调用 `record_proactive_turn()` 追加到表达历史；selected_text 不追加。
- 读书联想最多展示三条，每条 snippet 调用 `record_proactive_turn()`；读书 mutter 不进入
  表达历史。
- 空输出、解析失败、quote 缺失或非问句不创建 attempt、不发布成功提问事件。

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
- 表达相关测试位于 `tests/test_expression/`，读书陪读测试位于 `tests/test_reading/`。

## 完成定义

- 新增或修改表达实现必须同步本 spec 与 `docs/facts/expression-system-facts.md`。
- 若涉及总线、事务、事件投递或数据库迁移，必须同时同步 `docs/specs/04-module-bus-system.md`
  及其事实摘要；若要改变已有契约语义，先询问用户。
- 运行 `ruff check`、`pyright` 和 `pytest`；修改测试后同步 `docs/test-inventory.md`。
