# 三层检索 + 联想图

> 范围：`memory/retrieval.py`（keyword→vector→association 三层 + 去重合并）、`memory/graph.py`（networkx 联想图）。
> 纯基础设施 spec：只做「三层检索 + 联想图」，不含 Facade（09-memory-facade）、不含记忆创建/embedding 写入（09）。embedding 已持久化在 `memory.embedding` 列（01-types / 04-db / 07 的连锁改动），本 spec 只**读**它。
> spec 只定义契约（签名 + 三层语义 + 去重合并规则）；实现以 `nyx/memory/retrieval.py` / `nyx/memory/graph.py` 源文件为准。

## 元信息

- **前置依赖**：01-types（`Memory` / `MemoryEdge` / `SearchMode`）、02-config（`EmbeddingConfig.model` 供 `build_embed`）、07-memory-store（`MemoryStore`：`search_keyword` / `list_memories` / `list_edges`）

## 用户故事

> 作为 Nyx 系统的开发者，我想要记忆检索的三层管线——关键词（SQLite LIKE）、向量（embedding 余弦）、联想（networkx 扩散）顺序执行并去重合并，以便 `MemoryFacade.search(query)` 对外只返回一份去重的 `list[Memory]`，每层可单独 mock/测试。

## 验收标准

- [ ] `graph.py` 含 `MemoryGraph`（`neighbors(seeds, depth)`）（实现见 `nyx/memory/graph.py`）
- [ ] `retrieval.py` 含 `cosine` / `rank_by_cosine` / `build_embed` / `EmbedFn` / `MemoryRetrieval`（实现见 `nyx/memory/retrieval.py`）
- [ ] `cosine` 纯函数：正交=0、相同=1、相反=-1、零向量=0、维度不一致=0
- [ ] `search()` 空/空白查询（`not query.strip()`）短路返回 `[]`；顺序执行 keyword → vector → association，按此序去重合并（`seen` set，后到重复丢弃），`limit` 截断；每条命中 `sources` 按层标注（keyword+vector 重叠 = `[KEYWORD, VECTOR]`）
- [ ] vector 层：`embed=None` → 跳过返回 `[]`；query 只 embed 一次；memory embedding 从 DB 读（不重算）；`embedding=None` 的记忆跳过；`s > 0` 过滤 + `_VECTOR_TOP_K` 截断
- [ ] association 层：图从 `store.list_edges()` 构建，`neighbors` 无权重扩散、排除 seeds 本身、跳过不在图中的 seed（`nx.Graph.neighbors` 对不存在节点抛 `NetworkXError`）
- [ ] `pyright` strict 零报错

## 技术方案

- **新文件**：`nyx/memory/graph.py`、`nyx/memory/retrieval.py`（无 Facade、无 API、无数据变更）
- **库**：`networkx`（新增，联想图；依赖 pin 同 03-llm 约定）、`sentence-transformers`（新增，`build_embed` 惰性 import；依赖 pin）；其余标准库（`math` / `asyncio` / `collections.abc`）
- **公开面**：`from nyx.memory.retrieval import MemoryRetrieval, cosine, rank_by_cosine, build_embed, EmbedFn`；`from nyx.memory.graph import MemoryGraph`（不加 `__all__`）
- **三层顺序 = design §6.3**：keyword → vector → association；合并按此序（keyword 命中排最前），`seen` set 去重，`limit` 截断（默认 20）。对外返回一份去重 `list[Memory]`，每条 `sources` 标注命中来源层
- **`SearchMode` 来源标记（V2，翻转 MVP「不带来源」）**：`search()` 去重合并时按层累计 `sources_by_id`，给每条命中赋 `sources: list[SearchMode]`——keyword/vector 可重叠（`[KEYWORD, VECTOR]`），association 与 seeds 互斥（只 `[ASSOCIATION]`）。`sources` 是 `Memory` 瞬态字段（01-types）：不持久化、不进 prompt、不进导出；三层仍是私有方法（`_vector_search` 等）可单独测试
- **vector 层（读持久化 embedding）**：`MemoryRetrieval(store, embed)` 构造注入 `embed`，`None` = 向量层禁用（返回 `[]`）。query 只 `embed` 一次；memory embedding 直接读 `m.embedding`（07 持久化的列），**不重算**——这正是「持久化 embedding 列」的价值。`cosine` 纯函数；MVP 常量 `_VECTOR_TOP_K=5` + `s > 0.0` 过滤（非 config，不引入检索阈值配置项）
- **`rank_by_cosine` 共享纯函数（跨模块复用）**：`cosine` 之外，把「候选打分 + `s > 0` 过滤 + 降序」抽成纯函数 `rank_by_cosine(query_vec, candidates) -> list[tuple[float, Memory]]`（跳过 `embedding is None`）。`_vector_search` 用它做 vector 层打分；09-facade 的 `_similar` 也复用它（跨模块去重，见 09）。同一套余弦排序逻辑只此一处，改排序/过滤规则翻这里
- **`build_embed` 惰性 import**：sentence-transformers 是重依赖（下载模型），`build_embed(model_name)` 内 `from sentence_transformers import ...`，未启用向量层（测试/无模型环境）不加载；`model.encode` 同步 → `asyncio.to_thread` 包成 async；返回 `list[float]`
- **association 层（每次 search 现建图）**：`MemoryGraph(edges)` 从 `store.list_edges()` 构建——O(E) 小（≤ 几百条边），且图永远与 DB 一致（新建记忆后下次 search 自动包含）。`neighbors` 无权重扩散 `depth=1`（`weight` 存图待展示/加权扩散，当前不用）；seed 来自 keyword + vector 命中，扩散结果映射回 `by_id`（`list_memories()` 一次性建的 id→Memory 表，避免 N+1 查询）。**`neighbors` 先过滤不在图中的 seed**：图只从 edges 建节点，孤立记忆（无边的 keyword/vector 命中，属常态）不在图里，而 `nx.Graph.neighbors` 对不存在节点抛 `NetworkXError`——`frontier = [s for s in seeds if self._g.has_node(s)]` 过滤后孤立 seed 自然产出 `[]`，`search()` 不崩
- **embedding 写归 09**：本 spec 只读 `memory.embedding`；记忆创建时算 embedding + 存列是 09-facade 的活（用同一个 `build_embed`）
- **空查询守卫在检索层（已知限制）**：`search()` 入口 `not query.strip()` 短路，因为 store 的 `search_keyword("")` / `search_keyword(" ")` 会构造 `LIKE '%%'` / `LIKE '% %'` 全表命中。守卫只在本层——绕过 `search()` 直接调 `search_keyword` 仍会全表返回，属 07 层语义，本 spec 不强求改

## 测试要点

- [ ] 单元测试 `tests/test_memory/`：
  - [ ] **graph**（`test_graph.py`，纯 `MemoryGraph`，不触 DB）：
    - [ ] 空 edges → `neighbors([])` = `[]`；`neighbors(["x"])`（节点不存在）= `[]`
    - [ ] 单边 a-b：`neighbors(["a"])` = `["b"]`；`neighbors(["a", "b"])` = `[]`（都是 seed）
    - [ ] 链 a-b-c：`depth=1` → `["b"]`；`depth=2` → `["b", "c"]`
    - [ ] 菱形 a-b / a-c / b-d / c-d：`neighbors(["a"], depth=2)` = `["b", "c", "d"]`（d 去重只出现一次）
    - [ ] `weight` 不影响扩散（无权重，只可达性）
  - [ ] **retrieval**（`test_retrieval.py`）：
    - [ ] `cosine` 纯函数：正交 `[1,0]`/`[0,1]` = 0、相同 = 1、相反 `[1,0]`/`[-1,0]` = -1、零向量 `[0,0]` = 0、维度不一致 = 0
    - [ ] `rank_by_cosine` 纯函数：`embedding=None` 跳过、`s <= 0` 过滤、按 `s` 降序（cos=1 与 cos≈0.707 的记忆 → 顺序 `[高, 低]`）
    - [ ] `_vector_search`（fake embed + 含 embedding 的 `Memory` 列表）：`_VECTOR_TOP_K` 截断、`embedding=None` 的记忆跳过、`s <= 0` 过滤、`embed=None` 时返回 `[]`
    - [ ] `search` 编排（真 `MemoryStore`（`connect(":memory:")`）+ fake embed）：造 A（content 含 query、embedding `[1,0]`）、B（embedding `[0,1]`）、C（无 embedding）+ 边 A-B → `search(query)` 按 keyword 命中 A、vector 命中 A、association 扩散到 B，合并去重 = `[A, B]`（顺序 keyword 先）；`limit=1` → `[A]`
    - [ ] `search` 去重：keyword 与 vector 命中同一记忆 → 只出现一次
    - [ ] `search` 来源标记：A（keyword+vector 重叠）→ `[KEYWORD, VECTOR]`；B（association 扩散）→ `[ASSOCIATION]`；keyword-only（embed=None）→ `[KEYWORD]`；vector-only（content 不含 query、embedding 命中）→ `[VECTOR]`
    - [ ] `search` 全空：无 keyword 命中、embed=None、无边 → `[]`
    - [ ] `search` 空/空白查询：`search("")` / `search(" ")` / `search("   ")` → `[]`（`query.strip()` 短路，不触 store 查询）
    - [ ] `search` 无边命中不崩（回归）：keyword 命中一条**无边**记忆（图里无此节点）→ 不抛 `NetworkXError`，返回该命中本身（association 空，`neighbors` 过滤后产出 `[]`）
- [ ] 集成测试：无（retrieval 是内部类，无 Facade 管道；与 09 的编排归 09）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] `MemoryFacade.search(query)`（09）内部跑本三层并去重合并，对外一份 `list[Memory]`；`build_embed` 由组合根（18-api）按 `config.embedding.model` 决定是否启用，测试全程 mock embed
