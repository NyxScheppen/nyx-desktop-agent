# Lessons Learned

> Nyx Agent 项目经验教训汇总。每次踩坑后追加条目，格式见底部模板。

### 2026-09-19: 启动器必须阻止第二个后端共享 SQLite

**来源**：双击 `start_nyx.bat` 重启后，旧 backend 未随窗口关闭而退出；新 backend
同时打开 `nyx.db`，其 SQLite 操作持锁等待，并伴随
8000 端口 `WinError 10048`。
**教训**：开发 launcher 只在桌面配对模式检查端口是不够的；普通 Vite 模式也必须在创建
任何子进程前识别第二个 backend：旧 Nyx launcher 先接管并结束，未知进程则拒绝启动。
否则重复启动会把跨进程 SQLite 锁竞争误诊为应用故障。
**怎么做**：`dev.py` 的所有服务模式先按 PID 文件校验并结束上一次由本项目启动的
backend，再做 127.0.0.1:8000 预绑定检查；占用时不创建 backend 或 frontend，并提示先
关闭已有 Nyx 后端。PID 不匹配 Nyx 命令时不强杀，避免误伤复用该 PID 的其他进程。
**影响的文件/决策**：`dev.py`、`tests/test_launcher.py`

### 2026-09-20: SSE effect 替换时不要保留旧连接的 closed 状态

**来源**：Vite/React 开发期 effect 重放导致前端显示“已断开”。
**教训**：旧 `EventSource` 的 cleanup 会在新连接尚未 `open` 时先写入 `closed`；若新 effect 不立即声明连接正在建立，UI 会被旧生命周期状态卡住。
**怎么做**：每次 `useSSE` effect 建立新 `EventSource` 前先设置 `connecting`；继续让浏览器原生 EventSource 负责重连，不新增手写循环。回归覆盖 effect 替换、初次失败和恢复。
**影响的文件/决策**：`frontend/src/hooks/useSSE.ts`、`frontend/tests/sse.test.ts`

### 2026-09-20: 端口预检查必须和启动流程原子串行化

**来源**：两次双击启动几乎同时执行时，两个 launcher 都能在 backend 绑定 8000 之前通过
端口预绑定检查，随后各自启动 `nyx.main`，重新造成 SQLite 跨进程锁竞争。
**教训**：PID 清理和端口检查如果分开执行，仍存在“检查通过但尚未启动”的竞态窗口；单靠
端口占用不能实现启动器单实例。
**怎么做**：使用项目目录内的原子 lock 文件串行化清理、端口检查和子进程创建；发现仍存活
的旧 `dev.py` 时先结束其进程树，再由新 launcher 接管；旧 owner 退出后才回收 lock。lock
owner 不存在时回收 stale lock，未知程序占用端口时仍拒绝启动。
**影响的文件/决策**：`dev.py`、`.gitignore`、`tests/test_launcher.py`

### 2026-09-19: Windows venv wrapper 不能被 launcher 当成旧实例杀掉

**来源**：双击 `start_nyx.bat` 时启动器无日志直接退出并留下 `.nyx-launcher.lock`；真实进程树
包含 venv wrapper Python 与实际 Python 两层，扫描结果把当前启动链的 wrapper 误判成旧 launcher。
旧进程接管后还可能短暂持有 lock 文件，立即删除会触发 `WinError 32`。
**教训**：按命令行匹配进程不足以区分“当前启动链的父 wrapper”和“另一个旧 launcher”；结束进程
成功也不等于 Windows 文件句柄已经释放。
**怎么做**：扫描 PID 时读取父子关系并排除当前 Python 的全部祖先进程；结束旧 owner 后，在有限时限内
重试删除 lock 文件，超时才失败。回归必须覆盖 wrapper ancestor 和 lock handle 短暂不可删除。
**影响的文件/决策**：`dev.py`、`tests/test_launcher.py`

## 2026-08-24: 全量代码审查（起点 commit 5a319a9，共 33 条 finding）

> 本次为「后端 + 前端」全量审查，重点 bug + 冗余简化。17 条经代码改动修复（3 个 commit），
> #19-22 接受为 MVP 现状，#26-27 延期 V3。下面按根因主题提炼，不逐条罗列。

### 2026-08-24: 派生标签先分桶再格式化，别用原始值直接拼

**来源**：09-activity `_schedule_block_id` PAUSED 恢复后时间线错位 Bug（#2）
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
**影响的文件/决策**：`nyx/memory/retrieval.py`、`tests/test_memory/test_retrieval.py`、`docs/specs/06-memory-system.md`

### 2026-09-14: checkpoint 字段要区分阶段产物和终局产物

**来源**：活动状态机重构自审发现读书 checkpoint 曾准备用 `note` 同时表示“本块笔记”和“整本聚合笔记”，会导致恢复 finalize 时把片段笔记当完整笔记写入结果。
**教训**：可续状态机里，同名字段跨阶段复用会制造隐性语义漂移；测试只看“跳过重复副作用”还不够，还要看恢复后返回的是哪个阶段的真实产物。
**怎么做**：设计 checkpoint 时给中间产物和终局产物不同字段，例如 `note` vs `final_note`；为 finalized resume 增加“不重复副作用 + 返回终局产物”的回归测试，并同步事实表/spec/test-inventory。
**影响的文件/决策**：`nyx/activity/reading_runner.py`、`tests/test_activity/test_reading_runner.py`、`docs/specs/09-activity.md`

### 2026-09-14: 声明式路由必须和运行时注册共用单一来源

**来源**：模块与事件总线架构审查发现 `ROUTING`/`TICK_ROUTING` 只被文档和测试读取，实际订阅仍由 `subscriptions.py` 手写，新增或修改事件时两处可以静默漂移。
**教训**：路由表如果只是“说明数据”，就不能宣称它是运行时契约；仅测试 key 集合和模块名合法性，无法证明真实 handler 拓扑一致。
**怎么做**：让运行时订阅从同一个注册表派生，或在启动时对声明路由与实际 handler 做强校验；新增事件必须同时覆盖路由、订阅和失败策略测试。
**影响的文件/决策**：`nyx/events/routing.py`、`nyx/subscriptions.py`、`nyx/main.py`、`tests/test_api/test_subscription.py`

### 2026-09-14: 事件落库成功不等于模块副作用成功

**来源**：模块与事件总线架构审查发现总线在持久化后顺序执行 handler，handler 异常只记录日志并继续；事件不会重放，跨模块 `ACTIVITY_END` 等更新可能部分成功。
**教训**：event log 只能证明事件被记录，不能证明所有消费者都完成；“persist → dispatch”若没有消费状态、重试或幂等策略，会把局部失败变成静默不一致。
**怎么做**：为关键事件明确至少一次/至多一次语义；为消费者保留可重试的投递记录或幂等键；关停时排空队列，handler 失败要能被监控和补偿，而不是只依赖日志。
**影响的文件/决策**：`nyx/events/bus.py`、`nyx/subscriptions.py`、`docs/specs/04-module-bus-system.md`

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

### 2026-09-14: 外部思考必须先完成，事务只提交确定性结果

**来源**：内在生命反思重构审查
**教训**：把 LLM 调用放在 durable consumer 的 SQLite 事务里，会长时间占用数据库锁；解析失败若被吞掉，还会让 delivery 被误标记为成功，导致反思永久丢失。
**怎么做**：把读取上下文、LLM、评估和解析放到事务外，生成可提交计划；事务内只写已解析的本地状态、effect marker 和终局事件。解析失败或本地提交失败必须抛出，让 delivery 进入 retry_wait，成功消费者才写入 effect marker。
**影响的文件/决策**：`nyx/inner_life/reflection.py`、`nyx/inner_life/facade.py`、`nyx/events/bus.py`

### 2026-09-14: 下游事件成功前不要清理上游瞬态产物

**来源**：阅读重读反思事件链审查
**教训**：记忆已经落库不代表后续 `REFLECTION` 事件已经被总线受理；若先清空 buffer、再发布事件，事件受理失败会让后续反思触发永久丢失。
**怎么做**：把所有必需的下游事件发布放在瞬态快照清理之前；任一步失败都保留快照，允许边界重试。对于可重复的落库步骤依靠内容/语义去重，避免重试造成重复记忆。
**影响的文件/决策**：`nyx/reading/integration.py`、`docs/specs/12-reading-system.md`

### 2026-09-15: 聚合状态禁止分离式读改写

**来源**：欲望产生/消费系统审查
**教训**：`run_eval`、事件加压、满足结算和活动领取都采用「先读快照、稍后更新」；它们由不同 durable consumer 或直接调用方并发执行时，后写入的旧快照会覆盖新压力、进度或状态，甚至重复发布满足事件。
**怎么做**：对同一聚合状态使用单一串行入口，或在数据库事务内用条件更新/增量 SQL 完成领取和结算；所有并发路径都要有 lost-update、重复终态事件和 goal 进度丢失测试。
**影响的文件/决策**：`nyx/desire/lifecycle.py`、`nyx/desire/facade.py`、`nyx/activity/lifecycle.py`

### 2026-09-15: 业务完成与唤醒广播失败必须分层处理

**来源**：欲望消费与活动生命周期审查
**教训**：本地业务事务已经提交后，`announce_committed`/唤醒失败不等于业务执行失败；若外层统一进入失败收尾，会把已完成活动改成 `INCOMPLETE`，或把已满足欲望重新抑制。
**怎么做**：把「本地事实提交」「事件已持久化」「内存 worker 已被唤醒」分成独立结果；提交后的广播失败只记录并依靠启动扫描/重试恢复，不能再次执行反向业务状态转换。
**影响的文件/决策**：`nyx/activity/lifecycle.py`、`nyx/activity/facade.py`、`nyx/events/bus.py`

### 2026-09-15: 主动副作用必须晚于成功承诺

**来源**：表达系统主动搭话审查
**教训**：主动搭话在 LLM 产出和事件发布前就打断当前活动；若生成失败、事件受理失败或调用中途崩溃，活动已经被改变但搭话并未成立。
**怎么做**：先完成可重试的外部生成，再用一个可证明成功的本地事务/事件承诺记录主动行为，最后才执行活动打断等派生副作用；失败路径不得留下“已打断但没有主动行为”的状态。
**影响的文件/决策**：`nyx/runtime.py`、`nyx/expression/facade.py`、`nyx/activity/`

### 2026-09-15: 等待用户回应的状态不能只放进程内存

**来源**：表达系统提问与主动搭话审查
**教训**：`_waiting_user`、`_pending_chat_desire_id`、`_ask_at`、`_chat_at` 和 `last_chat_at` 都是进程内状态；重启或多实例后会丢失等待关系，导致未回答记忆不落库、互动欲不超时回灌或主动搭话冷却失效。
**怎么做**：需要跨 tick、重试或重启维持的等待关系持久化为可恢复状态，并以 correlation/desire id 做幂等键；超时结算与状态清理必须在同一业务事务中完成。
**影响的文件/决策**：`nyx/expression/facade.py`、`nyx/runtime.py`、`nyx/app_context.py`、事件/数据库契约

### 2026-09-15: 共享资源消费要先原子领取再执行

**来源**：表达系统主动搭话审查
**教训**：主动搭话从 `get_pending()` 读取互动欲望后直接调用 LLM，未原子领取；并发 tick、重放或其他消费者可能同时使用同一条欲望，产生重复搭话或与活动消费冲突。
**怎么做**：将欲望消费统一为条件更新的 claim；只有 claim 成功的调用方才生成和发送主动行为，失败调用方必须退出，超时/失败再按明确状态释放或结算。
**影响的文件/决策**：`nyx/runtime.py`、`nyx/desire/facade.py`、`nyx/expression/facade.py`

### 2026-09-15: 解析失败不能伪装成成功产出

**来源**：表达系统回复流程审查
**教训**：`_parse_reply` 失败后把原始输出直接当作 `speak`；空输出甚至会发布空 `SPEAK`，并记录为一次已完成回复，导致提问信号、历史和重试语义失真。
**怎么做**：解析失败或空产出必须进入明确失败/重试路径；只有结构合法且内容非空时才发布 `SPEAK`/`ASK`、写会话历史和结束本次消费。
**影响的文件/决策**：`nyx/expression/pipeline.py`、`nyx/expression/facade.py`、`nyx/events/`

### 2026-09-16: 写后回读的可空内部契约必须在公开边界显式收窄

**来源**：阅读进度 CAS 重构的 `pyright` 检查
**教训**：内部查询函数为了支持“写前不存在”的分支而返回 `T | None` 时，写入函数不能直接把它返回为公开的 `T`；数据库理论上应已写入，不等于类型系统可以替调用方证明这一点。
**怎么做**：写入提交后显式检查回读结果，空值抛出明确的内部一致性错误，再返回非空值；同时保留针对写后回读缺失的回归路径。
**影响的文件/决策**：`nyx/reading/store.py`、所有带写后回读的 store 写路径

---

### 2026-09-16: 移动文档后必须按新目录重新解析相对链接

**来源**：事实摘要统一迁移到 `docs/facts/`
**教训**：Markdown 文件移动到子目录后，文件内部相对链接的基准目录也会改变；只更新引用方而不检查被移动文件自身，会留下表面存在但实际失效的链接。
**怎么做**：文档移动后同时检查移动文件内部链接、所有引用方和删除文件引用；使用脚本按每个 Markdown 文件所在目录解析相对链接，并排除已删除文件和外部依赖目录。
**影响的文件/决策**：`docs/facts/`、`docs/specs/`、`docs/tech-reference.md`、`AGENTS.md`、`CLAUDE.md`

---

### 2026-09-17: 有界候选必须先过滤资格再截断并复用索引

**来源**：记忆 topics 联想漏召回与持久化 ANN 重复构建修复
**教训**：先截取 top-K 再排除 direct 或 kind 不匹配项，会让有效的第 K+1 条永远没有机会进入候选；同一请求按不同范围取候选时重复构建相同底库的 ANN，也会把写入成本放大。
**怎么做**：资格过滤进入候选生成过程，在计数达到 K 前跳过不可选项；同一份底库只构建一次索引，通过查询级过滤分别取得局部和全局候选。共享 topic 桶按请求缓存排序结果，不随 seed 重复排序。
**影响的文件/决策**：`nyx/memory/ann.py`、`nyx/memory/retrieval.py`、`nyx/memory/facade.py`

---

### 2026-09-17: durable claim 必须在外部结算成功后再完成

**来源**：表达 interaction attempt 回复结算修复
**教训**：先把 claim 永久写成完成态，再执行另一个模块的结算，会让 delivery 重试失去重新领取入口，留下无法自行恢复的 ACTIVE 状态。
**怎么做**：状态顺序固定为 `WAITING -> CLAIMED -> 外部结算 -> ANSWERED/EXPIRED`；结算或完成写入失败都释放 claim，并用失败注入测试验证下一次仍能领取。
**影响的文件/决策**：`nyx/expression/facade.py`、`expression_interaction_attempt` 状态机

### 2026-09-17: durable handler 的中间提交也需要事件级阶段标记

**来源**：`DESIRE_EVAL` 重放重复加压修复
**教训**：最终产出幂等不代表整个 handler 幂等；LLM 前已经提交的衰减、压力或状态释放，在后续失败重放时仍会重复应用。
**怎么做**：对不可与最终结果放进同一事务的中间阶段，按上游 event id 保存阶段 marker，并与该阶段状态变更同事务提交；重放跳过已完成阶段但继续未完成阶段。
**影响的文件/决策**：`desire_eval_applied`、`nyx/desire/lifecycle.py`、durable tick 消费

### 2026-09-17: 提交后再启动的后台任务必须恢复提交前状态

**来源**：活动 PENDING 崩溃窗口修复
**教训**：数据库先写 PENDING、提交后才创建内存 task 时，进程可能停在两者之间；只恢复 RUNNING 会永久遗留 PENDING 记录和已领取资源。
**怎么做**：启动恢复同时扫描 PENDING/RUNNING；从未真正启动的 PENDING 记为 ABANDONED 并释放 claim，已运行记录再按可续性暂停或放弃。
**影响的文件/决策**：`ActivityStore.list_unfinished()`、`ActivityLifecycle.recover_stale_running()`

### 2026-09-17: supervisor 必须区分正常返回与异常重启

**来源**：EventBus 正常关停忙循环修复
**教训**：只在异常分支退避、正常返回后无条件重入的 supervisor，会在被监督对象关闭后形成无 await 忙循环，使关停无法完成。
**怎么做**：正常返回代表生命周期结束并立即退出；只有明确异常才执行带上限和退避的重启。状态 finalize 的持久化失败则在 worker 内重试，不伪装成成功。
**影响的文件/决策**：`nyx/runtime.py:supervise_bus`、`nyx/events/bus.py`

---

### 2026-09-17: Windows 批处理调用 npm.cmd 必须用 call

**来源**：一键启动脚本停在 Python 路径后，不再启动前后端。
**教训**：在 `.bat` 中直接运行另一个 `.cmd` 会转移控制权，后续命令不会执行；版本检查重定向到 `nul` 后，这种提前退出看起来像卡住。不同 Python 小版本的二进制依赖也不能通过 `PYTHONPATH` 无条件混入可用环境。
**怎么做**：批处理调用 `npm.cmd` 等脚本时使用 `call`；检查步骤输出进度，并验证版本检查之后确实启动了前后端。优先使用可用的虚拟环境，仅在常规环境检查失败后尝试本地依赖回退。
**影响的文件/决策**：`start_nyx.bat`、`README.md`

### 2026-09-17: 采样失败不能用正常数值伪装成功观察

**来源**：时间感知与 presence 原生采样收口审查。
**教训**：系统输入 API 失败或平台不支持时成功返回 `idle_ms=0`，会绕过前端降级并长期误判在线；默认值在观察链里也会被当作真实事实。
**怎么做**：原生命令保留明确错误通道，失败触发已有 WebView fallback；用 mock 原生失败测试覆盖 idle 增长、away 和恢复输入，不依赖真实桌面。
**影响的文件/决策**：`frontend/src-tauri/src/lib.rs`、`frontend/src/hooks/usePresence.ts`、`frontend/tests/presence.test.ts`。

## 时间与在线观察

### 2026-09-17: 观察事实按采样时间排序，回拨判断必须在锁内读钟

**来源**：presence 对抗性并发与重放测试。
**教训**：请求接收晚不代表采样新；旧观察和旧 USER_MESSAGE 会倒灌当前状态。事件在等待锁前创建，不能用它的时间判断系统时钟回拨。
**怎么做**：采样携带 sampled_at，观察和用户证据共用锁及时间水位；拒绝旧采样且不投递。锁内检查未来时间与真实系统回拨，重新 away 废弃旧归来 claim；重连重新建立基线。
闲置使用单调时钟，而非墙上时钟；采样时刻回拨时即使 presence 不变也必须重新上报。
**影响的文件/决策**：`nyx/app_context.py`、`frontend/src/hooks/usePresence.ts`、04/09 契约。

### 2026-09-17: 异步返回被取消不能否定已经提交的终局事实

**来源**：正常回复后的场景记忆失败、announce 失败、SQLite commit 后取消测试。
**教训**：一整轮 reply 成功太晚；publish 或事务退出抛异常也可能发生在 commit 之后，盲目释放 claim 会重复表达同一次归来。未发布的 mutter 反而不能提前去重。
**怎么做**：正常终局提交立即完成 claim，广播和后处理失败不恢复。结果不确定时仅异常路径用已有 is_durable 核实并重抛原异常；真正未提交才释放。分别测试提交前和提交后取消。
**影响的文件/决策**：`nyx/expression/facade.py`、`pipeline.py`、`nyx/app_context.py`、11/04 契约。

### 2026-09-17: 网络失败意味着结果未知，不是服务端没有执行

**来源**：presence A/B/A 与响应丢失测试。
**教训**：最后成功上报 A、B 网络失败后，服务端可能已经是 B；继续用 last-sent A 去重会永远丢掉最新 A。原生采样也可能乱序，HTTP 单请求在途不足以防止旧结果覆盖。
**怎么做**：失败清空 last-sent，后续重新确认最新采样；请求设超时并取消，原生调用用序号守卫。历史与 SSE 合并后全局排序去重，相同时间戳用用户/think/对外文本顺序。
**影响的文件/决策**：`frontend/src/hooks/usePresence.ts`、`stores/chatStore.ts`。

### 2026-09-17: 拒绝非法数值之后，错误响应也要能够序列化

**来源**：raw JSON NaN/Infinity 与 Unicode 标题截断测试。
**教训**：Pydantic 拒绝非有限数值，但 FastAPI 默认回显原输入会使 422 自身序列化失败；JS slice 还会把有效 Unicode 字符切成孤立代理项。
**怎么做**：不把不可信原值放入 observe 的错误响应；数值严格校验且限制范围，前后端按字符限制标题，不切代理对。时间戳除 isFinite 外还检查 Date 范围；沉默和电脑输入闲置不能被描述为用户实际去向。
store 的校验必须先于等待状态/未读标记等副作用；只阻止消息 append 仍会留下半次消费。
**影响的文件/决策**：`nyx/api/routes.py`、`nyx/expression/prompt.py`、`frontend/src/lib/time.ts`、`usePresence.ts`。

### 2026-09-17: 大型调试详情必须懒加载并按真实实体去重

**来源**：eval 面板完整 prompt 查看方案审查。
**教训**：把大型 prompt 塞进 recent 列表会让未展开的内容也被批量传输；按展示行永久保存又会让同一 LLM 调用拆出的 think/speak 重复占库。无界 limit 会进一步把本地调试接口放大成全表读取。
**怎么做**：列表只返回摘要并限制分页参数；详情按精确 id 懒加载。持久化以真实 call_id 为键去重，展示行通过关联读取；旧数据、空数据、损坏数据必须使用不同状态。
**影响的文件/决策**：`eval_prompt`、`EvalStore`、eval REST 端点、`evalStore`、`EvalPanel`、10-eval 契约。

### 2026-09-18: 跨 Facade 调用不能隐藏事务或外部计算

**来源**：反思回填长期欲望 `linked_values` 的真实栈集成测试。
**教训**：上层已经持有 `Database.transaction()` 时调用另一个 Facade，看似普通的方法可能再次开事务；即使简单绕过嵌套事务，也可能把 embedding 等线程/外部计算留在 SQLite 锁内。只用 fake Facade 的单元测试会同时掩盖这两类问题。
**怎么做**：跨 Facade 的“外部计算 + 本地提交”拆成事务外预检计划和事务内确定性写入；计划携带足以检测准入条件变化的快照，冲突时整体回滚并重算。至少保留一条共享真实 `Database`、真实两个 Facade 的集成测试，并对事务中段失败做回滚注入。
**补充验证**：活动记忆 durable consumer 在事务内调用 `_persist_memory`，会持锁等待本地 embedding，候选命中时还可能等待矛盾检测 LLM。受控内存库复现确认 embedding 等待期间浏览 `recover_expired()` 获取同一锁超时，计算完成后锁释放；报错模块不一定是持锁源头，不能靠增加锁超时掩盖。
**影响的文件/决策**：`nyx/inner_life/reflection.py`、`nyx/desire/facade.py`、`tests/test_inner_life/test_inner_life_reflection.py`、07/08 契约。

### 2026-09-19: 共享事务锁不可重入，读方法必须复用外层事务

**来源**：应用启动后后台任务持续报告数据库暂不可用；数据库 schema 为最新且
`PRAGMA quick_check` 正常。真实栈显示内在生命事件消费者持有
`Database.transaction()` 时调用 `ActivityStore.get_current()`，该方法再次获取同一
`asyncio.Lock`，导致消费者永久等待自身释放的锁，后台任务随后在锁超时。
**教训**：同一个 `Database` 的事务锁是不可重入的；把“读方法”标成只读并不能安全地
在事务内再次调用。修复 schema、删除数据库或单纯增大锁超时都不能解决应用内死锁。
**怎么做**：store 方法在检测到当前 task 已处于外层事务时直接复用连接，否则才获取共享
锁；为该路径保留共享真实 `Database` 的回归测试。旧数据库只需正常迁移，不得为此删除或重建。
**影响的文件/决策**：`nyx/activity/store.py`、`tests/test_activity/test_activity_store.py`。

### 2026-09-18: durable 活动记忆只把核心提交放进事务

**来源**：后台任务报数据库锁等待超时；真实共享数据库复现活动记忆 embedding 持锁。
**教训**：`event_effect`、记忆行和 `memory_created` 是可恢复核心；关系边、矛盾检测和衰减是可丢失的旁路，不能在核心事务中等待外部或耗时计算。
**怎么做**：活动 durable consumer 先在事务外准备 embedding，再在事务内完成幂等 marker、记忆和核心事件。commit 后运行旁路，失败只记录日志，重放不重复核心写入。
**影响的文件/决策**：`nyx/memory/facade.py`、06-memory-system、共享 SQLite 锁边界。

### 2026-09-20: 删除持久化功能必须提供跨版本清理迁移

**来源**：移除共同浏览后，旧库中的事件、记忆和等待提问仍携带已删除枚举值，启动或读取时物化失败。
**教训**：删除代码中的枚举并不会删除已经落盘的数据；旧 schema 版本还可能高于当前代码保留的最后迁移版本，导致清理迁移被跳过。
**怎么做**：为废弃功能使用高于历史最高版本的迁移，显式删除事件及其 delivery/effect、记忆、等待状态和专用表；迁移测试要从旧版本数据出发验证清理后的枚举物化与总线启动。
**影响的文件/决策**：`nyx/db.py`、`tests/test_db/test_db.py`、已删除的共同浏览数据模型。

## 模板（条目格式）

```
### YYYY-MM-DD: [一句话教训]

**来源**：[哪个功能 / 哪个 Bug / 哪次评审]
**教训**：[发生了什么、根因是什么]
**怎么做**：[从此以后怎么做、检查什么]
**影响的文件/决策**：[如果改了什么，列出来]
```
