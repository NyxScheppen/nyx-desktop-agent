# 多客户端续传与 HTTPS 认证

> 阶段三；用户于2026-10-30审核。复用SSE、SQLite和现有访问guard，不增加数据库表。

## 元信息与验收

- 实现：`nyx/events/bus.py`、`nyx/api/routes.py`；前端`api/client.ts`、`hooks/useSSE.ts`、
  `stores/connectionStore.ts`、`App.tsx`、设置及角色图片组件；根`Caddyfile`。
- 两个reader读取同一角色，各自断线、续传和慢读，已提交事件不丢、同连接不重复。
- REST/SSE/角色图片使用同一Bearer凭证；TLS由本机Caddy终止，后端仍仅监听回环8000。
- 不修改LLM、领域事件、OneBot队列、数据库schema或角色选择语义。

## 事件流契约

`EventBus.stream_events(after: str | None, queue_size: int) -> AsyncIterator[Event | None]`
是SSE的日志读取入口。每个迭代器独立保存位置，按`event_log.event_seq`升序分页（每页1000）。
`event_seq`只作数据库内排序，不作为公开游标；帧仍用`character_id:event_id`，时间仍取事件本身。
首次无游标取最近1000条、按提交序发送；已知游标从对应行之后持续读；未知游标从日志开头分页恢复。
跨角色游标409。管理查询`list_events`及`/events/log`保留既有时间排序契约。
队列只作唤醒，丢提示不丢日志；空闲至多15秒补扫，并发送SSE注释心跳。
断开/卸载时关闭迭代器、移除sink；数据库失败传播并断流，客户端带最后完整帧ID重连。
前端按完整SSE帧推进游标，保留原payload校验、事件ID去重、角色scope和代数检查；网络失败后1秒重连。
处理UTF-8跨块、CRLF、注释、多行data和同块多帧。401停止重连并请求重新输入凭证。
客户端游标属于当前hook与角色生命周期；页面重载由现有历史快照恢复，服务端不存reader登记表。

## 认证与TLS

- `NYX_AUTH_TOKEN`：服务启动时读取；空值沿用本地模式，非空启用根token。常量时间比较，不记录凭证。
- `NYX_PUBLIC_ORIGIN`：可选单一HTTPS origin（含可选端口，不含路径/凭证/query/fragment）；
  非空时必须同时设置token，否则启动失败。它是Origin许可值，不改变后端监听地址。
- 所有`/api`业务入口（含导出、上传、角色资产、命名空间和旧别名）要求Bearer头；
  `/api/ready`保留本地launcher身份握手，Caddy拒绝代理此路径。预检不要求token，严格限制Origin、方法和头。
- 保留现有Host/Origin/Content-Type校验；新增Origin只接受配置值。
  Caddy覆盖转发Host为127.0.0.1:8000，不向公网暴露8000；HTTPS入口不代理ready。
- 前端浏览器生产构建与API同源；Tauri生产构建仍连接回环地址。REST与SSE携带Authorization；
  角色图片通过认证fetch取得blob URL，切换/卸载释放，不把token放入URL、构建变量或日志。
- `connectionStore`仅保存token与required；token放sessionStorage，401切到输入界面，提交后重新初始化；
  设置可清除凭证。清除/认证失败会卸载活动界面，取消SSE、采样和旧请求。
- 从仓库根目录启动根`Caddyfile`；它用`NYX_PUBLIC_ORIGIN`作为站点地址，服务`frontend/dist`并代理/api；
  本机验收使用localhost:8443与内部CA，域名接入改用公开证书。验收显式信任测试CA，不跳过证书验证。
  不修改系统证书信任库；不新增设备token、配对码或账户。
- 本机启动需在后端和Caddy进程环境设置相同的`NYX_AUTH_TOKEN`及`NYX_PUBLIC_ORIGIN=https://localhost:8443`，
  启动Caddy后在浏览器打开该地址。Caddy全局关闭自动安装信任；需要浏览器静默使用本机CA时由用户主动运行`caddy trust`。
  公网域名接入时将Origin改为实际HTTPS域名，并从Caddyfile删除`tls internal`。

## 对象完整性

| 对象 | 全部入口 | 全部消费者 |
|---|---|---|
| reader游标 | 首次连接、完整帧、断线重连、角色切换 | SSE日志扫描、客户端重连与去重 |
| 根凭证 | 环境变量；前端输入/sessionStorage恢复/清除 | host及角色guard、REST、SSE、图片fetch、输入界面 |
| 图片blob | 既有卡片注册/内置资产经认证GET | EmotionSprite；旧直接img入口改用相同认证边界 |

| 对象/状态 | 条件 | 下一状态/副作用与失败落点 |
|---|---|---|
| reader connecting | HTTP成功 | open；只以完整帧更新游标 |
| reader open | 网络断开/EOF | connecting；保留位置重连 |
| reader任意 | 切换/卸载/401 | 取消旧连接；401进入凭证输入 |
| 凭证可用 | 401/清除 | required；取消活动界面，重新输入后初始化 |
| 图片加载中 | fetch成功且scope仍有效 | 展示blob；失败保留角色名称占位 |
| 图片已展示 | 资源/角色/token变化或卸载 | 撤销blob URL，迟到回包不展示 |

无新增持久业务状态；reader与blob随页面/角色生命周期销毁，根凭证随进程环境及浏览器会话管理。

| Bad case | reader | 凭证/图片 |
|---|---|---|
| 空 | 空库心跳，无游标按首次策略 | 本地模式无需token；缺图显示名称 |
| 失败 | DB错误断流，网络重连 | 错token401；图片错误显示名称 |
| 部分完成 | 仅完整帧作为续传位置 | 不完整图片不展示；blob创建后必须释放 |
| 乱序 | 日志提交序，不以timestamp跳过晚提交 | 旧fetch/旧token响应不得覆盖新角色/凭证 |
| 重放 | 每reader独立，前端按event_id去重 | 重复GET无业务副作用 |
| 删除 | 角色卸载取消连接；未知游标有界分页恢复 | 清除凭证退出；图片卸载撤销blob |

## 验证

- 真实SQLite流：独立reader、超过1000条分页、晚提交旧时间戳、队满、未知游标、资源回收。
- API：根/角色/资产/旧别名认证、预检、非法Host/Origin、公开origin缺token拒绝启动。
- 前端：分块SSE、独立续传/取消/401、REST头、输入/清除及图片blob回收。
- 本机Caddy真实HTTPS：校验测试CA，双reader分别断线恢复，比较全部event_id，检查401和ready隔离。
- Ruff、Pyright、全量pytest、前端测试、生产构建及代码审查；同步测试快照和受影响契约。
