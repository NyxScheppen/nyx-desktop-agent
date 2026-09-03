# eval 可观测（LLM 调用 + token 消耗查询）

> eval 系统从「只告警、不落库」升级为「落库 + 可查询」：`LlmClient.complete()` 补上 token 抽取与 `call_id`，`Evaluator.evaluate()` 每次算完 OOC 后写一条 `eval_log` 记录（含 token 消耗）；新增两个 REST 查询端点；前端设置面板展示「总 token + 最近 5 条 LLM 调用（OOC 结果 + token）」。
> spec 只定义**契约**（签名 + 语义 + 决策），不内联完整代码；代码的唯一事实来源是 `nyx/` 源文件，spec 指向它、不改写它。

## 元信息

- **前置依赖**：03-llm（`LlmClient.complete`）、01-types（`LLMOutput`）、04-db（版本化迁移）、18-api（REST 薄封装）、17-expression（respond 节点 think/speak 拆分 + `_voice_output`）
- **实现文件**：`nyx/llm/client.py`、`nyx/types.py`、`nyx/eval/evaluator.py`、`nyx/eval/store.py`（新）、`nyx/db.py`、`nyx/main.py`；前端 `api/client.ts`、`types/api.ts`、`stores/evalStore.ts`（新）、`components/layout/SettingsView.tsx`

## 用户故事

> 作为 Nyx 的开发者，我想要查询每次 LLM 调用的 token 消耗与 OOC 评估结果，以便在设置面板看到「总 token 消耗 + 最近 5 条 LLM 调用及其 OOC 结果、token 消耗」，验证 eval 记账与 token 开销在正常工作。

## 验收标准

- [ ] `LLMOutput` 增补 `prompt_tokens` / `completion_tokens` / `call_id`（`nyx/types.py`；默认 `0`/`0`/`""`，既有构造点与测试 mock 不破坏）
- [ ] `LlmClient.complete()` 每次调用生成唯一 `call_id`，并从 AIMessage 抽取 token（抽不到记 0）
- [ ] `Evaluator.evaluate()` 每次调用写一条 `eval_log` 记录（best-effort，落库失败不重抛）
- [ ] `EvalStore` 提供 `insert` / `list_recent(limit)` / `total_tokens()`；`total_tokens` 对共享 `call_id` 去重（think/speak 只计一次）
- [ ] 迁移 v13 建 `eval_log` 表（**不存 `content` 原文**）
- [ ] `GET /api/eval/recent?limit=N` 返回最近 N 条；`GET /api/eval/total_tokens` 返回累计 token
- [ ] 前端设置面板展示「总 token + 最近 5 条」（think/speak 分两行，各自 OOC + 共享 token）

## 技术方案

- **token 抽取（03-llm）**：`complete()` 里 `response = await self._model.ainvoke(...)` 后用纯函数 `_extract_tokens(response) -> tuple[int, int]` 抽 `(prompt_tokens, completion_tokens)`——优先 `response.usage_metadata`（`input_tokens`/`output_tokens`，langchain-core 1.5.5 规范字段），回退 `response.response_metadata["token_usage"]`（`prompt_tokens`/`completion_tokens`，OpenAI 兼容 provider），皆无则 `(0, 0)`。`call_id = str(uuid.uuid4())` 每次调用生成。三者随 `LLMOutput` 返回（保持 `json_mode`/`tools`/`tool_calls` 既有行为不变）。
- **`LLMOutput` 增补（01-types）**：加 `prompt_tokens: int = 0`、`completion_tokens: int = 0`、`call_id: str = ""`，默认值保证既有构造点（各 Facade 的 mock、`_voice_output`、测试）不改也能过。
- **`_voice_output` 透传（17-expression）**：respond 节点拆 think/speak 时，`_voice_output` 把 `prompt_tokens`/`completion_tokens`/`call_id` 原样带过去——think、speak 两条 eval 记录**共享同一 `call_id` 与同一 token 消耗**（一次 LLM 生成拆两份 OOC，token 归这一次调用）。
- **`EvalStore`（新，`nyx/eval/store.py`）**：SQLite store，遵循既有 store 锁约定（`db.lock` 串行化、方法不嵌套持锁，见 store-lock-scope）。三方法：
  - `insert(record: EvalRecord) -> None`
  - `list_recent(limit: int = 5) -> list[EvalRecord]`（`ORDER BY created_at DESC LIMIT ?`）
  - `total_tokens() -> EvalStats`（对 `eval_log` 按 `call_id` 分组后求和——同 `call_id` 的 think/speak 只计一次，避免 reply 双计）
  - **保留策略**：不裁剪、永久累计（用户已定「持久化 + 永久累计」）——记录永久保留、总 token 自首次调用累计、重启不清零；本 spec 不做裁剪，日后若担心膨胀可另加。
- **`Evaluator` 落库（`nyx/eval/evaluator.py`）**：`__init__` 增注入 `store: EvalStore | None = None`。`evaluate()` 重构为「先算 OOC 关键词分 +（voice 且有 embed 时）embedding 分，再统一落一条记录」——`store` 为 `None` 或 `insert` 抛异常时降级为日志、不重抛（best-effort 旁路，同 eval 现有豁免约定）。docstring 由「不再落库、不再计 token、不再返回报告」改为「写 eval_log，best-effort」。
- **数据变更（04-db 迁移 v13）**：建 `eval_log` 表 + `idx_eval_log_created` 索引（见下）。**不存 `content` 原文**（数据最小化，CLAUDE.md 安全节；面板只看 OOC 分 + token，不回看具体输出）。
- **API 端点（18-api，main.py 薄封装）**：
  - `GET /api/eval/recent?limit=5` → `list[EvalRecord]`（`app.eval_store.list_recent(limit)`）
  - `GET /api/eval/total_tokens` → `EvalStats`（`app.eval_store.total_tokens()`）
  - `_App` 增 `eval_store: EvalStore`；`build_app_context` 构造 `eval_store = EvalStore(db)`、`evaluator = Evaluator(embed, eval_store)`。
- **类型（01-types）**：`EvalRecord`（`id`/`created_at`/`call_id`/`module`/`output_type`/`model`/`correlation_id`/`ooc_keyword`/`ooc_embed`/`prompt_tokens`/`completion_tokens`）、`EvalStats`（`total_tokens`/`prompt_tokens`/`completion_tokens`）。字段名 = 前端 JSON 键（snake_case 零映射，README §4）。
- **前端**：`types/api.ts` 增 `EvalRecord`/`EvalStats`；`api/client.ts` 增 `getEvalRecent(limit?)`/`getEvalTotalTokens()`；新 `stores/evalStore.ts`（state：`records`/`stats`/`error`/`loading`；action：`load()` = `Promise.all([getEvalRecent(5), getEvalTotalTokens()])`）；`SettingsView.tsx` 加一个 `<Panel title="LLM 调用 / token">`——顶部总 token（`stats.total_tokens`，可细分 prompt/completion），下面列最近 5 条：**think/speak 分两行**（用户已定），每行显 `output_type`、`ooc_keyword`（embed 非空也显 `ooc_embed`）、`prompt_tokens + completion_tokens`。

### `eval_log` 表（迁移 v13）

```sql
CREATE TABLE eval_log (
    id TEXT PRIMARY KEY,              -- 每行 uuid（每次 evaluate 一条）
    created_at REAL NOT NULL,          -- 评估时间戳（排序键）
    call_id TEXT NOT NULL,             -- 一次 complete() 唯一 id；think/speak 共享，总 token 去重用
    module TEXT NOT NULL,              -- 产出模块（expression/desire/…）
    output_type TEXT NOT NULL,         -- 产出类型（think/speak/tool/desire/…）
    model TEXT NOT NULL,               -- 本次调用模型名
    correlation_id TEXT NOT NULL,      -- 溯源链
    ooc_keyword REAL NOT NULL,         -- 关键词 OOC 分 [0,1]，1=完全贴合
    ooc_embed REAL,                    -- embedding OOC 分 [0,1]；非 voice 类型 / embed 关闭为 NULL
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_eval_log_created ON eval_log(created_at);
```

## 测试要点

- [ ] 单元测试 `tests/test_llm/test_client.py`：`_extract_tokens` 纯函数——`usage_metadata`（input/output_tokens）/ `response_metadata.token_usage`（prompt/completion_tokens）/ 皆无 → `(0,0)`；`complete`（fake AIMessage 带 usage）→ `LLMOutput.prompt_tokens`/`completion_tokens` 正确、`call_id` 非空且两次调用不同。
- [ ] 单元测试 `tests/test_expression/test_pipeline.py`：`_voice_output` 透传 `prompt_tokens`/`completion_tokens`/`call_id`。
- [ ] 集成测试 `tests/test_eval/test_store.py`（新）：`insert` 后 `list_recent` 倒序 + limit 正确、字段读回一致；`total_tokens` 对共享 `call_id` 的两行（think/speak）只计一次、不共享的分别计。
- [ ] 集成测试 `tests/test_eval/test_evaluator.py`（新，Mock embed + fake/真 store）：`evaluate`（`store` 有值）写一条记录、`ooc_keyword`/`ooc_embed`/token 字段正确；`store=None` 不写不崩；`store.insert` 抛异常降级不重抛。
- [ ] 集成测试 `tests/test_api/test_endpoints.py`：`GET /api/eval/recent?limit=5` 返回最近 5、`GET /api/eval/total_tokens` 返回累计。
- [ ] 前端 `tests/api.test.ts`：`getEvalRecent`（limit 拼 query）/`getEvalTotalTokens` 端点与方法正确。`tests/stores.test.ts`：`evalStore.load` 落 `records`/`stats`、失败置 `error`。`tests/settingsView.test.tsx`：面板渲染总 token + 最近 5 条。
- 不测 LLM 文本质量 / OOC 分数大小；验证管道正确（token 抽对、落库对、去重对、端点走对），不验证「分打得好不好」。

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `docs/test-inventory.md` 已更新（快照）
- [ ] `docs/tech-reference.md` Evaluator 段（`evaluate` 落库）+ API 端点表 + 迁移版图（v13）同步
- [ ] 前端 `tsc` + vitest 全绿
- [ ] 用户能在设置面板看到总 token 与最近 5 条 LLM 调用
