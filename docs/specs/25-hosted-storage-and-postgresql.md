# Railway 托管与 PostgreSQL 存储

## 元信息

- **前置依赖**：04-module-bus-system、12-reading-system、14-character-cards、20-plugin-harness、21-onebot-transport、22-multi-client-transport、23-device-pairing。
- **实现文件**：`nyx/db.py`、`nyx/app_context.py`、`nyx/main.py`、`nyx/config.py`、`pyproject.toml`、`scripts/migrate_sqlite_to_postgres.py`、`Dockerfile`、`docker-compose.yml`。
- Railway 托管应用使用一个 PostgreSQL 实例；SQLite 保留为默认本地开发和测试后端。
- 本文件定义云端存储和部署契约；各领域对象的业务语义仍由上述领域 spec 定义。

## 用户故事

> 作为 Nyx 的维护者，我想用 Railway 部署多角色宿主，以托管 PostgreSQL 保存宿主和角色状态、持久化卷保存角色卡及 workspace 文件，使手机、QQ 和桌面客户端共享同一份可恢复状态。

## 验收标准

- [ ] `NYX_DATABASE_URL` 非空时使用 PostgreSQL；为空时沿用 `NYX_DB`/SQLite 的现有选择和旧存档兼容语义。
- [ ] PostgreSQL 的宿主数据及每个角色的数据在同一数据库的独立 schema 中隔离；每个角色仍使用自己的连接、事务边界和 lifecycle。
- [ ] PostgreSQL 与 SQLite 对领域查询、排序、事务回滚、幂等、投递重放、关闭语义提供相同可观察结果。
- [ ] PostgreSQL 模式支持 LangGraph checkpoint、tool execution receipt 和 transport outbox 的恢复。
- [ ] 事件 SSE 顺序仍为 durable admission 顺序；schema 52 的 `event_log.event_seq` 是 SQLite 与 PostgreSQL 共用的稳定序列键，不依赖 `rowid`、`ctid` 或无序查询。
- [ ] 角色文件继续使用 `data/characters/<id>/{card,workspace}`；内置 Nyx 资源只读，其他持久文件位于 `/app/data` Volume。
- [ ] SQLite 到 PostgreSQL 的一次性迁移可重复预检；实际导入失败不修改或删除源 SQLite/文件。
- [ ] Docker Compose 启动应用和 PostgreSQL，停止/重建容器后数据保留在 volume；Railway 使用相同镜像、托管 PostgreSQL 与挂载到 `/app/data` 的 volume。
- [ ] Railway 对外监听平台注入的 `PORT`；TLS 由 Railway 公网入口提供，应用仅在容器内提供 HTTP。
- [ ] 真实部署不要求 Caddy 容器；本地 HTTPS 既有 Caddy 契约继续有效。
- [ ] 备份和恢复同时覆盖 PostgreSQL 与 `/app/data`，恢复后验证宿主、角色、角色卡、workspace、书籍文件和凭证撤销状态。

## 配置与存储边界

### 后端选择

- `NYX_DATABASE_URL`：PostgreSQL 连接 URL。只从进程环境读取，不写入 `config.yaml`、角色卡或日志。
- `NYX_DATABASE_URL` 未设置或为空时，沿用 SQLite，`NYX_DB` 与旧默认路径的优先级不变。
- 两种后端在一个进程中互斥，不双写，也不自动从 SQLite 切换到 PostgreSQL。
- 本地 pytest 默认仍使用 SQLite；PostgreSQL 集成验收通过专用测试数据库 URL 显式启用。

### PostgreSQL schema

- `host` schema 对应现有 `data/characters/host.db`，保存角色注册记录、当前选择、共享 presence、配对码与设备 token 摘要。
- `character_<id>` schema 对应一个角色数据库，保存该角色的全部领域状态、事件、投递、checkpoint 和传输 outbox。
- `<id>` 必须来自已校验的角色卡 ID；创建 schema 使用参数化/安全标识符 API，不拼接任意外部标识符。
- schema 使用现有宿主/角色各自的迁移版本，不能把宿主表混入任一角色 schema。
- PostgreSQL 角色连接设定其 schema 的 `search_path`；同一事务不跨 schema 写入，继续保持 04 定义的本地事务 + durable event 模型。
- PostgreSQL 角色 checkpoint 表由锁定版本的官方 `AsyncPostgresSaver.setup()` 创建和迁移，使用其 `checkpoint_migrations`、`checkpoints`、`checkpoint_blobs`、`checkpoint_writes` 布局；通用 schema 编译器不得预建同名但 SQLite 类型/列不兼容的 `checkpoints` 或 `writes` 表。SQLite 仍由 schema 49 创建 `checkpoints` / `writes` 并使用 `AsyncSqliteSaver`。

### 持久文件

以下文件位于 Railway Volume 的 `/app/data`，保留现有相对布局：

- 角色卡快照及图片资源：`data/characters/<id>/card/`。
- 角色 workspace：`data/characters/<id>/workspace/`。
- 阅读/活动产生的持久文件（仅限现有流程确实写入磁盘的文件）；具体相对路径仍由 09、12 和 14 定义。
- 本地 SQLite 文件仅用于 SQLite 模式，不会在 PostgreSQL 模式作为运行时事实来源。

应用镜像内的 `nyx/cards/nyx/` 是只读内置资源，不复制到 Volume；首次运行生成的 Nyx 注册记录仍保存于 host schema，legacy workspace 仍遵守 14 的既有路径语义。

不得写入 Volume：EPUB 原始上传内容、临时上传缓冲、日志、LLM/OneBot 凭证和运行时缓存。EPUB 在内存中解析，书籍与段落内容保存在角色 schema；创作产物保存在角色 workspace。

## SQLite 到 PostgreSQL 迁移

迁移预检入口：`python -m scripts.migrate_sqlite_to_postgres --source <data-root>`；预检只读并输出
host/角色数据库、版本、行数和文件目录。实际导入必须显式使用
`--import --target-data-root <data-root>`，并通过 `NYX_DATABASE_URL`（或
`--database-url-env` 指定的环境变量）提供目标 PostgreSQL URL。

1. 预检发现 host SQLite、所有已登记角色 SQLite、schema 版本和角色文件目录；只读，不改源。
2. 目标只允许为空的 host/character schema。已有目标数据时停止，不覆盖、不合并。
3. 按源 schema 的外键依赖顺序导入；SQLite `rowid` 语义映射到 schema 52 的 `event_log.event_seq`，event_log 顺序保持一致。
4. 同步复制角色卡与持久文件到目标 `/app/data` 相对布局；不删除源文件。
5. 校验每个表的行数、主键集合、event_log 顺序、schema 版本、角色注册路径映射及 device token hash。
6. 所有 schema 和文件校验通过后才写出成功报告。失败时删除本次创建的目标 schema/新复制文件，源数据保持原状；不能在失败时留下可启动的半迁移目标。
7. 迁移完成后通过设置 `NYX_DATABASE_URL` 显式切换运行时。回退时先停止写入并保留 PostgreSQL；恢复旧 SQLite 只代表切回迁移前快照，迁移后新增的云端写入不会自动回灌。

导入器当前接受 host schema 2 和角色 schema 51/52；schema 51 的 `event_log.rowid`
会显式映射为目标 schema 52 的 `event_seq`。非空 SQLite checkpoint 表暂不导入，导入器会拒绝
继续，避免把 SQLite saver 布局静默写入 PostgreSQL 官方 saver 表。

迁移工具不得导出 token 原文；凭证表只搬运摘要。PostgreSQL URL 通过指定环境变量名读取，报告仅记录目标 schema 和校验结果，不输出连接凭证。

## 对象完整性

### 宿主注册与共享状态

| 入口 | 消费者 | PostgreSQL 位置 | Volume 内容 |
|---|---|---|---|
| 内置 Nyx 首次注册、角色卡上传、恢复/重试、角色选择 | `_Host` 生命周期、角色管理 API、当前角色请求准入 | `host.characters`、`host.selection` | 卡片快照和 workspace |
| 桌面观察、聊天在线事实、宿主恢复 | presence FIFO、角色观察 handler、表达上下文 | `host.presence_*` 及各角色事件/游标 | 不适用，纯数据库状态 |
| 配对码生成/兑换、设备撤销 | 配对 API、host/role API guard、SSE 与图片入口 | `host.pairing_code`、`host.device_token` | 不适用；仅保存 token 摘要 |

入口和消费者的请求、事件、事务、失败、重放及删除语义以 04、14、22、23 为准；本 spec 只改变数据库物理承载。

### 角色状态与事件

| 入口 | 消费者 | PostgreSQL 位置 | Volume 内容 |
|---|---|---|---|
| 聊天、记忆、欲望、内在生命、反思、活动、评估 | 对应 Facade/Store、Prompt、后台 runner、API | `character_<id>` 下现有迁移定义的表 | 活动创建的持久 workspace 文件 |
| EPUB 上传、划线、书签、笔记、阅读进度 | ReadingFacade、阅读 API、活动选材和陪读上下文 | 书籍、段落与阅读表 | 原始 EPUB 不落盘；不适用 |
| durable event admission、handler delivery、effect marker | EventBus、消费者 worker、SSE reader | `event_log`、`event_delivery`、`event_effect` | 不适用 |
| LangGraph 回复、工具执行、OneBot 输出 | expression、tool pipeline、transport supervisor | `checkpoints`、`writes`、`tool_execution`、`transport_*` | 不适用 |
| 角色卡/头像读取和 workspace 文件读写 | 角色装配、Prompt、资产 API、文件工具 | host 的角色路径记录 | `card/`、`workspace/` |

领域表的完整写入入口、消费者、状态迁移和 Bad case 继续由 04、06–12、14、17–21 唯一完整契约定义；PostgreSQL 模式不得遗漏任何现有入口或消费者。

## 状态迁移与 Bad cases

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| SQLite runtime | `NYX_DATABASE_URL` 未设置 | SQLite runtime | 保留旧路径优先级和既有迁移语义 |
| 无 DB 后端 | PostgreSQL URL 非空 | PostgreSQL runtime | 启动时校验连接、迁移及 checkpoint；失败关闭已创建连接并拒绝服务 |
| SQLite 数据 | 迁移预检 | 只读预检结果 | 不创建/修改目标，不修改源 |
| SQLite 数据 | 目标为空且全部校验成功 | PostgreSQL 数据可切换 | 源库及源文件保留，报告成功 |
| SQLite 数据 | 导入或校验失败 | 源仍可用；目标回到迁移前状态 | 撤销本次 PostgreSQL schema 与文件副作用并报告失败 |
| 运行中的 PostgreSQL 数据 | 备份恢复 | 停机恢复后的 PostgreSQL + Volume | 两者版本须匹配并通过启动检查后再接入客户端 |

| Bad case | 处理 |
|---|---|
| 空 | 空目标允许首次迁移；空角色 schema 由该角色迁移清单初始化；没有已登记角色时只初始化宿主及内置 Nyx。 |
| 失败 | 数据库连接、迁移或 checkpoint 初始化失败即启动失败并关闭已开启连接；迁移源保持不变。 |
| 部分完成 | 迁移为可回滚步骤；目标不得留下已被视为成功的部分数据，源不删除。 |
| 乱序 | event_log 显式序列保存 admission 顺序；读取和导入均按序列，不按 timestamp 或 PostgreSQL 物理行位置排序。 |
| 重放 | 继续沿用各表唯一键、effect marker、delivery lease 与 transport outbox 的现有幂等语义。 |
| 删除 | 角色卸载仍保留归档；用户明确卸载/删除角色的语义遵守 14。备份清理由运维执行，不因删除数据库行隐式删除共享卷中的其他角色文件。 |

## 部署

- Docker image 以非 root 用户运行，工作目录 `/app`，持久化路径 `/app/data`。
- Compose 包含 Nyx 与 PostgreSQL 服务；数据分别用 named volume 保存。可选 Caddy 仅用于本地 HTTPS 验收。
- Railway 通过托管 PostgreSQL 插件提供 URL，将其映射到 `NYX_DATABASE_URL`；持久化卷挂载 `/app/data`。
- 容器监听 `0.0.0.0:$PORT`。外部 TLS 和 HTTPS 域名由 Railway 提供，`NYX_PUBLIC_ORIGIN` 配置真实 HTTPS origin；不暴露数据库端口。
- API Key、`NYX_AUTH_TOKEN`、OneBot access token 通过 Railway secrets/Compose 环境注入，不放入镜像、仓库或 volume。
- OneBot 若启用，云端服务必须能通过私有网络访问配置的 NapCat/LLOneBot；默认 localhost 地址不适用于 Railway。

## 测试要点

- [ ] SQLite 现有完整测试与 18 个 E2E 驱动保持通过。
- [ ] PostgreSQL 集成测试覆盖 host/角色 schema 隔离、角色启动恢复、事务回滚、event 顺序、设备配对、checkpoint 和 outbox 重放。
- [ ] 迁移测试覆盖空目标、重复预检、目标冲突、导入失败清理、主键/事件顺序/凭证摘要校验及源文件不变。
- [ ] Compose 测试覆盖健康启动、服务重启和容器重建后的数据库与文件持久性。
- [ ] Railway 部署验证 `$PORT`、PostgreSQL URL、Volume、HTTPS origin 和多角色共享状态。
- [ ] 备份恢复覆盖数据库与 Volume，并验证配对撤销状态、角色资产、workspace 和阅读记录。
- [ ] `ruff check`、`pyright`、全量 `pytest`、前端测试、生产构建及完整 E2E。
