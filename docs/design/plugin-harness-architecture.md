# 插件化 Harness 架构（概念与决策）

> 状态：**已定决策 D1–D13，概念层文档**。本文只保留概念、动机与决策，不定义契约签名。
> 契约（FacadePlugin / PluginContext / 装配器 / TransportAdapter 的签名 + 语义 + 对象完整性审计）
> 见 `docs/specs/20-plugin-harness.md`。本文不改源码、不定义实现。
> 目标读者是参与架构讨论的人；交付验收以 spec 为准。

## 1. 动机与目标

两个目标：

1. **拓宽她的存在边界**：让 Nyx 同时存在于多个软件（QQ、手机、未来嵌入式电子宠物），最终托管到租来的服务器上。
2. **把框架抽成更通用的「人格扮演 agent harness」**：Nyx 只是 harness 上的一个角色 + 一套域插件 + 一张角色卡。

一句话：从「本地桌面单前端应用」演进为「**单脑多感官**的跨端存在」，并让引擎可复用。

## 2. 已定决策

| # | 决策 | 取值 |
|---|---|---|
| D1 | 插件边界 | Facade 级（memory / desire / inner_life / expression / activity / reading / eval） |
| D2 | 内部插件接口 | 进程内直连 Python 接口；MCP 只做跨进程边界 |
| D3 | 能力暴露 | tool 面与 lifecycle 面显式分开；lifecycle 垫在工具层之下 |
| D4 | 多外部接口 | 真实需求（多存在平台），不算过度设计 |
| D5 | QQ 接入 | 把 Nyx 接进 QQ——QQ 是她多个存在面之一 |
| D6 | 云端托管 | Railway 应用 + 托管 Postgres + 持久化卷（数据在用户控制的 Railway 项目） |
| D7 | 拓扑 | 单脑多感官：一个核心实例，多传输接入 |
| D8 | 云端数据层 | Railway 托管 Postgres（云端事实来源）；本地开发/测试继续 SQLite |
| D9 | 桌面采集 | 桌面 app 降级为本地 presence 采集 agent，并入统一传输适配器模型 |
| D10 | 公网认证 | TLS + 根 token（`NYX_AUTH_TOKEN`）+ 每设备 token + 配对码；OneBot 走 `access_token`；不引入账户 |
| D11 | 依赖装配 | 依赖分两档（`requires` 强 / `optional` 弱）；两阶段：强依赖拓扑排序 + 可选能力注入 |
| D12 | 角色×传输 | 正交两维；MVP 单角色多传输；前台角色 per-transport，不做全局前台状态 |
| D13 | 出向投递 | 独立 `transport_outbox` 表 + per-transport 游标；出向至少一次，MVP 接受极端单次重复 |

D14：云端使用 Railway 应用、托管 PostgreSQL 和挂载到 `/app/data` 的持久化卷；同一 PostgreSQL 数据库以 `host` 与 `character_<id>` schema 保持宿主/角色隔离。Docker Compose 用于本地 PostgreSQL 验收，不替代 Railway 部署配置。契约见 [25-hosted-storage-and-postgresql](../specs/25-hosted-storage-and-postgresql.md)。

D5 + D7 合并理解：QQ、手机、桌面、未来电子宠物都是「同一个脑」的感官/存在面，彼此平级，
共享同一套记忆/欲望/内在生命/活动状态。没有哪个是「主」，也没有哪个是「外部附属」。

## 3. 目标架构（六层）

```
┌─────────────────────────────────────────────────────────────┐
│ 6 角色内容层   canon/ask/personality/初始欲望/技能   ← "灵魂"，数据不是代码
├─────────────────────────────────────────────────────────────┤
│ 5 Agent 运行时  LangGraph（现状=expression 回复图；未来 supervisor 编排）
├─────────────────────────────────────────────────────────────┤
│ 4 工具注册层    聚合各插件 tool surface → function-calling / MCP
├─────────────────────────────────────────────────────────────┤
│ 3 Facade 插件层  memory/desire/inner_life/expression/activity/reading/eval
│                每个插件暴露三样：lifecycle hooks + tool surface + 依赖声明
├─────────────────────────────────────────────────────────────┤
│ 2 生命周期底座   EventBus(durable) + 调度(tick/mutter/reflect) + 监督重启
├─────────────────────────────────────────────────────────────┤
│ 1 传输适配器层   SSE+REST / OneBot(QQ) / WebSocket(手机) / 嵌入式宠物
└─────────────────────────────────────────────────────────────┘
```

- **harness = 1 + 2 + 4 + 5**（通用引擎，与「人格扮演」无关）；
- **3 = 人格扮演域插件集**（可复用资产，但属于这个域）；
- **6 = 角色卡**（纯数据）。
- Nyx = harness + 域插件集 + 一张 nyx 角色卡 + 若干传输适配器。

第 2 层是底座：现有 `04-module-bus-system` 的 EventBus（durable admission / delivery / retry /
RouteSpec）+ `runtime.py` 的 tick/监督已经覆盖大半，**不重造**。本重构只在第 2 层之上加一层
「插件身份 + 依赖装配」，并把散落在 `app_context.py` 的手工装配收敛成契约。

## 4. FacadePlugin（概念）

把 `app_context.build_app_context()` 里十几个 late-bound 闭包（`state_reader` /
`observation_reader` / `reading_context_reader` / `claim_return` / `memory_recall` …）以及散在各
Facade 的 `start/restore/quiesce`，统一成一个可声明、可校验的插件接口，替代「跑到一半抛
`RuntimeError("尚未绑定")`」。核心两点：

- **依赖声明**：插件声明 `provides`（暴露什么）/ `requires`（强依赖）/ `optional`（弱依赖）；
  装配器读声明做拓扑排序 + 弱依赖注入，缺依赖启动即 fail fast。
- **循环依赖**：靠「声明 provide + 运行时惰性解析」断开（D11 / O4）。现状有三处环——
  `expression ↔ reading`、`activity ↔ inner_life`、`evaluator ↔ memory`——都由一条 `optional`
  弱边闭合，正是 04 契约里那些闭包在干的事，只是从手写变成框架保证。

完整签名（`FacadePlugin` Protocol、`PluginContext`）见 `docs/specs/20-plugin-harness.md`。

## 5. 传输适配器（概念）

统一模型：**所有输入 = 某处采集 → 传输适配器 → `Source.EXTERNAL` 事件 → 单脑**。这使桌面
presence、QQ 消息、手机消息、电子宠物传感器全部同构。

- 入向复用现有 `EventBus.publish` / `POST /api/observe` 的 durable admission 语义（04 已有）。
- **出向需要 per-adapter 的「已确认发送游标」**：QQ/手机是推送型（非拉取），投递也是
  at-least-once，重连后要能续发不重发。心智与现有 `event_delivery` / `presence_delivery` 的
  per-recipient + lease + retry 一致，只是 recipient 从「角色内 consumer」扩展为「外部 transport」。

第一个实现是 **OneBot adapter**（QQ）：连 NapCat / LLOneBot，QQ 私聊消息 → `USER_MESSAGE`
（`Source.EXTERNAL`），`SPEAK/ASK` 事件 → 主动发 QQ 消息。**Nyx 不是 NoneBot 插件**——不引入
NoneBot2 作为宿主，只实现 OneBot 协议这一条适配器，避免两套插件框架打架。

完整签名（`TransportAdapter` Protocol）见 `docs/specs/20-plugin-harness.md`（阶段 2 边界）。

## 6. 事件分发模型演进

现状 04 已支持 SSE 游标：`id: <character_id>:<event_id>` + `Last-Event-ID` 回放（最多 1000 条），
是「单前端实时流」假设。多端演进点：

- **拉取型 client**（桌面 SSE / 手机 WebSocket）：复用现有游标 + 回放语义，每个 reader 各自游标。
- **推送型 adapter**（OneBot）：不拉取，靠 §5 的 per-adapter 已发送游标防重。
- 事件仍是唯一事实来源，SSE/推送只是广播通道（与 04 一致：SSE 不保证消费完成）。

## 7. 安全共识需要改写的条款

D6（Railway 托管）改变了“数据只在本地 SQLite”的部署假设，但仍保持单用户和基础安全边界。
用户已明确授权将 Nyx 数据存入其自己的 Railway 项目；这不是 Nyx 代运营的第三方 SaaS。以下条款必须明确：

1. **数据位置**：对话/记忆/欲望等从本地迁到用户控制的 Railway 项目。存储引擎为
   Railway 托管 **Postgres**（D8），角色卡、书籍和 workspace 放在 Railway Volume；本地开发/测试继续 SQLite。
   SQLite 到 Postgres 的映射、迁移和备份契约见 [25](../specs/25-hosted-storage-and-postgresql.md)。
2. **公网暴露与认证**：SSE/REST 从 localhost 变公网。**已定（D10）**：
   - **TLS 强制**：反向代理（Caddy/Nginx + Let's Encrypt）终止 TLS，公网只走 HTTPS。
   - **根凭证**：一个从环境变量注入的强随机 token（`NYX_AUTH_TOKEN`，与现有 LLM key 同源同
     精神）。REST 走 `Authorization: Bearer`；SSE 用自定义流式 fetch 带同一头（不用 EventSource）。
   - **每设备 token**：不把根 token 到处贴，而是给每个 client 发独立设备 token，存一张
     `device_tokens(token_hash, device_name, created_at, revoked_at)`。好处是手机丢失可单独吊销，
     不影响其它端。**这仍是单用户、不是多账户**——没有用户名/密码/注册，只有一张设备准入清单。
   - **配对**：新设备首次用一次性、短时效配对码换取设备 token（桌面/服务器端生成，手机输入）。
   - **OneBot 认证**：OneBot 连接用协议自带 `access_token`，服务端校验。
   - **不做**：用户名/密码、OAuth 第三方登录、RBAC 权限、端到端加密——仍守「单用户、基础安全」。
   - **分阶段**：阶段 3 先上「TLS + 单根 token」跑通多 reader；阶段 4 上「每设备 token + 配对码」。
3. **LLM 出站**：不变——仍在服务器出站，不经过用户本地，仍走 HTTPS。
4. **桌面 presence 采集**：已定（D9）——桌面 app 降级为「本地采集 agent」，经传输适配器上报。
   屏幕观察/窗口标题仍是本地能力，但上报路径与 QQ/手机同构。

## 8. 分阶段路线（每步一个可验证检查点）

| 阶段 | 内容 | 检查点 |
|---|---|---|
| 0 | 定稿本设计 + 落正式 spec（对象完整性审计 + 验收标准） | 用户审核通过；契约签名定死 |
| 1 | 纯重构：FacadePlugin 契约 + 装配器，收敛 `app_context` 手工装配 | **零行为变化**：现有 `pytest` 全绿 + E2E 通过 |
| 2 | 传输适配器抽象 + 第一个 OneBot adapter（QQ） | 本地连 NapCat，QQ 消息↔脑 往返；不重复发消息 |
| 3 | 多 client 事件游标 + 认证（token + TLS） | 两个 reader 各自断线重连不丢不重 |
| 4 | 每设备 token/配对码、Railway 云端部署、手机端接入、SQLite→Postgres 迁移 | 设备配对契约见23；手机与桌面共享状态；Postgres 成为云端事实来源。剩余事项见 [统一待办清单](../TODO.md) |
| 5 | harness 框架化：抽 1/2/4/5 层为独立包，Nyx 作为角色+域插件挂载；按需上 MCP / supervisor | 第二个角色卡在 harness 上跑通 |

阶段 1 是收益最大、风险最低的一步：只改装配，不改行为，用全绿测试做安全网。阶段 1 的实现契约
与对象完整性审计见 `docs/specs/20-plugin-harness.md`。

## 9. 决策归档（原开放问题，已全部关闭）

- ~~O1 云端数据层~~：已定 Postgres（D8）。迁移与备份策略见阶段 4。
- ~~O2 公网认证~~：已定（D10），见 §7.2。
- ~~O3 桌面采集的角色~~：已定（D9）——桌面 app 降级为本地采集 agent，并入统一模型。
- **O4 循环依赖规则**（已定，D11）：依赖分两档——`requires`（强，缺失 fail-fast）+ `optional`
  （弱，缺失返回 None/降级默认）。装配两阶段：① 按强依赖拓扑排序构造（强依赖必须无环）；
  ② 注入可选能力。落法：reading 强依赖 expression；expression 的 `reading_context` 是可选依赖，
  装配器在 reading 完成后注入 `reading.build_reply_context`（未绑定降级空串，与现状一致）。
- **O5 多角色 × 多传输**（已定，D12）：「角色」与「传输」是正交两维——角色 = 独立脑（现有
  `_Host` 已支持多角色），传输 = 感官（新增维度）。最终模型是多角色 × 多传输，但 MVP 先单角色
  跑多传输验证抽象。角色路由：每个 transport 维护自己的「当前角色」指针，默认唯一角色；
  **不引入跨 transport 的全局前台状态**（避免「QQ 切角色、桌面跟着切」的隐式全局）。
- **O6 输出投递幂等**（已定，D13）：新建独立 `transport_outbox` 表
  `(event_id, transport, status, attempts, available_at, lease_until, last_error)`，不复用
  `event_delivery`（后者是入向消费语义，与出向发送在生命周期/重试/关停上都不同）。出向至少一次
  投递 + per-transport 游标；OneBot 无发送幂等键，MVP 接受进程崩溃后的极端单次重复，记为已知限制。

## 10. 明确不做的（防过度设计）

- 不在阶段 1 引入 MCP——进程内插件直连 Python 接口，MCP 只留给跨进程/远端（阶段 5 再议）。
- 不引入 NoneBot2 作为宿主——只做 OneBot 协议适配器，避免双插件框架。
- 不重造 EventBus/RouteSpec/ToolRegistry——复用 04/05 既有底座，只在其上补「插件契约」。
- 不为「未来可能的」插件类型预留配置项；每个抽象都先有至少一个真实消费者。
