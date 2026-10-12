# 角色卡与多角色运行

> 本契约落实已审核的实施计划；角色运行仍复用 `_App` 和现有 Facade。

## 元信息

- 前置依赖：01-types、03-llm、04-module-bus-system、05-tools、07-desire、08-inner-life、11-expression、12-reading-system。
- 实现：`nyx/character_cards.py`（卡包类型、读取、校验）、`nyx/app_context.py`（宿主与角色装配）、`nyx/runtime.py`（生命周期）、`nyx/api/routes.py`（宿主与角色 API）、`nyx/cards/nyx/`（内置卡）；前端角色 Store、选择和管理组件。

## 用户故事与验收

- 同时注册最多四个角色，包含 Nyx；每个角色独立运行和保存数据。
- 切换只改变前台选择，不重建、不暂停旧角色；已受理工作保留原归属。
- 聊天、阅读、任务和编辑只交给当前角色；后台继续活动、欲望、反思和超时维护。
- 共享 online/busy/away；标题、屏幕和新主动表达只属于当前角色。
- 卸载保留存档；恢复不重新 seed、不覆盖成长；无永久删除和卡片更新入口。

## 卡包

入口为 `.nyxcard` 或 `.zip`，根目录必须有 `card.yaml` 和非空 `canon.md`，可选 `character-reference.md`、`ask.md`、`assets/`。设置页也提供结构化新建表单，填写 ID、名称、身份设定和三组初始数值，可附加头像/表情图片；后端将其生成同样的标准卡包后走注册流程。
压缩文件和实际解压总量分别不超过 50 MiB；重复路径（含大小写等价）、绝对路径、越界、链接、未知根文件和无效图片拒绝。

`card.yaml` 只允许以下字段：

| 字段 | 契约 |
|---|---|
| schema_version | 整数 1 |
| id | 稳定小写字母开头，字母/数字/下划线/短横线，最多 64 字符；不得是 Windows 保留名 |
| name | 非空显示名，最多 100 字符 |
| personality / values / aesthetic | 对应现有 TypedDict 的全部维度；严格有限数值 1–10 |
| identity | 非空初始自我叙事 |
| long_term_desires | 可为空；最多配置容量且不超过领域容量；type/name/description/strength/subtopics/linked_values 使用现有欲望契约；初始化生成 ID、时间戳，progress=0 |
| assets | 可选 key → 卡内 `assets/` 相对路径；`avatar` 和现有八种表情键；图片必须能由 Pillow 完整解析 |

卡片禁止配置密钥、模型、路径、重试或执行代码。精力和通用压力由框架提供。
Nyx 数值、三条初始欲望、设定、专属提问方向和八种表情迁入内置卡；结构、工具和知识边界留在框架。

## 存储与宿主

宿主使用 `data/characters/host.db` 和独立迁移；保存角色记录、绝对卡片/DB/工作目录路径、状态、错误、当前 ID、selection_revision、共享 presence 和每角色补投记录。
角色新存档位于 `data/characters/<id>/{card,nyx.db,workspace}`。默认 Nyx 保留显式路径/NYX_DB/旧 nyx.db/data/nyx.db 选择顺序及原 workspace，不移动旧数据。
同 ID 不覆盖卡或存档，不允许两个活实例。宿主共享 SDK 和 embedding；角色独立 LlmClient 与 FIFO 锁，每角色最多一个主 LLM 调用，总计最多四个。

初始化 marker 与初始数据在同一事务提交。旧存档补齐缺失单例后登记；旧长期欲望为空也不重新填充。此规则覆盖 07-desire 的空表重复 seed 规则。
工具绑定自己的 workspace；搜索、直接读取、列目录均拒绝宿主和其他角色内部目录，普通用户文件保留现有探索规则。

## 对象完整性

| 对象 | 全部入口 | 全部消费者 |
|---|---|---|
| 卡片/注册 | 内置卡、上传、恢复、旧 Nyx 登记 | 装配、Prompt、图片、管理 UI |
| 运行时 | 启动、挂载、失败重试、重启恢复 | REST、SSE、tick、总线、活动/阅读 runner |
| 领域存档 | 原有聊天、任务、书籍、进度、记忆、eval 全部写入口及重放 | 原有 Facade、Store、搜索、统一书库候选集和 UI |
| 共享观察 | 桌面 FIFO、聊天在线事实、初始同步、持久补投 | 各角色观察事件消费者、表达上下文 |
| 当前选择 | 启动恢复、串行切换 | 请求准入、SSE、全部 UI 回调、屏幕采样 |

### 状态迁移

| 当前 | 条件 | 下一状态 | 副作用/失败 |
|---|---|---|---|
| 未注册/ARCHIVED | 校验成功、事务预留名额 | STARTING | 安装卡快照；非法包无预留 |
| STARTING | 装配与恢复成功 | RUNNING | 接受新请求 |
| STARTING/RUNNING | 不可恢复错误 | FAILED | 保存错误，停止新入口；其他角色不退出 |
| RUNNING/FAILED | 非当前角色卸载 | STOPPING | 停周期任务、quiesce、drain、等待全部旧工作 |
| STOPPING | 工作全部结束 | ARCHIVED | 关闭数据库、结束补投、释放名额，保留文件 |
| STOPPING | 取消/drain 超时 | STOPPING | DB 保持打开、名额占用、不能同 ID 重启 |

重启恢复注册角色；中断 STARTING 重试启动，中断 STOPPING 完成停止，ARCHIVED 不自动运行。
当前失败角色仍选中；卸载当前角色后端拒绝，前端须先切到首个其他 RUNNING，无候选则拒绝。

### Bad case

| 情况 | 契约 |
|---|---|
| 空 | 空包拒绝；空欲望合法；无图片显示名称占位 |
| 失败 | A 的 SDK/DB/bus 故障只影响 A 的锁、预算、delivery 和欲望计数；B 继续运行 |
| 部分完成 | seed 原子；卡安装失败保留 FAILED；presence 只补失败接收者；成功 handler 收尾失败只补写 |
| 乱序 | 每角色 presence FIFO；前端所有回调检查 ID+本地代数；过期私有观察不转交新角色 |
| 重放 | 稳定 sample/event ID、事务水位和 effect marker 去重；成功 fallback 为终态；恢复不 seed |
| 删除 | 仅卸载保留存档；停止完成才关闭 DB；补投终止，恢复以当前状态建立基线 |

## API

宿主：`GET/POST /api/characters`、`POST /api/characters/create`、`PUT /api/characters/current`、`POST /api/characters/{id}/retry-start`、`DELETE /api/characters/{id}`、`POST /api/characters/{id}/restore`、`GET /api/characters/{id}/assets/{key}`、`POST /api/observe`。`/create` 接受 multipart 的 JSON `payload` 与可选 `images`，图片字段名为头像或表情键。
现有业务接口复用在 `/api/characters/{id}/…`；不增加一套领域实现。
写操作必须带 `X-Nyx-Selection-Revision`，准入捕获 runtime 引用；旧选择 409、未知 ID 404、不可运行 503。已受理请求继续在原角色完成。
原 `/api/...` 是固定 Nyx 的兼容入口，写入仅在 Nyx 为当前时接受；ready 保持服务身份和 nonce。
SSE 加 `character_id`，游标为角色与事件 ID 的组合；跨角色游标拒绝，同角色未知游标保持有界恢复。

## 观察与重试

宿主先事务保存稳定 sample_id、顺序号、事实和当时接收者，再返回受理。每角色独立事件与结果，按序补投；角色在同一事务提交事件、水位和转换快照，宿主确认丢失时先核实已提交记录。
退避为 1/2/4/8/30 秒，之后每 30 秒；五次失败显示同步异常，继续保留并补投。
卸载完成取消未完成接收记录；恢复先同步当前状态，不制造卸载期间的 returned。
聊天在线事实走同一宿主顺序，只广播在线事实；目标同步失败则聊天未受理并返回 503，在线记录继续补投。
标题绑定采样 ID/revision；失去焦点清空当前窗口/屏幕缓存，保留历史记忆；视觉完成时版本已变则丢弃。
总线 `run()` 监督意外 worker 退出，恢复阶段也在 finally 清理范围；保持五次 delivery 和 finalize-only 重试。

## 前端

标题和桌宠菜单提供选择，设置提供导入/卸载/恢复/重试和额度/状态。
复用领域 Store；切换增加本地代数、断旧 SSE、取消旧读请求和未发送操作、重置展示、串行 PUT、加载快照、恢复角色草稿和阅读位置；失败按实际宿主选择恢复。
全部 success/error/finally/timer/retry 检查 ID+代数，覆盖 A→B→A。阅读重试固定角色、书籍、session、payload；无聊天/任务 POST 自动网络重试。
观察采样与 SSE 独立，使用有界 FIFO；队满暂停采样，切换不制造离开。

## 验证与完成定义

卡包非法/容量并发/一次 seed/旧存档；四角色与单角色故障；presence 部分成功、确认丢失、FIFO、重启；已受理写跨切换、A→B→A、SSE 游标；停止超时与恢复；工具目录隔离；默认 Nyx 和资源打包。
执行 `ruff check`、`pyright`、`pytest -q`、`npm test -- --run`、`npm run build`、release smoke；同步受影响 spec/facts、导航和测试快照。
