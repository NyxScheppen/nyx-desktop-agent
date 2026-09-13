# 记忆门面（Facade）

> 范围：`memory/facade.py`（`MemoryFacade`：场景化记忆创建 + 检索委托 + 想起升级 + 新鲜度衰减/容量淘汰 + 导出 + bounded persistence/build-edge logic）。
> Facade 层 spec：记忆生命周期（新鲜度衰减、短期→长期升级、容量淘汰）都在这层；纯 CRUD 在 07（`MemoryStore`），融合召回与图算法在 08（`MemoryRetrieval` / `MemoryGraph` / `AnnIndex`）。
> 矛盾检测走「bounded persist semantic candidates 门控 + 独立单任务 LLM 调用」：候选来自 ANN 上限，top 5 且 cosine 过阈值才 +1 调用，无候选则 0 调用。
> spec 只定义契约（签名 + 流程 + 阈值决策）；实现以 `nyx/memory/facade.py` 源文件为准。

## 元信息

- **前置依赖**：01-types（`Memory` / `MemoryEdge` / `Event` / `EventType` / `MemoryType` / `MemoryEdgeKind` / `Source`）、02-config（`MemoryConfig`：`short_term_capacity` / `promote_threshold` / `freshness_decay`）、03-llm（`LlmClient.complete`）、05-event（`EventBus.publish`）、07-memory-store（`MemoryStore`）、08-memory-retrieval（`MemoryRetrieval` / `EmbedFn` / `extract_keywords`）、08-memory-retrieval 的 ANN（`AnnIndex`）、eval（`Evaluator`）

## 用户故事

> 作为 Nyx 系统的开发者，我想要 `MemoryFacade` 统一处理记忆写入、去重、建边、矛盾检测、生命周期与检索委托，以便表达管道（17）只调 `search` / `create_scene_memory` / `record_recall`，而写入侧不再用无界全表余弦扫描或单一语义边污染图结构。

## 验收标准

- [ ] `facade.py` 含 `MemoryFacade` + `PersistSemanticHit` + `decay_freshness` / `extract_entities` / `_parse_scene` / `_build_scene_prompt` / `_has_negation` / `_content_preview` / `_build_contradiction_prompt` / `_parse_contradiction` / `_parse_relation_edges` / `_memory_relation_prompt` / `_join_list` / `_activity_memory_fields` / `_memory_to_dict` / `_memory_to_markdown`（实现见 `nyx/memory/facade.py`）。
- [ ] 公开方法签名：`create_scene_memory(reply_context: dict[str, str]) -> Memory` / `remember_activity(event: Event) -> None` / `remember_user_profile(content: str, summary: str, aspects: list[str], correlation_id: str) -> None` / `remember_knowledge(items: list[dict[str, str]], correlation_id: str) -> None` / `remember_reading(content: str, summary: str, correlation_id: str) -> None` / `record_no_answer(question: str, correlation_id: str) -> None` / `search(query: str) -> list[Memory]` / `record_recall(memory_id: str) -> None` / `list_memories(tag, type, limit) -> list[Memory]` / `count_new(tag, since) -> int` / `export(fmt) -> str`。
- [ ] `create_scene_memory`：LLM 调用 1（`json_mode=True`、`module="memory"`、`output_type="scene_memory"`）产出 `{content, tag, summary}` → 构造短期记忆 → 复用 `_persist_memory` → 返回最终持久化 `Memory`。
- [ ] 活动/读书/知识/用户画像/未答记录入口都复用 `_persist_memory`，不绕过统一去重、建边、矛盾检测、衰减/淘汰尾段；确定性入口无 scene-memory LLM 调用。
- [ ] `_persist_memory` 两层去重：先 `store.find_by_content(memory.content)` 精确 content hash，再 bounded persist semantic candidates 最高候选 `cosine >= _DEDUP_SIM_THRESHOLD`。命中旧记忆时只 `strengthen` 并返回持久化旧记忆，不建边、不做矛盾检测、不发布 `memory_created`。
- [ ] 未命中去重时才 `store.add(memory)`，随后 `_build_edges(memory, candidates, now, correlation_id)`、`_detect_contradiction(memory, candidates, correlation_id)`、`_decay_and_evict(now)`、发布 `memory_created`。
- [ ] `PersistSemanticHit(memory, cosine)` 是 `_persist_memory` 专用候选类型；语义去重、语义建边、矛盾检测共用 `_persist_semantic_candidates` 得到的 bounded ANN 候选，不做无界全表候选列表。
- [ ] `_detect_contradiction` 只接收 `list[PersistSemanticHit]`；从前 5 个候选中筛 `cosine >= 0.6` 的记忆调用一次 LLM，无候选或全低于阈值时 0 调用。LLM 失败、JSON 解析失败、返回未知 id 时只记录并跳过 reflection，不回滚已入库记忆。
- [ ] 建边使用五类信号：语义、实体、关键词、时间、LLM 关系。每类最多 `_EDGE_PER_KIND_LIMIT=4` 条；节点总 incident typed edges 最多 `_EDGE_TOTAL_LIMIT=16` 条。
- [ ] 语义边优先复用 persist semantic candidates；实体候选优先复用 ANN 候选，缺失时用最新记忆补足；关键词边复用 `extract_keywords(summary + "\n" + content)` 和 `store.search_keywords(..., limit=40)`；时间边只取 24 小时窗口内最近候选；LLM 关系只对综合候选 top 5 调用，`none` 不写边。
- [ ] degree pruning 对新节点和触达旧节点都执行；按完整 `(from_id, to_id, kind)` typed key 调 `delete_edges` 删除，canonical edge 语义由 store 保证。
- [ ] `search(query)` 纯委托 `MemoryRetrieval.search(query)`，对表达层保持 `search(query: str) -> list[Memory]`，不暴露 `direct_limit` / `association_limit` 参数。
- [ ] `record_recall`：`recall_count+1`；短期满 `promote_threshold` 次升级长期 + 发布 `memory_promoted`；长期不重复升级。
- [ ] `pyright` strict 零报错。

## 技术方案

- **修改文件**：`nyx/memory/facade.py`（无 API、无数据变更、无新表）。
- **依赖注入**：`MemoryFacade(store, retrieval, bus, llm, evaluator, config, embed=None)`；`embed` 与 `retrieval` 共享同一实例（组合根 18-api 注入），写入时算 embedding，检索时读持久化 embedding。
- **事件 content**：`memory_created` / `memory_promoted` = `{"memory_id": id}`；`reflection` = `{"summary": str}`。Facade 自己 publish，绝不返回 Event。

### `_persist_memory` 顺序

模块常量：

```python
_PERSIST_SEMANTIC_CANDIDATE_K = 200
_DEDUP_SIM_THRESHOLD = 0.95
_CONTRADICTION_CANDIDATE_K = 5
_CONTRADICTION_SIM_THRESHOLD = 0.6
```

1. 调 `store.find_by_content(memory.content)`；命中则 `strengthen(existing.id, now)` 并返回旧记忆。
2. 如果 `memory.embedding is None` 且 `embed` 可用，调用 `embed(memory.content)` 补 embedding；失败只记录日志，语义去重/矛盾检测跳过。
3. 如果新记忆有 embedding，调用 `AnnIndex.build(await store.list_memories()).query(memory.embedding, candidate_k=_PERSIST_SEMANTIC_CANDIDATE_K)` 得到 bounded persist semantic candidates，并转成 `PersistSemanticHit`。
4. 如果最高候选 `cosine >= _DEDUP_SIM_THRESHOLD`，则 `strengthen(hit.memory.id, now)` 并返回旧记忆。
5. 未命中才 `store.add(memory)`，然后建边、矛盾检测、衰减/淘汰、发布 `memory_created`。

`_PERSIST_SEMANTIC_CANDIDATE_K` 是持久化语义候选上限，不受聊天召回 `direct_limit` / `association_limit` 影响。

### 矛盾检测

`_detect_contradiction(memory, candidates, correlation_id)` 只看 `candidates[:_CONTRADICTION_CANDIDATE_K]` 中 `cosine >= _CONTRADICTION_SIM_THRESHOLD` 的记忆。候选 prompt 使用 `summary + content 前 60 字`，新记忆含否定/转折锚点时追加「重点核对」提示。触发时调用统一 LLM client（`output_type="contradiction"`）并紧跟 `evaluator.evaluate(output)`。

### 建边算法

模块常量：

```python
_EDGE_SEMANTIC_CANDIDATE_K = 40
_EDGE_ENTITY_CANDIDATE_K = 40
_EDGE_KEYWORD_CANDIDATE_K = 40
_EDGE_TEMPORAL_CANDIDATE_K = 20
_EDGE_LLM_CANDIDATE_K = 5
_EDGE_PER_KIND_LIMIT = 4
_EDGE_TOTAL_LIMIT = 16
_ENTITY_THRESHOLD = 0.34
_KEYWORD_THRESHOLD = 0.25
_TEMPORAL_WINDOW_SECONDS = 86400.0
```

- **语义边**：复用 `persist_semantic_hits[:40]`；缺失时才对 `summary + "\n" + content` 生成 embedding 并用 ANN 取 40 个候选。`vector_score = (cosine + 1.0) / 2.0`，只保留 `>= 0.72`，写 `semantic`。
- **实体边**：`extract_entities(memory)` 从 summary/content/tag/aspect 提取书名号内容、英文专名、数字字母标识、2-12 字中文专名片段；候选优先来自 persist hits，缺失时按 `created_at DESC, id ASC` 补足到 40。`shared_entity_count / max(len(new_entities), len(old_entities), 1) >= 0.34` 写 `entity`。
- **关键词边**：`extract_keywords(summary + "\n" + content)` 后调 `store.search_keywords(..., limit=40)`；权重为 token Jaccard，`>= 0.25` 写 `keyword`。
- **时间边**：按 `abs(old.created_at - new.created_at) ASC, old.id ASC` 取 20 个候选，只保留 24 小时窗口内，权重 `1.0 - abs(delta) / 86400.0`，写 `temporal`。
- **LLM 关系边**：语义/实体/关键词/时间候选并集计算 `combined_edge_score = max(semantic_score, 0.9*entity_score, 0.75*keyword_score, 0.35*temporal_score)`，取 top 5 调 LLM。合法 relation kind 为 `same_topic` / `elaborates` / `contrasts` / `causes` / `updates_preference` / `user_profile_link` / `none`；`none`、未知 kind、未知 id 跳过，`weight` 夹到 `[0.0, 1.0]`，非法默认 `0.7`。

### 度数控制

同一 unordered pair 的不同 `kind` 分别计入 incident typed degree。剪枝先处理单 kind 超限，再处理总度超限；排序键为 `edge.weight * prune_kind_weight, created_at, from_id, to_id, kind`，低优先级先删。实现可以循环重算 touched nodes，直到每个 touched node 都满足每 kind ≤4、总 typed degree ≤16。

### 生命周期与导出

- `decay_freshness(freshness, created_at, now, rate)`：按天线性衰减，下限 0，`now < created_at` 不变。
- `_decay_and_evict(now)`：批量回写衰减后的 freshness；短期数量超过 `short_term_capacity` 时删除 freshness 最低、平局最旧的短期记忆。
- `export("json")` 输出 JSON 数组；`export("md")` 输出 Markdown；非法格式抛 `ValueError`。
- `Memory.sources` 是检索瞬态字段，`_memory_to_dict` 导出不包含它。

## 测试要点

- [ ] 纯函数：`decay_freshness`、`_parse_scene`、`_build_scene_prompt`、`_has_negation`、`_content_preview`、`_build_contradiction_prompt`、`_parse_contradiction`、`extract_entities`、`_parse_relation_edges`、`_join_list`、`_activity_memory_fields`、导出 helper。
- [ ] `_persist_memory`：精确去重优先；bounded semantic candidate 命中去重时 strengthen 旧记忆并返回旧 id；阈值以下正常新增；`embed=None` 跳过语义去重。
- [ ] 矛盾检测：只从 bounded persist semantic candidates 的前 5 个且 cosine `>=0.6` 的候选进入 prompt；无候选/低阈值 0 调用；LLM 返回 null/未知 id/非法 JSON 不发布 reflection 或不崩主流程。
- [ ] 建边：语义、实体、关键词、时间边分别可构造；LLM relation edge 可构造，`none`/未知值跳过；LLM 失败不阻塞持久化。
- [ ] 度数控制：每 kind 最多 4 条、总 typed degree 最多 16；新节点和触达旧节点都会剪枝；删除使用完整 typed key。
- [ ] 活动/画像/知识/读书/未答入口：字段映射正确，复用 `_persist_memory`，确定性入口不调用 scene-memory LLM。
- [ ] `search` / `list_memories` / `count_new` 委托正确；`record_recall` 升级只发布一次；`export` 格式正确。

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] 17-expression 慢通道调 `MemoryFacade.search(message)` 后，对返回的全部记忆逐条 `record_recall`；18-api 组合根构建 `embed` → `retrieval` → `facade` 并注入同一 embedding 函数。
