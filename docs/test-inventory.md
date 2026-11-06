# 测试覆盖索引

> 本文件是测试套件的轻量快照，不定义行为，不记录变更历史。
> 详细测试名、fixture 和断言以 `tests/` 源文件为准；契约以 `docs/specs/` 为准。
> 新增、删除测试或改变关键断言后，只更新本表的文件/覆盖范围和关键回归清单，不复制每条测试的说明。

## 当前快照

- 记忆召回/时间回归：自然位置/偏好问法、显式第一人称归因、带句末标点的寒暄、embedding失败关键词降级、快慢普通经历、原事件/段范围/工具计划时间、超时不写记忆及历史月份查询；另覆盖旧场景未知时间、矛盾/关系创建时间、批注三种原始/处理时钟、材料入库与发布时间、主动搭话欲望时间。P1 HTTP/SSE驱动增加清空滑动历史后的持久化召回、时间标签、段计数重放和超时零新增验收。

- 对话续接（26）：覆盖32条/24000字符连续输入与屏障、最新纠正/完整撤回子句、3次分析预算和eval记账、统一终局/ASK/降级、input_pending/input_update、已确认回执复用与uncertain、归档原文/索引原子回滚及提交确认丢失、旧库回填和51/52/53跨库导入、真实ID历史/direct顺序、pending快照和多角色隔离。P5驱动通过真实宿主/磁盘saver/HTTP/SSE与子进程崩溃恢复验证E01—E12；真实PostgreSQL、模型及复杂浏览器状态另行验收通过，真实IME组合输入未验收，详见26验收结论。前端覆盖逐ID等待、单调超时、覆盖协议/损坏快照、POST/SSE乱序、代数隔离及发送草稿快照。

- 后端测试文件：76
- 后端测试：1493项（包含参数化实例），76个文件；2026-11-06发布复核：非E2E用例1473 passed / 1 skipped（318.67s），19个完整E2E驱动独立通过；孤立依赖清理后表达模块221项通过，见26验收记录；本轮覆盖 PostgreSQL URL、Railway `$PORT` 与公网 Host、公网 ready 隔离、event_seq 回填、PostgreSQL 占位符/JSON 查询适配、新库 schema 编译、迁移回滚、连接清理、checkpoint saver 初始化、autocommit/标量函数转换、工具观测从存储到 PostgreSQL 占位符适配、容器配置和 SQLite→PostgreSQL 迁移源只读发现、正式角色迁移函数、内置 Nyx 卡路径保留、外键排序、自引用校验及 SQLite REAL 精度映射；OneBot 配置接受 Railway 私网主机并支持环境 URL 覆盖，Docker 镜像携带最小 Railway OneBot 配置且 Compose 保持空配置；Docker 部署回归验证 Railway Volume 权限修复后以 root 修复挂载属主并降权启动；统一 E2E 当前19个验收驱动，本次发布前全部复跑通过。
- 当前独立pytest覆盖率：86.95%，测试用例全绿，但未达到90%门槛，覆盖率命令退出码1。阶段五历史覆盖率：仅仓库内pytest整体87%；合并15个外部验收驱动后Nyx91.09%、含独立包91.13%，核心100%。CI已显式运行历史 E2E；90%覆盖率门槛缺口仍见 [统一待办清单](TODO.md)。
- 阶段五：`tests/test_harness.py` 4项独立核心回归与原插件10项；`tests/test_character_host.py` 新增第二角色真实 HTTP/SSE 注册、身份 prompt、聊天隔离和卸载恢复。wheel/sdist 与隔离安装通过；发布工具回归验证 CI 的 Ruff/Pyright 同时检查独立 packages；launcher 构建回归验证 PyInstaller 显式包含独立包路径。
- 历史 E2E 统一入口：`scripts/run_e2e.py` 按稳定顺序运行仓库内 P1-P4、插件 HTTP/SSE 及持久化/边界驱动；`tests/test_e2e_acceptance.py` 参数化复用同一集合，故意失败的红探针排除；两个入口隔离 `.env` 的部署认证变量，保持独立内存应用语义。CI 后端质量 job 显式运行该入口。
- 前端测试：402项，26个文件；本轮全量通过且生产构建通过；阶段三新增 fetch SSE、Bearer、凭证输入/清除、认证图片和凭证迟到响应隔离回归；阶段四覆盖设备 API 与配对登录。
- 阶段三质量门（2026-10-30）：后端全量 1375 passed / 1 skipped（1376项）；Ruff/Pyright零错误。前端全量 389 passed，生产构建通过。
- 阶段三真实本机 HTTPS：临时 Caddy 内部 CA 显式信任，通过同源页面、Chrome 登录、REST/SSE/图片 Bearer、非法 Origin、预检、`/api/ready` 阻断、1005 条提交序双 reader 续传、旧时间戳和 sink 回收；未写入系统证书库或真实数据。
- 阶段四设备配对切片（2026-10-30）：后端全量 1378 passed / 1 skipped；Ruff/Pyright 零错误。前端全量 391 passed，生产构建通过。新增真实 SQLite/API 回归覆盖配对码摘要、单次兑换、过期、撤销、凭证权限和关闭认证时端点禁用；真实服务器/公网与手机验收见 [统一待办清单](TODO.md)。
- 阶段2及QQ内心话最终质量门（2026-10-30）：全量1364 passed / 1 skipped（190.91s），未排除用例，退出码0；
  Ruff/Pyright零错误，git diff --check通过。传输/数据库51项、更新后的OneBot/停止边界14项通过。
  桌面脚本化HTTP/SSE E2E复跑40帧通过；规范审查通过，契约审查发现的并发异常覆盖已修复并复核。
  host.close测试仅阻塞期保留20ms drain，释放后恢复原预算，保留2秒完成与数据库关闭断言。
- OneBot阶段2新增tests/test_transports两文件13项，配置5项、宿主端到端1项和拒绝取消传输1项。
  覆盖首次跳历史、日志提交序/FIFO/退避、磁盘lease与recipient隔离、扫描/确认事务回滚、
  私聊过滤/转义/入向去重、echo匹配/断线续发、网络与DB超时分离、并发网络失败不盖过DB错误。
  真实SQLite/宿主/表达管道+本地模拟OneBot WS的私聊、独立角色绑定、卸载恢复通过；用户已确认真实QQ往返。
  取消打开DB测试兼容aiosqlite0.20/0.22线程结构，仍验证真实worker退出和连接关闭。
- QQ内心话扩展沿用既有测试，覆盖THINK首次跳历史、FIFO/退避、磁盘恢复、断线重发及发送时单次前缀。
  真实宿主/表达链+本地模拟WS验证“💭 内心话→正文”的独立消息顺序，恢复后两条均不重发；
  修正宿主embedding夹具为异步回调，与生产await接口一致。相关35项回归通过，未新增测试数量。
- 最终回复执行证据回归（2026-10-30）：`test_expression_facade.py` 新增2项参数化实例，
  验证本轮完整查询参数和执行叙述约束进入首次回复、续写及JSON重试；无工具结果/反馈不注入。
  宿主卸载测试只在handler阻塞时缩短drain预算，释放后恢复原预算，保留两秒归档和succeeded断言；
  连续5次真实宿主探针通过。真实模型最终回复3个受控样本的观察与限制见 `docs/eval/README.md`。
- 批注纠错回归补充显式 `【主题】` 聚类键优先级，防止真实模型漏填或改写主题导致三次纠正无法累计。
- 插件装配阶段1（2026-10-29）：新增 `tests/test_plugins.py` 的10项确定性回归，覆盖稳定拓扑、
  能力别名、缺失/自环/强环、弱环、必要弱依赖在所有 start 前校验、只读声明视图及完整17条路由等价。
  新旧装配联网开关两种配置的完整工具schema/顺序、技能、初始状态、表结构及弱回调基线一致。
  仓库外脚本化 LLM/embedding、真实HTTP/SSE/EventBus/内存SQLite验收通过，覆盖快回复、
  两步读取、天气降级、空结果/耗尽学习、一次重规划、三次纠正后真实反思管道，共40帧SSE。
  真实模型另跑同组路径，共43帧SSE，无dead-letter；当时发现的R1查询叙述失真及后续修复复测
  见 `docs/eval/README.md`。
- 本轮后端验证（耗尽后计划决策，2026-10-29）：完整 `python -m pytest -q` 为
  1332 passed / 1 skipped，未排除任何用例，耗时241.44s，退出码0；Ruff、Pyright 零错误。
  既有表达测试文件新增12项参数化实例，覆盖 continue/replan/stop、非法决策停止、
  决策持久恢复/旧 checkpoint 补做、一次重规划上限，以及空结果/uncertain/预算边界。
  重规划后的模型输入包含已用/剩余次数与旧决策已处理的事实。
- 本轮 P3 真实模型受控验收：无备用固定初始计划5/5先重规划再查新版，已有备用路径3/3继续，
  全空搜索2/2如实结束。后续决策、重规划和回复均使用 `deepseek-chat`（temperature=0.8）；
  工具资料/错误为合成夹具，10轮事件交付成功。无备用场景仅初始计划固定，不代表全流程真实规划。
- 本轮后端验证（副作用重放修复，2026-10-29）：完整 `python -m pytest -q` 为
  1320 passed / 1 skipped，未排除任何用例，耗时156.42s，退出码0；Ruff、Pyright 零错误。
  在既有文件新增9项确定性回归，覆盖副作用失败转 uncertain 并暂停同批后续调用、
  执行中/已完成/旧 checkpoint 的磁盘恢复、重启后关闭工具的回执恢复、
  回执准备/结果保存失败及完整结果持久化与单次领取；更新 schema50 表/索引快照。
  既有只读自动重试、耗尽反馈、去重/授权说明、一次 replan 上限和终局清理回归仍保留。
- 副作用双进程恢复探针：18项检查通过，覆盖执行副作用后强制退出、结果回执保存后
  checkpoint 提交前强制退出，以及旧 checkpoint 恢复；均验证副作用只执行一次、
  复用结果或报告 uncertain、不重新规划、真实 EventBus 保存 SPEAK、checkpoint 与回执终局清理。
  使用隔离磁盘 SQLite 与脚本化 LLM，临时驱动与测试库已清理；真实模型 replan 效果见本轮 P3 验收。
- 既有只读双进程恢复探针：强制退出后由新进程恢复的6项检查通过，已成功搜索只执行一次、
  失败文件调用累计3次、原参数与次数保留、不重新规划、真实 EventBus 保存 SPEAK、终局清理。
  使用隔离磁盘 SQLite 与脚本化 LLM，临时驱动与测试库已清理；真实模型 replan 效果见本轮 P3 验收。
- 工具升级临时 E2E：真实 HTTP/SSE、EventBus、内存 SQLite 与脚本化 LLM/embedding 的33项检查通过。
  覆盖多步规划、非法/空/失败规划降级、一次重规划/第二次停止、同轮公开工具优先、跨计划去重、
  新经验进入重规划与慢回复、授权/调用记账 JOIN、预算丢弃 adopted=0、快通道旁路、四种空值、
  技术失败不反思、相似向量下不同失败主题隔离、三次纠正/事实立即应用/真实叙事更新、effect重放、
  空主题兜底、沉淀/反思提交失败保留计数及重试、长topic显示截断后的身份隔离、观测失败与取消、
  v46→48数据保留及幂等。
  临时驱动与轨迹已清理；真实模型效果记录单列于 `docs/eval/README.md`。
- 后端运行：`pytest -q`
- 前端测试目录：`frontend/tests/`
- 前端运行：`cd frontend; npm test`
- 最近一次前端全量验证：391 passed（26 个测试文件）；`npm run build` 的 TypeScript/Vite 生产构建通过。覆盖完整窗口默认聊天、阅读抽屉、知识库作用域、知识库内投喂、阅读宽度重分页及完整窗口返回桌宠/临时提示归属回归。
- Tauri 壳回归：发布版 sidecar 路径与桌面可执行文件同目录；ready 响应必须同时匹配固定 Nyx 服务标识、本次启动 nonce 和响应大小上限；Debug/Release 均通过编译检查。
- 桌面 launcher 回归：普通与桌面入口均以 `--reload` 启动后端，且只在后端就绪后启动对应前端；原子 lock 防止并发启动；重启会等待并接管旧 launcher/backend；排除 venv wrapper 父进程，避免启动器自杀；未知程序占用 8000 时不创建进程；普通 TCP、错误 HTTP 状态、非法/过大 JSON、错误服务标识或 nonce 均不能触发前端启动；身份匹配后 owned backend 仍须存活。
- 启动身份 API 回归：`test_ready_endpoint_returns_owned_instance_identity`、`test_ready_endpoint_keeps_manual_start_compatible`；release smoke 使用同一 nonce 握手且拒绝身份不匹配、过大响应和探测后进程退出。
- 冻结后端生命周期：父管道 EOF 请求正常退出；daemon watcher 不阻塞服务失败后的关停。
- 前端测试覆盖通用 API、SSE、状态、聊天、阅读、设置和桌面 presence。
- 讨论三态回归：API client 透传 null/true/false，chatStore 保存并发送模式、reset 恢复自动，ChatInput 下拉框开启/关闭/自动选择与受控状态。
- P1 agent HTTP/SSE E2E：11 个场景全部通过，覆盖自动陪伴/拒绝讨论、自动讨论、强制开关、普通慢通道保留原检索/工具、三工具授权与 scope 隔离、结果回注、越权拦截、工具失败、重复调用及 4 轮/5 项上限，另验证非法 discussion 返回 422、真实段召回计数。
  使用真实 FastAPI/EventBus/内存 SQLite 与脚本化 LLM/embedding；验证流程和 prompt 接线，不代表真实模型讨论风格或引用质量已验收。单项 2000/总块 8000 字符上限经源码核对，未单独做 HTTP 场景。
- SSE 回归：初次连接失败保持 `connecting`，同一连接恢复后转为 `open`，不把启动期失败误报为 `closed`。
- P4 临时验收：真实 HTTP/SSE 的3组主线、5组实库边界、4组补充边界及回滚计数探针通过。
  覆盖 schema45→46 与迁移回滚/旧阅读批注保留、严格输入/精确 SPEAK 锚点/原文与 Unicode、
  FAST/SLOW/讨论工具/续写 prompt、waiting 隔离、同原始键并发/第一快照、录入和中介各四处事务
  失败/重试、表达终局后的独立录入恢复、丢 announce 的扫描/重启、事实过滤/无知识写入、
  坏 JSON/严格 bool/LLM/eval/取消、eval HTTP 的 token/history/prompt、纠错反思失败/恢复/DONE/过期，
  以及录入连续5次失败进入 dead_letter 时回复仍输出且回执 pending。跨角色锚点用隔离数据库验证。
  使用仓库外临时驱动、真实 SQLite/EventBus/FastAPI 与脚本化 LLM；未验收真实模型语义质量。
- P4 前端在既有5个文件新增15项测试：API 完整 payload/回执、SSE 解析/分发、单 SPEAK 选区及
  长文本限制、编辑发送、失败保留、waiting 旁路、同键回执先到/不降级与历史恢复、角色/reset 隔离。
  包含 `reset 后忽略迟到的历史 %s 请求` 的 events/pending 两个 await 阶段回归。
- P3 临时验收：5 组通过，含自适应新增问题、精确出处校验、原文与结论事实归因/检索/重放、
  关闭联网及空/失败/非法结构降级、超时部分结果和取消传播、最终 prompt 完整 JSON、真实 HTTP/SSE。
  另 5 组边界通过，覆盖硬上限及向下收紧、每个等待阶段超时、模型/网络/摄入失败、
  超长 Unicode/转义正文及完整序列化结果预算。来源满额后停止发现的红探针已转绿，10 组复跑通过。
  created_ids 实库探针验证实际插入、精确/语义命中、跨源、忽略插入、失败提交和外层回滚通过。
  两项红探针均已转绿；批准的补充方案另有 6 组实库验收通过，覆盖原文/结论 digest 和事实中断、
  提交确认窗口的明示/来源查询/幂等重试、累计器与事务边界、生产 eval 注入与五阶段 token/prompt、
  非法 JSON 记账、评估失败隔离及取消/时限。规范与规格审查通过，用户已授权提交 P3 并开始 P4 调查。
  使用仓库外驱动、脚本化 LLM/网络及真实 SQLite/EventBus；
  不代表真实模型语义或引用质量已验收。
- P2 agent E2E：8 组摄入/检索场景和 4 组故障恢复场景通过，覆盖 42→43 迁移与删除 SET NULL、
  手动 text/url 与双输入、实际新增计数/重试、registered fetch 单次摄入、摄入→工具→HTTP/SSE 来源回复、
  非法/空/关闭联网/SSRF/畸形域名与部分成功、chunk 去重/关联/消费/NULL 向量及完整 JSON 预算，
  EPUB/web/exploration 各持久阶段失败重放与旧 pending 不猜测关联。另验证关闭联网的 raw fetch 装配与恢复。
  最后修正后重跑 8 组 HTTP/SSE 场景通过；两条真实 DB 探针验证关键词低覆盖排除/高覆盖与向量命中、
  重试沿用已存来源名和正文。定向既有回归：记忆 113 项、API/fetch 20 项通过。
- P2 性能第 1 步的临时真实 SQLite 验收：1000/10000 块预热后各 3 次查询均读取 0 条正文、
  重建 0 次 ANN、候选遍历量均为 2991；各新增 30 块只同步 30 行，未重建索引。
  90 次与修改前 ANN 的候选/排序比较、增量移除/重插、同维度来源共享投影、空来源移除、
  空 ANN 切换维度、来源候选隔离、数字/时间/否定保护、
  关键词门槛、NULL 向量关联、消费、增量更新/删除、回滚及日志序号复用、同步失败恢复、
  43→44 原文 hash 迁移/索引查询计划、书籍级联与事实 SET NULL、跨连接提交、重启和锁顺序均通过；
  完整 chunks JSON ≤12000、正文 ≤2000 的预算通过。驱动在仓库外，使用脚本化 embedding/事实查询，
  是检索/持久化验收，不代表本轮重新验证 HTTP/SSE 或真实模型质量。
- P2 性能第 2 步的临时真实 SQLite 验收：5 组通过，五条知识点从 5 次事实抽取降为 1 次；
  实际新增 5/5，来源名/topic/chunk 与 memory_index 归因正确。覆盖空/单条、跨块隔离、
  来源隔离、已存来源重放、digest 部分失败重试、批量抽取失败 fallback、事实存储失败、
  第三条核心写入失败返回 2/2 后重试新增 3/3，以及累计器已有值、空/重复项、无事实存储。
  驱动在仓库外，使用真实 EventBus/隔离 SQLite 与脚本化 LLM；该步未重跑 HTTP/SSE 或真实模型质量。
- P2 性能第 3 步的临时真实 SQLite 验收：200000 字符、34 块仅保存一次完整正文；
  102 次 checkpoint 更新在脚本化输出下最大 6570 字符。11 个失败窗口均在关闭/重开数据库、
  远端正文变化后验证恢复，覆盖快照/pending/提取/最终游标保存与提交、证据、摘要和部分知识。
  44→45 迁移、原子回滚、空/超长/非字符串旧值、NUL 后正文无损、中文/emoji 字符截断、
  含 NUL 超长值拒绝、首写不覆盖、任务/数据库隔离、CASCADE、
  长度校验、取消与事务回滚、旧 pending 优先重放且不猜测关联均通过。
  驱动在仓库外，使用真实 ActivityFacade/MemoryFacade/FactStore/EventBus 与脚本化 LLM；
  本步未重跑 HTTP/SSE 或真实模型质量。
- P2 既有夹具同步：阅读 fake 支持显式 chunk ID，checkpoint 失败保留已提交证据；反思三块夹具
  使用不同原文，避免被来源内精确去重合为一块。
- 对话活动工具回归：网页阅读、书籍阅读、创作和浏览器问卷统一受理，任务进入同一活动队列；浏览器问卷覆盖同 URL 幂等、模拟页面点击与结果、重启不重做已点击题目、运行中聊天不打断、活动页结果展示。

## 后端覆盖

| 系统 | 测试目录 | 文件数 | 主要覆盖 |
|---|---|---:|---|
| 类型 | `tests/test_types/` | 2 | 后端 EventType 与前端 SSE 监听器同步、欲望初始状态、记忆/笔记/长期欲望可变默认值隔离 |
| 配置 | `tests/test_config/` | 1 | 默认值、嵌套配置、非法输入、Railway 私网 OneBot URL |
| LLM | `tests/test_llm/` | 2 | 统一客户端、首次调用延迟构造模型及配置透传、视觉客户端、token 元数据、最终 prompt 快照 |
| DB | `tests/test_db/` | 1 | 新旧默认路径兼容与父目录创建、SQLite/PostgreSQL URL 选择与无密钥错误、PostgreSQL autocommit 与标量函数转换及 SQLite `LIMIT -1` 兼容、event_seq 从旧 rowid 回填、迁移与 schema52 表/索引快照、PostgreSQL 正式角色 schema 编译、外键依赖排序、自引用校验与 REAL 精度映射、工具 checkpoint 与执行回执终局清理及未完成保留、连接打开取消时两个 worker/句柄回收、记忆 freshness 结算锚点保值迁移、旧及段级 recall 使用标记/事实段来源迁移、无 owner 的旧 interaction claim 恢复、短期欲望父外键及删除置空、活动历史/委派任务 FIFO 索引、书籍原文记忆 checkpoint 列、旧 material 孤儿活动退役、可空性、旧共同浏览数据清理、eval prompt 表、事务回滚、关闭 |
| 事件总线 | `tests/test_event/` | 3 | durable admission、批量事件原子受理、投递状态、重试、FIFO、按 `(timestamp, id)` 回放游标、SSE |
| API/运行时 | `tests/test_api/` | 5 | 组合根、REST、记忆搜索类别与事实作用域参数、聊天非空/12000 字符边界、旧上传端点 404、委派任务创建/列表及 404/409/422、活动结果过滤/有界分页参数、eval recent offset 分页与边界及 prompt 详情、订阅、用户消息活动快照/打断失败证据、tick、活动优先关停、恢复重放、Railway `PORT` 容器监听、公网 Host 白名单及非法端口、presence 原子提交、采样新旧顺序、异常输入与归来 |
| 工具 | `tests/test_tools/` | 6 | 文件沙箱、分块判等与同内容幂等写、搜索、工具注册和网络抓取、执行回执完整结果持久化与单次领取、统一网页/书籍/创作/浏览器问卷任务工具 |
| 记忆 | `tests/test_memory/` | 7 | kind/单一受控来源 topic、精确与语义去重、同内容有来源/无来源隔离、去重强化不增加真实召回、真实召回按 user event 幂等并升级、升级事务回滚按增量补偿事件计数、freshness 增量结算/回拨、episode 保守去重、原文画像/类别归因、来源及类别过滤先于排序和联想上限、embedding 延迟单次加载、ANN、带向量/关键词相关性门槛的融合召回、联想图、场景/活动 durable 重试与重放幂等、核心事务回滚、通用实体事实抽取/类型与别名索引/说话者归因/极性/多值关系、有效期替代/幂等/冲突折叠/作用域先筛后截断/有界召回、观察窗口防伪造事实、知识批量抽取与降级 |
| 欲望 | `tests/test_desire/` | 4 | 值机制、行动优先级、strength 缩放加压、系统构造 goal、父长期欲望选择/持久化/反馈、容量裁剪、满足与 durable attempt 重放 |
| 内在生命 | `tests/test_inner_life/` | 4 | 情感、精力、旧记忆归纳式反思、触发 evidence 注入、消费条件复核、慢变量 CAS、非有限数/候选类型边界、长期欲望数值/文字强度 prompt、事务回滚 |
| 活动 | `tests/test_activity/` | 12 | 本地自然日时间线、跨日暂停隔离、结果过滤/分页、行动优先级排期、父 ID 活动传递、活动/欲望/任务/事件原子生命周期与故障回滚、`IDLE_REFLECTION` durable 请求原子追加、收尾失败同进程恢复与下一次准入重试、有界取消、任务提交后调度失败仍成功、委派任务 FIFO/低精力/打断/关闭/崩溃恢复、网页任务 6000 字符 checkpoint、浏览器问卷任务领取/结果与模拟点击、EPUB-only 模糊选材及部分进度不结算、无匹配搜索、探索只追加父长期欲望子主题、观察、本地探索读取真实文件、创作结果严格校验、主题/旧作参考与唯一文件名 |
| 表达 | `tests/test_expression/` | 6 | prompt 动态资料预算/历史指令边界/记忆展示元数据/走神显式开关、人格与审美三档倾向及中文情感标签、活动事实/历史归属与终态措辞、慢通道时间/话题回溯与短回复检索词继承、慢通道默认单轮及显式续写、快慢通道正常文本终局原子批次及回滚增量计数补偿、场景请求、同轮记忆 id 去重召回、工具判断失败降级、只读工具三次尝试/耗尽交回模型/空结果区别/去重和授权说明/原参数跨重启恢复/终局清理/一次 replan 上限、副作用 uncertain 暂停与回执复用/旧 checkpoint 与关闭工具恢复/回执存储失败不重复执行、回复、搭话等待事件的 attempt/过期时间、碎碎念、durable interaction attempt owner/重放/多等待项显式归属/暂停恢复超时、读书提问短回复三层上下文、时间/对话锚点/归来消费 |
| 阅读 | `tests/test_reading/` | 7 | EPUB 受控结构/嵌套块语义/UTF-16 offset/脚本样式过滤、进度 CAS 与后台单调推进及 `reading_progress` 广播、活动阅读不触发陪读冲动、原文 6000 字符/超长段落/余量 flush、pending 恢复、来源内 Top 5 与书籍画像、笔记/划线/书签、重读反思证据、整合和后台生命周期 |
| 评估 | `tests/test_eval/` | 4 | OOC、embedding、记账、token、prompt 去重持久化与损坏数据、recent offset 分页 |
| 发布工具 | `tests/test_release_tools.py` | 1 | 六处版本一致性、tag 校验、冻结制品布局、REST/SSE smoke、stdin EOF 回收、占位 key、workflow 权限/依赖/Action SHA 与锁文件 |
| 角色卡与宿主 | `tests/test_character_cards.py`, `tests/test_character_host.py` | 2 | 卡包安全校验、旧存档一次初始化、并发四角色限额、切换归属、独立文件边界、presence 部分失败/FIFO/确认与重启恢复、设备配对一次兑换/过期/撤销和摘要存储、失效角色隔离、晚启动 runner、安装取消、停止超时保留 worker/数据库、宿主完整关停及恢复基线 |

## 前端覆盖

| 范围 | 测试目录 | 主要覆盖 |
|---|---|---|
| REST/SSE | `frontend/tests/api.test.ts`, `sse.test.ts` | 请求封装、委派任务端点与 `task_updated` 分发、创作过滤/分页参数、记忆类别/事实作用域端点、开发/打包共享 base URL、eval recent 分页参数与 prompt 懒加载端点、取消信号、错误、事件解析、SSE `id`/`Last-Event-ID` 回放游标、按 `event_id` 去重、`reading_progress` 帧校验与分发、`scene_memory_requested` 监听与 no-op 分发、`reflection_done` 同时刷新欲望与内在状态 |
| 状态与交互 | `frontend/tests/stores.test.ts`, `presence.test.ts`, `activityResult.test.ts`, `activityPanel.test.tsx`, `creationPanel.test.tsx` | Zustand、主动搭话等待字段落消息、委派网页/EPUB 表单与段落预览、任务状态/失败原因、安排任务与任务列表分栏、完成问卷结果在任务详情展开、普通进度冲突合并与显式重读回退、阅读进度快照的异书/旧 revision/Nyx 回退过滤、书签切书乱序回包隔离、笔记加载未完成即新增时保留旧划线、创作分批追加与刷新竞态、创作书架/正文详情/加载更多、记忆搜索与事实图独立降级、eval store 分页加载历史记录及逐行 prompt 缓存/重试、历史/SSE 去重与因果排序、非法帧不改变等待/未读状态、活跃度、异常原生采样、请求超时/取消、网络结果不确定 |
| 聊天与设置 | `chatPanel.test.tsx`, `settingsView.test.tsx`, `evalPanel.test.tsx` | 用户输入、知识投喂成功/部分成功重试/角色切换旧响应隔离、下拉选择开启/关闭/自动讨论模式、时间分隔、主动搭话等待倒计时、消息首次逐字与重新挂载即时显示、未 ready 不提前记已看、同回合播放顺序、角色重置隔离、角色卡导入与新建表单提交、设置中不再嵌入评估面板、独立评估页面逐条展开详情与安全文本渲染 |
| 记忆面板 | `memoryPanel.test.tsx` | 关键词查询提交、普通记忆统一搜索、每页五条、普通/知识库事实图独立选择、实体关系渲染、知识库作用域及切回记忆清除筛选、普通记忆在分页和空态前排除知识、知识库内投喂、事实加载失败降级 |
| 内在状态与欲望 | `innerStatePanel.test.tsx`, `desiresPanel.test.tsx`, `labels.test.ts` | 未连接/有数据/错误状态展示、情绪选图与拖拽隔离、情绪分类标签、欲望终态过滤、长期/短期和类别检索、压力数值、每页五条、中文标签命中与未知值回退 |
| 阅读 | `readerView.test.tsx`, `notePanel.test.tsx` | 真分页与长段可达、只改变宽度时重测换行与页数、竖向工具菜单及 Escape 关闭回焦、受控富文本、UTF-16 同段划线、重叠划线线性端点读取、跨段拒绝、划线查询/删除/定位、书签定位、普通笔记和章节交互 |
| 角色与请求归属 | `characterScope.test.ts`, `characterStore.test.ts` | scoped URL/选择 revision、A→B→A 旧回调隔离、请求取消、角色切换/卸载/恢复与草稿、切换中 registry 轮询防回退；presence/SSE 测试覆盖公共采样独立于当前连接和私有样本归属 |
| 角色形象 | `avatar.test.tsx` | 卡片图片使用匿名 CORS 通过桌面本地访问校验；无图片角色显示名称占位 |
| 视觉与通用 UI | `avatar.test.tsx`, `app.test.tsx`, `rightDock.test.tsx`, `desktopWindow.test.ts`, `petShell.test.tsx`, `time.test.ts`, `useTypewriter.test.tsx` | 创作页/评估页导航入口、完整端设置页导航及桌宠设置弹层回退、昼夜状态与休眠恢复校时、昼夜/头像、完整端与桌宠态拖动模式隔离、桌宠双击后重新挂载完整内容、桌宠点击/拖动阈值与窗口层级、聊天/读书子面板靠右停靠标记、头像东西南北四项入口/内心摘要/可修改设置入口、状态气泡、桌宠陪读长段滚动与翻页边界、非法时间标签边界、打字机 |

## 关键回归清单

这些测试保护跨模块一致性和最容易回归的失败模式；完整列表直接在测试源码中搜索对应名称。

### 总线、事务与重放

- `test_connect_cancelled_during_migration_closes_connection`
- `test_unload_drain_timeout_keeps_worker_until_accepted_work_finishes`
- `test_host_close_waits_for_timed_out_delivery_then_closes_databases`
- `test_fatal_role_failure_preserves_other_role_and_releases_stopped_lease`
- `test_presence_partial_failure_keeps_per_role_order`
- `test_restart_replays_pending_presence_without_skipping_away`
- `test_restore_syncs_presence_without_rewriting_historical_return`
- `test_unload_waits_for_runner_started_by_admitted_request`
- `test_publish_is_durable_before_return`
- `test_publish_failure_does_not_return_event_id`
- `test_handler_failure_retries_only_failed_consumer`
- `test_delivery_failure_is_recovered_after_expired_lease`
- `test_success_finalization_retries_without_blocking_consumer`
- `test_failure_finalization_retries_without_wedging_delivery`
- `test_effect_marker_skips_duplicate_handler_replay`
- `test_publish_many_rolls_back_all_events_on_conflict`
- `test_publish_many_cancellation_rolls_back_count_and_events`
- `test_user_message_replay_skips_after_reply_event_exists`
- `test_attempt_insert_rolls_back_with_outer_transaction`
- `test_concurrent_reply_claims_only_claim_once`
- `test_close_rejects_new_events_and_closes_database`
- `test_supervise_bus_returns_when_bus_stops_normally`
- `test_connect_cancelled_during_checkpoint_open_closes_both_workers`
- `test_connect_cleans_terminal_tool_checkpoint_but_keeps_unfinished`

### 工具重试与模型决策

- `test_tool_retry_reaches_success_before_next_model_decision`
- `test_exhausted_tool_reports_error_parameters_and_duplicate_reason`
- `test_tool_checkpoint_resumes_failed_call_after_database_reopens`
- `test_explicit_tool_error_retries_but_empty_result_does_not`
- `test_unauthorized_tool_is_explained_to_next_decision`
- `test_exhausted_call_returns_control_before_remaining_batch`
- `test_exhausted_plan_requires_review_before_next_tool`（三种动作及重规划完成状态回注）
- `test_invalid_plan_review_stops_without_new_tool`
- `test_plan_review_survives_database_reopen_without_repeating_failure`（新/旧 checkpoint）
- `test_plan_review_respects_replan_limit`
- `test_plan_review_does_not_replace_other_tool_boundaries`（empty/uncertain/预算）
- `test_tool_replan_keeps_results_and_explains_second_replan_limit`
- `test_side_effect_error_is_uncertain_and_stops_remaining_batch`
- `test_side_effect_receipt_recovers_without_reexecuting`（执行中/已完成/旧 checkpoint，以及重启后关闭工具）
- `test_receipt_storage_failure_propagates_without_repeating_effect`（准备/结果保存两个阶段）
- `test_execution_receipt_keeps_full_result_and_claims_once`

### 跨模块状态一致性

- `test_activity_end_transaction_rolls_back_on_derived_event_failure`
- `test_start_activity_rolls_back_when_event_append_fails`
- `test_interrupt_activity_rolls_back_when_event_append_fails`
- `test_run_eval_rolls_back_desire_when_generated_event_append_fails`
- `test_activity_end_transaction_rolls_back_energy_and_emotion`
- `test_reflection_event_rolls_back_slow_variables_when_event_append_fails`
- `test_subscription_consistency`（含 durable consumer id 接线）
- `test_answer_waiting_releases_claim_when_desire_settlement_fails`
- `test_recover_stale_pending_abandons_and_releases_desire`
- `test_compat_apply_event_restores_emotion_when_append_fails`
- `test_get_state_settles_emotion_and_energy`
- `test_run_rejects_invalid_json_for_delivery_retry`
- `test_run_real_desire_facade_commits_without_nested_transaction`
- `test_run_embedding_failure_preserves_all_reflection_state`
- `test_run_commit_failure_rolls_back_long_term_and_slow_variables`
- `test_apply_snapshot_conflict_rolls_back_reflection`
- `test_apply_slow_variable_conflict_rolls_back_reflection`
- `test_stale_periodic_reflection_is_consumed_without_running_llm`
- `test_periodic_reflection_rechecks_memory_threshold_at_consumption`
- `test_reflection_trigger_evidence_reaches_prompt`
- `test_reflection_failure_preserves_event_applied_during_prepare`
- `test_integrate_revisit_publishes_reflection_event`
- `test_integrate_keeps_buffer_when_reflection_admission_fails`
- `test_record_recall_rolls_back_when_promoted_event_append_fails`
- `test_record_recall_same_user_event_is_idempotent`
- `test_scene_memory_rolls_back_when_created_event_append_fails`
- `test_remember_scene_replay_is_idempotent`
- `test_remember_scene_failure_keeps_effect_unapplied`
- `test_remember_scene_rolls_back_memory_and_effect_on_event_append_failure`

### 记忆、活动和用户路径

- `test_search_fuses_vector_keyword_and_limits_direct_then_association`
- `test_search_rejects_weak_vector_and_keyword_matches`
- `test_backtrack_does_not_keep_unrelated_latest_nyx_reply`
- `test_reply_slow_short_answer_searches_recent_topic_not_old_topic`
- `test_packaged_entry_resolves_resources_without_launch_directory`
- `test_build_embed_loads_model_lazily_once`
- `test_fact_store_replaces_old_valid_fact_and_keeps_history`
- `test_memory_facts_endpoint`、`memoryPanel.test.tsx`：事实快照 API、实体关系图与事实独立降级
- `test_fact_store_duplicate_is_idempotent`
- `test_observation_window_title_does_not_update_fact_graph`
- `test_remember_knowledge_batches_fact_extraction_and_maps_sources`
- `test_build_system_prompt_renders_facts_separately`
- `test_search_topic_association_limits_after_excluding_direct`
- `test_topic_association_sorts_shared_bucket_once`
- `test_ann_allowed_ids_filter_applies_before_candidate_limit`
- `test_persist_builds_ann_index_once_when_dedup_misses`
- `test_associate_depth_two_scores_and_excludes_seeds`
- `test_digest_source_block_updates_profile_and_attributes_fiction`
- `test_same_knowledge_from_two_books_keeps_both_source_scopes`
- `test_source_semantic_dedup_ranks_only_within_same_source`
- `test_find_by_content_keeps_sourced_and_unsourced_knowledge_separate`
- `test_repeated_writes_do_not_promote_but_real_recalls_do`
- `test_remember_activity_creation_exploration_and_browser_survey`
- `test_reply_slow_records_each_memory_id_once_per_turn`
- `test_reply_slow_publishes_nothing_until_generation_finishes`
- `test_reply_batch_failure_leaves_no_partial_text_events`
- `test_reply_attempt_batch_failure_rolls_back_state_and_count`
- `test_claim_owner_recovery_and_release_are_owner_scoped`
- `test_reading_question_same_event_recovers_claimed_context`
- `test_reply_slow_tool_decision_failure_continues_without_tools`
- `test_dynamic_prompt_blocks_are_bounded`
- `test_memory_budget_keeps_only_complete_entries`（恰好边界 / 超一字符不展示尾条）
- `test_reply_slow_counts_only_memories_visible_in_prompt`（长条 / 长短混合 / 重复 id）
- `test_reply_slow_empty_memory_search_does_not_count_recall`
- `test_build_system_prompt_renders_observed_activity_only`
- `test_activity_context_does_not_infer_pause_or_missing_progress`
- `test_history_activity_fact_is_secondary_to_current_context`
- `test_chat_rejects_blank_or_oversized_message`
- `test_settle_freshness_is_incremental_and_same_time_idempotent`
- `test_settle_freshness_ignores_clock_rollback`
- `test_strengthen_restarts_freshness_clock`
- `test_extract_fact_candidates_does_not_attribute_nyx_first_person_to_user`
- `test_scene_first_person_fact_is_not_attributed_to_user`
- `test_search_filters_source_before_ranking`
- `test_each_chunk_persists_profile_and_source_before_advancing`
- `test_pending_digest_is_reused_without_second_extraction`
- `test_sediment_splits_long_paragraph_and_flushes_remainder`
- `test_sediment_reuses_pending_without_digest_call`
- `test_reading_question_reply_injects_context_on_fast_short_reply`
- `test_build_reply_context_contains_three_scoped_layers`（事实层使用正文而非主题 summary）
- `test_script_and_style_content_is_ignored`
- `test_nested_block_direct_text_preserves_document_order`
- `test_nested_block_tail_text_preserves_document_order`
- `test_source_knowledge_keeps_web_url_scope`
- `test_run_empty_source_skips_memory_digest`
- `test_migrate_adds_source_memory_state_columns`
- `test_assigned_task_does_not_preempt_running_activity`
- `test_web_task_sediments_every_6000_character_block`
- `test_web_task_evidence_failure_replays_without_duplicate_memory`
- `test_web_task_checkpoint_failure_preserves_reading_evidence`
- `test_interrupted_assigned_task_returns_to_queue`
- `test_maybe_start_reading_can_match_uploaded_epub`
- `test_best_book_match_does_not_fall_back_to_latest`
- `test_start_activity_rolls_back_when_event_append_fails`
- `test_fail_activity_rolls_back_when_task_update_fails`
- `test_recovery_finishes_task_linked_to_completed_activity`
- `test_quiesce_cancels_runner_and_returns_task_to_queue`
- `test_quiesce_recovers_even_when_cancel_cleanup_raises`
- `test_quiesce_timeout_leaves_live_runner_durably_running`
- `test_interrupt_timeout_keeps_live_runner_and_activity_running`
- `test_complete_failure_recovers_orphaned_task`
- `test_failed_settlement_recovers_orphaned_task`
- `test_interrupt_failure_recovers_cancelled_task`
- `test_next_admission_recovers_done_runner_before_selection`
- `test_assignment_survives_post_commit_scheduling_failure`
- `test_complete_idle_reflection_appends_durable_reflection`
- `test_idle_reflection_uses_durable_reflection_route`
- `test_resume_skips_same_block_from_previous_local_day`
- `test_main_quiesces_activity_before_reading_and_bus_close`
- `test_material_retirement_handles_missing_source_row`
- `test_advance_nyx_position_is_monotonic_and_preserves_user_state`
- `activityPanel.test.tsx`：网页/EPUB 委派、目标段前后预览、任务失败原因展示
- `stores.test.ts`：普通进度冲突保留服务端更靠前的 Nyx 位置，显式重读仍可回退
- kind-scoped exact/semantic dedup and humanized memory prompt regressions
- schema 19 clears legacy memory/edges and installs kind/topics indexes
- `test_resume_skips_committed_fragment`
- `test_resume_skips_finalized_note_and_knowledge`
- `test_parse_activity_result_requires_non_empty_strings`
- `test_creation_output_path_is_unique_per_activity`
- `test_creation_activity_injects_context`
- `test_write_same_content_does_not_rewrite`
- `test_same_content_comparison_reads_in_bounded_chunks`
- `test_write_replaces_non_utf8_content`
- `test_connect_creates_parent_directory`
- `test_connect_reuses_legacy_default_database`
- `test_reload_skips_missing_config_path`
- `test_summarize_injects_related_memories`
- `test_chat_endpoint`
- `test_check_reflect_triggers`

### 欲望系统重构

- `test_claim_for_activity_is_single_use`
- `test_trim_pending_keeps_high_expression_weight`
- `test_trim_pending_uses_action_priority_and_refunds_parent`
- `test_add_long_term_subtopics_is_idempotent_and_missing_is_noop`
- `test_deleting_parent_clears_short_term_and_attempt_links`
- `test_prepare_long_term_candidates_filters_before_capacity`
- `test_prepare_long_term_candidates_deduplicates_batch_semantically`
- `test_add_prepared_long_terms_rejects_snapshot_conflict`
- `test_apply_value_delta_preserves_concurrent_increments`
- `test_run_eval_reuses_saved_generation_after_commit_failure`
- `test_run_eval_same_tick_applies_periodic_pressure_once`
- `test_pick_parent_long_term_by_strength_then_fifo`
- `test_run_eval_zero_strength_long_term_has_no_drive`
- `test_run_eval_zero_strength_pending_does_not_block_same_topic`
- `test_run_eval_trimmed_new_desire_refunds_parent_without_event`
- `test_satisfy_reinforces_explicit_parent_only`
- `test_get_pending_uses_action_priority`
- `test_run_without_parent_still_persists_knowledge`
- `test_describe_long_term_strength`

### 前端桌面采集

- `expressionHistoryStore` / `ExpressionHistoryPanel`：表达历史、碎碎念和主动搭话查询展示

- `presence.test.ts`：Tauri 空闲毫秒/前台标题采样、三态边界、浏览器降级与时钟跳变、失败重试、single-flight A/B/A、采样乱序、Unicode 截断、超时/卸载取消和重连同步
- `readerView.test.tsx`：阅读位置和 Nyx 追赶/等待派生态展示、结构化标题/粗体/已有划线、当前书划线搜索、UTF-16 同段选区、跨段拒绝，以及划线/书签持久定位
- `stores.test.ts`：旧书书签请求成功或失败晚到时，均不得覆盖当前书状态
- `readerView.test.tsx`：重叠划线分段保持端点读取为线性次数，不使用耗时阈值
- `app.test.tsx`：Tauri 桌宠态不保留隐藏的完整布局，双击头像后重新挂载主布局及临时提示层；完整端不挂载浮动头像，导航底部印章可返回桌宠；返回后由桌宠接管提示
- `petShell.test.tsx`：聊天和读书子面板打开时设置统一靠右停靠标记，避免固定窗口裁切
- `petShell.test.tsx`：超长陪读段落在固定高度正文区域内纵向滚动，不再被隐藏裁切
- `packaged_backend_is_a_sibling_of_the_desktop_executable`

### CI/CD 与发布制品

- `test_validate_versions_accepts_matching_sources_and_tag`
- `test_validate_versions_reports_drift`
- `test_release_binaries_require_desktop_and_sidecar`
- `test_run_smoke_uses_nonsecret_placeholder_key`
- `test_ci_workflow_has_quality_package_and_tag_release_gates`
- `test_ci_actions_are_pinned_and_python_dev_dependencies_are_locked`

### Eval prompt 可观测

- `test_complete_concurrent_first_calls_build_model_once`、`test_from_config_ok`、`test_from_config_passes_timeout_and_retries`、`test_from_config_passes_temperature`：LLM 模型延迟且并发只构造一次，配置不丢失
- `test_complete_captures_independent_prompt_messages`
- `test_prompt_round_trip_is_shared_by_call_id`
- `test_prompt_and_eval_record_insert_roll_back_together`
- `test_eval_prompt_legacy_and_missing`
- `test_eval_prompt_corruption_returns_controlled_error`
- `evalPanel.test.tsx`：展开懒加载、旧/空/加载/错误状态、Unicode/换行和 HTML 字面安全渲染

### 时间感知与离开归来

- `test_build_temporal_block_short_cross_midnight_does_not_exaggerate`
- `test_build_temporal_block_acceptance_scenario_and_quote_boundary`
- `test_last_dialogue_anchor_skips_current_and_incomplete_turns`
- `test_reply_recovers_overnight_dialogue_from_durable_log`
- `test_reply_fallback_releases_return_context`
- `test_return_is_consumed_when_normal_reply_precedes_later_failure`
- `test_stale_observation_cannot_overwrite_user_online`
- `test_observation_and_message_are_serialized_without_false_return`
- `test_clock_rollback_rebuilds_presence_baseline_without_return`
- `test_failed_clock_reset_preserves_existing_return`
- `test_nonfinite_json_observation_returns_validation_error`
- `test_non_utf8_window_title_is_rejected`
- `test_committed_expression_does_not_restore_return_on_announce_failure`
- `test_reply_cancelled_at_publish_return_does_not_restore_durable_claim`
- `test_transaction_cancelled_after_commit_does_not_restore_return`
- `test_committed_observation_cancellation_still_commits_snapshot`
- `test_mutter_publish_failure_does_not_suppress_retry`
- `test_old_user_message_cannot_overwrite_fresh_away`
- `test_new_absence_invalidates_pending_and_claimed_return`
- `test_old_return_is_not_described_as_just_returned`
- `test_return_context_is_not_rendered_for_away_user`
- `test_observe_rejects_adversarial_payload_without_mutation`
- `test_observe_admission_failure_preserves_presence_snapshot`
- `test_release_old_return_claim_does_not_overwrite_new_return`
- `test_user_message_marks_away_user_returned_before_reply`
- `test_user_message_preserves_activity_evidence_when_interrupt_fails`
- `test_first_user_message_only_establishes_presence_baseline`
- `test_sse_frame_uses_backend_event_timestamp`
- `app.test.tsx`：无 SSE/用户操作跨 06:00/22:00，根主题、消息时间分隔和 Avatar 同步

## 维护规则

- `tests/test_memory/test_segments.py` 覆盖宽松续接、明确转题、原文保留与段链、真实召回幂等晋升、短期容量淘汰只退出索引、新鲜度排序及超过最近 200 条后长期段仍可检索；表达测试覆盖独立区块和预算内段计数；记忆 Facade 覆盖空闲定时判定、事实网与续接后旧事实关闭，运行时测试覆盖独立计时器；数据库迁移覆盖 schema 42。

- 测试行为不写入本表；需要查看断言时直接打开对应 `tests/` 文件。
- 测试文件移动或新增系统时，更新上面的文件数和覆盖表。
- 关键回归测试删除或重命名时，同步更新本表。
- 测试快照不是历史记录，不保留旧轮次、旧数量或已删除测试名。
- `docs/LessonsLearned.md` 记录可复用的失败模式；本文件只记录当前覆盖情况。
