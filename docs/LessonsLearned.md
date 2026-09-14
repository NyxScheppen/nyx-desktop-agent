# Lessons Learned

> Nyx Agent 项目经验教训汇总。每次踩坑后追加条目，格式见底部模板。

## 2026-08-24: 全量代码审查（起点 commit 5a319a9，共 33 条 finding）

> 本次为「后端 + 前端」全量审查，重点 bug + 冗余简化。17 条经代码改动修复（3 个 commit），
> #19-22 接受为 MVP 现状，#26-27 延期 V3。下面按根因主题提炼，不逐条罗列。

### 2026-08-24: 派生标签先分桶再格式化，别用原始值直接拼

**来源**：14-activity `_schedule_block_id` PAUSED 恢复后时间线错位 Bug（#2）
**教训**：时间线/网格标签本应从「第几个格子」推出，却用 `now % 一天秒数` 的原始余数直接格式化，产生 14:37 这类非整点标签，恢复时错位。
**怎么做**：任何「桶/格/周期」标签，先 `block_index = int(now % SECONDS_PER_DAY) // 60 // grid_minutes` 分桶，再 `format_time_label(block_index, ...)` 格式化——标签必须只依赖格序号，不依赖格内偏移。
**影响的文件/决策**：`nyx/activity/facade.py:_schedule_block_id`

### 2026-08-24: LLM 调用限量分块，工具结果注入 prompt 要封顶

**来源**：知识提取无分块（#1）、工具输出注入 prompt 无上限（#6）
**教训**：对无界/不可信输入直接喂 LLM，或把工具结果原样拼进 prompt，都会失控。
**怎么做**：LLM 提取按字符分块（`_READ_CONTEXT_CHARS=6000`）+ 上限（`_KNOWLEDGE_MAX_CHUNKS=16`/`_KNOWLEDGE_MAX_POINTS=5`）；工具结果截断到 `_TOOL_OUTPUT_MAX_CHARS=4000` 再注入。空 chunk 分支也要走同一条提取路径（#5）。
**影响的文件/决策**：`nyx/activity/facade.py:_extract_knowledge`、`nyx/expression/pipeline.py`

### 2026-08-24: upsert 要保留主键，否则关联数据被孤儿化

**来源**：读书笔记按 path 重存丢标注 Bug（#3）
**教训**：按 path 去重 upsert 时若用 INSERT OR REPLACE 会换 note id，外键关联的 annotations 全丢。
**怎么做**：`upsert_by_path` 先 SELECT by path，命中则 UPDATE 复用原 id，annotation 才存活；文件名用 `_path_hash_suffix(source)`（md5[:8]）后缀。
**影响的文件/决策**：`nyx/activity/reading_note_store.py`、`nyx/db.py`（migration v5 加 `path` 列）、`nyx/types.py`

### 2026-08-24: 配置字段必须真接线，secrets 走 env 名 + 校验强制

**来源**：LLM timeout/max_retries 配了没用（#7）、vision 硬编码 env 名且非 ollama 缺 key 不报错（#17）
**教训**：config dataclass 字段写出来不等于被用；secret 读哪个环境变量应可配置并在校验时强制，而非硬编码 env 名。
**怎么做**：client 传 `timeout=config.timeout, max_retries=config.max_retries`；vision 读 `os.environ.get(config.api_key_env)`，非 ollama 无 key 抛 `ConfigError`。
**影响的文件/决策**：`nyx/llm/client.py`、`nyx/llm/vision.py`、`nyx/config.py`

### 2026-08-24: I/O 流式/chunked，网络客户端必设 timeout

**来源**：文件上传整读进内存（#23）、web_search 的 `DDGS()` 默认无超时（#8）
**教训**：`await file.read()` 无界读、网络库默认 timeout，都会挂死或爆内存。
**怎么做**：上传 `while chunk := await file.read(1 << 20)` 分块读 + 早 400；`DDGS(timeout=10)` 且 try/except 返 `[]`（best-effort 豁免）。
**影响的文件/决策**：`nyx/main.py:api_upload`、`nyx/tools/web_search.py`

### 2026-08-24: 快慢通道必须发同样的信号，fast path 不能悄悄丢

**来源**：快通道问句绕过 should_ask 丢 ASK 信号（#12）
**教训**：快通道为省时跳过了慢通道的发信号节点，问句结尾的 ASK 事件没发，下游监听方拿不到信号。
**怎么做**：fast 分支自己判 `_is_question(speak)` → 发 ASK 并 `ask: speak`；慢通道专属 guidance 抽 `_ask_guidance_for(mode, ...)` 只在 SLOW 注入（#28）。
**影响的文件/决策**：`nyx/expression/pipeline.py`

### 2026-08-24: 异步回包防乱序，用 request 序号守卫

**来源**：标注并发请求结果乱序覆盖 Bug（#11）
**教训**：多个 async 请求并发，晚回的旧结果会覆盖早发的新结果。
**怎么做**：`annoRequest = useRef(0)` 每次请求自增，回调里比对序号，非最新则丢弃；变更后 `await refresh()` 重新拉取（#13）。
**影响的文件/决策**：`frontend/src/components/panels/ReadingNotesPanel.tsx`

### 2026-08-24: 数值入评分前先 isfinite 校验

**来源**：评分出现 NaN/Infinity（#16）
**教训**：情绪强度等浮点可能算出 NaN/Infinity，直接进评分污染结果。
**怎么做**：`math.isfinite(value)` 不过则 score=0.0。
**影响的文件/决策**：`nyx/eval/judge.py`

### 2026-08-24: 死代码/未用字段定期清（本轮清理 #24/#29/#30/#31/#32/#33）

**来源**：全量审查 low 档死代码清理
**教训**：store 单方法（`update`/`delete`）被批量版取代后残留、TypedDict 未用字段（`waiting_user`）、组件未用 prop（`placeholder`）、6 个 store 未用字段（`loading`）、union 未用成员（`"small"`）都长期残留。
**怎么做**：改完删自己造成的 orphan；类型 union 加成员前先查调用方；字段写出来但没人读，要么接线要么删。
**影响的文件/决策**：`nyx/memory/store.py`、`nyx/expression/pipeline.py`、`frontend/src/components/layout/Panel.tsx`、`frontend/src/stores/*.ts`、`frontend/src/components/inner/EmotionSprite.tsx`

### 2026-08-24: 已接受的取舍与延期项（决策留档）

- **#19-22 接受为 MVP 现状**：retrieval 三层读非原子（见 memory [[retrieval-non-atomic-reads]]）、bus `run()` 需 supervisor 重启（见 [[bus-run-supervisor]]）等，均有对应 memory 条目。
- **#26-27 延期 V3**：`LongTermDesire.strength` 写而未读、慢通道判定相关字段，待 V3 再做接线或删除。

### 2026-09-13: 替换评分公式时删掉旧过滤语义

**来源**：记忆召回重构终审发现 `_vector_scores()` 仍保留旧的 `cosine > 0` 过滤，导致 ANN 返回的零余弦候选被丢弃，违背新 spec 的 bounded candidate 语义。
**教训**：重构排序/检索公式时，旧实现里的 guard、filter、fallback 往往也是契约；如果新 spec 没明确保留，就必须逐个移除或改写，而不是只换主公式。
**怎么做**：审查召回、排序、候选池、source 标注类改动前，先查本文件是否有类似“旧 guard 残留”教训；对边界分数（0、阈值等于、负值）、fallback 补足和 sources 加回归测试。
**影响的文件/决策**：`nyx/memory/retrieval.py`、`tests/test_memory/test_retrieval.py`、`docs/specs/07-memory-system.md`

### 2026-09-14: checkpoint 字段要区分阶段产物和终局产物

**来源**：活动状态机重构自审发现读书 checkpoint 曾准备用 `note` 同时表示“本块笔记”和“整本聚合笔记”，会导致恢复 finalize 时把片段笔记当完整笔记写入结果。
**教训**：可续状态机里，同名字段跨阶段复用会制造隐性语义漂移；测试只看“跳过重复副作用”还不够，还要看恢复后返回的是哪个阶段的真实产物。
**怎么做**：设计 checkpoint 时给中间产物和终局产物不同字段，例如 `note` vs `final_note`；为 finalized resume 增加“不重复副作用 + 返回终局产物”的回归测试，并同步事实表/spec/test-inventory。
**影响的文件/决策**：`nyx/activity/reading_runner.py`、`tests/test_activity/test_reading_runner.py`、`docs/specs/14-activity.md`

### 2026-09-14: 声明式路由必须和运行时注册共用单一来源

**来源**：模块与事件总线架构审查发现 `ROUTING`/`TICK_ROUTING` 只被文档和测试读取，实际订阅仍由 `subscriptions.py` 手写，新增或修改事件时两处可以静默漂移。
**教训**：路由表如果只是“说明数据”，就不能宣称它是运行时契约；仅测试 key 集合和模块名合法性，无法证明真实 handler 拓扑一致。
**怎么做**：让运行时订阅从同一个注册表派生，或在启动时对声明路由与实际 handler 做强校验；新增事件必须同时覆盖路由、订阅和失败策略测试。
**影响的文件/决策**：`nyx/events/routing.py`、`nyx/subscriptions.py`、`nyx/main.py`、`tests/test_api/test_subscription.py`

### 2026-09-14: 事件落库成功不等于模块副作用成功

**来源**：模块与事件总线架构审查发现总线在持久化后顺序执行 handler，handler 异常只记录日志并继续；事件不会重放，跨模块 `ACTIVITY_END` 等更新可能部分成功。
**教训**：event log 只能证明事件被记录，不能证明所有消费者都完成；“persist → dispatch”若没有消费状态、重试或幂等策略，会把局部失败变成静默不一致。
**怎么做**：为关键事件明确至少一次/至多一次语义；为消费者保留可重试的投递记录或幂等键；关停时排空队列，handler 失败要能被监控和补偿，而不是只依赖日志。
**影响的文件/决策**：`nyx/events/bus.py`、`nyx/subscriptions.py`、`docs/specs/05-module-bus-system.md`

### 2026-09-14: 事务回滚不能自动恢复进程内派生状态

**来源**：模块总线重构中 `inner_life` durable consumer 的反思/情绪事务测试
**教训**：数据库事务回滚只能撤销 SQLite 行；情感数值、时间锚点等进程内状态已经在事务中改变时，若派生事件追加失败，数据库虽回滚，内存仍会残留半次消费，重放会得到错误结果。
**怎么做**：凡是把内存快照与数据库写入放进同一业务事务，进入事务前保存可恢复快照；异常路径先恢复快照再让事务回滚。为派生事件写入失败增加回归测试，并检查重放是否只产生一次结果。
**影响的文件/决策**：`nyx/inner_life/facade.py`、`tests/test_inner_life/test_inner_life_facade.py`

### 2026-09-14: 消费者重放要先识别已经产生的终局事件

**来源**：模块总线重构中 `USER_MESSAGE` 消费者的重放测试
**教训**：消费 handler 可能在业务逻辑已完成、但 delivery 成功标记尚未提交前崩溃；仅依赖 delivery 状态会再次调用 LLM、打断活动或写入会话历史。
**怎么做**：对能产生可识别终局事件的消费者，重放入口先按 correlation 查询终局事件并短路；同时仍需为没有终局事件的中途失败保留重试路径，不能把任意中间事件当完成标记。
**影响的文件/决策**：`nyx/runtime.py`、`tests/test_api/test_subscription.py`

---

## 模板

```
### YYYY-MM-DD: [一句话教训]

**来源**：[哪个功能 / 哪个 Bug / 哪次评审]
**教训**：[发生了什么、根因是什么]
**怎么做**：[从此以后怎么做、检查什么]
**影响的文件/决策**：[如果改了什么，列出来]
```
