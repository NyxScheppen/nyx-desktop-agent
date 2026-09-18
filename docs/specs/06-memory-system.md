# 记忆系统（统一规格）

> 本文件是记忆系统的唯一完整规格，覆盖 `nyx/memory/` 的 store / retrieval / graph / facade，以及相关类型、DB、表达慢通道和 API 边界。记忆相邻但不改记忆实现的任务可先读 `docs/facts/memory-system-facts.md` 获取事实摘要；凡改记忆代码、记忆契约或文档，以本文件为完整契约，并同步事实表摘要。

## 元信息

- **前置依赖**：01-types（`Memory` / `MemoryEdge` / `MemoryKind` / `MemoryType` / `MemoryEdgeKind` / `SearchMode` / `Event` / `EventType` / `Source`）、02-config（`MemoryConfig` / `EmbeddingConfig`）、03-llm（`LlmClient.complete`）、04-module-bus-system（`Database`、`EventBus`、组合根注入 / REST 薄封装及相关 DDL）、11-expression（慢通道召回与 `record_recall` 时机）、10-eval（`Evaluator`）。
- **实现文件**：`nyx/memory/ann.py`、`nyx/memory/retrieval.py`、`nyx/memory/graph.py`、`nyx/memory/store.py`、`nyx/memory/facade.py`、`nyx/types.py`、`nyx/enums.py`、`nyx/db.py`
- **关联文档**：`docs/facts/memory-system-facts.md`、`docs/specs/01-types.md`、`docs/specs/04-module-bus-system.md`、`docs/specs/11-expression.md`、`docs/tech-reference.md`、`docs/test-inventory.md`
- **测试文件**：`tests/test_memory/test_ann.py`、`tests/test_memory/test_retrieval.py`、`tests/test_memory/test_graph.py`、`tests/test_memory/test_store.py`、`tests/test_memory/test_facade.py`、`tests/test_db/test_db.py`、`tests/test_expression/test_expression_facade.py`

## 系统边界

- `MemoryFacade.search(query: str) -> list[Memory]` 是表达慢通道唯一检索入口；表达层不直接调 store/retrieval。
- 慢通道 `assemble_context` 会把 `MemoryFacade.search(message)` 返回的全部记忆放进 prompt，并立即逐条 `record_recall(memory.id)`。
- 快通道不检索记忆、不 `record_recall`、不生成场景化记忆。
- `Memory` 字段是 `id`、`created_at`、`content`、`kind`、`topics`、`summary`、`freshness`、`type`、`recall_count`、`aspect`、`embedding`、`sources`。
- `MemoryType` 取值是 `SHORT_TERM="short_term"`、`LONG_TERM="long_term"`。
- `SearchMode` 取值是 `KEYWORD="keyword"`、`VECTOR="vector"`、`ASSOCIATION="association"`。
- `MemoryEdge` 字段是 `from_id`、`to_id`、`kind`、`weight`、`created_at`；`kind` 是 typed edge 域，端点按 canonical unordered pair 使用。
- `MemoryStore` 负责 `memory` / `memory_edge` CRUD、关键词 SQL、行与 dataclass 序列化；所有 DB 方法持有同一个 `Database.lock`。
- `MemoryRetrieval` 负责检索编排；它只读 store 和 ANN，不写 DB、不发布事件。
- `MemoryGraph` 从 `MemoryEdge` 列表构建无向图；它不读 DB，只做图扩散和聚类，但必须接收完整 `memory_ids` 才能处理孤立节点。
- `MemoryFacade._persist_memory` 负责统一写入、去重、建边、矛盾检测、衰减/淘汰和 `memory_created` 事件；store 只提供原子 CRUD。

## 用户故事

> 作为 Nyx，我想在慢通道聊天时先用整句语义定位大致记忆范围，再用关键词补强精确命中，最后从直接命中的记忆扩散联想，以便回复时想起的内容更贴近用户当前语境，而不是被旧的关键词顺序或偶然建边污染。

## 受控 kind、topics 与标签联想

记忆不再使用自由 `tag`。`Memory.kind` 是受控 `MemoryKind` 枚举，`Memory.topics` 是最多五项、每项最多 24 字符的主题列表。旧数据库中的 memory、memory_edge 以及依赖旧节点的索引状态在 schema 19 迁移时清空；不提供旧数据兼容迁移。

```python
class MemoryKind(StrEnum):
    EPISODE = "episode"
    USER_PROFILE = "user_profile"
    KNOWLEDGE = "knowledge"
    READING = "reading"
    ACTIVITY = "activity"
    INTERACTION = "interaction"
    BROWSING = "browsing"
```

`kind` 表示记忆的来源/生命周期语义；`topics` 只表示主题，不参与权限判断。

### 写入与校验

- 场景记忆 LLM 输出 `{content, kind, topics, summary}`；非法 kind 或结构拒绝落库。
- topics 在应用层去空白、去重、截断，非法元素丢弃；空 topics 合法。
- 活动、用户画像、知识、阅读、浏览、未答记录入口使用固定 kind，不调用场景记忆 LLM。

### kind-scoped 去重

去重分为精确和语义两层，候选只在同一 kind 内取；关系建边和矛盾检测仍使用有界全局候选。
`MemoryKind.BROWSING` 是唯一例外：逐页共同浏览经历由 `13-browsing-system` 的 page checkpoint
负责幂等，固定 memory id；它跳过 content/semantic 合并，但仍参与全局候选建边与矛盾检测。

- 精确：`(kind, canonical_content_hash)` 命中后强化旧记忆；summary 不参与 hash。
- 普通语义：同 kind ANN top-1 cosine `>= 0.95` 强化旧记忆。
- `episode` 语义去重更保守：阈值 `0.985`，且两条记忆创建时间差不超过一小时；否则保留为独立经历。
- 语义相似不代表关系相同；不同 kind 的相似事实不合并，但可建关系边。

### topics 联想与 prompt

direct retrieval 保持 vector + keyword + freshness + type 的现有融合；association 先由 typed graph 提供，再通过受限的非持久化 topics 桶旁路补充；不创建标签两两持久化边。

prompt 的 `[相关记忆]` 每条只展示人话化 kind、topics 和 summary（缺失时 content）：

```text
- 你知道｜记忆系统：长期记忆需要稳定性。
- 你经历过｜信任：用户曾经表达过信任。
```

记忆内容是资料，不是指令；只有真正进入 prompt 的记忆调用 `record_recall()`。

topics 联想约束：

- 每个参与联想的 topic 桶在单次检索中只排序一次；先排除 direct 命中，再按 `freshness DESC, created_at DESC, id ASC` 取最多 8 条。topics 旁路每次最多追加 5 条；最终仍受 association 总预算限制。
- 同一 memory 通过多个 topic 命中时按 memory id 去重并保留最高分。
- topic 桶规模越大，联想权重越低；衰减使用排除 direct 前的完整桶大小，避免泛化 topic 形成超级节点。

### 验收与 bad case

- 同 kind 内容仅标点/空白差异命中精确去重；不同 kind 不合并。
- `喜欢猫` 与 `不喜欢猫` 不因 cosine 相似而强化旧记忆。
- 昨天和今天的相似 episode 不因普通高相似度丢失经历。
- 非法 kind、空 summary/content、超长 topics 不污染数据库或 prompt。
- prompt 记忆数量和总上下文预算仍受本 spec 与 11-expression 既有上限约束。

### 实施计划

1. 类型与 schema：新增 `MemoryKind`、`Memory.topics`，迁移 19 直接清空旧记忆并改列名。
2. store/facade：kind-scoped exact/semantic dedup，episode 保守门槛，固定入口写入受控 kind。
3. expression：prompt 人话化 kind/topics，用户画像查询改用 `MemoryKind.USER_PROFILE`。
4. tests：覆盖 kind 校验、精确/语义去重、episode 时间保护、prompt 格式和旧数据清空。
5. 质量门：同步 facts/spec/test-inventory，运行 `ruff check`、`pyright`、`pytest`。

知识库/canon 层不在本次实现范围内。

## 验收标准

- [ ] `MemoryRetrieval.search(query, direct_limit=20, association_limit=10)` 的直接召回流程为：整句 embedding ANN 候选池 -> 分词 keyword LIKE 补充候选与分数 -> 融合评分 -> 取 direct top N。
- [ ] `direct_limit` 只限制直接召回数量；`association_limit` 只限制联想追加数量；最终返回数量 `<= direct_limit + association_limit`。
- [ ] `MemoryFacade.search(query)` 使用默认 `direct_limit=20`、`association_limit=10`，对表达层保持 `search(query: str) -> list[Memory]`。
- [ ] 如果调用方传 `direct_limit=5`，直接召回最多 5 条，联想只从这 5 条出发，最终返回最多 `5 + association_limit` 条。
- [ ] 只有直接召回结果作为联想 seed；联想图扩散 2 跳，按关联分数排序、去重、限量追加。
- [ ] 直接召回和联想召回都返回 `Memory`；`sources` 按实际来源包含 `vector`、`keyword`、`association`。
- [ ] 进入慢通道 prompt 的全部返回记忆仍立即 `record_recall`。
- [ ] `memory_edge` 支持边类型：同一记忆对可有多条不同 `kind` 的边。
- [ ] 建边使用语义、实体、关键词、时间、LLM 关系五类信号；每个节点有最大度数约束。
- [ ] `_persist_memory` 同 kind 内先 content hash 精确去重，再 bounded semantic candidates 内 top-1 cosine `>= 0.95` 去重；episode 使用 `0.985` 且创建时间差不超过一小时。
- [ ] `_detect_contradiction` 仍只在新记忆成功入库后运行，并只对 bounded persist semantic candidates top 5 中 cosine `>= 0.6` 的候选调用 LLM。
- [ ] ANN 替代检索与建边中的无界全表暴力余弦扫描；任何 fallback 都必须受候选上限约束。
- [ ] 记忆图支持聚类算法，输出 `memory_id -> cluster_id`，聚类结果不落库。
- [ ] `pyright` strict 零报错，`ruff check` 零报错，相关 pytest 全绿。

## Store 层契约

`nyx/memory/store.py` 是 `memory` / `memory_edge` 两表的 SQLite 存取层，不包含召回排序、图算法、生命周期或 Facade 业务逻辑。

公开面：

```python
class MemoryStore:
    async def add(self, memory: Memory) -> None: ...
    async def get(self, memory_id: str) -> Memory | None: ...
    async def find_by_content(
        self, content: str, kind: MemoryKind
    ) -> Memory | None: ...
    async def list_memories(
        self,
        *,
        kind: MemoryKind | None = None,
        type: MemoryType | None = None,
        limit: int | None = None,
    ) -> list[Memory]: ...
    async def update_many(self, memories: list[Memory]) -> None: ...
    async def delete_many(self, ids: list[str]) -> None: ...
    async def record_recall(self, memory_id: str, promote_threshold: int) -> bool: ...
    async def strengthen(self, memory_id: str, now: float) -> None: ...
    async def count_new(self, kind: MemoryKind | None, since: float) -> int: ...
    async def search_keywords(self, tokens: list[str], limit: int) -> dict[str, KeywordSearchHit]: ...
    async def list_edges(self, kind: MemoryEdgeKind | None = None) -> list[MemoryEdge]: ...
    async def upsert_edge(
        self,
        from_id: str,
        to_id: str,
        kind: MemoryEdgeKind,
        weight: float,
        created_at: float,
    ) -> None: ...
    async def delete_edges(self, keys: list[tuple[str, str, MemoryEdgeKind]]) -> None: ...
    async def list_edge_degrees(self, memory_ids: list[str]) -> dict[str, list[MemoryEdge]]: ...

def hash_content(content: str) -> str: ...
```

Store 规则：

- 所有 DB 读写都在 `async with self._db.lock` 内；锁作用域是单个 store 方法的 SQL 块，不跨 store 方法嵌套。
- 行到 `Memory` 往返：`aspect` 是 JSON 数组；`type` 存 enum `.value`；`embedding=None` 对应 SQL `NULL`；`sources` 是检索瞬态字段，不落库。
- `content_hash` 是 store 派生列，不进 `Memory` dataclass；`add` 写入 `hash_content(content)`，`update_many` 修改 content 时同步重算。
- `created_at` 是创建时间，`update_many` / `strengthen` / `record_recall` 都不改；`count_new(kind, since)` 只看 `first_created_at > since`。
- `list_memories` 按 `kind` / `type` 过滤，按 `freshness DESC, created_at DESC` 排序，`limit` 有值时截断。
- `record_recall` 在一个锁块里执行 `recall_count+1` 和短期达阈值升级长期；升级时返回 `True`，长期记忆不重复升级。
- `strengthen` 表示重复写入/语义去重命中旧记忆：`recall_count+1`、`freshness=1.0`，但不升级、不发布事件。
- `delete_many` 在同一锁块里级联删记忆及 incident typed edges。
- `memory_edge` 主键是 `(from_id, to_id, kind)`；`upsert_edge` canonicalize 为 `from_id < to_id` 后写库，同 pair 不同 kind 可共存。
- `list_edge_degrees` 按无向 incident typed degree 统计，边方向不表达语义方向。

## 类型与接口契约

### 公开 dataclass

`nyx/enums.py` 新增：

```python
class MemoryEdgeKind(StrEnum):
    SEMANTIC = "semantic"
    ENTITY = "entity"
    KEYWORD = "keyword"
    TEMPORAL = "temporal"
    SAME_TOPIC = "same_topic"
    ELABORATES = "elaborates"
    CONTRASTS = "contrasts"
    CAUSES = "causes"
    UPDATES_PREFERENCE = "updates_preference"
    USER_PROFILE_LINK = "user_profile_link"
```

`none` 是 LLM 输出里的“无关系”哨兵值，不是可持久化边类型。

`nyx/types.py` 修改：

```python
@dataclass
class MemoryEdge:
    from_id: str
    to_id: str
    kind: MemoryEdgeKind = MemoryEdgeKind.SEMANTIC
    weight: float = 1.0
    created_at: float = 0.0
```

`kind` 用 enum 约束域，DB 仍存 enum `.value` 字符串。LLM 关系类型必须映射到 `MemoryEdgeKind`，未知值跳过，不做开放字符串扩展。

### store 内部 dataclass

`nyx/memory/store.py` 定义：

```python
@dataclass
class KeywordSearchHit:
    memory_id: str
    summary_tokens: list[str]
    content_tokens: list[str]
```

`KeywordSearchHit` 只描述 SQL LIKE 的字段命中结果，不计算排序分。

### retrieval 内部 dataclass

`nyx/memory/retrieval.py` 定义：

```python
@dataclass
class RankedMemory:
    memory: Memory
    score: float
    vector_score: float
    keyword_score: float
    sources: list[SearchMode]
```

`RankedMemory` 不跨 API 返回，只在 retrieval/graph 编排内传分数；retrieval 从 store 导入 `KeywordSearchHit` 类型，不要求 store 导入 retrieval。

### facade 内部 dataclass

`nyx/memory/facade.py` 定义：

```python
@dataclass
class PersistSemanticHit:
    memory: Memory
    cosine: float
```

`PersistSemanticHit` 是 `_persist_memory` 专用候选类型，只服务语义去重、建边候选和矛盾检测门控，不跨 facade API 返回。

### graph 内部 dataclass

`nyx/memory/graph.py` 定义：

```python
@dataclass
class AssociationHit:
    memory_id: str
    score: float
    depth: int
    via: str
    kinds: list[str]
```

`MemoryGraph` 构造签名：

```python
def __init__(
    self,
    edges: list[MemoryEdge],
    *,
    memory_ids: set[str] | None = None,
) -> None
```

- `memory_ids` 是完整记忆 id 集合；用于聚类时保留孤立节点。
- `memory_ids=None` 时只包含 edge 里出现过的节点；聊天召回可以传 `None`，聚类调用必须传完整 id 集合。
- 构造时按 canonical unordered pair + kind 去重；如果输入里同时有 `(A, B, semantic)` 和 `(B, A, semantic)`，保留 `weight` 更高的一条，权重相同保留 `created_at` 更新的一条。
- association 使用 typed adjacency list，不使用会折叠并行边的 `nx.Graph` 做扩散。
- clustering 可以使用 `nx.Graph`，但只能在聚类前按本 spec 的聚合规则显式折叠 typed edges。

`MemoryGraph.associate` 签名：

```python
def associate(
    self,
    seeds: dict[str, float],
    *,
    depth: int = 2,
    limit: int = 10,
    exclude: set[str] | None = None,
) -> list[AssociationHit]
```

- `seeds` 是 `memory_id -> direct_score`。
- `exclude` 默认空；调用方传入直接召回 id 集合，避免 association 重复返回 direct。
- 返回按 `score DESC, depth ASC, memory_id ASC` 排序。
- 多路径到同一 `memory_id` 取最高 `score`；分数相同取更浅 `depth`；仍相同取字典序更小的 `via`。
- `via` 是最佳路径中目标节点的前一个节点 id；1 跳时 `via` 是 seed id，2 跳时 `via` 是中间节点 id。
- `kinds` 是最佳路径每一跳选中的 edge kind value，按路径顺序排列；1 跳长度为 1，2 跳长度为 2。

### store 签名

`MemoryStore` 公开方法变更：

```python
async def search_keywords(
    self,
    tokens: list[str],
    limit: int,
) -> dict[str, KeywordSearchHit]
async def list_edges(self, kind: MemoryEdgeKind | None = None) -> list[MemoryEdge]
async def upsert_edge(
    self,
    from_id: str,
    to_id: str,
    kind: MemoryEdgeKind,
    weight: float,
    created_at: float,
) -> None
async def delete_edges(self, keys: list[tuple[str, str, MemoryEdgeKind]]) -> None
async def list_edge_degrees(self, memory_ids: list[str]) -> dict[str, list[MemoryEdge]]
```

- `search_keyword(query: str)` 删除；所有检索与建边流程只调用 `search_keywords(tokens, limit)`。
- `search_keywords(tokens, limit)` 的 `limit <= 0` 返回 `{}`。
- `list_edges(kind=None)` 返回所有边；传 kind 时只返回该 kind。
- `upsert_edge` 先把 `from_id` / `to_id` 规范化为字典序升序再写库；冲突键为 `(from_id, to_id, kind)`，其中 `from_id < to_id`。
- `delete_edges` 按完整三元键删除；调用方传入的端点也必须使用 canonical 顺序。
- `list_edge_degrees` 返回每个给定 memory id 的 incident edges；边方向按无向度数统计，即 `from_id = id OR to_id = id`。

### retrieval 签名

`MemoryRetrieval` 公开方法：

```python
async def search(
    self,
    query: str,
    direct_limit: int = 20,
    association_limit: int = 10,
) -> list[Memory]
```

- 空白 query 返回 `[]`。
- `direct_limit <= 0` 时直接召回为空，联想也为空，返回 `[]`。
- `association_limit <= 0` 时只返回 direct。
- 返回顺序固定为 direct ranked results 在前，association ranked results 在后。

### facade 签名

`MemoryFacade` 是表达、活动、读书、共同浏览、反思和 API 读取记忆的唯一入口：

```python
async def create_scene_memory(reply_context: dict[str, str]) -> Memory: ...
async def remember_activity(event: Event, consumer_id: str | None = None) -> None: ...
async def remember_user_profile(
    content: str,
    summary: str,
    aspects: list[str],
    correlation_id: str,
) -> None: ...
async def remember_knowledge(items: list[dict[str, str]], correlation_id: str) -> None: ...
async def remember_reading(content: str, summary: str, correlation_id: str) -> None: ...
async def remember_browsing(
    page_id: str,
    lease_token: str,
    content: str,
    summary: str,
    topics: list[str],
) -> Memory: ...
async def forget_browsing(page_id: str) -> None: ...
async def forget_all_browsing() -> None: ...
async def record_no_answer(question: str, correlation_id: str) -> None: ...
async def search(query: str) -> list[Memory]: ...
async def record_recall(memory_id: str) -> None: ...
async def list_memories(
    kind: MemoryKind | None = None,
    type: MemoryType | None = None,
    limit: int | None = None,
) -> list[Memory]: ...
async def count_new(kind: MemoryKind | None, since: float) -> int: ...
async def export(fmt: str) -> str: ...
```

Facade 规则：

- `create_scene_memory` 只在慢通道回合末调用，LLM 调用 1 次（`json_mode=True`、`module="memory"`、`output_type="scene_memory"`）生成 `{content, kind, topics, summary}` 后复用 `_persist_memory`。
- 活动、读书、浏览、知识、用户画像、未答记录入口都复用 `_persist_memory` 的入库
  尾段，不绕过建边、矛盾检测、衰减/淘汰和事件；浏览分支只为固定 memory/event id
  和跳过合并做最小特例。确定性入口不调用 scene-memory LLM。
- `remember_browsing` 的整合文本由 `13-browsing-system` 生成。它创建
  `LONG_TERM / BROWSING` 记忆并固定 `Memory.id = page_id`；对应
  `MEMORY_CREATED` 使用 `uuid5(NAMESPACE_URL, "nyx:browsing-memory:" + page_id)`
  作稳定 event id，`correlation_id=page_id`。只有当前有效的 browsing page
  `integrating` lease token 才可写入：在同一共享 DB 事务内验证 token、page 存在且
  `lease_until` 未到期，并原子插入固定 memory id 与稳定 event；旧 worker 或已删除 page
  影响 0 行且不发事件。embedding/LLM 与候选计算在事务外；建边/矛盾检测等
  best-effort 副作用不在事务锁内执行。同 memory id 已存在时不 strengthen、
  不重做 embedding/建边/矛盾检测；有效 claim 只核对稳定 event 并幂等补发缺失事件。
  如进程在核心事务之后、best-effort 建边/矛盾检测之前崩溃，恢复不重放 LLM，
  该记忆可能少部分关系边；核心记忆和事件仍可达。`BROWSING` 跳过 content/semantic
  合并，避免相似网页的不同共同浏览经历只被强化而丢失正文；刷新/后退
  等重复由浏览 page checkpoint 的 URL+hash 唯一键处理。其它 kind 的两层去重
  语义不变。
- `forget_browsing(page_id)` 先按 id 读取并严格校验 `kind is MemoryKind.BROWSING`；
  不存在时幂等返回，kind 不匹配时拒绝。删除 memory 时依赖 SQLite 外键同一
  提交级联删除对应 browsing page 和当前页指针；不清浏览 profile。
- `forget_all_browsing()` 在一个本地事务批量删除全部 `MemoryKind.BROWSING` 记忆及其 embedding/
  incident edges，依赖外键级联删除对应 browsing page；不删除其它 kind、浏览 profile或通用
  event/eval 记录。由 browsing Facade 的全量删除流程调用，流程重试必须能收尾没有 memory 的
  failed page/session。
- `search(query)` 纯委托 `MemoryRetrieval.search(query)`，对表达层不暴露 `direct_limit` / `association_limit` 参数。
- `remember_activity(event, consumer_id=None)` 是 `ACTIVITY_END` 的记忆消费者；RouteSpec 注册时传 `consumer_id="memory.activity_end"`。durable 路径在同一本地事务内写 `event_effect`、记忆状态和 `memory_created` 事件，事务外才运行 embedding/关系边/矛盾检测/衰减等旁路；旁路失败不撤销已提交核心记忆，重放时已应用则 no-op。普通直接调用不传 `consumer_id`，保留旧调用面。
- `record_recall(memory_id)` 只表示“进入慢通道 prompt 后被想起”：委托 store 加一；短期达阈值时发布 `memory_promoted`，长期不重复发布。升级和 `memory_promoted` 事件行在同一本地事务提交，commit 后再 `announce_committed`。
- `export("json")` 输出 JSON 数组；`export("md")` 输出 Markdown；非法格式抛 `ValueError`；导出不包含 `Memory.sources`。
- Facade 自己发布 `memory_created` / `memory_promoted` / `reflection` 事件，返回值只返回数据对象或 `None`，不返回 `Event` 给调用方发布。

## 召回流程

`MemoryRetrieval.search` 分四步：

召回模块常量：

```python
_RECALL_VECTOR_CANDIDATE_K = 80
_RECALL_KEYWORD_CANDIDATE_K = 80
```

1. `query_vec = await embed(query)`。`embed is None` 或 embedding 失败时，`query_vec=None`，跳过 ANN，只走 keyword。
2. `AnnIndex.query(query_vec, candidate_k=_RECALL_VECTOR_CANDIDATE_K)` 返回 ANN 候选 id 和 cosine；只对这些候选计算精确 vector score。
3. `extract_keywords(query)` 后调 `store.search_keywords(tokens, limit=_RECALL_KEYWORD_CANDIDATE_K)`，得到 keyword 候选。
4. 合并候选，算 `RankedMemory.score`，取 `direct_limit`，再把 direct 分数传给 `MemoryGraph.associate`。

融合公式：

```text
score = 0.65 * vector_score
      + 0.25 * keyword_score
      + 0.07 * freshness
      + 0.03 * type_boost
```

- `vector_score = (cosine + 1.0) / 2.0`，夹到 `[0, 1]`；无向量候选为 0。
- `keyword_score = min(1.0, 0.7 * coverage + 0.3 * field_score)`。
- `coverage = matched_unique_token_count / query_token_count`；同一 token 多次命中只算一次。
- `field_score = min(1.0, 0.7 * summary_hit_ratio + 0.3 * content_hit_ratio)`。
- `summary_hit_ratio = summary_unique_token_count / query_token_count`。
- `content_hit_ratio = content_unique_token_count / query_token_count`。
- `freshness` 直接使用 `Memory.freshness`，调用方保证在 `[0, 1]`。
- `type_boost`：`LONG_TERM=1.0`、`SHORT_TERM=0.5`。
- 排序 tie-break：`score DESC, vector_score DESC, keyword_score DESC, memory.freshness DESC, memory.created_at DESC, memory.id ASC`。

`sources` 标注：

- ANN 候选加入 `SearchMode.VECTOR`。
- keyword 候选加入 `SearchMode.KEYWORD`。
- association 追加结果加入 `SearchMode.ASSOCIATION`。
- direct 同时命中 ANN 和 keyword 时 sources 顺序为 `[SearchMode.VECTOR, SearchMode.KEYWORD]`。
- association 结果如果原本不是 direct，不继承 seed sources，只标 `[SearchMode.ASSOCIATION]`。

## 关键词提取与 LIKE

`extract_keywords(text: str) -> list[str]` 是纯函数：

- 英文/数字：按 `[A-Za-z0-9_]+` 提取并 lower。
- 中文：按连续 CJK 片段提取；长度 2-8 的片段直接保留，长片段用 2 字和 3 字滑窗切分。
- 过滤长度 1 的 token。
- 过滤停用词：`这个`、`那个`、`什么`、`怎么`、`为什么`、`然后`、`就是`、`一下`、`可以`、`还是`、`一个`、`我们`、`你们`。
- 去重并保持首次出现顺序。

`MemoryStore.search_keywords(tokens, limit)`：

- 空 token list 返回 `{}`，不查 DB。
- `limit <= 0` 返回 `{}`，不查 DB。
- 每个 token 都用 escaped `LIKE '%token%' ESCAPE '\'` 查 `summary` 和 `content`。
- 返回 `dict[memory_id, KeywordSearchHit]`。
- `summary_tokens` 只记录命中 summary 的 unique token，按输入 token 顺序。
- `content_tokens` 只记录命中 content 的 unique token，按输入 token 顺序。
- 同一 token 在同一字段出现多次只记一次。
- 返回前按 `matched_unique_token_count DESC, summary_unique_token_count DESC, content_unique_token_count DESC, freshness DESC, created_at DESC, memory_id ASC` 排序并截断到 `limit`。
- `matched_unique_token_count = len(set(summary_tokens) | set(content_tokens))`。
- store 不计算 keyword score；score 只在 retrieval 中计算。

## ANN 契约

新增 `nyx/memory/ann.py`：

```python
@dataclass
class AnnCandidate:
    memory_id: str
    cosine: float

class AnnIndex:
    @classmethod
    def build(
        cls,
        memories: list[Memory],
        *,
        planes: int = 16,
        tables: int = 4,
        seed: int = 0,
    ) -> AnnIndex: ...

    def query(
        self,
        vector: list[float],
        candidate_k: int,
        *,
        allowed_ids: set[str] | None = None,
    ) -> list[AnnCandidate]: ...
```

规则：

- 只索引 `embedding is not None` 且维度等于首个有效 embedding 维度的记忆；维度不一致的记忆跳过。
- 随机平面用 `random.Random(seed)` 生成，所有表和平面确定性可复现；每个平面向量的每个维度用 `rng.gauss(0.0, 1.0)` 采样。
- 平面维度来自首个有效 embedding 长度；没有有效 embedding 时 index 为空。
- hash bit：`dot(vector, plane) >= 0` 为 1，否则 0。
- 查询维度与 index 维度不一致时返回 `[]`。
- 查询先取同桶候选，再枚举 Hamming radius 1，再 radius 2；每一层按 `memory_id ASC` 稳定加入，达到 `candidate_k` 停止。
- 如果 radius 2 后仍不足，从 indexed memories 按 `created_at DESC, id ASC` 补足；总数仍不超过 `candidate_k`。
- `allowed_ids` 不是 `None` 时，同桶、Hamming 邻桶和 newest fallback 都先排除集合外 id，再应用 `candidate_k`；空集合表示没有候选，不能先截断全局 top-K 再过滤。
- 最后只对候选集计算精确 cosine，并按 `cosine DESC, memory.created_at DESC, memory.id ASC` 排序。
- `candidate_k <= 0` 返回 `[]`。

`MemoryRetrieval` 缓存 ANN index，并用 fingerprint 判断失效：

```python
fingerprint = tuple(
    (m.id, m.created_at, hash_embedding(m.embedding))
    for m in memories
    if m.embedding is not None
)
```

- fingerprint 变化即重建，能覆盖新增、删除、embedding 更新。
- `created_at` 不随 recall/strengthen 改动；召回计数变化不会触发 ANN 重建。
- `hash_embedding` 对 float 用 `round(x, 8)` 后 tuple hash，避免 JSON 字符串格式差异。

## 持久化去重与矛盾候选

模块常量：

```python
_PERSIST_SEMANTIC_CANDIDATE_K = 200
_DEDUP_SIM_THRESHOLD = 0.95
_CONTRADICTION_CANDIDATE_K = 5
_CONTRADICTION_SIM_THRESHOLD = 0.6
```

`MemoryFacade._persist_memory(memory, correlation_id)` 顺序：

1. 先调用 `store.find_by_content(memory.content, memory.kind)` 做同 kind 的 canonical content hash 精确去重；命中则 `strengthen(existing.id, now)` 并返回持久化旧记忆。
2. 如果 `memory.embedding is None` 且 `embed` 可用，调用 `embed(memory.content)` 补 embedding。
3. 如果新记忆有 embedding，一次读取全部旧记忆并构建一个 `AnnIndex`；先用 `allowed_ids` 限定同 kind 查询去重候选，未命中去重时再复用同一 index 查询全局候选。两次查询都受 `_PERSIST_SEMANTIC_CANDIDATE_K` 约束，并在候选内计算精确 cosine，得到 `PersistSemanticHit(memory, cosine)` 列表。
4. 如果候选 top-1 达到 kind 对应阈值，并且否定极性、数字集合、显式时间锚点均兼容，则 `strengthen(top.memory.id, now)` 并返回持久化旧记忆；`episode` 还要求创建时间差不超过一小时。
5. 未命中去重时才 `store.add(memory)`，随后建边、矛盾检测、衰减/淘汰、发布 `memory_created`。

规则：

- 同 kind 语义去重与全局建边/矛盾候选复用同一个 ANN index；未命中去重后的全局 `PersistSemanticHit` 同时供语义建边和矛盾检测使用，不再创建旧的无界 `scored` 全表列表，也不重复构建 index。
- `_PERSIST_SEMANTIC_CANDIDATE_K` 是持久化语义候选上限；它不受聊天召回的 `direct_limit` / `association_limit` 影响。
- `embed is None`、embedding 失败、ANN index 为空或维度不一致时，语义去重与矛盾检测跳过；content hash 去重仍生效。
- 语义去重的确定性冲突门控宁可漏合并也不误合并：否定极性不同、数字集合不同或显式时间锚点不同均视为不兼容，继续创建新记忆。
- `_detect_contradiction(memory, candidates, correlation_id)` 只接收 `PersistSemanticHit`，不再接收旧 `scored`。
- 矛盾检测候选取 `candidates[:_CONTRADICTION_CANDIDATE_K]` 中 `cosine >= _CONTRADICTION_SIM_THRESHOLD` 的记忆。
- 无候选或全低于阈值时不调用 LLM。
- 矛盾 LLM 失败、JSON 解析失败、返回未知 id 时只记录日志并跳过矛盾触发的 reflection，不回滚已入库记忆；这只适用于 memory 的矛盾检测旁路，不改变 `inner_life.reflection` durable consumer 对反思自身解析/提交失败的 retry 语义。
- 去重命中旧记忆时不建边、不矛盾检测、不发布 `memory_created`，保持事实表的 strengthen 语义。

## 联想记忆

联想从 direct top N 出发：

```python
seeds = {ranked.memory.id: ranked.score for ranked in direct_ranked}
hits = graph.associate(
    seeds,
    depth=2,
    limit=association_limit,
    exclude=set(seeds),
)
```

分数公式：

```text
assoc_score = seed_score * edge_weight * depth_decay * kind_weight
```

- 第 1 跳 `depth_decay=1.0`，第 2 跳 `depth_decay=0.55`。
- `kind_weight`：`semantic=1.0`、`entity=0.9`、`keyword=0.75`、`temporal=0.35`。
- LLM 关系 kind：`same_topic=1.05`、`elaborates=1.1`、`contrasts=1.0`、`causes=1.0`、`updates_preference=1.15`、`user_profile_link=1.1`。
- association 只接收 `MemoryEdgeKind` 合法值；不存在未知 kind 默认分支。
- 多 kind 边连接同一 pair 时，每条 typed edge 都参与扩散；到达同一目标时按最高路径分数去重。
- association 结果只追加 `by_id[memory_id]` 存在的记忆；孤儿边忽略。

## 边表与迁移

`memory_edge` 新 schema：

```sql
CREATE TABLE IF NOT EXISTS memory_edge (
    from_id TEXT NOT NULL REFERENCES memory(id) ON DELETE CASCADE,
    to_id TEXT NOT NULL REFERENCES memory(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'semantic',
    weight REAL NOT NULL DEFAULT 1.0,
    created_at REAL NOT NULL DEFAULT 0.0,
    CHECK (from_id < to_id),
    PRIMARY KEY (from_id, to_id, kind)
);
```

迁移规则：

- 旧表主键从 `(from_id, to_id)` 改为 `(from_id, to_id, kind)`；SQLite 迁移用新表复制、删旧表、重命名新表完成。
- 旧边端点按字典序 canonicalize 后迁移为 `kind='semantic'`。
- 如果旧表里存在方向相反的重复边，合并为同一 typed edge：`weight` 取最大值，`created_at=0.0`。
- 旧边 `created_at=0.0`。
- 新边写入前端点必须 canonicalize，`from_id` / `to_id` 不表达方向。
- `MemoryGraph` 读取时按无向图使用，不能因原始调用方向影响扩散。
- 不新增 `memory_cluster` 表。
- 不新增配置项；候选数、权重、度数上限先作为模块常量。

## 建边算法

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

`MemoryFacade._persist_memory` 在新记忆写入后调用 `build_edges_for(memory, persist_semantic_hits, now)`；去重命中旧记忆并 `strengthen` 时不建新边。

### 语义边

- 对新记忆 content + summary 生成细化 embedding，输入格式为 `summary + "\n" + content`。
- 优先复用 `_persist_memory` 已取得的 `persist_semantic_hits`，按 cosine 排序后取 `_EDGE_SEMANTIC_CANDIDATE_K`。
- 如果调用方没有传入 `persist_semantic_hits`，才用 `AnnIndex.query(new_embedding, candidate_k=_EDGE_SEMANTIC_CANDIDATE_K)` 取候选。
- 跳过自身、无 embedding、维度不一致候选。
- 权重用 `vector_score = (cosine + 1.0) / 2.0`。
- 只保留 `vector_score >= 0.72` 的候选。
- 写入 `kind="semantic"`，每个新节点最多保留 `_EDGE_PER_KIND_LIMIT` 条。

### 实体边

`extract_entities(memory: Memory) -> set[str]` 是纯函数：

- 从 `summary`、`content`、`kind`、`topics`、`aspect` 提取实体。
- 中文书名号内容、连续英文专有名、连续数字字母标识、长度 2-12 的中文专名片段进入实体集合。
- 全部实体 lower 去重；长度 1 的实体丢弃。

候选池来源：

- 先从 ANN 候选里取最多 `_EDGE_ENTITY_CANDIDATE_K` 条。
- 如果 ANN 为空，则按 `created_at DESC, id ASC` 从已有记忆补足到 `_EDGE_ENTITY_CANDIDATE_K`。

权重：

```text
entity_score = shared_entity_count / max(len(new_entities), len(old_entities), 1)
```

- `entity_score >= _ENTITY_THRESHOLD` 才写边。
- 写入 `kind="entity"`，每个新节点最多保留 `_EDGE_PER_KIND_LIMIT` 条。

### 关键词边

- 复用 `extract_keywords(summary + "\n" + content)`。
- 候选池来自 `MemoryStore.search_keywords(new_tokens, limit=_EDGE_KEYWORD_CANDIDATE_K)`。
- 候选排序按 `matched_unique_token_count DESC, created_at DESC, memory_id ASC`。
- 权重为 Jaccard：

```text
keyword_score = len(new_tokens & old_tokens) / len(new_tokens | old_tokens)
```

- `keyword_score >= _KEYWORD_THRESHOLD` 才写边。
- 写入 `kind="keyword"`，每个新节点最多保留 `_EDGE_PER_KIND_LIMIT` 条。

### 时间边

- 候选池按 `abs(old.created_at - new.created_at) ASC, old.id ASC` 取 `_EDGE_TEMPORAL_CANDIDATE_K` 条。
- 只考虑 `abs(delta) <= _TEMPORAL_WINDOW_SECONDS`。
- 权重：

```text
temporal_score = 1.0 - abs(delta) / _TEMPORAL_WINDOW_SECONDS
```

- 写入 `kind="temporal"`，每个新节点最多保留 `_EDGE_PER_KIND_LIMIT` 条。
- 时间边只作为弱联想线索，后续聚类和剪枝都会降权。

### LLM 关系边

LLM 关系只对综合候选 top5 调用：

- 综合候选来源是语义、实体、关键词、时间四类候选的并集。
- 每个候选的临时分数：

```text
combined_edge_score = max(
    semantic_score,
    0.9 * entity_score,
    0.75 * keyword_score,
    0.35 * temporal_score,
)
```

- 按 `combined_edge_score DESC, candidate.created_at DESC, candidate.id ASC` 取 `_EDGE_LLM_CANDIDATE_K`。
- LLM prompt 只包含新记忆与 top5 候选的 `id`、`summary`、`content`、`kind`、`topics`，要求判断二者是否存在明确关系。
- LLM 输出必须是 JSON object：

```json
{
  "relations": [
    {
      "memory_id": "old-id",
      "kind": "elaborates",
      "weight": 0.8
    }
  ]
}
```

- 合法 `kind`：`same_topic`、`elaborates`、`contrasts`、`causes`、`updates_preference`、`user_profile_link`、`none`。
- `kind="none"` 不写边。
- `weight` 夹到 `[0.0, 1.0]`；缺失或非法时默认 `0.7`。
- LLM 返回未知 `memory_id`、未知 `kind`、无法解析 JSON 时跳过对应项。
- LLM 整体失败只记录日志并跳过 LLM 关系边，不阻塞记忆持久化。
- LLM 客户端仍走统一 LLM client，不直接使用 httpx。

### 写边顺序

1. 计算五类候选和分数。
2. 每类按本类分数排序并截断到 `_EDGE_PER_KIND_LIMIT`。
3. 调 `MemoryStore.upsert_edge(new_id, old_id, kind, weight, created_at=now)` 写入。
4. 对新节点和被触达旧节点执行度数控制。

## 度数控制

- 存储是有向边，度数计算是无向有效度：`from_id = node_id OR to_id = node_id`。
- 同一 unordered pair 的不同 `kind` 分别计入度数；例如 A-B 有 `semantic` 和 `entity` 两条 typed edge，则计 2。
- 需要对新节点和本次 upsert/delete 触达的旧节点都执行剪枝，避免旧节点无限涨度。
- 每个节点每种 `kind` 最多 `_EDGE_PER_KIND_LIMIT` 条 incident edges。
- 每个节点总 incident typed edges 最多 `_EDGE_TOTAL_LIMIT` 条。
- 剪枝必须通过 `MemoryStore.delete_edges(keys)` 删除完整 `(from_id, to_id, kind)`。

剪枝排序：

```text
prune_priority = edge.weight * prune_kind_weight
```

- `prune_kind_weight`：`semantic=1.0`、`entity=0.95`、`keyword=0.8`、`temporal=0.2`、`same_topic=1.1`、`elaborates=1.1`、`contrasts=1.1`、`causes=1.1`、`updates_preference=1.1`、`user_profile_link=1.1`。
- 先处理单 kind 超限：每个 kind 内按 `prune_priority ASC, edge.created_at ASC, edge.from_id ASC, edge.to_id ASC, edge.kind ASC` 删除到上限内。
- 再处理总度超限：按同一排序继续删除到 `_EDGE_TOTAL_LIMIT` 内。
- 如果一条边连接两个节点，删除后两个节点度数同时下降；剪枝实现可以循环重算 touched nodes，直到所有 touched nodes 满足约束。
- 高权重语义边和 LLM 关系边没有绝对豁免；只通过权重和 kind weight 获得更高保留概率。

## 聚类算法

`MemoryGraph.clusters() -> dict[str, int]`：

- 调用方必须用 `MemoryGraph(edges, memory_ids={m.id for m in memories})` 构造图，确保孤立记忆进入聚类结果。
- 基于当前 `MemoryEdge` 和完整 `memory_ids` 构建加权无向图。
- 同一 unordered pair 的多 kind 边聚合成一条聚类边，聚类权重为 `max(edge.weight * cluster_kind_weight[kind])`。
- `cluster_kind_weight`：`semantic=1.0`、`entity=0.9`、`keyword=0.7`、`temporal=0.15`、`same_topic=1.0`、`elaborates=1.0`、`contrasts=1.0`、`causes=1.0`、`updates_preference=1.0`、`user_profile_link=1.0`。
- 首选 `networkx.community.louvain_communities(G, weight="weight", seed=0)`。
- 如果当前 networkx 不支持 Louvain，则 fallback 到 `networkx.community.greedy_modularity_communities(G, weight="weight")`。
- 聚类结果不落库，不发布事件，不影响聊天召回排序。
- 返回 `dict[str, int]`；`cluster_id` 稳定规则为：先按每个 community 的最小 `memory_id` 升序排序，再从 0 开始编号。
- 孤立节点也必须出现在返回值中，单独成为一个 cluster。

原因：聚类是分析与未来召回多样性工具，不应先污染核心存储。动态计算对当前桌面规模足够，且避免每次写记忆都维护 cluster 状态。

## 数据变更

- `memory_edge` 增加 `kind TEXT NOT NULL DEFAULT 'semantic'`。
- `memory_edge` 增加 `created_at REAL NOT NULL DEFAULT 0.0`。
- 主键从 `(from_id, to_id)` 改为 `(from_id, to_id, kind)`。
- 迁移旧边：端点 canonicalize 后全部视为 `kind='semantic'`，`created_at=0.0`，方向相反重复边合并。
- 不新增 `memory_cluster` 表。
- 不新增配置项；候选数、权重、度数上限先作为模块常量，避免未请求的配置膨胀。

SQLite 不支持直接改主键；迁移需要创建新表、复制旧数据、删除旧表、重命名新表，并恢复外键约束。

## API 端点

无新增 API。`GET /api/memories/search` 仍返回 `Memory[]`；`sources` 会更准确地体现 `vector` / `keyword` / `association`。

## 测试要点

- [ ] `extract_keywords`：中文长句切出稳定 token，英文 lower，停用词与单字被过滤，顺序去重。
- [ ] ANN：构建后能召回近邻；空 embedding 记忆跳过；维度不一致跳过；查询维度不一致返回空；候选数不超过 `candidate_k`；fingerprint 覆盖新增、删除、embedding 更新。
- [ ] keyword LIKE：多个 token 分别匹配；summary/content 命中集合正确；空 token 返回空；`limit <= 0` 返回空；token 多次出现只计一次；LIKE 特殊字符被 escape；返回按命中排序并截断到 limit。
- [ ] 融合排序：vector 强但 keyword 弱、keyword 强但 vector 弱、二者都强三类样本按公式稳定排序；`direct_limit=5` 只返回 5 条 direct。
- [ ] 召回顺序：先 direct，再 association；association 不重复 direct seed；最终数量 `<= direct_limit + association_limit`。
- [ ] `_persist_memory`：content hash 命中优先；bounded semantic candidate top-1 达到 kind 阈值且通过事实冲突门控时 strengthen 旧记忆；未命中才 add/build edges/detect contradiction/publish；矛盾检测只取 top5 且 cosine `>=0.6`。
- [ ] 浏览记忆：固定 memory/event id，新增时原子提交；已有 memory 但缺事件时幂等补发，
  两者都有时不 strengthen/不重复广播；过期/接管 lease token 或已删除 page 不能创建/补发；
  `forget_browsing` 只删 `BROWSING` 且级联清 page/pointer。
- [ ] `MemoryGraph.associate(depth=2)`：二跳可达，按边权/跳数/kind 权重排序，多路径取最高分，seed score 参与计算；同 pair 多 kind 作为 typed edges 分别扩散；`AssociationHit.kinds` 记录最佳路径。
- [ ] 边 schema：`MemoryEdge.kind` / `created_at` 序列化与反序列化；同一 canonical pair 不同 kind 可共存；方向相反同 kind 被 canonicalize 为同一边；旧边迁移后 kind 为 `semantic`。
- [ ] 建边：语义、实体、关键词、时间边分别可被构造；语义边复用 persist semantic candidates；LLM 关系边只对 top5 候选调用；`none` 不写边；LLM 失败不阻塞持久化。
- [ ] 度数控制：每 kind 最多 4 条，总有效度最多 16；新节点和触达旧节点都会剪枝；剪枝通过 `delete_edges` 删除完整三元键。
- [ ] 聚类：`MemoryGraph(edges, memory_ids=...)` 下 Louvain/greedy fallback 均返回稳定 `memory_id -> cluster_id`；孤立节点保留；temporal 边低权重不主导聚类。
- [ ] 表达慢通道：`MemoryFacade.search` 返回 direct + association 后，`assemble_context` 对全部返回记忆逐条 `record_recall`。

## 完成定义

- [ ] `ruff check` 零报错。
- [ ] `pyright` 零报错。
- [ ] `pytest` 全绿。
- [ ] `docs/facts/memory-system-facts.md`、相关 spec、`docs/tech-reference.md`、`docs/test-inventory.md` 与本文件保持同步；如果实现需要改变既有文档语义，先询问用户确认。
