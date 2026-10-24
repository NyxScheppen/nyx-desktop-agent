# 记忆系统事实表

> 本文件是给“无关代码但会碰到记忆事实”的快速摘要，例如表达慢通道、反思触发、读书/活动落记忆、前端 `Memory` 类型。完整记忆系统契约在 `docs/specs/06-memory-system.md`；修改 `nyx/memory/` 或记忆契约时必须先读完整 spec，并同步更新本摘要。

## 时间字段

- `conversation_segment` 保存对话原文及段链；摘要、主题和 embedding 是可重建索引，不替代原文。时间近、主题相接或短确认语默认续接，明确转题才切段。
- 慢通道查询在全部活跃 conversation segment 中单独排序，最多带入 20 段原文；旧长期段不因超过最近 200 条而消失，外部书籍/网页知识不通过该入口召回。

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
  挤占。`material:` 只兼容旧库已有记忆；旧活动材料上传入口已退役，EPUB 产生 `book:`，
  P2 手动文本/URL 和网页摄入产生 `local:` / `web:` 来源。
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
- 默认中文模型为 `BAAI/bge-small-zh-v1.5`；向量直接召回要求原始 cosine ≥ 0.45，纯关键词直接召回要求 keyword score ≥ 0.45。未过门槛的候选不能靠 freshness 填满直接召回名额；旧模型向量须随测试库归档或重建。
- `MemoryFacade.search_source(query, source_topic, limit=5)` 先按来源 topic 过滤全部候选，
  再执行 direct 融合排序，且不追加跨来源 association；读书提问回复用它取同一本书的
  Top 5，不能先取全局 Top N 再过滤。
- `build_embed(model_name)` 只在装配期创建闭包；本地 `SentenceTransformer` 在首次实际编码时转到工作线程加载，并发首次调用只构造一次，避免后端启动和端口监听被模型初始化阻塞。
- topics 旁路对每个参与联想的 topic 在单次检索中只排序一次，先排除 direct 命中再截取最多 8 条；热门度衰减仍按排除前的完整桶大小计算。
- `extract_keywords` 的 CJK 规则按 `docs/specs/06-memory-system.md` 契约执行：长度 2-8 的连续 CJK 片段直接保留，长 CJK 片段只做 2 字/3 字滑窗；不做隐藏边界字符剥离，也不在长片段内部按停用词预拆。
- `Memory.sources` 是瞬态检索来源：`keyword`、`vector`、`association`。它不落库、不进 prompt、不进导出，但 REST `Memory[]` 会序列化给前端。
- `list_memories` 返回库内快照，通常 `sources=[]`；`search` 返回的命中带 sources。
- 记忆面板可按六种 `MemoryKind` 筛选列表和关键词搜索；类别过滤进入列表 SQL、关键词 SQL 和混合检索候选集，联想不会跨到其他类别。

## 事实层

- `memory_entity` / `memory_fact` 独立于 `memory` / `memory_edge`，是通用实体关系图，不只保存用户事实；实体类型覆盖 person/agent/book/character/place/organization/concept 等。
- schema 25 已迁移实体唯一键为 `(canonical_name, entity_type)`，并为事实增加 `polarity`；已有事实和来源外键保留。schema 26 增加 `memory_entity_alias` 及 `(alias, entity_type)` 索引，并从旧 aliases JSON 回填。
- 事实抽取由 `LlmClient(output_type="fact_extraction")` best-effort 完成；未预备候选的单条路径在精确/语义去重后、持久化事务前抽取，去重命中跳过。LLM/JSON/事实 SQL 失败只记日志，不阻塞原始记忆。
- `memory_entity` 以规范化名称 + `entity_type` 唯一，实体合并和查询都走别名索引表，
  Nyx/Nyx 夏本/尼克斯等别名不会重复建实体；同名异类不合并，旧 `aliases` JSON 只作快照。
- `remember_knowledge` 批量输入多个知识点时在逐条去重前调用一次 fact_extraction，输出用 `memory_index` 映射回各条 Memory；重放仍可能调用一次 LLM，重复 Memory 不再写事实，也不补齐缺失事实。批量调用失败逐条使用确定性 fallback，原始记忆仍落库。
- `observe_user` 生成的 `USER_PROFILE` 不参与事实抽取，窗口标题和摘要不能制造用户偏好或状态事实；显式 functional 模式优先于谓词白名单。
- `MemoryFact` REST/前端展示同时带 `subject_type`、`object_type`，前端用“类型 + canonical name”
  区分同名异类实体。
- 查询只围绕命中的实体返回全部当前有效事实；事实召回失败降级为空集。
- `GET /api/memories/facts?limit=64&scope=...` 在 SQL 截断前按 `profile`、`conversation` 或 `knowledge` 筛选，为前端三张独立事实图各提供有界当前有效快照；省略 scope 保留原混合读取。它不进入
  `Memory[]`，也不调用 `record_recall`。
- 查询包含明确月份时按该月份的有效事实召回，否则按当前时间过滤。
- 单值谓词（就业状态、当前职业、居住地、当前公司等）按有效时间关闭旧事实；多值谓词（书中人物、主题、作者、影响等）同一时间保留多个对象；`polarity` 区分正/负关系。
- 抽取 prompt 带 memory kind、来源范围和说话者规则；conversation 的 LLM 用户事实还要求
  记忆正文/摘要明确出现“用户”，避免书中人物、引用和 Nyx 第一人称自述误归因给用户；
  旧就业/偏好规则作为 fallback。
- 表达层先做事实查询提示词门控；store 先 SQL 筛选实体/别名/谓词/宾语，再按实体展开，单次最多 64 条。
- 事实不会进入 `Memory[]`，不会调用 `record_recall`；非讨论快慢通道都可把事实放入独立的 `[相关事实]` prompt 段。
- `search_facts(query, scope=None)` 支持显式作用域；None 保留原知识意图判断，显式 scope 在 SQL 筛选及实体展开前应用。
  讨论模式用 `memory_recall` 读取 profile/conversation 事实与对话段，用 `knowledge_search` 读取 knowledge 事实，关闭原隐式注入。
  完整段 JSON 进入最终回复 prompt 才沿用现有段召回 marker 计数；P2 的知识工具另返回原文证据块及可空 `source_chunk_id`，不增加对话段召回计数。
- 事实不单独发布反思事件；反思准备阶段读取有界近期有效事实，注入独立的 `[近期事实变化]` 段，避免普通记忆矛盾与事实变化各反思一次。
- 记忆矛盾仍可触发普通 durable `REFLECTION(reason=memory_contradiction)`；payload 保留新旧记忆 id 和两项 `{memory_id, summary}` 结构化 evidence。证据只说明触发原因，反思 consumer 会把它与当前仍存在的近期旧记忆一并送入“归纳模式并形成新点子”的 prompt。

### 连续对话段与事实作用域

- schema 40 为 `memory_fact` 增加 `scope`，区分用户画像、对话事实和资料知识；普通表达组装改为召回 `conversation_segment` 原文，不再调用旧 Memory 检索和 recall 升级。
- schema 41 为对话段增加判定时间、原因和原话事件 ID，并新增跨段证据表。明确断段、长期空闲后再次查询及角色卸载可触发判定；普通偏好需 LLM 指认另一历史段，重要经历需高重要度和用户原话证据。
- 对话段检索已接入中文 embedding，纯向量命中需 cosine ≥ 0.45；聊天端不再产生旧场景记忆请求。
- schema 42 为段增加 freshness/结算锚点、活跃召回资格、召回次数、事实抽取时间与 scope，并新增事件级段召回标记；旧段默认活跃且原文不变。独立记忆计时器每分钟检查满四小时的待判段及事实补写，并结算短期段衰减和容量淘汰；freshness 参与活跃召回排序。淘汰只取消召回资格，原文与事实保留。
- 对话段真实进入慢通道独立 prompt 区块后才按 `(user_event_id, segment_id)` 计数，达到既有阈值可独立于 LLM 判定晋升；预算落选、快通道和重放不计。段追加新轮次会重置判定并刷新新鲜度，但不清除历史召回计数。
- 对话段判定后从原文抽取明确事实，`memory_fact.source_segment_id` 提供溯源；画像段的用户主体事实进入 `profile`，其余明确对话事实进入 `conversation`，阅读/网页资料仍在 `knowledge`，不同 scope 的单值事实不会互相关闭。重新抽取前关闭该段的旧事实；事实失败不丢原文。

## 双层知识与摄入（P2）

- schema 43 为 `reading_evidence` 增加可空 `location` / JSON `embedding`，为
  `memory_fact` 增加 `source_chunk_id` 外键，删除块时 SET NULL；旧数据不补算向量、不猜测来源。
- `MemoryFacade.ingest_source` 将原文分为最多 6000 字符的块，先提交 chunk，再摘要并调用
  `remember_knowledge(items, ..., source_chunk_id=...)`；先给同一块的全部知识点补已存来源，
  每块一次批量调用，多个知识点只做一次事实抽取。可选 `created_counts` 累计器在每条
  持久化成功后累加；摄入路径在异常收尾时也汇总，保留中途失败前已提交的数量。
  返回值仍是该次调用的新增数，累计器不重置旧值；异常继续抛出，记忆逐条提交。
  其返回值和 API 的 `created_memories` /
  `created_facts` 是本次实际新增数，重复命中为零，已有事实保留原来源。
- chunk 精确/语义去重限定同来源；语义 cosine ≥ 0.95，并保护数字、时间与否定差异。
  digest 按 chunk ID 读取已存原文及来源，避免语义命中后用另一份正文生成证据关联。
- schema 44 回填原文 UTF-8 SHA-256，并增加来源/hash/时间/ID 复合索引，精确查重仍核对全文。
  Store 写入同时维护 hash；直接 SQL 导入/修复正文须提供相同 hash。ID-only 变更日志由 INSERT、
  检索字段 UPDATE、DELETE/级联触发器同事务记录，`consumed_at` 更新不产生日志。
- Facade 持有隔离的原文映射、全局/来源 ANN 和二字子串 posting，首次冷加载，之后只读取变更行；
  ANN/关键词 posting 用有序段增量合并、按需取候选，不在每次查询或每块摄入时重建全库索引。
  同一 Facade 内相同维度的 ANN 共用不可变投影；来源删空即移除来源索引，ANN 删空后可用新维度重新初始化。
  每 token 从最稀有 posting 取至多 200 个 ID，再校验完整子串和覆盖率；向量也至多 200 个候选。
  密集命中是近似选择，不承诺全库最优。调用方事务内使用临时视图，回滚不推进常驻版本；
  同步失败失效后冷重建。常驻内存随库容量增长，变更日志保留；不涵盖普通 Memory 持久化成本。
- `search_knowledge` 仅查 knowledge 事实，合并其关联块和相关向量/关键词块，ID 去重。
  直接块候选要求非空向量，且 cosine 或关键词覆盖率 ≥ 0.45；NULL 向量只能由事实关联带出，不用最新块兜底。
  每块正文 ≤ 2000 字符，整个 chunks JSON 数组（含来源元数据）≤ 12000 字符。
- `consumed_at` 只控制既有反思消费者，已消费块仍能检索。手动和网页原文同样可被反思消费；
  活动总结继续经 `remember_activity` 保存，不冒充原文证据。
- EPUB/网页任务/探索先存原文 pending，依次提交 chunk、提取结果和显式 chunk ID、knowledge，
  最后同事务推进 cursor/profile 并清 pending。失败保留已提交阶段；旧已提取 pending 无 ID 时保持 NULL。
- 事实抽取/存储沿用 best-effort：失败保留 chunk/Memory，可能没有事实行；重复 Memory
  不补抽事实，因此不承诺失败重试必然补齐事实。事实批次 SQL 失败由 savepoint 回滚。
- 手动 `POST /api/knowledge/sources` 支持 text/url；注册的 `web_fetch` 摄入正文，checkpoint
  阅读器使用不重复摘要的 raw fetch。网络关闭时不注入 raw fetch，恢复旧任务也遵守开关。

## 活动记忆

- `reading` 活动记忆：`result.note` 作 content，`result.book` 作 summary，kind=`activity`。
- `creation` 活动记忆：`result.content` 作 content，`result.title` 作 summary，kind=`activity`。
- `free_exploration` 活动记忆：`result.summary` 作 content，`result.core_discovery` 作 summary，kind=`activity`。`findings`/`knowledge` 可存在于活动结果，但不是活动记忆的主字段。
- `browser_survey` 活动记忆：`result.summary` 作 content，`result.url` 作 summary，kind=`activity`；步骤数和原始结果文本继续留在活动/任务快照中。
