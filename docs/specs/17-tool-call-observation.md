# 工具执行观测

> 范围：`nyx/tools/`（`ToolRegistry` 加执行上下文参数 + `ToolCallStore`）、`nyx/db.py`（迁移 v47）、
> `nyx/expression/pipeline.py`（`use_tools` 标记采纳）。记录每次工具调用的完整路径：谁触发、调了什么、
> 成功/失败、耗时、结果、是否被采纳，供 eval 与调试定位「工具白调了」与 agent 行为。
> 本 spec 只定义契约（签名 + 语义 + 决策），不内联完整代码；实现以 `nyx/` 源文件为准。

## 元信息

- **前置依赖**：01-types（`Tool`）、05-tools（`ToolRegistry`）、10-eval（`call_id` 语义）、
  04-module-bus-system（版本化迁移 v47）。
- **实现文件**：`nyx/tools/registry.py`、`nyx/tools/tool_call_store.py`、`nyx/db.py`、
  `nyx/expression/pipeline.py`、`nyx/app_context.py`（注入 store）。
- **测试文件**：`tests/test_tools/test_registry.py`（扩展）、`tests/test_expression/test_pipeline.py`（扩展）。

## 用户故事

> 作为 Nyx 的开发者，我想查询每次工具调用的完整执行路径（哪轮对话、哪次 LLM 调用触发、调了哪个工具、
> 成没成功、耗了多久、结果有没有被采纳），以便在 eval 里看出「工具白调了」并定位 agent 行为问题。

## 验收标准

- [x] `tool_call_log` 表（迁移 v47）：`id`/`created_at`/`correlation_id`/`call_id`/`tool_name`/
  `args_json`/`status`/`duration_ms`/`result_json`/`error`/`adopted`。
- [x] `ToolRegistry.__init__` 注入 `store: ToolCallStore | None = None`；`store=None`（测试/无库）纯执行不落库。
- [x] `call` 增加 keyword-only 参数 `correlation_id: str | None = None`、`call_id: str | None = None`、
  `log_id: str | None = None`；返回工具结果不变（不破坏 05-tools 既有 `handler(**args)` 返回契约）。
- [x] `call` 执行成功写 `status="success"` + `result_json`（截断）；失败写 `status="error"` + `error`（截断）；
  计时写 `duration_ms`。
- [x] `mark_adopted(log_id)` 把对应记录的 `adopted` 置 1；`log_id` 由关心采纳的调用方预生成传入。
- [x] 截断：`args_json`/`result_json`/`error` 超限按常量截断，不整存超长内容。
- [x] 写 `tool_call_log` 失败 best-effort：不重抛、不阻断工具执行（同 eval 落库豁免约定）。
- [x] `use_tools` 每条执行的 tool_call 预生成 `log_id`，结果 append 进 `outputs` 后 `mark_adopted(log_id)`。
- [x] `ruff check`、`pyright`、`pytest` 全绿；`docs/test-inventory.md` 同步。

## 技术方案

### 分层与边界

- 执行观测在 `ToolRegistry.call` 单点记录，覆盖所有经过该入口的调用方。
  15 的研究下位工具继续通过构造注入直接调用 handler，不另经 registry；观测包含外层
  `deep_research` 执行，不为它的内部 search/fetch 另建记录。
- `tool_call_log` 是「执行事实」，`eval_log` 是「LLM 记账」——两层独立，不并入 `eval_log`
  （避免污染 `total_tokens` 的按 `call_id` 去重）。两者通过 `call_id` 关联，可 JOIN 出完整路径。

### `ToolRegistry` 契约变更

```python
class ToolRegistry:
    def __init__(self, store: ToolCallStore | None = None) -> None: ...

    async def call(
        self,
        name: str,
        args: dict[str, Any],
        *,
        correlation_id: str | None = None,
        call_id: str | None = None,
        log_id: str | None = None,
    ) -> Any: ...

    async def mark_adopted(self, log_id: str) -> None: ...
```

- **返回类型不变**：`call` 仍返回 `handler(**args)` 的工具结果，05-tools 的 `call` 语义不动。
- **`log_id` 预生成**：`call` 需要 `adopted` 事后标记，但返回类型不能携带记录 id，故由调用方
  预生成 `log_id` 传入。`log_id=None` 时 `call` 内部生成一条记录 id（`uuid4`）但不外泄——
  不关心采纳的调用方（activity/research）保持零改动，`adopted` 恒 0。
- **`mark_adopted` 按 `log_id` 定位**：无歧义，不依赖 `args` 序列化或截断一致性。
- **best-effort 旁路**：写 `tool_call_log` 失败只记日志、不重抛，不阻断工具执行；`mark_adopted`
  失败同理（观测是旁路，不能反向影响对话）。

### 截断

- 记录用常量：`_ARGS_MAX_CHARS = 2000`、`_RESULT_MAX_CHARS = 2000`、`_ERROR_MAX_CHARS = 1000`。
- `args_json` = `json.dumps(args, ensure_ascii=False, sort_keys=True)` 规范化后截断；`result_json`
  = `json.dumps(result, ensure_ascii=False)` 后截断（仅 `status="success"`）；`error` = 异常 `str`
  截断（仅 `status="error"`）。
- 截断与 use_tools 回注的 `_MATERIAL_ITEM_MAX_CHARS` 无关——观测比回注保留更多信息。
- 执行恢复不得依赖这个截断且 best-effort 的观测日志。18 的可靠临时 `tool_execution` 回执同样由 ToolCallStore 管理，但完整保存结果、写失败传播、终局后删除；不改变本表的永久观测语义。

### `use_tools` 标记采纳

- `use_tools` 对每条执行 `tc` 预生成 `log_id = str(uuid.uuid4())`，`deps.tools.call(..., log_id=log_id)`。
- 18 的工具子图为每次实际尝试分别生成并保存 log_id；同一待执行节点重放沿用该 id，正常重试使用新 id，沿用原模型 call_id/correlation_id。中间重试不独立回注模型，保持 adopted=0；最终结果接纳后标记该终局尝试。返回顶层 error 的 handler 仍按正常返回记录 success，表达侧将其识别为需重试的业务失败，不改变 registry 返回契约。
- 结果 append 进 `outputs` 后（即进入下一轮 LLM 上下文）调 `deps.tools.mark_adopted(log_id)`；
  因超限 `break`、未 append、`name not in allowed` 跳过（未执行不写 log）、`deep_research` 超长 break
  的，不 mark。

### 查询

- 本 spec 只建数据源（表 + 写入）。查询 API / 前端 eval 视图不在 MVP，后续按需加，不预建。

## 数据变更

### `tool_call_log`（迁移 v47）

```sql
CREATE TABLE tool_call_log (
    id TEXT PRIMARY KEY,              -- 记录 id（调用方预生成或 call 内部生成）
    created_at REAL NOT NULL,          -- 执行时间戳
    correlation_id TEXT,               -- 溯源链；调用方未传为 NULL
    call_id TEXT,                      -- 触发它的 LLM complete() call_id；非 LLM 触发为 NULL
    tool_name TEXT NOT NULL,
    args_json TEXT NOT NULL,           -- 规范化 args（截断）
    status TEXT NOT NULL,              -- success / error
    duration_ms REAL NOT NULL,         -- handler 执行耗时
    result_json TEXT,                  -- 成功时结果摘要（截断）
    error TEXT,                        -- 失败时异常信息（截断）
    adopted INTEGER NOT NULL DEFAULT 0 -- 结果是否进入后续 LLM 上下文
);
CREATE INDEX idx_tool_call_log_created ON tool_call_log(created_at);
CREATE INDEX idx_tool_call_log_correlation ON tool_call_log(correlation_id);
CREATE INDEX idx_tool_call_log_call ON tool_call_log(call_id);
```

## 对象完整性

### tool_call 记录

**入口清单**

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| `ToolRegistry.call` | 每次工具执行（store 已注入） | `tool_call_log` |
| `ToolRegistry.mark_adopted` | 调用方确认结果已采纳 | 更新 `adopted` |

**消费者清单**

| 消费者 | 发现方式 | 用途 |
|---|---|---|
| 开发者 / eval 查询（后续） | `call_id` JOIN `eval_log` | 定位「工具白调了」、调试行为 |

**状态迁移表**

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| 执行中 | handler 成功返回 | `status=success` + `result_json` | 终态 |
| 执行中 | handler 抛异常 | `status=error` + `error` | 终态 |
| `adopted=0` | 调用方 `mark_adopted` | `adopted=1` | 单向 |

**Bad case 表**

| 情况 | 处理 |
|---|---|
| 空 | `args` 空 dict 照记（`args_json="{}"`）；`result` 空照记 `result_json` |
| 失败 | 写 log 抛异常 → best-effort 记日志不重抛；handler 抛异常 → 记 `status=error` + `error`，工具结果照常上抛给调用方按既有语义处理 |
| 部分完成 | 记录写成功、进程崩溃在 `mark_adopted` 前 → `adopted` 恒 0，MVP 接受（观测偏差，不影响对话） |
| 乱序 | `mark_adopted` 与 `call` 顺序由 use_tools 串行保证，不并发同 `log_id` |
| 重放 | `log_id` 主键冲突 → no-op（预生成 id 不应重复，防御） |
| 删除 | 不适用：观测记录不删除，与 eval_log 同生命周期 |

## 测试要点

- [x] **E2E（agent 驱动，完整用户路径）**：发一条触发工具调用的消息 → 收回复 → 查 `tool_call_log`：
  - [x] `correlation_id`/`call_id`/`tool_name`/`status`/`duration_ms`/`result_json`/`error` 落对。
  - [x] 结果被采纳的行 `adopted=1`；「工具白调了」可被 `adopted=0` 查出。
- [x] **确定性回归（既有 registry 测试，真实 SQLite）**：
  - [x] 截断：超长 `args`/`result`/`error` 按常量截断。
  - [x] `log_id` 主键冲突 → no-op（幂等）；`log_id=None` 内部生成。
  - [x] `call` 返回类型仍是 handler 返回值（不破坏 05-tools 契约）。
- [x] 不测 LLM 文本质量；验证「坏没坏」（落库对、adopted 标对），「好不好」留 `docs/eval/` 真 LLM 评测。

## 完成定义

- [x] `ruff check` 零报错
- [x] `pyright` 零报错
- [x] `pytest` 全绿
- [x] `docs/test-inventory.md` 已更新（快照）
- [x] `docs/tech-reference.md` 源码导航同步（`nyx/tools/tool_call_store.py`、表数量 +1）
