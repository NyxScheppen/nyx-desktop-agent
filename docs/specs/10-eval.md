# eval 可观测（LLM 调用、token 与最终 prompt）

> eval 系统记录 OOC/token，并把传给 `ainvoke` 的应用层最终有序消息按 `call_id` 永久明文保存。最近记录接口保持轻量；用户展开某行时才读取完整 prompt。
> spec 只定义**契约**（签名 + 语义 + 决策），不内联完整代码；代码的唯一事实来源是 `nyx/` 源文件，spec 指向它、不改写它。

## 元信息

- **前置依赖**：03-llm（`LlmClient.complete`）、01-types（`LLMOutput`）、04-module-bus-system（版本化迁移与 REST 薄封装）、11-expression（respond 节点 think/speak 拆分 + `_voice_output`）
- **实现文件**：`nyx/llm/client.py`、`nyx/types.py`、`nyx/eval/evaluator.py`、`nyx/eval/store.py`、`nyx/db.py`、`nyx/api/routes.py`；前端 `api/client.ts`、`types/api.ts`、`stores/evalStore.ts`、`components/panels/EvalPanel.tsx`

## 用户故事

> 作为 Nyx 的开发者，我想在最近的 eval 记录上展开查看当次组装完成的最终 prompt，以便定位上下文、记忆和人格设定是怎样进入模型请求的。

## 验收标准

- [ ] `LLMOutput` 除 token/call_id 外包含 `prompt_messages: list[LlmMessage] | None`，不进入 repr
- [ ] `LlmClient.complete()` 在 `ainvoke` 前复制有序 role/content，并用同一快照调用模型和回填输出
- [ ] `Evaluator.evaluate()` 每次调用写一条 `eval_log` 记录（best-effort，落库失败不重抛）
- [ ] `EvalStore` 原子写 `eval_log` + `eval_prompt`；同 `call_id` 的 think/speak 只保存一份 prompt
- [ ] 迁移 v21 建 `eval_prompt`，旧 eval 记录保持可读但 prompt 为 unavailable
- [ ] recent 的 `limit` 范围为 1..100，响应不包含 prompt；详情端点按 record id 懒加载并返回 `messages | null`
- [ ] 前端 think/speak 继续分两行；每行可独立展开，逐记录加载、缓存、报错和重试

## 技术方案

- **token 抽取（03-llm）**：`complete()` 里 `response = await self._model.ainvoke(...)` 后用纯函数 `_extract_tokens(response) -> tuple[int, int]` 抽 `(prompt_tokens, completion_tokens)`——优先 `response.usage_metadata`（`input_tokens`/`output_tokens`，langchain-core 1.5.5 规范字段），回退 `response.response_metadata["token_usage"]`（`prompt_tokens`/`completion_tokens`，OpenAI 兼容 provider），皆无则 `(0, 0)`。`call_id = str(uuid.uuid4())` 每次调用生成。三者随 `LLMOutput` 返回（保持 `json_mode`/`tools`/`tool_calls` 既有行为不变）。
- **最终 prompt 定义**：仅指 `LlmClient.complete()` 传给 `ainvoke` 的 `system/user/assistant` 有序消息。`tools`、`response_format`、provider 隐式字段、LangChain 内部重试和 `VisionClient` 不在本契约内。调用失败时没有 `LLMOutput`，不新增 eval 行。
- **`LLMOutput` / `_voice_output`**：`prompt_messages` 默认 `None` 兼容旧 mock；真实 client 即使收到空消息列表也写 `[]`。respond 拆出的 think/speak 原样透传同一快照、token 与 call_id。
- **`EvalStore`**：
  - `insert(record, prompt_messages=None) -> None`：在一个 `Database.transaction()` 中 `INSERT OR IGNORE eval_prompt` 后写 `eval_log`
  - `list_recent(limit=5) -> list[EvalRecord]`（`ORDER BY created_at DESC, id DESC`，不读取 prompt）
  - `get_prompt(record_id) -> tuple[bool, list[LlmMessage] | None]`：首项区分记录不存在；次项 `None` 表示旧记录未保存
  - `total_tokens() -> EvalStats`（对 `eval_log` 按 `call_id` 分组后求和——同 `call_id` 的 think/speak 只计一次，避免 reply 双计）
  - JSON 解码必须验证顶层 list、role 枚举和字符串 content；损坏数据不能作为任意结构返回。
- **保留与隐私**：prompt 可能包含用户文本、记忆、canon、历史和工具结果；按产品决策在本地 SQLite 永久明文保存，不裁剪、不加密、不做保留期配置。prompt 不进入 `EvalRecord` 和 recent 响应，避免列表批量暴露及 think/speak 重复存储。
- **`Evaluator` 落库（`nyx/eval/evaluator.py`）**：`__init__` 增注入 `store: EvalStore | None = None`。`evaluate()` 重构为「先算 OOC 关键词分 +（voice 且有 embed 时）embedding 分，再统一落一条记录」——`store` 为 `None` 或 `insert` 抛异常时降级为日志、不重抛（best-effort 旁路，同 eval 现有豁免约定）。docstring 由「不再落库、不再计 token、不再返回报告」改为「写 eval_log，best-effort」。
- **共同浏览记录**：`13-browsing-system` 的 companion 与 integration 分别使用
  `module="browsing"`、`output_type="companion" | "integration"`。LLM 调用/JSON 解析/业务
  结构校验可以使浏览任务失败；`Evaluator.evaluate()` 自身或 eval 持久化失败仍只记日志，
  不得把合法 companion action 改为 none，也不得增加 page attempt 或触发整合重试。
- **数据变更**：v13 的 `eval_log` 仍不存输出 `content`；v21 新增 `eval_prompt`，按真实 call 去重保存输入 prompt。
- **API 端点（04-module-bus-system，main.py 薄封装）**：
  - `GET /api/eval/recent?limit=5` → `list[EvalRecord]`（`app.eval_store.list_recent(limit)`）
  - `GET /api/eval/total_tokens` → `EvalStats`（`app.eval_store.total_tokens()`）
  - `GET /api/eval/{record_id}/prompt` → `list[LlmMessage] | null`；不存在 404、损坏 500、成功响应 `Cache-Control: no-store`
  - `_App` 增 `eval_store: EvalStore`；`build_app_context` 构造 `eval_store = EvalStore(db)`、`evaluator = Evaluator(embed, eval_store)`。
- **类型（01-types）**：`EvalRecord`（`id`/`created_at`/`call_id`/`module`/`output_type`/`model`/`correlation_id`/`ooc_keyword`/`ooc_embed`/`prompt_tokens`/`completion_tokens`）、`EvalStats`（`total_tokens`/`prompt_tokens`/`completion_tokens`）。字段名 = 前端 JSON 键（snake_case 零映射，README §4）。
- **前端**：`evalStore` 用 record-id keyed maps 管理 prompt/loading/error，成功结果缓存、失败可重试、晚到旧记录结果丢弃。`EvalPanel` 用原生 `<details>/<summary>`，React 文本节点 + `<pre>` 渲染，不使用 HTML 注入；长内容内部滚动且不截断。

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

### `eval_prompt` 表（迁移 v21）

```sql
CREATE TABLE eval_prompt (
    call_id TEXT NOT NULL PRIMARY KEY,
    prompt_json TEXT NOT NULL
);
```

## 测试要点

- [ ] `tests/test_llm/test_client.py`：调用前快照、Unicode/换行、解除别名、repr 隐藏。
- [ ] `tests/test_expression/test_pipeline.py`：`_voice_output` 透传 prompt/token/call_id。
- [ ] `tests/test_eval/test_eval_store.py`：JSON 往返、think/speak 只存一份、旧/空/缺失区分、损坏拒绝、token 去重。
- [ ] 集成测试 `tests/test_eval/test_evaluator.py`（新，Mock embed + fake/真 store）：`evaluate`（`store` 有值）写一条记录、`ooc_keyword`/`ooc_embed`/token 字段正确；`store=None` 不写不崩；`store.insert` 抛异常降级不重抛。
- [ ] `tests/test_api/test_endpoints.py`：detail 成功/null/404/损坏、no-store、recent limit 边界。
- [ ] 前端：client URL/cache；store 逐行缓存与失败重试；EvalPanel 展开、状态区分、Unicode/换行及 HTML 字面安全渲染。
- 不测 LLM 文本质量 / OOC 分数大小；验证管道正确（token 抽对、落库对、去重对、端点走对），不验证「分打得好不好」。

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `docs/test-inventory.md` 已更新（快照）
- [ ] `docs/tech-reference.md` 的源码导航同步；API、迁移和 `evaluate` 语义以本 spec 与源码为准
- [ ] 前端 `tsc` + vitest 全绿
- [ ] 用户能在设置面板看到总 token 与最近 5 条 LLM 调用
