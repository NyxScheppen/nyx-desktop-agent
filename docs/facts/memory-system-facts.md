# 记忆系统事实表

> 本文件是给“无关代码但会碰到记忆事实”的快速摘要，例如表达慢通道、反思触发、读书/活动落记忆、前端 `Memory` 类型。完整记忆系统契约在 `docs/specs/06-memory-system.md`；修改 `nyx/memory/` 或记忆契约时必须先读完整 spec，并同步更新本摘要。

## 时间字段

- `Memory.created_at` 是创建时间，不随 `update_many`、`strengthen`、`record_recall` 改动。
- DB 列 `memory.first_created_at` 是首次创建锚点，INSERT 时等于 `created_at`，旧行迁移时回填为当时的 `created_at`。
- DB 列 `memory.freshness_updated_at` 是 freshness 增量结算锚点；新行等于 `created_at`，
  schema 34 迁移保留旧 freshness 并以迁移时刻起算后续衰减。时间回拨不会返鲜或回拨锚点。
- `count_new(kind: MemoryKind | None, since: float)` 只看 `first_created_at > since`。`kind=None` 表示统计全部记忆。
- 记忆使用受控 `MemoryKind` 和最多五项 `topics`；旧 `tag` 数据在 schema 19 迁移时清空。

## 计数语义

- `record_recall(memory_id, user_event_id)` 表示这条记忆进入慢通道 prompt；
  `memory_recall_use` 用复合主键保证同一用户事件重放不重复计数。首次使用才令
  `recall_count+1`，短期达到阈值后升级长期并发布 `memory_promoted`。marker、计数、升级和
  事件行同事务提交，失败一起回滚；事务内事件追加造成的运行时持久计数按实际增量补偿，
  不覆盖其它发布的计数；memory 删除时 marker 级联清理。
- 慢通道 `assemble_context` 用 `select_prompt_memories` 从搜索结果选择可完整放入 prompt
  预算的排序前缀，渲染共用同一规则；只对入选项按 memory id 去重调用 `record_recall`。
  单条行最多 2000 字符，含标题和换行的记忆块最多 12000 字符；放不下的尾条整条省略，
  不计数、不晋升。同一回合同 id 只计一次，同一用户事件重试也不重复计数。
- 快通道不检索记忆，不调用 `record_recall`，不生成场景化记忆。
- `strengthen(memory_id, now)` 表示重复写入/语义去重命中旧记忆：只重置
  `freshness=1.0` 和衰减锚点；不增加 `recall_count`、不刷新 `created_at`、不触发升级、
  不发布 `memory_created`。

## 写入与去重

- `content_hash` 是 store 派生列，不在 `Memory` dataclass 中；`add` 写入 `hash_content(content)`，`update_many` 改 content 时同步重算。
- `_persist_memory` 两层去重：先精确 content hash，再 bounded persist semantic candidates 内 top-1 cosine >= 0.95。新记忆有 embedding 时只读取一次旧记忆、构建一次 ANN index；同 kind 去重查询把 id 过滤放在候选上限之前，未命中时复用同一 index 取全局候选供语义建边和矛盾检测门控，不做无界全表余弦扫描或重复建索引。命中时强化并返回持久化旧记忆；未命中时 memory、事实 apply 和 `memory_created` 同事务提交，之后才逐项 best-effort 建边、矛盾检测和增量衰减/淘汰。
- `create_scene_memory` 返回最终持久化的 `Memory`：新建时返回新记忆；去重命中时返回旧记忆。
- `remember_activity` / `remember_knowledge` / `remember_reading` 复用 `_persist_memory`，不要绕过统一去重尾段。
- 原文知识通过 `build_source_topic(kind, identifier)` 获得稳定的 `book:` / `material:` /
  `web:` / `local:` topic；`remember_knowledge` 把该来源 topic 和知识主题一同写入
  `Memory.topics`。原文记忆的精确与语义去重都保留来源边界，同一句内容来自不同书籍或
  页面时不会折叠成一条；语义 top-1 也在同一来源候选池内选取，不受其它来源更高相似项
  挤占。`material:` 只兼容旧库已有记忆；通用文本上传入口已退役，新读书知识只产生
  `book:` 来源。
- 每条 Memory 最多一个来源 topic；多个来源、不支持的来源 kind 或空标识直接拒绝。
  无来源记忆只和无来源候选去重，不会精确命中任意带来源记忆。
- `digest_source_block()` 一次只处理最多 6000 字符，使用上一版
  `{summary, themes, content_category}` 生成滚动画像和最多 5 条带归因的知识；fiction、
  essay、unknown 不得伪装为无来源的现实事实，也不得把原文第一人称归给用户或 Nyx。
- `memory.activity_end` durable consumer 调用 `remember_activity(event, consumer_id)`；同一 `(event_id, consumer_id)` 重放不会重复新增或 strengthen。`event_effect`、活动记忆和 `memory_created` 同事务提交；embedding、关系边、矛盾检测和衰减在事务外 best-effort，避免阻塞共享数据库锁。
- 慢通道最终 `SPEAK/ASK` 与 `scene_memory_requested` 原子受理；`memory.scene_reply` consumer
  在事务外生成/解析/embedding/抽取事实，在事务内提交 effect、记忆和
  `memory_created`。失败由 delivery 重试，重放 effect 命中即 no-op；快通道和 fallback 不发请求。

## 检索与前端

- `MemoryRetrieval.search` 流程是整句 embedding ANN 候选 + keyword LIKE 候选融合评分 -> direct top N -> 2 跳 association 追加；`direct_limit` 只限制直接召回，`association_limit` 只限制联想追加。
- `MemoryFacade.search_source(query, source_topic, limit=5)` 先按来源 topic 过滤全部候选，
  再执行 direct 融合排序，且不追加跨来源 association；读书提问回复用它取同一本书的
  Top 5，不能先取全局 Top N 再过滤。
- `build_embed(model_name)` 只在装配期创建闭包；本地 `SentenceTransformer` 在首次实际编码时转到工作线程加载，并发首次调用只构造一次，避免后端启动和端口监听被模型初始化阻塞。
- topics 旁路对每个参与联想的 topic 在单次检索中只排序一次，先排除 direct 命中再截取最多 8 条；热门度衰减仍按排除前的完整桶大小计算。
- `extract_keywords` 的 CJK 规则按 `docs/specs/06-memory-system.md` 契约执行：长度 2-8 的连续 CJK 片段直接保留，长 CJK 片段只做 2 字/3 字滑窗；不做隐藏边界字符剥离，也不在长片段内部按停用词预拆。
- `Memory.sources` 是瞬态检索来源：`keyword`、`vector`、`association`。它不落库、不进 prompt、不进导出，但 REST `Memory[]` 会序列化给前端。
- `list_memories` 返回库内快照，通常 `sources=[]`；`search` 返回的命中带 sources。

## 事实层

- `memory_entity` / `memory_fact` 独立于 `memory` / `memory_edge`，是通用实体关系图，不只保存用户事实；实体类型覆盖 person/agent/book/character/place/organization/concept 等。
- schema 25 已迁移实体唯一键为 `(canonical_name, entity_type)`，并为事实增加 `polarity`；已有事实和来源外键保留。schema 26 增加 `memory_entity_alias` 及 `(alias, entity_type)` 索引，并从旧 aliases JSON 回填。
- 新语义记忆成功落库后由 `LlmClient(output_type="fact_extraction")` best-effort 更新事实；精确或语义去重命中不重复抽取。LLM/JSON/事实 SQL 失败只记日志，不阻塞原始记忆。
- `memory_entity` 以规范化名称 + `entity_type` 唯一，实体合并和查询都走别名索引表，
  Nyx/Nyx 夏本/尼克斯等别名不会重复建实体；同名异类不合并，旧 `aliases` JSON 只作快照。
- `remember_knowledge` 批量输入多个知识点时只调用一次 fact_extraction，输出用 `memory_index` 映射回各条 Memory；批量调用失败逐条 fallback，原始记忆仍落库。
- `observe_user` 生成的 `USER_PROFILE` 不参与事实抽取，窗口标题和摘要不能制造用户偏好或状态事实；显式 functional 模式优先于谓词白名单。
- `MemoryFact` REST/前端展示同时带 `subject_type`、`object_type`，前端用“类型 + canonical name”
  区分同名异类实体。
- 查询只围绕命中的实体返回全部当前有效事实；事实召回失败降级为空集。
- `GET /api/memories/facts?limit=64` 为前端事实图提供有界当前有效事实快照；它不进入
  `Memory[]`，也不调用 `record_recall`。
- 查询包含明确月份时按该月份的有效事实召回，否则按当前时间过滤。
- 单值谓词（就业状态、当前职业、居住地、当前公司等）按有效时间关闭旧事实；多值谓词（书中人物、主题、作者、影响等）同一时间保留多个对象；`polarity` 区分正/负关系。
- 抽取 prompt 带 memory kind、来源范围和说话者规则；conversation 的 LLM 用户事实还要求
  记忆正文/摘要明确出现“用户”，避免书中人物、引用和 Nyx 第一人称自述误归因给用户；
  旧就业/偏好规则作为 fallback。
- 表达层先做事实查询提示词门控；store 先 SQL 筛选实体/别名/谓词/宾语，再按实体展开，单次最多 64 条。
- 事实不会进入 `Memory[]`，不会调用 `record_recall`；快慢通道都可把事实放入独立的 `[相关事实]` prompt 段。
- 事实不单独发布反思事件；反思准备阶段读取有界近期有效事实，注入独立的 `[近期事实变化]` 段，避免普通记忆矛盾与事实变化各反思一次。
- 记忆矛盾仍可触发普通 durable `REFLECTION(reason=memory_contradiction)`；payload 保留新旧记忆 id 和两项 `{memory_id, summary}` 结构化 evidence。证据只说明触发原因，反思 consumer 会把它与当前仍存在的近期旧记忆一并送入“归纳模式并形成新点子”的 prompt。

## 活动记忆

- `reading` 活动记忆：`result.note` 作 content，`result.book` 作 summary，kind=`activity`。
- `creation` 活动记忆：`result.content` 作 content，`result.title` 作 summary，kind=`activity`。
- `free_exploration` 活动记忆：`result.summary` 作 content，`result.core_discovery` 作 summary，kind=`activity`。`findings`/`knowledge` 可存在于活动结果，但不是活动记忆的主字段。
