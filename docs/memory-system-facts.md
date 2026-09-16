# 记忆系统事实表

> 本文件是给“无关代码但会碰到记忆事实”的快速摘要，例如表达慢通道、反思触发、读书/活动落记忆、前端 `Memory` 类型。完整记忆系统契约在 `docs/specs/06-memory-system.md`；修改 `nyx/memory/` 或记忆契约时必须先读完整 spec，并同步更新本摘要。

## 时间字段

- `Memory.created_at` 是创建时间，不随 `update_many`、`strengthen`、`record_recall` 改动。
- DB 列 `memory.first_created_at` 是首次创建锚点，INSERT 时等于 `created_at`，旧行迁移时回填为当时的 `created_at`。
- `count_new(tag: str | None, since: float)` 只看 `first_created_at > since`。`tag=None` 表示统计所有 tag；传具体 tag 时只统计该 tag。

## 计数语义

- `record_recall(memory_id)` 表示这条记忆进入慢通道 prompt：`recall_count+1`，短期达到阈值后升级长期并发布 `memory_promoted`。升级和 `memory_promoted` 事件行同事务提交，失败一起回滚。
- 慢通道 `assemble_context` 会把 `MemoryFacade.search(message)` 返回的全部命中放进 prompt，并立即对每条命中调用 `record_recall`。
- 快通道不检索记忆，不调用 `record_recall`，不生成场景化记忆。
- `strengthen(memory_id, now)` 表示重复写入/语义去重命中旧记忆：`recall_count+1`、`freshness=1.0`，但不刷新 `created_at`，不触发升级，不发布 `memory_created`。

## 写入与去重

- `content_hash` 是 store 派生列，不在 `Memory` dataclass 中；`add` 写入 `hash_content(content)`，`update_many` 改 content 时同步重算。
- `_persist_memory` 两层去重：先精确 content hash，再 bounded persist semantic candidates 内 top-1 cosine >= 0.95。bounded persist semantic candidates 由 ANN 候选上限约束，同一批候选供语义去重、语义建边和矛盾检测门控使用，不再做无界全表余弦扫描。命中时强化并返回持久化旧记忆；未命中才新增、建边、做矛盾检测、衰减/淘汰、发布 `memory_created`。
- `create_scene_memory` 返回最终持久化的 `Memory`：新建时返回新记忆；去重命中时返回旧记忆。
- `remember_activity` / `remember_knowledge` / `remember_reading` 复用 `_persist_memory`，不要绕过统一去重尾段。
- `memory.activity_end` durable consumer 调用 `remember_activity(event, consumer_id)`；同一 `(event_id, consumer_id)` 重放不会重复新增或 strengthen，活动记忆与派生 `memory_created` / `reflection` 事件行同事务提交。

## 检索与前端

- `MemoryRetrieval.search` 流程是整句 embedding ANN 候选 + keyword LIKE 候选融合评分 -> direct top N -> 2 跳 association 追加；`direct_limit` 只限制直接召回，`association_limit` 只限制联想追加。
- `extract_keywords` 的 CJK 规则按 `docs/specs/06-memory-system.md` 契约执行：长度 2-8 的连续 CJK 片段直接保留，长 CJK 片段只做 2 字/3 字滑窗；不做隐藏边界字符剥离，也不在长片段内部按停用词预拆。
- `Memory.sources` 是瞬态检索来源：`keyword`、`vector`、`association`。它不落库、不进 prompt、不进导出，但 REST `Memory[]` 会序列化给前端。
- `list_memories` 返回库内快照，通常 `sources=[]`；`search` 返回的命中带 sources。

## 活动记忆

- `reading` 活动记忆：`result.note` 作 content，`result.book` 作 summary，tag=`reading`。
- `creation` 活动记忆：`result.content` 作 content，`result.title` 作 summary，tag=`creation`。
- `free_exploration` 活动记忆：`result.summary` 作 content，`result.core_discovery` 作 summary，tag=`free_exploration`。`findings`/`knowledge` 可存在于活动结果，但不是活动记忆的主字段。
