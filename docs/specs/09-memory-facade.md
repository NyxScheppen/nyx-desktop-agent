# 记忆门面（Facade）

> 范围：`memory/facade.py`（`MemoryFacade`：场景化记忆创建 + 检索委托 + 想起升级 + 新鲜度衰减/容量淘汰 + 导出）。
> Facade 层 spec：记忆生命周期（新鲜度衰减、短期→长期升级、容量淘汰）都在这层；纯 CRUD 在 07（`MemoryStore`）、三层检索在 08（`MemoryRetrieval`）、embedding 工厂在 08（`build_embed`）。
> 矛盾检测走「embedding 召回门控 + 独立单任务 LLM 调用」：召回 top-K 候选、相似度过阈值才 +1 调用，无候选则 0 调用（design §5.3）。
> spec 只定义契约（签名 + 流程 + 阈值决策）；实现以 `nyx/memory/facade.py` 源文件为准。

## 元信息

- **前置依赖**：01-types（`Memory` / `MemoryEdge` / `Event` / `EventType` / `MemoryType` / `Source`）、02-config（`MemoryConfig`：`short_term_capacity` / `promote_threshold` / `freshness_decay`）、03-llm（`LlmClient.complete`）、05-event（`EventBus.publish`）、07-memory-store（`MemoryStore`）、08-memory-retrieval（`MemoryRetrieval` / `EmbedFn` / `rank_by_cosine`）、eval（`Evaluator`）

## 用户故事

> 作为 Nyx 系统的开发者，我想要 `MemoryFacade` 把记忆生命周期的三件事（场景化记忆创建、想起升级、新鲜度淘汰）加上检索委托与导出统一成一个门面，以便表达管道（17）只调 `search` / `create_scene_memory` / `record_recall`、仪表盘只调 `list_memories` / `export`；场景记忆与矛盾检测分离成两次调用，矛盾检测只在召回候选过阈值时才发起（调用数可控、判断单任务），所有 LLM 调用和事件发布都走可注入的 client / bus。

## 验收标准

- [ ] `facade.py` 含 `MemoryFacade` + `decay_freshness` / `_parse_scene` / `_build_scene_prompt` / `_has_negation` / `_content_preview` / `_build_contradiction_prompt` / `_parse_contradiction` / `_join_list` / `_activity_memory_fields` / `_memory_to_dict` / `_memory_to_markdown`（实现见 `nyx/memory/facade.py`）
- [ ] 九个公开方法签名：`create_scene_memory(reply_context: dict[str, str]) -> Memory` / `remember_activity(event: Event) -> None` / `remember_user_profile(content: str, summary: str, aspects: list[str], correlation_id: str) -> None` / `remember_knowledge(items: list[dict[str, str]], correlation_id: str) -> None` / `record_no_answer(question: str, correlation_id: str) -> None` / `search(query) -> list[Memory]` / `record_recall(memory_id) -> None` / `list_memories(tag, type, limit) -> list[Memory]` / `export(fmt) -> str`
- [ ] `create_scene_memory`：LLM 调用 1（`json_mode=True`、`module="memory"`、`output_type="scene_memory"`）产出三样 → 入短期（`freshness=1.0`）→ 算 embedding → 建边 → 矛盾检测（门控，可能调用 2）→ 命中矛盾发布 `reflection` → 衰减+淘汰 → 发布 `memory_created` → 返回 `Memory`
- [ ] `remember_activity(event)`：读 `event.content["type"]`/`["result"]` 确定性映射（reading→note/book、creation→content/title、free_exploration→notes/findings，tag=活动类型值）；读书/创作/探索三类有产出才写，rest/idle_reflection 或空 result 跳过；`observe_user` 走 `_sediment_observation` 画像沉淀分支（presence/window_title 相对上次变化才写 tag='user' 长期记忆）；复用 `_persist_memory` 入库尾段，**无 LLM 调用**（除门控触发的矛盾判断）
- [ ] `remember_knowledge(items, correlation_id)`：读书提取的客观知识点入长期记忆（`tag="knowledge"`、`type=LONG_TERM`、无 LLM、确定性拼好）；items 每项 `{topic, content}`，content 空则跳过；复用 `_persist_memory` 入库尾段（embed → 建边 → 门控矛盾检测 → 淘汰），`type=LONG_TERM` 豁免短期淘汰、知识点不随时间冲掉、供创作检索参考（`list_memories(tag="knowledge")`）
- [ ] 两层去重（`_persist_memory`）：精确（content 哈希命中）→ 语义（embedding 余弦 top-1 ≥ `_DEDUP_SIM_THRESHOLD`）；命中合并强化旧记忆（`strengthen`：`recall_count+1`、`freshness=1.0`），不新建、不发 `memory_created`；未命中才 `add` → 建边 → 门控矛盾检测 → 淘汰 → 发 `memory_created`。embedding 禁用（`embed is None`）时语义去重自动跳过，仅精确去重生效
- [ ] 矛盾检测门控：`embedding=None` 或召回 top-K 候选相似度全低于 `_CONTRADICTION_SIM_THRESHOLD` → **0 次**矛盾 LLM 调用；有候选过阈值 → **1 次**矛盾 LLM 调用（`output_type="contradiction"`），单任务判 `conflicts_with`
- [ ] 两处 LLM 产出后紧跟 `await evaluator.evaluate(output)`：`create_scene_memory`（`output_type="scene_memory"`）与 `_detect_contradiction`（`output_type="contradiction"`，仅门控触发时）
- [ ] 三杠杆落地：候选判据用 `summary + content 前 N 字`（非只 summary）；召回 `_RECALL_TOP_K=5`；新记忆含否定/转折词时矛盾 prompt 附「重点核对」提示（`_has_negation` 纯函数）
- [ ] `record_recall`：`recall_count+1`；短期满 `promote_threshold` 次升级长期 + 发布 `memory_promoted`；长期不重复升级
- [ ] `decay_freshness` 纯函数：线性衰减、下限 0、`now < created_at` 不变
- [ ] `search` / `list_memories` 纯委托（不重写 SQL）；`export` 支持 `json` / `md`，非法 `fmt` → `ValueError`
- [ ] 事件发布遵守「Facade 自己 publish、绝不返回 Event」；事件 `source=INTERNAL`
- [ ] `pyright` strict 零报错

## 技术方案

- **新文件**：`nyx/memory/facade.py`（无 API、无数据变更、无新表）
- **库**：无新库（标准库 `json` / `time` / `uuid` / `typing`）
- **公开面**：`from nyx.memory.facade import MemoryFacade`（不加 `__all__`；`decay_freshness` / `_parse_scene` 等 helper 私有或纯函数，纯函数优先测全）
- **依赖注入**：7 个构造参数（`store` / `retrieval` / `bus` / `llm` / `evaluator` / `config` / `embed`）。这是 Facade 层的 DI 构造器（跨 5 个前置 spec 的注入点），不是「>3 参数就该拆解」的场景——每个都是单一职责的外部依赖。`embed` 与 `retrieval` 共享**同一实例**（组合根 18-api 注入），创建时算 embedding、检索时读 embedding 用同一模型
- **生命周期归 09**：新鲜度衰减、容量淘汰是 09-facade 的生命周期逻辑；短期→长期升级的「何时升」（阈值）在 09、原子「加一 + 条件升型」在 07 的 `record_recall`（单锁，见 07 边界）。本 spec 落这三件事：
  - **升级**：`record_recall` 委托 store 的原子 `record_recall(memory_id, promote_threshold)`（单锁「加一 + 条件升型」，避免跨方法竞态）→ 返回 `True` 则发布 `memory_promoted`
  - **淘汰**：短期数量 > `short_term_capacity` → 挤掉 `freshness` 最低的短期（平局按 `created_at` 早的优先，避免稳定排序误删最新）
  - **衰减**：见下「新鲜度衰减」
- **`create_scene_memory` 流程**（design §5.3）：
  1. `_build_scene_prompt(reply_context)` → `llm.complete(json_mode=True, output_type="scene_memory")` **一次**产出 `{content, tag, summary}`（回归三样，场景记忆调用不再承担矛盾判断）
  2. `_parse_scene` 纯函数解析校验（结构非法 `ValueError`，错误可溯源）
  3. `Memory(id=uuid4, created_at=now, freshness=1.0, type=SHORT_TERM, ...)`；`embed` 非空则算 `embedding` 存列（持久化，同 07/08 决策）
  4. `store.add` → 一次 `_similar` 全表余弦排序（建边 top-3 与矛盾召回 top-5 共用，避免重复扫描）→ `_build_edges` → `_detect_contradiction`（门控，可能 +1 调用；best-effort，失败 log 后跳过 reflection 不反噬创建）→ 命中矛盾 publish `reflection` → `_decay_and_evict` → publish `memory_created`
- **`remember_activity` 流程**（design §8.2/§8.6 活动记忆，落地的确定性写入）：
  1. `_activity_memory_fields(event.content["type"], event.content["result"])` 确定性映射，非读书/创作/探索类型或空 result → `None` 直接 return（不调 LLM、绝不编造）
  2. `Memory(freshness=1.0, type=SHORT_TERM, tag=活动类型值)`；`embed` 非空则算 `embedding`
  3. 复用 `store.add` → `_similar` → `_build_edges` → `_detect_contradiction`（门控，可能 +1 调用）→ `_decay_and_evict` → publish `memory_created`——与 `create_scene_memory` 同一条入库管线，只缺开头的 LLM 场景构建
- **矛盾检测 = 门控 + 独立单任务调用（决策：C 方案，准确率优先，已与用户确认）**：召回候选（embedding 余弦 top-K）做**门控**，判断交给**独立 LLM 调用**——单任务判矛盾，准确率最高，代价是**有条件地 +1 调用**。门控**无损**：矛盾 ⟹ 语义相近（"喜欢猫" vs "讨厌猫"同话题才矛盾），不同话题的旧记忆不可能与新记忆矛盾，所以「相似度过阈值才判断」不损失准确率，只省掉无谓调用。`embedding=None`（未启用向量层）→ 直接跳过
- **三杠杆（决策：B 方案，不增调用，已与用户确认）**：
  1. **候选判据用全文截断**：`_content_preview` 给 `summary + content 前 60 字`（非只 summary），矛盾常藏细节，判据更实 → 漏报↓
  2. **召回 `_RECALL_TOP_K=5`**：比建边的 `_EDGE_TOP_K=3` 大，减少漏召回；两者值不同，故分开常量
  3. **否定词规则预筛**：`_has_negation` 纯函数检测新记忆是否含否定/转折锚点（`不`/`没`/`别`/`讨厌`/`恨`/`拒绝`/`否认`/`放弃`/`再也不` 等），命中则在矛盾 prompt 附「重点核对是否推翻旧记忆」提示，把模型注意力引到最可疑方向。**软信号非判定**：`不`/`没` 高频、会误命中，但只增一句提示、不影响门控，模型自己看内容裁决——误报无害、漏报才有害
- **门控阈值 `_CONTRADICTION_SIM_THRESHOLD=0.6`（决策，可推翻）**：sentence-transformers 余弦同话题中文约 0.6–0.9、不同话题约 0.1–0.4，0.6 作「同话题」分界合理；要调翻一处
- **去重阈值 `_DEDUP_SIM_THRESHOLD=0.95`（决策，可推翻）**：语义去重把「几乎同一句话」合并，取 0.95 严于矛盾阈值 0.6，避免把「同话题不同事实」误合并（矛盾仍靠 0.6 门控单独判断）；精确去重（content 哈希）无条件先跑，语义去重只在 `embed` 可用时补一层
- **建边与矛盾候选复用 `_similar`（跨模块去重）**：`_similar(query_vec, exclude_id)` 是「排除某 id 后、query 向量 vs 全表记忆余弦排序（`s>0` 保留、降序）」的共享 helper；建边取 `[:_EDGE_TOP_K]`、矛盾门控取 `[:_RECALL_TOP_K]` 再 `s >= threshold` 过滤。两处各自调用（余弦 O(N)、本地 ≤ 几百条，代价可忽略，不值得为省这点把 scored 传参破坏两方法内聚）。核心「打分+过滤+排序」循环不在 facade 重写——复用 08 抽出的 `rank_by_cosine` 纯函数（`_similar` 只做 exclude + 委托，与 08 `_vector_search` 同一份实现，facade 不再直接 import `cosine`）
- **`reply_context` 契约**：`dict[str, str]`，键 `correlation_id`（溯源）/ `user_message`（用户说了什么）/ `nyx_think`（尼克斯内心）/ `nyx_speak`（尼克斯说了什么）——由 17-expression 慢通道填充。缺键 `KeyError`（fail-fast，契约违反立即暴露，不静默降级）
- **建边机制（决策，可推翻）**：新记忆与已有记忆按 `embedding` 余弦相似度建边，`_EDGE_TOP_K=3`、`weight=相似度`、`s > 0` 才建；`embed=None` 或新记忆无 embedding → 跳过。方向 `new → old`，`MemoryGraph` 无向所以方向无关
- **新鲜度衰减（决策，可推翻）**：纯函数 `decay_freshness(freshness, created_at, now, rate) = max(0, freshness - rate × elapsed_days)`，`rate` 单位「/天」（`SECONDS_PER_DAY=86400.0`，共享常量见 events/event.py；02-config 的 `freshness_decay=0.01` 未标单位，此处定为「0.01/天」，要改单位翻 events/event.py 一处）。触发点 = `create_scene_memory` 的 `_decay_and_evict` 扫描：读全表 → 收集变化 → 一次 `store.update_many` 批量回写 → 短期满则一次 `store.delete_many` 批量挤掉最低新鲜度（平局按 `created_at` 早的优先）。**局限**：两次创建之间新鲜度不变；但衰减单调（越旧越衰减），相对顺序保持，「长期只新鲜度下降、检索排后」的语义不破坏。2 次 commit/次创建（N 条衰减不再 N 次 commit），本地单用户 ≤ 几百条记忆，可接受
- **事件 content（tech-ref §4 未定义这两者的 SSE payload，最小化）**：`memory_created` / `memory_promoted` = `{"memory_id": id}`；`reflection` = `{"summary": str}`（含冲突双方 id 的可溯源字符串）。SSE 完整 payload 形状归 18-api/frontend 细化
- **`record_recall` 的 `correlation_id`（已知局限）**：tech-ref 签名只有 `record_recall(memory_id)`，无上游 correlation，故 `memory_promoted` 的 `correlation_id = memory_id`（溯源到记忆本身，与触发它的 reply 因果链在此断开）。`memory_created` / `reflection` 用 `reply_context["correlation_id"]` 接上 reply 链
- **`search` / `list_memories` 纯委托**：不重写 SQL、不做二次过滤；衰减/淘汰已在写入侧处理
- **新增 `output_type="contradiction"`**：`LLMOutput.type` 自由字符串，开放集合新增无冲突

## 测试要点

- [ ] 单元测试 `tests/test_memory/test_facade.py`（`pytest-asyncio`；`db = await connect(":memory:")`；`store = MemoryStore(db)`；`retrieval = MemoryRetrieval(store, embed)`；fake `LlmClient.complete` 按 `output_type` 分支返回 fixture 并记录调用（`scene_memory` → 三样 JSON、`contradiction` → `conflicts_with` JSON）；fake `Evaluator`（记录 `evaluate` 调用）；`EventBus` 用真实例 + 订阅 recording handler，`run()` 作 task 驱动——同 05-event 模式）：
  - [ ] **纯函数**：
    - [ ] `decay_freshness`：`now == created_at` → 不变；1 天后 → `freshness - rate`；`now < created_at` → 不变；衰减到负 → 夹到 0
    - [ ] `_parse_scene`：合法 JSON → 3 元组；缺 `tag` → `ValueError`；空串 → `ValueError`；JSON 是数组 → `ValueError`
    - [ ] `_build_scene_prompt`：含三输入（`user_message`/`nyx_think`/`nyx_speak`）；缺键 → `KeyError`
    - [ ] `_has_negation`：`"我不喜欢猫"` → `True`；`"我喜欢猫"` → `False`
    - [ ] `_content_preview`：`content` 短于 60 字 → 不截断、含 `summary`；长于 60 字 → 截到 60 字 + `…`
    - [ ] `_build_contradiction_prompt`：含新记忆 `content` + 候选 `id` + 候选预览；新记忆含否定词 → 含「重点核对」句；不含否定词 → 无该句
    - [ ] `_parse_contradiction`：`conflicts_with` 字符串 → 该串；`null` → `None`；数字 → `ValueError`；缺 `conflicts_with` 键 → `None`
    - [ ] `_memory_to_dict`：`type` 是 `.value` 字符串、`embedding` 透传
    - [ ] `_memory_to_markdown`：含 `summary` 与 `content`
    - [ ] `_join_list`：`str` 原样、`list` 换行拼接、空 `list`/`None`/非 str-list → `""`
    - [ ] `_activity_memory_fields`：reading→`(note, book)`、creation→`(content, title)`、free_exploration→`(notes 拼接, findings 拼接)`；非目标类型/空 result/空内容/类型非 str → `None`；summary 超 80 字截断
  - [ ] **create_scene_memory**：
    - [ ] fake LLM 返回 `{"content","tag","summary"}` → 返回 `Memory` 各字段正确（`content`/`tag`/`summary`、`freshness==1.0`、`type is SHORT_TERM`、`embedding` 已算且 = fake embed(content)）；`evaluator.evaluate` 被调 1 次（收到 `output_type="scene_memory"` 的 `LLMOutput`）
    - [ ] 发布 `memory_created`：`content["memory_id"] == memory.id`、`source is INTERNAL`、`correlation_id == reply_context["correlation_id"]`
    - [ ] **矛盾检测门控**：
      - [ ] `embed=None` → 仅 1 次 LLM 调用（`scene_memory`），无 `contradiction` 调用，无 `reflection`
      - [ ] 有 embedding 但旧记忆相似度 < 阈值（fake embed 造正交向量）→ 无 `contradiction` 调用，无 `reflection`
      - [ ] 有候选过阈值（fake embed 造相同/高相似向量）→ 第 2 次调用 `output_type="contradiction"`；fake LLM 返回 `conflicts_with=<旧记忆id>` → 发布 `reflection`（`content["summary"]` 含冲突双方 id）；第 2 次 `complete` 后 `evaluator.evaluate` 再被调 1 次（`output_type="contradiction"`）
      - [ ] contradiction 返回 `null` → 不发 `reflection`
    - [ ] **三杠杆落地**：矛盾 prompt 含候选 `content` 截断预览（非只 summary）；召回 `_RECALL_TOP_K=5`（造 5 条高相似旧记忆 → 矛盾 prompt 候选 ≤ 5）；新记忆含否定词 → prompt 含「重点核对」句
    - [ ] **建边**：先建一条含 embedding 的记忆，再 create 一条相似 embedding 的记忆 → 新记忆有到旧记忆的 `memory_edge`（`weight > 0`）
    - [ ] **淘汰**：`MemoryConfig(short_term_capacity=1, ...)`，create 第二条 → 旧的那条（freshness 更低）被删，`list_memories()` 只剩新的一条
    - [ ] **衰减回写**：monkeypatch `time.time` 使两条创建间隔 1 天 → 旧记忆的 `freshness` 被衰减（`< 1.0`）
  - [ ] **去重（`_persist_memory`）**：
    - [ ] 精确去重：同 content 二次 `create_scene_memory` → 库内 1 条、`recall_count==1`、仅 1 个 `memory_created`
    - [ ] 语义去重：新记忆与旧记忆 embedding 余弦 = 1.0（≥ 0.95）→ 合并到旧记忆（`recall_count+1`）、不新增、无 `memory_created`
    - [ ] 阈值以下：余弦 < 0.95 → 正常新建入库（`list_memories` 2 条、发 1 个 `memory_created`）
    - [ ] `embed=None`：语义去重跳过（即使库里旧记忆带 embedding，新记忆无 embedding 也不做语义比较），仅精确去重生效
  - [ ] **remember_activity**：
    - [ ] reading/creation/free_exploration 三类 mock `activity_end` 事件 → 各写一条 `Memory`（`content`/`summary`/`tag` 正确、`type is SHORT_TERM`）、发布 `memory_created`、**无 LLM 调用**（`llm.calls == []`）
    - [ ] rest/idle_reflection 或空 result → 不写、无 `memory_created`；observe_user 无 presence → 不写
    - [ ] observe_user 带 presence → 沉淀一条 tag='user' 的长期画像记忆（`type is LONG_TERM`）；同快照重复上报 → 不重复写
    - [ ] 有相似旧记忆 + embed（fake embed 造高相似向量）→ 门控触发 1 次 `contradiction` 调用（参与矛盾判断，无 `scene_memory`）
  - [ ] **search / list_memories**：`search` 委托 fake `MemoryRetrieval`（返回预设 list）；`list_memories(tag, type)` 委托真 store（过滤/排序同 07）
  - [ ] **record_recall**：
    - [ ] 未达 `promote_threshold` → `recall_count` 递增、`type` 仍 `SHORT_TERM`、无 `memory_promoted`
    - [ ] 达阈值 → `type is LONG_TERM` + 发布一条 `memory_promoted`
    - [ ] 已是 `LONG_TERM` → 只 `recall_count` 递增，不再发布
  - [ ] **export**：`export("json")` → `json.loads` 能还原出记忆列表（含 `type` 字符串）；`export("md")` → 含某条记忆的 `summary`/`content`；`export("csv")` → `ValueError`
- [ ] 集成测试：无（Facade 的 LLM 全 mock、DB 用 `:memory:`；与表达管道的真实编排归 17）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] 17-expression 慢通道调 `create_scene_memory` / `record_recall` / `search`，不各自调 store/retrieval；18-api 组合根构建 `embed`（按 `config.embedding.model`）→ `retrieval` → `facade` 并注入 `store` / `bus` / `llm` / `evaluator` / `config`；矛盾检测是门控独立调用（无候选 0 调用、有候选过阈值 1 调用）
