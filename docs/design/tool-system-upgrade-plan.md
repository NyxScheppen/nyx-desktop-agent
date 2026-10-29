# 工具系统升级实现计划（17 / 18 / 19）

> 状态：**已批准并实现**，验证与效果限制见本文末尾。
> 对应 spec：`docs/specs/17-tool-call-observation.md`、`18-plan-and-execute.md`、`19-failure-reflection.md`。
> 本文是三者联合落地的实现计划；用户已批准实现及补充事务边界。

## 需求复述

把 4 个工具升级点落地为代码：

1. 记录完整工具调用路径（17，`tool_call_log` 观测）。
2. plan-and-execute（18，`use_tools` 内部先规划、再 ReAct 执行、计划失效有限次重规划）。
3. 失败反思自进化（19，失败信号 → `MemoryKind.LESSON` 沉淀 → 规划/回复注入）。
4. （工具/技能系统更完整已并入 18，沿用 05 的 skill 授权，不单列。）

三者都改 `use_tools`，有先后依赖。

## 关键假设（已确认）

- 触发时机二分：`tool_error` 在自动重试耗尽后沉淀 `LESSON`，`tool_empty` / `ooc` **当场**沉淀；`user_correction` **阈值**（同类 3 次）沉淀，减少用户信息误判。
- 双管齐下仅 `user_correction` 达阈值时：`annotation_correction` 反射（内心反思）+ `LESSON`（技能/策略）同阈值触发。
- **15 契约变更**：`annotation_correction` 反射从「当场」改「阈值」，由 19 阈值判断器统一触发；事实修正仍当场。
- 当场类（工具报错/空结果/OOC）**不**触发内心反思，只沉淀策略。
- **`user_correction` 主题提取**：复用 `apply_annotation_feedback` 已有 fact extraction LLM 调用，`is_correction=true` 时额外输出 `correction_topic` 作聚类键，缺失/空兜底 `user_correction`。不新增 LLM 调用。

## 实现顺序：17 → 18 → 19

- **17 先**：`ToolRegistry` 观测是基础设施；18 的 execute 里要调 `call(..., log_id=...)` + `mark_adopted`，依赖 17 的新签名。
- **18 次**：`use_tools` 内部改造为 plan → execute(ReAct) → 有限次 replan。
- **19 后**：失败信号采集挂在 18 的 `use_tools`（工具失败/空结果）、plan 注入依赖 18 的 plan 步骤；`record_failure_signal` 依赖 17 就位后的 `ToolRegistry`（非硬依赖，但同批落地顺序自然靠后）。

## 各 spec 改动落点

### 17 tool-call-observation

| 文件 | 改动 |
|---|---|
| `nyx/tools/tool_call_store.py` | **新增** `ToolCallStore`（`insert` / `mark_adopted`） |
| `nyx/db.py` | 迁移 **v47** `tool_call_log` 表 + 3 索引 |
| `nyx/tools/registry.py` | `__init__(store=None)`；`call` 加 keyword-only `correlation_id`/`call_id`/`log_id`（返回类型不变）；`mark_adopted(log_id)`；截断常量 |
| `nyx/expression/tool_pipeline.py` | 每次真实尝试预生成 `log_id`，终局结果接纳后 `mark_adopted`；中间失败尝试、未执行及未回注结果不 mark |
| `nyx/app_context.py` | `build_tools`（行 351）注入 `ToolCallStore` |

### 18 plan-and-execute

| 文件 | 改动 |
|---|---|
| `nyx/types.py` | `PlanStep`（`text`/`expected_tool`/`done_condition`）、`Plan`（`goal`/`steps`）dataclass |
| `nyx/expression/prompt.py` | `PLAN_TASK` 规划 prompt（产出 JSON `{goal, steps[]}`） |
| `nyx/expression/{pipeline,tool_pipeline}.py` | `use_tools` 调用持久工具子图：先 `plan`（`output_type="plan"`）→ 解析失败/空 steps 降级无计划 → `execute`（ReAct，复用 `_MAX_TOOL_ROUNDS=4`）→ `replan` 伪工具（不入公开 `schema()`，`_MAX_REPLANS=1`） |

### 19 failure-reflection

| 文件 | 改动 |
|---|---|
| `nyx/enums.py` | `MemoryKind.LESSON = "lesson"`（行 120 后） |
| `nyx/db.py` | 迁移 **v48** `lesson_signal` 表（仅 `user_correction` 用） |
| `nyx/memory/store.py` | `bump_lesson_signal(topic, kind)`（upsert 计数，返回新值）/ `clear_lesson_signal(topic)` |
| `nyx/memory/facade.py` | 新增 `record_failure_signal(kind, topic, summary, correlation_id=None, *, evidence=None)` + `_persist_lesson(topic, kind, summary)`；改 `apply_annotation_feedback`（行 1062–1072 correction 分支 → 走 `record_failure_signal`） |
| `nyx/eval/evaluator.py` | `__init__` 加 `on_failure` 回调；OOC 命中（行 41 / 56）时调 |
| `nyx/expression/{pipeline,tool_pipeline}.py` | 工具重试耗尽采集 `tool_error`，空结果采集 `tool_empty`；plan 步骤 `search(kind=LESSON)` 注入 |
| `nyx/app_context.py` | 装配 `on_failure`（`Evaluator` 构造行 379 与 `MemoryFacade` 构造行 381 需调整顺序/回调适配） |

**`apply_annotation_feedback` 具体改法**：
- fact extraction prompt 增输出 `correction_topic`（`is_correction=true` 时一句话主题）。
- 解析 `data["correction_topic"]`（缺失/空兜底 `user_correction`）。
- `correction=True` 分支：删除当场 `internal_event(REFLECTION, …)` + `append_in_transaction`，改为
  `await self.record_failure_signal("user_correction", f"user_correction:{correction_topic}", "用户纠正了回复", event.correlation_id, evidence=[{source_id, summary}])`。
- `record_failure_signal(user_correction)` 达阈值时构造 `REFLECTION(reason="annotation_correction", evidence=…)` 并 append（evidence 由调用方传入）。

## 对象完整性审计（要点，完整表见各 spec）

- **plan / 工具 checkpoint**（18）：按 correlation 保存到回复终局，支持跨进程恢复；非法 JSON/空 steps 降级无计划；`replan` 超限解释后停止；自动重试保留原参数和次数，模型重复调用去重时解释原因。入口、消费者、状态及坏例完整表见18。
- **tool_call_log**（17）：执行事实，`log_id` 预生成 + `mark_adopted`；写失败 best-effort；`adopted` 单向 0→1；`log_id=None` 内部生成不外泄。
- **lesson_signal**（19，仅 user_correction）：计数单调 → 达阈值沉淀 + 删行；未达阈值不产生任何记忆；沉淀失败计数保留下次重试。
- **LESSON 记忆**（19）：`LONG_TERM` 豁免短期淘汰；固定 content 模板 → 语义去重合并不无限新建；当场类（工具/OOC）不计数直接写。

## 验收方式（E2E 为主，减少无意义单测）

遵循「验证管道正确性，不验证 LLM 文本质量」+「E2E 取代单测」约定：

- **agent E2E 为主**：仓库外驱动脚本 + 脚本化 LLM/embedding + 真实 FastAPI/EventBus/内存 SQLite，
  走完整 HTTP/SSE 用户可见路径（发消息 → 工具/规划/反思 → 收回复 → 查表落库）。
- **减少单测**：**不新增 mock LLM 的 pytest 单测**去验证 plan 格式 / replan 次数 / 经验好坏等 LLM 行为管道；
  只保留确定性纯逻辑的纯函数单测。

### E2E 场景（每个 spec ≥1 条用户故事）

- **17**：带工具调用的完整对话 → 查 `tool_call_log`：`correlation_id`/`call_id`/`tool_name`/`status`/`duration_ms`/`adopted`
  落对；「工具白调了」可被 `adopted=0` 查出。
- **18**：多步任务 → plan 产出多步 + execute 逐步执行 + 回复；错误先有界重试，已有备用步骤继续原计划，整体失效才 replan 且 ≤1 次，超限进 respond。
- **19**：工具重试耗尽/空结果/OOC → 沉淀 `LESSON`；同 `correction_topic` 纠正累计达阈值 → 沉淀 LESSON +
  `annotation_correction` 反射；后续规划/回复 `search(kind=LESSON)` 召回注入。

### 保留的纯函数单测（仅确定性逻辑）

- 空结果判定（None/[]/{}/""）、`correction_topic` 兜底、topic 构造、`tool_call_log` 截断与幂等键、
  `lesson_signal` upsert 计数/删除。

### 收尾

- `ruff check`、`pyright` 零报错；`pytest` 全绿（含保留纯函数单测）。
- 同步 `docs/test-inventory.md`、`docs/tech-reference.md`（`MemoryKind.LESSON`、`tool_call_log`/`lesson_signal` 共新增两表）。
- 全部落地后跑 `docs/eval/README.md` P1–P3 / R1–R3 **真 LLM 效果评测**（人工 ✓/✗，测「好不好」，与 E2E 的「坏没坏」区分）。

## 预计修改/新增文件汇总

- 新增：`nyx/tools/tool_call_store.py`
- 修改：`nyx/{db.py, enums.py, app_context.py}`、`nyx/tools/registry.py`、`nyx/expression/{pipeline.py, prompt.py}`、`nyx/types.py`、`nyx/memory/{facade.py, store.py}`、`nyx/eval/evaluator.py`
- 文档：`docs/test-inventory.md`、`docs/tech-reference.md`
- 纯函数单测：`tests/test_tools/test_registry.py`、`tests/test_memory/`、`tests/test_expression/test_pipeline.py`（仅确定性逻辑）
- E2E：仓库外 agent 驱动脚本（验收时跑，不落地 `tests/`）

## 已批准补充与实现边界

- 批注 facts/effect 同事务，学习在提交后 best-effort 执行；提交与信号间中断可丢学习，
  已完成 effect 重放不补发。计数先提交，经验成功后 REFLECTION/清除计数同事务。
- 重规划先检查次数再递增，确实允许一次；增加前端 `lesson` 类型与旧数据库/表达测试快照同步。
- LESSON 只复用核心入库/去重/事件，不提取用户事实或运行矛盾反思旁路；语义候选按完整主题限定，
  重规划刷新经验。技术失败只沉淀策略。
- 外部临时 E2E 的33项检查通过；完整验证快照见 `docs/test-inventory.md`。
  真实模型 P1—P3/R1—R3 的逐项判定与限制见 `docs/eval/README.md`。

### 2026-10-29 已批准补充：持久恢复与失败决策

- 只读失败暂停当前批次，原参数自动尝试最多3次（含首次），间隔1、2秒；不在中间重试时调用决策或学习 LLM。耗尽后把错误、参数、次数、实际步骤状态与未执行调用交回模型。
- 使用官方 `AsyncSqliteSaver` 和角色数据库内的 schema49 `checkpoints` / `writes` 表。新增内部 `tool_pipeline.py`，复用现有 USER_MESSAGE delivery；重启后从待完成节点恢复，不重跑已确认成功的调用。
- 自动重试与模型重复请求分开；去重、未授权、预算及 replan 上限造成的跳过均说明原因。prompt 区分备用步骤、计划整体失效与如实结束。
- 终局 SPEAK/ASK 提交后删除 checkpoint，启动补清已完成残留；后续已批准的副作用修复以可靠执行回执消除盲目重放：已完成结果复用，未确认结果进入 uncertain 停批交模型；只读调用继续有界重试。
- 在既有测试文件补充确定性重试/恢复回归；双进程退出/恢复探针6项通过。最终验证快照见 `docs/test-inventory.md`。本次未重跑真实模型 P3，不把管道回归视为模型主动 replan 已稳定。

### 副作用重放修复

- schema50 增加临时 `tool_execution`，复用现有 ToolCallStore/Registry。执行前提交 running，返回后保存完整结果；回执错误向上传播，观测日志仍 best-effort。
- 已保存结果在重启后直接复用；只有 running 的副作用或旧 checkpoint 的未确认调用进入 uncertain，不自动重做，不继续同批剩余调用。工具被关闭时也保留既有成功/未确认事实。
- checkpoint 删除后清理本回合回执；启动补清终局残留。三种双进程退出/恢复场景共18项检查通过，副作用均只发生一次；最终套件结果见测试清单。
