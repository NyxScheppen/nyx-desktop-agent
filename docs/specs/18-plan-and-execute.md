# 规划与执行（plan-and-execute）

## 输入更新与交还（26）

外层回复图是输入、版本和授权的唯一写入方。工具子图在安全边界探测补充，返回 `exit_reason=input_pending` 并同步保存 checkpoint，正常出口写 complete。
同根 input_update 先只读核对旧执行回执，再整组更新输入/schema/allow_new_actions；旧 calls、未执行计划失效，records/outputs/attempts/rounds/replans 保留。
成功结果复用，已开始而未确认的副作用 uncertain，尚未开始的旧动作不补执行。只读 retry_wait 需新版模型重新选择才使用剩余次数。
输入/授权相同才续跑旧 snapshot.next；completed snapshot 遇新输入也必须走更新入口。外层选择回答时仅导出已确认事实，不发起新动作。
完整确定时序、预算与旧 checkpoint 规则见 [26 §5](26-conversation-steering.md)。

> 范围：`nyx/expression/pipeline.py`（回复图接线）、`nyx/expression/tool_pipeline.py`（持久工具子图）、`nyx/expression/prompt.py`（规划与执行 prompt）。
> 给慢通道工具查询加上「先规划、再执行（ReAct）、计划失效时有限次重规划」的能力，取代「边想边查」。
> 本 spec 只定义契约（签名 + 语义 + 决策），不内联完整代码；实现以 `nyx/` 源文件为准。

## 元信息

- **前置依赖**：11-expression（reply 图、`use_tools` 节点）、05-tools（`ToolRegistry`）、
  17-tool-call-observation（execute 复用工具观测的 `log_id`/`mark_adopted`）、10-eval（`output_type` 记账）。
- **实现文件**：`nyx/expression/{pipeline,tool_pipeline,prompt,facade}.py`、`nyx/types.py`（`Plan`/`PlanStep`）、`nyx/db.py`、`nyx/app_context.py`。
- **测试文件**：`tests/test_expression/{test_pipeline,test_expression_facade}.py`、`tests/test_db/test_db.py`。

## 用户故事

> 作为 Nyx 的用户，我想让她在需要多步工具查询的复杂任务上先规划出步骤、再逐步执行，
> 并在计划走不通时有限次重新规划，而不是边想边查。

## 验收标准

- [x] `use_tools` 节点：慢通道进入后先 `plan`（LLM 出结构化计划），再 `execute`（ReAct），
  计划失效时有限次重规划。
- [x] 计划产出结构化步骤：`goal` + `steps[{text, expected_tool, done_condition}]`。
- [x] 计划非法 JSON / `steps` 空 → 降级为无计划直接 execute，不阻断回复。
- [x] execute 每轮 prompt 带当前计划（`goal` + `steps`）+ 已执行工具结果。
- [x] `replan` 伪工具：不进公开 `ToolRegistry`，只在 execute 轮 schema 临时注入；LLM 调 `replan` → 重规划。
- [x] 重规划上限 `_MAX_REPLANS = 1`；超限忽略 `replan`，用当前结果进 respond。
- [x] plan/replan 的 LLM 调用走 eval 记账（`output_type="plan"`）。
- [x] execute 的工具调用复用 17 观测（预生成 `log_id` + 采纳后 `mark_adopted`）。
- [x] 快通道不变（不走 `use_tools`）。
- [x] `ruff check`、`pyright`、`pytest` 全绿；`docs/test-inventory.md` 同步。

## 技术方案

### 分层与边界

- 外层回复图按26增加收集、分析和统一终局节点；`use_tools` 继续运行本契约的持久子图，在安全点通过 input_pending 交还。
  本次只把 `use_tools` 节点内部从「纯 ReAct」升级为「plan → execute(ReAct) → 有限次重规划」。
- `use_tools` 内部运行独立 LangGraph 子图；规划、判断、单次调用准备/执行、重试等待和结果接纳形成 checkpoint 边界。外层回复图不持久化。

### `Plan` 类型

```python
@dataclass
class PlanStep:
    text: str                      # 步骤描述
    expected_tool: str | None      # 预期工具（可空；指导，不硬校验注册）
    done_condition: str            # 完成条件（自然语言）

@dataclass
class Plan:
    goal: str
    steps: list[PlanStep]
```

### plan 步骤

- `use_tools` 开头，慢通道先调一次 LLM 产出计划（JSON 文本，复用 respond 的 JSON 解析模式）：
  `{"goal": ..., "steps": [{"text": ..., "expected_tool": ..., "done_condition": ...}]}`。
- 产出走 `deps.llm.complete(..., output_type="plan")` + `deps.evaluator.evaluate`（10-eval 记账；
  `plan` 非 voice 类型，不走 OOC embedding）。
- 解析失败 / `steps` 为空 → 降级为「无计划」，直接进入 execute 的现有 ReAct（行为对齐改造前），
  不抛、不阻断回复。

### execute 循环（ReAct）

- 复用现有 `_MAX_TOOL_ROUNDS = 4` 轮循环。
- 每轮 prompt 带：当前计划（`goal` + `steps`）+ 已执行工具结果（现有 `outputs` 回注）。
- 执行任务提示继续未完成步骤、不重复同名同参数调用；计划失效时请求 replan。
- 每轮 `tool_schemas` = 当前授权工具（现有 skill 过滤逻辑不变）+ 临时注入的 `replan` 伪工具。
- 公开工具按实际调用记录去重，自动重试沿用待执行调用而不经过去重；截断、`memory_recall` 特殊处理保留，并接入 17 观测
  （预生成 `log_id`、采纳后 `mark_adopted`）。

### replan 伪工具与重规划

- `replan` 是**内部信号工具**，不注册进 `ToolRegistry`、不出现在公开 `schema()` 与 skill 授权里；
  只在 execute 轮的 `tool_schemas` 末尾临时追加一条：
  `{"name": "replan", "description": "当前计划走不通，请求重新规划", "parameters": {"reason": {"type": "string"}}}`。
- execute 遍历 `tool_calls` 时，若 `name == "replan"`：不执行任何 handler，标记重规划。
- 重规划：先检查已重规划次数 `< _MAX_REPLANS`，满足时计数 +1，回到 plan 步骤（prompt 带
  「上次计划 + 已执行结果 + replan reason」）重出计划，重置 execute 轮次；否则结束 execute 进 respond。
- 只读调用耗尽后的结构化计划决策选择 `replan` 时，也进入同一重规划节点，遵守相同上限。

### 常量

```text
_MAX_REPLANS = 1
```

## 数据变更

2026-11 prompt时间补充：ToolState checkpoint可选`plan_at`记录计划生成时间；旧checkpoint缺失标未知。
pending/records可选`started_at`、`received_at`分别记录本次尝试准备与结果接收时间，不能当作资料发生时间。
入口仅为既有plan/begin/execute；消费者为既有plan/decide/review及最终执行反馈。无新状态或表。
空计划不提供生成时间，失败沿用原重试/uncertain；部分完成保留已存时钟，乱序不覆盖来源时间，
恢复不以当前时间回填旧记录，删除随既有checkpoint/回执清理。标签受既有预算限制。

schema49 增加官方 AsyncSqliteSaver 使用的 `checkpoints`、`writes` 表，schema50 增加临时执行回执 `tool_execution`；计划及执行进度仅保存到本次回复终局提交。工具执行事实仍由 17 的 `tool_call_log` 记录。

## 对象完整性

### 计划（plan）

> 生命周期 = 同一 correlation 的回复完成前，随工具 checkpoint 持久化；可跨进程恢复。

**入口清单**

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| plan 步骤 LLM 产出 | 慢通道进入 use_tools | 工具子图 checkpoint |
| 重规划再产出 | execute 中 LLM 调 `replan` 或耗尽后计划决策选择 replan，且未超限 | 工具子图 checkpoint（覆盖计划，保留调用记录） |

**消费者清单**

| 消费者 | 发现方式 | 用途 |
|---|---|---|
| execute 循环 | 读 checkpoint 中的 `plan` | 每轮 prompt 带计划执行 |
| respond（间接） | execute 结束 | 工具结果回注后回复 |

**状态迁移表**

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| 无计划 | plan 步骤产出合法 | 有计划（execute） | 走 eval 记账 |
| 无计划 | plan 解析失败/空 steps | 无计划直接 execute | 降级，不阻断 |
| 有计划 | execute 中 LLM 调 `replan` 且 `replan<1` | 重新 plan（重规划） | 带上次计划+结果重出 |
| 有计划 | execute 中 LLM 调 `replan` 且 `replan≥1` | 结束 execute | 忽略 replan，进 respond |
| 有计划 | 耗尽后的计划决策为 continue | 继续 execute | 保留计划与已有调用，下一步指向原计划步骤 |
| 有计划 | 耗尽后的计划决策为 replan | 既有 replan 节点 | 同样执行一次上限，保留结果和去重记录 |
| 有计划 | 耗尽后的计划决策为 stop 或失败 | 结束 execute | 将原因和已有结果回注 respond |

**Bad case 表**

| 情况 | 处理 |
|---|---|
| 空 | `steps` 空 → 降级无计划 execute；`goal` 空但 `steps` 非空 → 照常（goal 是描述） |
| 失败 | plan LLM 调用异常 → 降级无计划 execute，不阻断；plan 解析非法 → 同上 |
| 部分完成 | 计划多步只执行部分、工具全失败 → 结果照常回注 respond，不强制补全步骤 |
| 乱序 | 正常情况下同轮公开工具先执行，再处理 replan；工具重试耗尽则先交回模型，剩余调用明确标记未执行 |
| 重放 | 同 correlation 恢复原工具子图；已完成调用不重跑，`replan` 不超过一次 |
| 删除 | 终局回复持久提交后删除本 correlation 的 checkpoint；启动补清已终局残留 |

## 持久恢复与工具重试（2026-10-29 已批准）

- 使用 `langgraph-checkpoint-sqlite==3.1.1` 的 `AsyncSqliteSaver`，保存状态只含 JSON 数据，不序列化 handler、闭包或运行时对象。`ToolState`、`build_tool_graph(deps)` 定义在 `nyx/expression/tool_pipeline.py`，复用 `ReplyDeps`，不新增 Service/Manager 层。
- 每角色复用现有 SQLite 文件；checkpointer 使用独立连接，由 `connect()` 创建、`Database.close()` 关闭。`:memory:` 使用 InMemorySaver，无额外连接；宿主自定义迁移库不创建 checkpointer。外部调用期间不持有 SQLite 事务。
- `thread_id=correlation_id`。子图以同步 durability 保存边界；`USER_MESSAGE` 重投时恢复待执行节点，保留原参数、计划、结果、轮数和累计尝试次数。已完成子图可直接复用结果；存在 checkpoint 时强制走慢通道，避免重启后的重新分类绕过恢复。
- 只读工具异常或顶层显式 `error` 结果最多尝试3次（含首次），失败后分别等待1、2秒；重试期间不调用决策 LLM。空结果是查询完成但未命中，不机械重试。取消继续传播，保留恢复进度。
- 调用步骤状态为 `running → retry_wait → running → success/empty/exhausted`，副作用未确认进入 `uncertain`；记录调用编号、工具名、原参数、累计次数和各次错误。它表示实际调用状态，不能据此伪造自然语言 `done_condition` 已满足。
- 重试耗尽后才向模型交付完整失败事实；失败学习也在耗尽后一次执行，空结果照常学习。重试成功不沉淀本次暂时错误。
- 同批后续调用在前一步重试耗尽后不继续执行，向模型说明未执行调用的参数，等待新的决策。重规划和4轮判断计数独立于同一次调用的重试次数。
- 自动重试不走模型调用去重。模型重复请求已完成或已耗尽次数的同名同参数调用，返回明确跳过原因；只含重复请求的一轮仍允许下一轮决策。授权、结果预算、调用数量和重规划上限造成的跳过也必须反馈原因。
- 计划已有备用步骤时继续原计划；原计划整体失效才调用 `replan(reason)`；无可行路径或预算耗尽时如实结束。执行与重规划 prompt 都带当前计划、调用步骤状态和跳过说明。
- 原参数持久保存；prompt 展示仍受有界预算约束，超长资料截断必须标明。`deep_research` 的结果 JSON 保持完整。
- 工具副作用与 checkpoint 不能跨存储原子提交；执行回执负责阻止结果未确认的副作用被自动重放，规则见下节。

### 耗尽后的计划决策（2026-10-29 已批准）

- 已有合法计划、只读调用重试耗尽且仍有判断/结果预算时，必须先完成一次 JSON 计划决策，
  再允许新的工具判断；不能直接把临时想到的路线当作原计划的备用步骤。
- 决策复用 `LlmClient.complete(..., output_type="tool", json_mode=True)` 和既有 eval，
  不暴露能力工具。输入包含原计划、实际调用状态/原参数/错误、已有结果及剩余预算。
  输出为 `{"action":"continue|replan|stop","reason":"原因","next_step":整数或null}`。
- `continue` 必须指向原计划中仍可执行的步骤（1 起算），只有原计划已有的备用步骤才能直接续跑；
  原计划整体失效、无备用步骤但仍可另找方案时选 `replan`，先进入既有重规划节点再查询。
  无可行路径或任务已完成选 `stop`。代码校验动作、非空原因和步骤范围，语义选择由 LLM 判断。
- 每个新耗尽调用最多完成一次该决策；不额外消耗四轮工具判断预算，不重置调用/结果预算。
  一次 replan 上限仍由既有节点执行；超过上限如实结束。预算耗尽不增加决策调用。
- 重规划后的反馈必须说明新计划已生成、已用/剩余 replan 次数；旧决策是已处理的历史，
  不能让模型把它误当成仍需执行的重规划请求。
- 非法 JSON、非法动作/步骤、空原因或决策调用失败时，停止新工具调用，并向最终回复解释
  “计划决策失败”；保留已经取得的结果。无计划、空结果与副作用 uncertain 沿用原有语义。

**计划决策 checkpoint 完整性**

| 项目 | 契约 |
|---|---|
| 入口 | 既有工具子图中，合法计划的只读调用耗尽后进入 `review_plan`；旧 checkpoint 缺少决策时在继续判断/选调用前补做 |
| 消费者 | 子图路由读取 `ToolState.plan_review`；后续判断、重规划及回复从反馈读取决策及原因 |
| 状态 | 未决策→continue/replan/stop；保存耗尽调用编号、动作、原因和原计划步骤号，节点完成后同步 checkpoint |
| 空 | 无计划/无耗尽调用不适用；旧 checkpoint 缺字段视为尚未决策 |
| 失败 | 决策失败结束工具阶段并解释；checkpoint 写失败继续由原 durable delivery 恢复 |
| 部分完成 | 保留之前结果、去重记录和累计重试次数；决策不证明任何 done_condition 已满足 |
| 乱序 | 决策前不执行新工具；continue/replan 的后续节点只在决策持久完成后运行 |
| 重放 | 同一耗尽调用复用已保存决策；恢复不重新读取失败文件或重做已完成决策 |
| 删除 | 随现有 correlation checkpoint 在回复终局后清理；不新增表、API 或公开工具 |

### 副作用执行回执（2026-10-29 已批准补充）

- schema50 新增临时 `tool_execution(id, correlation_id, status, result_json, error)`，由现有 `ToolCallStore` 管理。`id` 复用已保存的尝试 log_id；每次实际重试使用新 id。完整结果 JSON 不截断，不使用 best-effort 的 `tool_call_log` 作为恢复依据。
- `ToolCallStore.start_execution(log_id, correlation_id)` 原子插入 running 回执；新插入返回 None，已有回执返回其快照。`finish_execution(log_id, result, error)` 保存 success/error；`clear_executions(correlation_id)` 清理。`ToolRegistry` 提供同名方法；未注入 store 时仅保留实例内回执，与内存 checkpoint 的生命周期一致。
- 执行前先成功提交 running 回执；执行返回后先成功保存结果，再返回工具节点。回执读写错误向上抛，不能被工具错误重试分支吞掉；准备失败不调用 handler，结果保存失败保留 running。
- `is_read_only_call(name, args)` 仅认可 `local_search`、`web_search`、`memory_recall`、`knowledge_search` 及 `file_io` 的 read/list。其它调用（包括写文件、任务受理、带摄入的 web_fetch、deep_research）有副作用，不能因异常推定没有执行。
- 恢复遇到 success 回执直接复用完整结果；只读 error 回执复用错误后进入原重试次数，running 可重跑。同一副作用调用遇到 running/error 回执、handler 异常或显式 error 时进入 `uncertain`，不自动重试、不沉淀为已确认失败，不执行本批剩余调用。
- 授权复核只阻止新的 handler 执行；工具重启后被关闭时，已保存结果仍复用，已有 running 副作用仍为 uncertain，不能误报为没有执行的 skipped。
- 新尝试在 checkpoint 中记录 `receipt_required=true`。旧 checkpoint 的副作用 running/retry_wait 没有该标记，无法证明未执行，转 uncertain；旧 pending 尚未尝试可正常启动。已完成旧结果继续复用。
- `uncertain` 的参数、原因、次数和未执行调用传给决策及回复；不得声称成功或完全未执行，也不能换参数规避未确认状态。模型应先查证实际结果或如实说明。相同参数重复请求解释“结果未确认，禁止重复副作用”。

**回执对象完整性**

| 项目 | 契约 |
|---|---|
| 入口 | 工具子图执行前原子领取、handler 返回后保存；不增加 API 或后台消费者 |
| 消费者 | 工具子图恢复按 log_id 精确查询；模型读取由 checkpoint 整理的有界状态 |
| 状态 | 无→running（提交后才执行）→success/error；运行中取消或结果保存失败保持 running，副作用恢复为 uncertain |
| 空 | 无回执的新尝试可领取；空返回值以完整 JSON 保存，仍按既有 empty 语义处理 |
| 失败 | 回执读写错误上抛；副作用异常不能证明未生效，进入 uncertain；只读错误保留有界重试 |
| 部分完成 | 已保存结果直接接纳；副作用部分完成/未确认时停批并交模型，不猜测完成 |
| 乱序 | 同一尝试先领取、后执行、再完成，原子领取只允许一个首次执行者；running 的副作用不再次执行 |
| 重放 | 完成回执复用；未确认副作用禁止自动重放；旧 checkpoint 同样保守处理 |
| 删除 | 终局提交且 checkpoint 删除后清理本 correlation；启动补清已有 SPEAK/ASK 的回执；删除角色库同时删除 |

### 工具 checkpoint 对象完整性

| 项目 | 契约 |
|---|---|
| 入口 | 慢通道首次启动子图、节点完成、失败重试、已有 USER_MESSAGE 的 durable 重投 |
| 消费者 | 工具子图恢复/路由；execute、replan 和最终回复读取有界结果与状态；启动清理 |
| 空 | 无计划仍可执行；没有调用直接结束子图 |
| 失败 | 只读工具错误进入有界重试，副作用未确认进入 uncertain；checkpoint/回执写入错误向上抛，由既有 delivery 恢复，不假装成功 |
| 部分完成 | 保留先前已接纳结果和本轮尚未执行的调用，只续跑待完成节点 |
| 乱序 | 同轮公开工具按模型顺序执行；replan 信号在本轮公开工具后处理 |
| 重放 | 同角色、同 correlation 恢复原进度；计数不重置；已有回执复用，未确认副作用转 uncertain |
| 删除 | 回复终局已持久提交后删该 thread；启动补清已有 SPEAK/ASK 的 thread，删除角色库同时删除 checkpoint |

### 验证

- 重试后成功、三次耗尽、显式错误、空结果、重复跳过与授权/预算反馈。
- 第一调用成功、第二调用失败后关闭并重建数据库/Facade，验证只重试第二调用、次数不重置、参数保留。
- 终局提交后清理与启动补清；取消不触发后续调用，跨角色相同 correlation 隔离。

## 测试要点

- [x] **E2E（agent 驱动，完整用户路径）**：发多步任务 → 观察 plan 产出多步 + execute 逐步执行 + 最终回复；
  计划走不通的场景 → replan 发生且 ≤1 次，超限进 respond。
- [x] **纯函数单测（仅确定性逻辑）**：
  - [x] `Plan`/`PlanStep` 解析：合法 JSON → 解析成功；非法 JSON / 空 `steps` → 降级无计划（不抛）。
- [x] 不测 LLM 规划质量；验证「坏没坏」（降级对、重规划次数对），「好不好」留 `docs/eval/` 真 LLM 评测。

## 完成定义

- [x] `ruff check` 零报错
- [x] `pyright` 零报错
- [x] `pytest` 全绿
- [x] `docs/test-inventory.md` 已更新（快照）
- [x] `docs/tech-reference.md` 源码导航同步（`Plan`/`PlanStep` 类型、`use_tools` 行为）
