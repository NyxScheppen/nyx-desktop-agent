# 融合召回 + 联想图

> 范围：`memory/ann.py`（确定性 ANN 候选索引）、`memory/retrieval.py`（整句向量候选 + keyword LIKE 候选融合排序 + association 追加）、`memory/graph.py`（typed edge 联想扩散 + 聚类）。
> 纯基础设施 spec：只做召回编排和图算法，不含 Facade（09-memory-facade）、不含记忆创建/embedding 写入（09）。embedding 已持久化在 `memory.embedding` 列（01-types / 04-db / 07），本 spec 只**读**它。
> spec 只定义契约（签名 + 排序公式 + 图扩散语义）；实现以 `nyx/memory/ann.py` / `nyx/memory/retrieval.py` / `nyx/memory/graph.py` 源文件为准。

## 元信息

- **前置依赖**：01-types（`Memory` / `MemoryEdge` / `SearchMode` / `MemoryEdgeKind`）、02-config（`EmbeddingConfig.model` 供 `build_embed`）、07-memory-store（`MemoryStore`：`search_keywords` / `list_memories` / `list_edges`）

## 用户故事

> 作为 Nyx 系统的开发者，我想要慢通道召回先用整句 embedding ANN 缩小语义候选，再用关键词 LIKE 补强精确命中，融合排序后只从直接命中的 top N 扩散 typed association，以便 `MemoryFacade.search(query)` 返回一份稳定、有来源标注、数量受控的 `list[Memory]`。

## 验收标准

- [ ] `ann.py` 含 `AnnCandidate` / `AnnIndex.build(memories, planes=16, tables=4, seed=0)` / `AnnIndex.query(vector, candidate_k)` / `hash_embedding` / `ann_fingerprint`。
- [ ] `retrieval.py` 含 `cosine` / `rank_by_cosine` / `extract_keywords` / `build_embed` / `EmbedFn` / `RankedMemory` / `MemoryRetrieval`（实现见 `nyx/memory/retrieval.py`）。
- [ ] `graph.py` 含 `AssociationHit` / `MemoryGraph(edges, memory_ids=None)` / `associate(seeds, depth=2, limit=10, exclude=None)` / `clusters()`；`neighbors()` 仅作旧调用兼容 wrapper，不是召回主契约。
- [ ] `MemoryRetrieval.search(query, direct_limit=20, association_limit=10)`：空白 query 或 `direct_limit <= 0` 返回 `[]`；`association_limit <= 0` 时只返回 direct。
- [ ] 直接召回流程为：整句 embedding ANN 候选池 + 分词 keyword LIKE 候选池 -> 融合评分 -> 取 direct top N。`direct_limit` 只限制直接召回数量。
- [ ] association 只从 direct top N 出发，沿 typed edges 扩散 2 跳，按关联分数排序、去重、限量追加。`association_limit` 只限制联想追加数量，最终数量 `<= direct_limit + association_limit`。
- [ ] 返回顺序固定为 direct ranked results 在前，association ranked results 在后。
- [ ] `Memory.sources` 按实际来源标注：direct ANN 候选含 `VECTOR`，keyword 候选含 `KEYWORD`，两者重叠顺序为 `[VECTOR, KEYWORD]`；association 追加结果只标 `[ASSOCIATION]`。
- [ ] ANN 替代检索中的无界全表余弦扫描；fallback 补足也必须受 `candidate_k` 限制。
- [ ] CJK keyword extraction 按确认 spec 字面执行：长度 2-8 的连续 CJK 片段直接保留，长 CJK 片段只做 2 字/3 字滑窗；不做隐藏边界字符剥离，也不按停用词切开长 CJK 片段。
- [ ] `pyright` strict 零报错。

## 技术方案

- **新文件**：`nyx/memory/ann.py`；**修改文件**：`nyx/memory/retrieval.py`、`nyx/memory/graph.py`（无 Facade、无 API、无数据变更）。
- **库**：`networkx`（聚类 fallback 与图工具）、`sentence-transformers`（`build_embed` 惰性 import）；其余标准库。
- **公开面**：`from nyx.memory.retrieval import MemoryRetrieval, cosine, rank_by_cosine, extract_keywords, build_embed, EmbedFn`；`from nyx.memory.graph import MemoryGraph, AssociationHit`；`from nyx.memory.ann import AnnIndex, AnnCandidate, ann_fingerprint, hash_embedding`（不加 `__all__`）。

### 召回流程

模块常量：

```python
_RECALL_VECTOR_CANDIDATE_K = 80
_RECALL_KEYWORD_CANDIDATE_K = 80
```

`search(query, direct_limit=20, association_limit=10)`：

1. `query_vec = await embed(query)`；`embed is None` 或 embedding 失败时 `query_vec=None`，跳过 ANN，只走 keyword。
2. `AnnIndex.query(query_vec, candidate_k=_RECALL_VECTOR_CANDIDATE_K)` 返回 bounded ANN 候选 id 和 cosine；只对候选集计算精确 vector score。
3. `extract_keywords(query)` 后调 `store.search_keywords(tokens, limit=_RECALL_KEYWORD_CANDIDATE_K)`，得到 bounded keyword 候选。
4. 合并候选，计算 `RankedMemory.score`，按 tie-break 排序并取 `direct_limit`。
5. 以 direct 分数为 seeds 调 `MemoryGraph(edges).associate(seeds, depth=2, limit=association_limit, exclude=set(seeds))`；命中的 id 必须存在于本次 `list_memories()` 快照中才追加。

融合公式：

```text
score = 0.65 * vector_score
      + 0.25 * keyword_score
      + 0.07 * freshness
      + 0.03 * type_boost
```

- `vector_score = (cosine + 1.0) / 2.0`，夹到 `[0, 1]`；非向量候选为 0。
- `keyword_score = min(1.0, 0.7 * coverage + 0.3 * field_score)`。
- `coverage = matched_unique_token_count / query_token_count`。
- `field_score = min(1.0, 0.7 * summary_hit_ratio + 0.3 * content_hit_ratio)`。
- `summary_hit_ratio = summary_unique_token_count / query_token_count`；`content_hit_ratio = content_unique_token_count / query_token_count`。
- `type_boost`：`LONG_TERM=1.0`、`SHORT_TERM=0.5`。
- 排序 tie-break：`score DESC, vector_score DESC, keyword_score DESC, freshness DESC, created_at DESC, id ASC`。

### 关键词提取

`extract_keywords(text: str) -> list[str]` 是纯函数：

- 英文/数字：按 `[A-Za-z0-9_]+` 提取并 lower。
- 中文：按连续 CJK 片段提取；长度 2-8 的片段直接保留，长片段用 2 字和 3 字滑窗切分。
- 过滤长度 1 的 token。
- 只过滤 exact 停用词 token：`这个`、`那个`、`什么`、`怎么`、`为什么`、`然后`、`就是`、`一下`、`可以`、`还是`、`一个`、`我们`、`你们`。
- 去重并保持首次出现顺序。

Task 5 controller ruling：CJK extraction follows the confirmed spec literally. 长 CJK 片段内部不因停用词、语气词或边界字符被预拆；因此滑窗可以保留跨这些位置的 2/3 字 token，只有最终 token 与停用词完全相等时才过滤。

### ANN 缓存

`AnnIndex.build` 只索引 `embedding is not None` 且维度等于首个有效 embedding 维度的记忆。查询维度不一致、空索引或 `candidate_k <= 0` 返回 `[]`。

查询先取同桶候选，再枚举 Hamming radius 1、radius 2；每层按 `memory_id ASC` 稳定加入，达到 `candidate_k` 停止。若仍不足，从 indexed memories 按 `created_at DESC, id ASC` 补足；总数仍不超过 `candidate_k`。最终只对 bounded candidate set 计算精确 cosine，并按 `cosine DESC, created_at DESC, id ASC` 排序。

`MemoryRetrieval` 缓存 ANN index，fingerprint 变化即重建：

```python
fingerprint = tuple(
    (m.id, m.created_at, hash_embedding(m.embedding))
    for m in memories
    if m.embedding is not None
)
```

`hash_embedding` 对 float 用 `round(x, 8)` 后 tuple hash；`recall_count` / `freshness` 变化不触发 ANN 重建。

### 联想与聚类

`MemoryGraph` 构造时按 canonical unordered pair + kind 去重；同 pair 同 kind 方向相反时保留更高 `weight`，权重相同保留更新 `created_at`。association 使用 typed adjacency list，不用会折叠并行边的 `nx.Graph` 做扩散。

Association 分数：

```text
assoc_score = seed_score * edge_weight * depth_decay * kind_weight
```

- 第 1 跳 `depth_decay=1.0`，第 2 跳 `depth_decay=0.55`。
- `kind_weight`：`semantic=1.0`、`entity=0.9`、`keyword=0.75`、`temporal=0.35`、`same_topic=1.05`、`elaborates=1.1`、`contrasts=1.0`、`causes=1.0`、`updates_preference=1.15`、`user_profile_link=1.1`。
- 多路径到同一 `memory_id` 取最高 score；分数相同取更浅 depth；仍相同取字典序更小的 `via`。
- 返回按 `score DESC, depth ASC, memory_id ASC` 排序。

`clusters() -> dict[str, int]` 在聚类前按 `(from_id, to_id)` 显式折叠 typed edges，权重取 `max(edge.weight * cluster_kind_weight[kind])`。调用方需要完整孤立节点时必须传 `memory_ids={m.id for m in memories}`。优先 Louvain（`seed=0`），不支持时 fallback 到 greedy modularity；不落库、不发布事件、不影响聊天召回排序。

## 测试要点

- [ ] ANN：空/非法查询返回空；跳过 None embedding 与维度不一致记忆；查询候选数不超过 `candidate_k` 且排序稳定；fingerprint 覆盖新增、删除、embedding 更新。
- [ ] `extract_keywords`：英文 lower、停用词 exact 过滤、2-8 字 CJK 片段直接保留、长 CJK 片段只做 2/3 字滑窗且不被停用词预拆。
- [ ] keyword LIKE：`search_keywords` 字段命中集合正确、空 token/`limit <= 0` 返回空、LIKE 特殊字符按字面匹配、结果按命中排序并截断。
- [ ] 融合排序：vector/keyword/freshness/type boost 按公式排序；`direct_limit` 只限制 direct；`association_limit` 只限制追加。
- [ ] 召回顺序：先 direct，再 association；association 不重复 direct seed；最终数量 `<= direct_limit + association_limit`；sources 正确。
- [ ] `MemoryGraph.associate(depth=2)`：二跳可达，按边权/跳数/kind 权重排序，多路径取最高分，`via` / `kinds` 记录最佳路径。
- [ ] 聚类：`MemoryGraph(edges, memory_ids=...)` 返回稳定 `memory_id -> cluster_id`，孤立节点保留，temporal 边低权重不主导聚类。

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] `MemoryFacade.search(query)`（09）保持对表达层的 `search(query: str) -> list[Memory]` 外观，内部使用本 spec 的默认 `direct_limit=20` / `association_limit=10`。
