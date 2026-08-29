# 记忆存取（store）

> 范围：`memory/store.py`（`MemoryStore`：`memory` / `memory_edge` 两表 CRUD + 关键词 LIKE + 行↔dataclass 序列化）。
> 纯基础设施 spec：不含检索三层（08-memory-retrieval）、不含 networkx 联想图（08）、不含 Facade（09-memory-facade）、不含「新鲜度衰减 / 短期升级长期 / 容量淘汰」的生命周期逻辑（09）。
> spec 只定义契约（方法签名 + 锁约定 + 序列化语义）；实现以 `nyx/memory/store.py` 源文件为准。

## 元信息

- **前置依赖**：01-types（`Memory` / `MemoryEdge` / `MemoryType`）、04-db（`Database`（conn+lock）+ `memory` / `memory_edge` 表）

## 用户故事

> 作为 Nyx 系统的开发者，我想要一份 `memory` / `memory_edge` 两表的 SQLite 存取层 + 行↔`Memory` 序列化，以便 08-retrieval 与 09-facade 通过注入的 `MemoryStore` 做 CRUD，不各自写 SQL。

## 验收标准

- [ ] `store.py` 含 `MemoryStore`（`add` / `get` / `find_by_content` / `list_memories` / `update_many` / `delete_many` / `record_recall` / `strengthen` / `search_keyword` / `list_edges` / `upsert_edge`）+ 模块级 `hash_content` + `_memory_row` / `_row_to_memory`（实现见 `nyx/memory/store.py`）
- [ ] 所有 DB 读写都在 `async with self._db.lock` 内；**锁作用域 = 单个 store 方法的 SQL 块，不跨 store 方法嵌套**（`asyncio.Lock` 不可重入，嵌套死锁）
- [ ] 行↔`Memory` 往返：`aspect` JSON 数组（空 = `"[]"`）、`type` 枚举 `.value`、`recall_count` 整数、`embedding` `list[float] | None`（`None` ↔ SQL `NULL`）
- [ ] `list_memories` 按 `tag` / `type` 过滤，`freshness DESC, created_at DESC` 排序；`limit` 截断（拼 `LIMIT {limit}`，避免无界拉取）
- [ ] `search_keyword` 用 `LIKE` 匹配 `content` 或 `summary`
- [ ] `delete_many` 级联删 `memory_edge`（删边 + 删记忆在**同一锁块**内原子完成）
- [ ] `upsert_edge` 用 `ON CONFLICT` 更新 `weight`
- [ ] `update_many` 不改 `id` / `created_at`（`created_at` 是创建时刻，不可变）
- [ ] `pyright` strict 零报错

## 技术方案

- **新文件**：`nyx/memory/store.py`（无 Facade、无 API、无业务逻辑）
- **库**：无新库（标准库 `json`；`aiosqlite` 已由 04-db 引入）
- **公开面**：`from nyx.memory.store import MemoryStore, hash_content`（不加 `__all__`；`_memory_row` / `_row_to_memory` / `_MEMORY_INSERT_COLS` 私有）
- **锁约定**：每个方法一个 `async with self._db.lock:` 的 SQL 块；`delete_many` 里删边 + 删记忆在**同一个锁块**内原子完成，不拆成两次锁；store 方法之间**不互相调用对方的持锁方法**（`asyncio.Lock` 不可重入，嵌套死锁）。这是 04-db `Database(conn, lock)` 共享连接约定的首个 store 落地
- **`created_at` 不可变**：`update_many` 的 SET 子句不含 `created_at` / `id`——创建时刻是事实，不可改
- **排序约定**：`list_memories` / `search_keyword` 按 `freshness DESC, created_at DESC`——检索要新鲜的在前（design §6「长期不消失，只新鲜度下降，检索时排后」）
- **关键词用 `LIKE`**：04-db 只有 `idx_memory_tag` / `idx_memory_type` 两个索引，**没有 FTS 表**，所以「SQLite FTS/LIKE」（design §6.3）落为 `LIKE '%query%'`。`%` / `_` / `\` 会被当通配符，已转义（`_escape_like` + `ESCAPE '\'`，让 query 按字面匹配）；ASCII 大小写不敏感、中文按字节
- **枚举列存 `.value`、`aspect` 存 JSON 数组**：与 04-db「枚举列存 `.value` 字符串、复杂字段存 JSON 字符串」一致；`aspect` 空集合存 `"[]"`（非 Optional → 列 `NOT NULL`，序列化不必判 None）
- **`embedding` 可空列（None ↔ SQL NULL）**：`list[float] | None` ⟺ `embedding TEXT` 可空；`_embedding_json` 把 `None` 序列化为 SQL `NULL`（不是 `"null"` 字符串）、`list` 序列化为 JSON 数组字符串，读回时 `None` 保持 `None`。这是首个可空 JSON 列，后续 store（`goal` / `ended_at` / `content_hash` 等）照此 `None ↔ NULL` 模式
- **`content_hash` 是 store 派生列（不进 `Memory`）**：`memory` 表加 `content_hash TEXT`（04-db 迁移 v6），由 `add` 写入 `hash_content(content)`、`find_by_content` 查重用；`Memory` dataclass 不承载它（不改 01-types、不改构造器），`_row_to_memory` 读回也不填充——`_MEMORY_COLS`（SELECT）不含它，`_MEMORY_INSERT_COLS`（INSERT）才追加。旧行 `content_hash` 为 NULL（不去重），新写入行有值
- **边界划分（明确不做）**：新鲜度衰减、容量淘汰是 09-facade 的生命周期逻辑；短期→长期升级的「何时升」也由 facade 决定（阈值经 `record_recall(memory_id, promote_threshold)` 传入）。但「加一 + 条件升型」这个原子原语必须落在 store 单锁内——原子性要求单锁、锁在 store，拆到 facade 会产生跨方法竞态（09 轮审查发现重复升级/丢计数）。`graph.py`（networkx 联想图）归 08，从 `list_edges()` 建图。FK 完整性靠 04-db 的 `PRAGMA foreign_keys=ON`（`upsert_edge` 引用不存在的 id 抛 `aiosqlite.IntegrityError`）

## 测试要点

- [ ] 单元测试 `tests/test_memory/test_store.py`（`pytest-asyncio`，`db = await connect(":memory:")`——内部已设 `row_factory=aiosqlite.Row` + 跑迁移，直接返回 `Database`；`store = MemoryStore(db)`）：
  - [ ] **add + get 往返**：`add` 一个含多值 `aspect`（`["身份背景", "情绪敏感点"]`）、非默认 `recall_count`、`embedding=[0.1, 0.2]` 的 `Memory` → `get` 返回各字段全等（`aspect` JSON 往返、`type` 枚举往返、`freshness` 浮点、`embedding` 列表往返）
  - [ ] **embedding 可空往返**：`add` 一个 `embedding=None` 的 `Memory` → `get` 返回 `embedding is None`（SQL NULL 不是 `"null"` 字符串）
  - [ ] **add 重复 id** → `aiosqlite.IntegrityError`（主键冲突）
  - [ ] **get 未命中** → `None`
  - [ ] **list_memories 过滤/排序/limit**：造 3 条不同 `tag` / `type` / `freshness` → `tag=` 过滤、`type=` 过滤、`tag+type` 组合、默认全量；排序按 `freshness DESC`（freshness 高的在前）；`limit=2` 截断、`limit` 与 `tag` 组合截断
  - [ ] **update_many（单条）**：改 `tag` / `summary` / `freshness` / `type` / `recall_count` / `aspect` / `embedding` → `get` 验证；`id` / `created_at` 不变
  - [ ] **update_many**：改多条（含 `embedding=None` 与 `embedding=[...]`）→ `get` 逐条验证；空列表 → no-op
  - [ ] **delete_many（单条）级联删边**：`add` 两条 memory + 两条关联它的 `upsert_edge` → `delete_many(["a"])` 后 `get=None`、`list_edges` 无残留（其它记忆的边不受影响）
  - [ ] **delete_many**：删多条（含关联边）→ `get` 全部 `None`、`list_edges` 无残留；空列表 → no-op
  - [ ] **record_recall**：未达阈值连调两次 → `recall_count == 2` 且 `type is SHORT_TERM`、返回 `False`；达阈值 → 升 `LONG_TERM`、返回 `True`；已是 `LONG_TERM` → 只递增、返回 `False`
  - [ ] **search_keyword**：`content` 命中 / `summary` 命中 / 无命中 → `[]` / ASCII 大小写不敏感（"FOO" 命中 "foo"）
  - [ ] **search_keyword 转义通配符**：搜 `"100%"` 只命中含字面 `100%`、搜 `"a_b"` 只命中字面 `a_b`（`_escape_like` + `ESCAPE '\'`，不误命中通配符匹配）
  - [ ] **list_edges + upsert_edge**：`upsert_edge` 新建 → `list_edges` 返回 `MemoryEdge`；同 `(from_id, to_id)` 再 `upsert_edge` 改 `weight`（ON CONFLICT 更新不重复建行）
  - [ ] **upsert_edge 引用不存在的 id** → `aiosqlite.IntegrityError`（`PRAGMA foreign_keys=ON` 生效）
  - [ ] **hash_content 确定性**：同 content 同 hash、不同 content 不同 hash、SHA-256 hex 长度 64（`hash_content("x")` 长度 `== 64`）
  - [ ] **find_by_content 命中/未命中**：`add` 后按原 content 命中返回 `Memory`（`id` 一致）、不同 content 返回 `None`
  - [ ] **strengthen**：`add`（`recall_count=0, freshness=0.3`）→ `strengthen` → `get` 验证 `recall_count == 1` 且 `freshness == 1.0`
- [ ] 集成测试：无（store 是基础设施，无 Facade 管道；与 08/09 的编排归各自 spec）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] 08-retrieval / 09-facade 通过注入的 `MemoryStore` 存取（不各自写 SQL）；`graph.py` 从 `list_edges()` 建图；每个 store 方法单锁块、不跨方法嵌套
