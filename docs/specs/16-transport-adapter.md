# 传输适配器系统

> 范围：`nyx/transport/`（新增）——`TransportAdapter` 契约、`transport_outbox` 出向投递、OneBot(NapCat) 适配器、图片降维。
> 本文件是传输适配器系统的唯一完整契约，定义「外部输入 → 脑」与「脑 → 外部输出」的边界、幂等、投递与图片处理。
> 脑（角色）与传输（感官）正交：角色是独立脑，传输是感官，一脑多感官。设计总纲见 `docs/design/plugin-harness-architecture.md`。
> 本 spec 只定义契约（签名 + 语义 + 决策），不内联完整代码；实现以 `nyx/transport/` 源文件为准。

## 元信息

- **前置依赖**：01-types（`Event` / `EventType` / `Source`）、04-module-bus-system（`EventBus`、`RouteSpec`、关停、SSE 游标）、05-tools（`ToolRegistry`）、03-llm（`VisionClient` 复用）。
- **实现文件**：`nyx/transport/adapter.py`、`nyx/transport/outbox.py`、`nyx/transport/onebot.py`、`nyx/api/routes.py`（新增 OneBot WS 端点）。
- **测试文件**：`tests/test_transport/`。

## 用户故事

> 作为 Nyx 的用户，我想让 Nyx 通过 QQ 等外部软件与我聊天，并且她的回复不重复、不丢失、断线能恢复，以便她作为同一个「脑」存在于多个软件上。

## 验收标准

- [ ] `TransportAdapter` 契约：`name` / `start()` / `stop()` / `deliver(payload)`；入向由 adapter 内部事件循环经 `EventBus.publish` 进脑，不进契约方法。
- [ ] 出向至少一次投递：SPEAK/ASK 事件 → `transport_outbox`（pending）→ worker 领取 → `deliver` → 成功标 `succeeded`；失败 `retry_wait` 退避重试，超上限 `dead_letter`。
- [ ] 出向幂等键 `(event_id, transport)`：重放不重复发外部消息。
- [ ] 出向回源路由：按 `correlation_id` 回源到引发对话的 transport；无来源表达（主动搭话/碎碎念）MVP 不推外部。
- [ ] 入向幂等键 `self_id:message_id`：重连后 NapCat 重推同一条 QQ 消息，不产生第二条 `USER_MESSAGE`。
- [ ] OneBot 反向 WS：Nyx 起 WS 端点，NapCat 主动连入；`access_token` 校验失败断连。
- [ ] `meta.connect` 是连接后第一个事件，用于初始化 transport 连接状态；`meta.heartbeat` 超时判定死连接。
- [ ] 只接 `message_type="private"` + `sub_type="friend"`；群聊/临时会话/其他丢弃。
- [ ] 图片降维：image 段经 `get_image` → 下载 bytes → 复用视觉客户端描述成文本，拼进 `USER_MESSAGE`；图片本身不持久化。
- [ ] 只发 SPEAK/ASK 出向，THINK/mutter 不发。
- [ ] `ruff check`、`pyright`、`pytest` 全绿；`docs/test-inventory.md` 同步。

## 技术方案

### 分层与边界

传输适配器是「感官」，职责是把外部事实转成脑能消费的 `Source.EXTERNAL` 事件，并把脑的可见表达
（SPEAK/ASK）送到外部通道。它不承载任何领域逻辑（记忆/欲望/活动判断都在脑内）。

```
外部(QQ/手机/桌面) ──入向──> TransportAdapter ──EventBus.publish──> 脑(现有 Facade)
脑 ──SPEAK/ASK 事件──> transport router ──transport_outbox──> TransportAdapter.deliver ──> 外部
```

### TransportAdapter 契约

```python
class TransportAdapter(Protocol):
    name: str                                  # "onebot" | "desktop-sse" | "mobile-ws"
    async def start(self) -> None: ...         # 建立连接/注册入向事件循环
    async def stop(self) -> None: ...          # 关停，回收连接与任务
    async def deliver(self, payload: dict) -> None: ...  # 出向：发送一条；失败抛异常供 outbox 重试
```

- `deliver` 的 `payload` 结构由 transport 自行定义；outbox 只透传，不解析。
- 入向（外部 → 事件）是 adapter 内部实现细节：读连接、校验、构造事件、`EventBus.publish`（durable admission）。

### 入向：外部事实 → 事件

1. adapter 从外部通道读到一条消息；
2. 幂等校验：`self_id:message_id` 已受理则丢弃（对应现有 `POST /api/observe` 的 `sample_id` 幂等心智）；
3. 提取文本（text 段拼接）+ 图片降维描述；
4. 构造 `USER_MESSAGE`（`Source.EXTERNAL`，`correlation_id` 新生成），`EventBus.publish`；
5. 登记回源：`session_origin[correlation_id] = (transport_name, target)`（内存 map，重启清空）。

### 出向：事件 → 外部通道（transport_outbox）

1. `transport router` 订阅 SPEAK/ASK 事件；
2. 查 `session_origin[correlation_id]`，命中则对该 transport 写一条 outbox（`payload` 含目标与文本）；
   未命中（无来源表达）→ 不写外部 outbox（MVP，避免 QQ 打扰）；
3. 每个 transport 的 outbox worker 领取自己的 `pending` → `deliver` → 成功标 `succeeded`；
4. 失败进入 `retry_wait` 退避重试，超上限 `dead_letter`。

出向语义与 04 的 `event_delivery` 同构但方向相反（出向发送而非入向消费），因此独立成表，不复用
`event_delivery`（避免 `consumer_id` 语义混入外部通道）。

### OneBot 实现契约

- **连接**：反向 WS，合并连接（api + event 同一连接）。Nyx 起 WS 端点（默认 `ws://host/onebot/v11/ws`），
  NapCat 配 `websocketClients` 的 `url`/`reconnectInterval`/`token` 主动连入；NapCat 负责重连（默认 3000ms）。
- **鉴权**：校验 `Authorization: Bearer` 头或 `?access_token=` query，与 NapCat 侧 `token` 一致；失败断连。
- **生命周期**：`meta_event: connect`（首个事件，含 version）→ 标记连接 established；`meta_event: heartbeat`
  连续 N 个 interval 未收到 → 判定死连接，清理 `session_origin` 中该 transport 的待回源项（不再回源，避免错发）。
- **入向映射**：`message` 事件（`private`+`friend`）→ 提取 text 段拼接 + image 段降维 → `USER_MESSAGE`。
- **出向映射**：`deliver(payload={"user_id", "text"})` → `send_private_msg(user_id, [{"type":"text","data":{"text":text}}])`。
- **消息段**：建议 NapCat 配 `messagePostFormat="array"`，直接拿结构化段。

### 图片降维处理

- image 段：`get_image(file)` → 得 `{url}` → HTTP GET 下载 bytes → 复用视觉客户端描述成文本。
- 描述 prompt 与屏幕观察区分（屏幕观察问「用户正在做什么」，聊天图片问「图片内容是什么」），
  语义契约是「一句话中文描述图片内容」；具体 prompt 是实现细节。
- 描述文本以 `[用户发来一张图片：<描述>]` 形式拼进 `USER_MESSAGE` 文本。
- 图片 bytes 与 URL 均不持久化、不入记忆；只保留文本描述（数据最小化）。
- 单条消息最多处理 N 张图（默认 1），超出忽略并提示；图片下载/描述失败记为「用户发了一张图片（无法识别）」，
  不阻断消息其余文本。
- 出向图片不在 MVP 范围。

### 心跳与断线

- `meta.heartbeat` 作健康信号；判定死连接由 NapCat 重连（反向 WS 下 NapCat 主动重连 Nyx）。
- Nyx 侧只做：连接建立/断开时清理 `session_origin` 与 in-flight 出向（避免向已死连接发送）。

## 数据变更

### `transport_outbox`

```sql
CREATE TABLE transport_outbox (
    event_id TEXT NOT NULL,
    transport TEXT NOT NULL,
    status TEXT NOT NULL,          -- pending / processing / retry_wait / succeeded / dead_letter
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at REAL NOT NULL DEFAULT 0.0,
    started_at REAL,
    completed_at REAL,
    lease_until REAL,
    last_error TEXT,
    payload TEXT NOT NULL,         -- 发送所需信息（JSON），重试不依赖 event_log 常驻
    PRIMARY KEY (event_id, transport),
    FOREIGN KEY (event_id) REFERENCES event_log(id)
);
```

索引：

```sql
CREATE INDEX idx_transport_outbox_ready ON transport_outbox(transport, status, available_at);
```

默认常量（对齐 04 出向语义）：

```text
_TRANSPORT_MAX_ATTEMPTS = 5
_TRANSPORT_RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0, 30.0)
_TRANSPORT_LEASE_SECONDS = 300.0
```

## 对象完整性

### 外部消息（QQ message）

**入口清单**

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| OneBot adapter 读 `message` 事件 | WS 连接 established 后收到 `private`+`friend` 消息 | 构造 `USER_MESSAGE` → `event_log` |

**消费者清单**

| 消费者 | 发现方式 | 用途 |
|---|---|---|
| `expression.user_message` 等既有消费者 | 04 的 `RouteSpec` 派生 | 表达回复、presence 在线证据、记忆等 |

**状态迁移表**

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| 收到 QQ 消息 | `self_id:message_id` 未受理 | durable `USER_MESSAGE` 受理 | 返回稳定事件 id |
| 收到 QQ 消息 | `self_id:message_id` 已受理 | 丢弃 | 无副作用（幂等） |
| 收到 QQ 消息 | 非 `private`/非 `friend` | 丢弃 | 无副作用 |
| 收到 QQ 消息 | 文本为空且无 image | 丢弃 | 无副作用（不发空事件） |

**Bad case 表**

| 情况 | 处理 |
|---|---|
| 空 | 文本空且无 image → 丢弃，不发 `USER_MESSAGE` |
| 失败 | `publish` 失败 → 不登记 `session_origin`，外部重推靠幂等键重试；图片下载/描述失败 → 折叠为「无法识别」占位 |
| 部分完成 | image 描述成功、后续 text 段丢失 → 按已解析部分拼接，不重放 |
| 乱序 | 重连后旧消息晚到 → 幂等键已受理则丢弃 |
| 重放 | NapCat 重推同 `message_id` → 幂等键丢弃 |
| 删除 | 不适用：消息只读，无删除路径 |

### transport_outbox 记录

**入口清单**

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| transport router 收 SPEAK/ASK | `session_origin[correlation_id]` 命中本 transport | `transport_outbox`（pending） |

**消费者清单**

| 消费者 | 发现方式 | 用途 |
|---|---|---|
| transport outbox worker | 按 `(transport, status, available_at)` 领取 | 调 `deliver` 发外部 |

**状态迁移表**

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| pending | worker 领取 | processing | 写 lease |
| processing | `deliver` 成功 | succeeded | `completed_at` |
| processing | `deliver` 失败且未超限 | retry_wait | `attempts+1`、`available_at=now+delay` |
| retry_wait | 到期 | pending | 重试 |
| processing/retry_wait | 达到 `_TRANSPORT_MAX_ATTEMPTS` | dead_letter | 停发 |

**Bad case 表**

| 情况 | 处理 |
|---|---|
| 空 | 不产生（只有 SPEAK/ASK 才入 outbox，事件必有文本） |
| 失败 | `deliver` 抛异常 → retry_wait 退避；QQ 拉黑/不可达 → 达到上限 dead_letter |
| 部分完成 | 发送成功但标记失败（崩溃窗口）→ lease 过期重发一次，MVP 接受极端单次重复（OneBot 无发送幂等键） |
| 乱序 | 同一 `(event_id, transport)` 唯一，worker FIFO 领取，不并发同一记录 |
| 重放 | 主键 `(event_id, transport)` 冲突 → no-op |
| 删除 | 不适用：outbox 记录不删除（succeeded/dead_letter 保留审计），可与 event_log 同生命周期 |

### transport 连接（OneBot WS）

**入口清单**

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| NapCat 反向 WS 连入 | `access_token` 校验通过 | 内存连接状态 |

**消费者清单**

| 消费者 | 发现方式 | 用途 |
|---|---|---|
| adapter 事件循环 | 连接对象 | 读消息、回 API 调用 |
| 心跳检测 | `meta.heartbeat` 到达时间 | 判死 |

**状态迁移表**

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| disconnected | 收到 `meta.connect` | established | 初始化连接状态 |
| established | 连续 N 个 interval 无心跳 | disconnected | 清理 `session_origin` 待回源项 |
| established | WS 关闭/鉴权失败 | disconnected | 等待 NapCat 重连 |

**Bad case 表**

| 情况 | 处理 |
|---|---|
| 空 | 不适用：无连接则无状态 |
| 失败 | 鉴权失败断连 → 记录日志，等待 NapCat 重连 |
| 部分完成 | `meta.connect` 后未收到心跳 → 心跳超时判死 |
| 乱序 | 旧连接残留消息晚到 → 幂等键 `message_id` 兜底 |
| 重放 | 重连后重推消息 → 入向幂等键兜底 |
| 删除 | 不适用：连接无持久化对象 |

## 测试要点

- [ ] 单元测试 `tests/test_transport/`：
  - [ ] adapter 契约：`deliver` 成功/抛异常 → outbox worker 分别标 succeeded / retry_wait。
  - [ ] 入向幂等：同 `self_id:message_id` 重放 → 只产生一条 `USER_MESSAGE`。
  - [ ] 回源路由：命中 `session_origin` 写 outbox；未命中（无来源）不写。
  - [ ] 图片降维：mock `get_image` + 下载 + 视觉客户端 → 描述拼进消息文本；失败折叠为占位。
  - [ ] 只接 `private`+`friend`：group/temp/other 丢弃。
- [ ] 集成测试：OneBot WS 端点鉴权通过/失败；`meta.connect` 建立、心跳超时判死。
- [ ] E2E 测试：mock OneBot server → QQ 消息进脑 → SPEAK 回源出向，不重复不丢失。

## 完成定义

- [ ] 本文件统一定义 TransportAdapter 契约、transport_outbox、OneBot 实现、图片降维与对象完整性。
- [ ] `docs/tech-reference.md` 已同步 `nyx/transport/` 实现文件、表数量、API/WS 端点。
- [ ] `docs/facts/transport-system-facts.md` 已同步当前事实。
- [ ] `ruff check` 零报错；`pyright` 零报错；`pytest` 全绿。
- [ ] `docs/test-inventory.md` 已同步测试清单。
