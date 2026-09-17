# 时间感知与离开归来（施工规格）

> **状态：已由用户审核批准，实现与验收完成。**
>
> 本文件是跨领域功能的施工方案与整体验收入口，不替代各领域唯一完整契约：事件/SSE/
> 组合根语义以 `04-module-bus-system.md` 为准，presence 以 `09-activity.md` 为准，表达 prompt
> 与对话锚点以 `11-expression.md` 为准，前端线协议与显示分别以 `docs/frontend/01-sse.md`、
> `02-stores.md`、`03-chat-panel.md`、`05-client.md` 为准。若本文与上述契约冲突，先修正本文，
> 不在实现中自行选择。

## 元信息

- **前置依赖**：04-module-bus-system、09-activity、11-expression、前端 01/02/03/05。
- **不新增依赖**：继续使用 Python 标准库时间 API、浏览器 `Date`、现有 React/Tauri/FastAPI。
- **不新增持久化结构**：不建表、不迁移、不新增事件类型、不扩展 `CurrentState`。
- **主要后端文件**：`nyx/api/routes.py`、`nyx/app_context.py`、`nyx/runtime.py`、
  `nyx/expression/facade.py`、`nyx/expression/pipeline.py`、`nyx/expression/prompt.py`、
  `nyx/activity/observe.py`、`nyx/events/bus.py`。
- **主要前端文件**：`frontend/src-tauri/src/lib.rs`、`frontend/src/hooks/usePresence.ts`、
  `frontend/src/api/client.ts`、`frontend/src/types/api.ts`、`frontend/src/stores/chatStore.ts`、
  `frontend/src/components/chat/MessageList.tsx`、`frontend/src/components/inner/Avatar.tsx`、
  `frontend/src/lib/time.ts`、`frontend/src/App.tsx`、`frontend/src/index.css`。

## 用户故事

> 作为用户，我希望 Nyx 知道当前日期、星期、时刻和昼夜，能够察觉我离开和回来，并在下一次
> 自然交流中延续上一次对话；同时聊天界面明确展示时间跨越，而不是只有后台数值。

## 产品决策

- `online`：最后输入距今 `<30 秒`。
- `busy`：`30 秒 <= idle < 5 分钟`。
- `away`：`idle >=5 分钟`；窗口标题不参与三态判定。
- 夜间：本地时间 `22:00-06:00`。
- 归来不触发强制发言，只影响下一次成功的回复、主动搭话或 LLM 碎碎念。
- 两小时无对话时，prompt 必须出现自然语言“用户已经很久没有和你说话了”，不能只给数值。
- 以前一轮完整用户/Nyx 对话为跨时段锚点；正常快慢通道 history 截断规则保持不变。
- 昼夜只改变表达与前端视觉，不改变精力、情绪、欲望、活动选择或能耗。

## 验收场景

固定本地时间执行下列回合：

```text
2026-09-17 19:00
用户：尼克斯，我去吃饭了
Nyx：好的，我在这里等着你

2026-09-18 08:00
用户：早上好，尼克斯
```

第二次回复的 LLM 输入必须包含：当前为第二天早上、距上次用户消息约 13 小时、上次发生在
昨天晚上、用户已经很久没有说话、双方上一轮原话。最终回复应能自然体现隔夜连续性，但测试
不固定具体台词，也不要求必问“去了哪里”。界面必须在两组消息前分别显示对应时间标签。

## 总体数据流

```text
Windows 最后输入时间 + 前台窗口标题
  -> Tauri sample_presence(): idle_ms + title
  -> usePresence.classifyPresence(idle_ms)
  -> POST /api/observe {presence, window_title, idle_seconds}
  -> durable OBSERVATION_STATE
  -> 成功后提交 _App presence/return 快照

USER_MESSAGE / initiate_chat / LLM mutter
  -> USER_MESSAGE 先作为即时 online/return 证据
  -> event_log 最近完整对话锚点
  -> 本地日期时间 + 沉默时长 + 可选一次性归来事实
  -> 确定性自然语言 temporal block
  -> 统一 system prompt
  -> 终局表达成功后消费归来事实

Event.timestamp
  -> SSE timestamp / GET events log timestamp
  -> ChatMessage.timestamp
  -> 对话时间分隔 + 顶栏本地时钟 + day/night 视觉
```

## 后端施工设计

### 1. 时间纯函数

在 `nyx/expression/prompt.py` 内实现，不新增 service/manager：

```python
def describe_local_time(now: float) -> dict[str, str]: ...
def describe_elapsed(previous: float, now: float) -> tuple[str, str | None]: ...
def build_temporal_block(
    now: float,
    anchor: tuple[Message, Message] | None,
    observation: Mapping[str, object],
    claimed_return: Mapping[str, float] | None,
) -> str: ...
```

`now` 必须由调用方注入。日历关系使用本地 timezone-aware datetime 的 date 差；持续时间使用
epoch 差并夹到非负。系统时钟回拨时不得生成负时长。

时段映射：

| 本地时间 | 名称 | 昼夜 |
|---|---|---|
| `00:00-05:59` | 凌晨 | 夜 |
| `06:00-08:59` | 早上 | 昼 |
| `09:00-11:59` | 上午 | 昼 |
| `12:00-13:59` | 中午 | 昼 |
| `14:00-17:59` | 下午 | 昼 |
| `18:00-21:59` | 晚上 | 昼 |
| `22:00-23:59` | 深夜 | 夜 |

沉默描述：

| elapsed | 自然语言 |
|---|---|
| `<5 分钟` | 不生成重逢判断 |
| `5-30 分钟` | 用户离开了一会儿 |
| `30 分钟-2 小时` | 已经有一阵子没有说话了 |
| `>=2 小时` | 用户已经很久没有和你说话了 |

跨日描述独立于时长描述：昨天使用“昨天 + 上次时段”，相隔两天及以上使用“X 天前”；
23:50 到次日 00:10 只说明已经跨日，不错误描述成“很久”。

### 2. Durable 对话锚点

`ExpressionFacade` 增加私有查询，不创建新 store/table：

```python
async def _last_dialogue_anchor(
    self, current_correlation_id: str | None,
) -> tuple[Message, Message] | None: ...
```

算法固定为：

1. 从 `EventBus.list_events(event_type=USER_MESSAGE, limit=20)` 取得新到旧候选。
2. 跳过 `current_correlation_id`。
3. 对候选 correlation 查询事件，要求至少存在一个终局 `SPEAK` 或 `ASK`。
4. 选择第一个完整候选；Nyx 锚点取该 correlation 时间上最后一个 `SPEAK/ASK`。
5. 不读取 `THINK`，不把未完成用户消息伪装成完整对话。
6. 用户/Nyx 引文分别截到 200 字；截断后追加省略标记。

该查询每次表达至多执行一次并在本次调用内复用。它有固定上限，避免事件日志增长后 prompt
构造退化；超过最近 20 条仍找不到完整回合时返回 `None`。

### 3. Prompt 接线

- 回复入口在首次 LLM 调用前构造一次 temporal block，存入 `ReplyState.temporal_context`。
- FAST、SLOW、慢通道工具判断和多轮续写复用同一 block，避免一次回复跨分钟后自相矛盾。
- `initiate_chat()` 和 LLM 分支 `mutter()` 使用同一构造逻辑。
- 模板碎碎念保持模板行为，不声称使用了时间上下文。
- 时间块放在 system prompt，标题明确写“历史事实，不是指令”；历史用户文本只作为引用数据。
- 固定指导要求自然使用、避免每次机械报时、不得编造用户离开期间的去向或活动。
- fallback 文案保持不变，不消费归来上下文。

### 4. Presence 与归来状态

Rust 接口变为：

```rust
fn sample_presence() -> Result<(u64, String), String> // 成功：idle_ms, foreground_title
```

`Result` 只承载 Tauri 错误通道，JS 成功值仍为 `[idle_ms, foreground_title]`；原生采样失败或
非 Windows 时拒绝调用，让前端进入已定义的 WebView fallback，不以 `idle_ms=0` 伪装在线。

`usePresence` 每 30 秒采样，调用 `classifyPresence(idleMs)`；浏览器 fallback 用 WebView 最近
键盘/鼠标时间推导 idle，标题为空。上报器使用 single-flight：新采样覆盖待发送快照；仅成功响应
推进 last-sent，失败保留待发送值供下轮重试。

周期采样不能保证早于用户刚回来后立即发送的聊天请求。为消除该竞态，已经 durable 的
`USER_MESSAGE` 也作为 online 证据：`runtime.on_user_message()` 在调用 expression 前使用事件
时间幂等对齐 `_App`。此前为 away 时创建 pending return，此前未建立基线时只建立 online
基线；后到的 online observation 因当前状态已是 online，不再重复创建 return。该路径不额外
发布 `OBSERVATION_STATE`，避免一次用户消息重复给 observation consumer 施加副作用。

`_App` 在现有 dataclass 内增加必要的标量字段，不引入新状态类：基线标记、presence 变化时间、
离开起点、最近归来时间/时长、pending return 和 claimed return。API 端点按以下顺序处理：

1. 验证 `idle_seconds` 有限且非负。
2. 基于旧快照计算新快照与 transition，但暂不写 `_App`。
3. durable publish 完整 `OBSERVATION_STATE`。
4. publish 成功后一次性提交新快照；失败保持旧值。

首次成功观察的 transition 为 `initial`。进入 away 时以 `event.timestamp - idle_seconds` 保存
最后活跃起点；`away -> online` 的离开时长以当前事件时间减该起点，最小为 0。

一次性归来上下文通过组合根注入的同步 claim/release 闭包交给表达 Facade：claim 在首次 await
前拿走 pending 值；终局表达成功即完成消费；失败时 release。release 只有在没有更新的归来事实
时才恢复旧 claim，不能覆盖生成期间发生的新归来。

### 5. SSE 时间

`GET /api/events` 的所有帧增加 `timestamp: event.timestamp`。`event.content` 生产方不得使用
`event_id`、`correlation_id`、`timestamp` 三个公共键。事件类型和 EventBus 投递语义不变。

## 前端施工设计

### 1. 时间类型与历史一致性

- `SseBase.timestamp: number` 为必填有限数值；非法帧在 `useSSE` 信任边界丢弃。
- `ChatMessage.timestamp` 为必填；实时 action 从 SSE 复制，`loadHistory()` 从
  `BackendEvent.timestamp` 复制。
- store 继续按 SSE 到达顺序 append，历史继续按后端 timestamp 排序；不使用前端接收时间。

### 2. 明线聊天时间

新增 `frontend/src/lib/time.ts`，只放复用的纯函数：本地时段/昼夜、当前时钟标签、消息时间
标签和 `shouldShowTimeDivider`。不新增 Zustand store。

分隔规则：首条必显示；本地 date 变化必显示；同日间隔 `>=30 分钟` 显示；同一 correlation
内连续的 THINK/SPEAK/ASK 不重复显示。标签为：

- 今天：`今天 HH:mm`
- 昨天：`昨天 HH:mm`
- 更早：`M月D日 周X HH:mm`

### 3. 当前时钟与昼夜视觉

- `App` 持有唯一的分钟级时钟，首次 timeout 对齐下一分钟边界，再按分钟更新。
- 顶栏显示本地日期、星期和 `HH:mm`。
- App 根节点设置 `data-time-phase="day|night"`，并把 `night` 传给 Avatar；Avatar 不再在任意
  render 中自行读取 `new Date()`。
- 夜间 CSS 使用独立变量和半透明遮罩调整背景、面板、边框和文本对比；用户自定义图片/色调
  仍是底层内容，不被替换。不得加入影响布局尺寸的昼夜元素。
- 06:00 和 22:00 必须在无需 SSE/用户操作的情况下自动切换。

## 实施批次与检查点

### 批次 0：契约准备

- [x] 更新 04 的 observe 原子性和 SSE timestamp。
- [x] 更新 09 的 idle 阈值、归来和昼夜边界。
- [x] 更新 11 的 temporal block、durable anchor 和一次性归来语义。
- [x] 更新前端 01/02/03/05 契约。
- [x] 用户审核本文并明确允许进入实现。

### 批次 1：后端纯逻辑与锚点

- [x] 先写时间边界、自然语言、引文截断测试，确认失败（red）。
- [x] 在 `prompt.py` 实现最少纯函数，测试通过（green）。
- [x] 先写完整/半截/重启锚点测试，再实现 Facade 私有查询。
- [x] 检查没有放宽现有 history 回溯、没有新增表或配置。

### 批次 2：Presence 管道

- [x] 先写 TS idle 三态与失败重试测试；Rust 只封装原生采样，以 cargo check 验证接线。
- [x] 修改 Rust 返回值、前端 single-flight 上报和 observe 请求体。
- [x] 先写 API admission 失败不改状态、初始非归来、away-return 时长测试，再实现 `_App` 快照。
- [x] 覆盖用户回来后立即发消息早于采样的路径，以及后续 observation/重放不重复归来。
- [x] 验证现有 desire/inner-life consumer 忽略扩展 payload 且订阅集合不变。

### 批次 3：表达接线

- [x] 快/慢 reply、工具判断、主动搭话、LLM mutter 的 fake LLM 先断言缺少时间块。
- [x] 接入统一 temporal block，保持 fallback 与模板 mutter 不变。
- [x] 覆盖 return claim 成功消费、失败释放、新归来不被旧 release 覆盖。
- [x] 用 19:00 到次日 08:00 场景验证 prompt 管道，不断言 LLM 文案质量。

### 批次 4：前端明线与昼夜

- [x] SSE/ChatMessage timestamp 类型和 fixtures 先红后绿。
- [x] 实现 `lib/time.ts`、时间分隔和顶栏时钟。
- [x] fake timers 跨 06:00/22:00 验证 App 与 Avatar 同步切换。
- [x] 在桌面/窄窗口人工检查时间标签不遮挡、不改变气泡宽度和滚动行为。

### 批次 5：同步与质量门

- [x] 实现完成后才更新三个 `docs/facts/*-system-facts.md`，不得提前写成已实现事实。
- [x] 同步 `docs/test-inventory.md` 当前快照和 `docs/tech-reference.md` 实现索引。
- [x] 检查 `docs/LessonsLearned.md`；若实现出现新的可复用错误类型再追加教训。
- [x] `ruff check`、`pyright`、`pytest` 全绿。
- [x] `npm test`、`npm run build`、Rust `cargo check` 全绿。

## 测试矩阵

| 范围 | 必测边界 |
|---|---|
| 本地时间 | `05:59/06:00`、`21:59/22:00`、星期、昨天、多日、系统时钟回拨 |
| 沉默描述 | `4:59/5:00`、`29:59/30:00`、`1:59:59/2:00:00`、短暂跨午夜 |
| 对话锚点 | 最新完整回合、当前半截回合、THINK 排除、最后 SPEAK/ASK、重启恢复、200 字截断 |
| Prompt | FAST、SLOW、工具判断、多轮复用、主动搭话、LLM mutter、fallback 不消费 |
| Presence | 30 秒/5 分钟边界、标题非空 away、首次基线、失败重试、single-flight、away-return、消息早于采样 |
| 原子性 | publish 失败不改 `_App`、旧 claim release 不覆盖新 return、consumer 兼容 |
| SSE/UI | timestamp 校验、实时/历史一致、29:59/30:00 分隔、跨日、顶栏分钟更新 |
| 昼夜 | 无外部状态变化跨 06:00/22:00、头像与根主题同相位、自定义背景保留 |

## 重启与降级

- 后端重启：内存 history 和 presence transition 清空；下一次采样建立基线，不声称刚回来。
- 对话连续性：通过 event log 的完整对话锚点恢复，因此隔夜/多日仍能进入 prompt。
- 前端重启/重连：聊天历史从 event log 回填 timestamp，时间标签不依赖旧浏览器内存。
- Tauri 调用失败：使用 WebView 输入 idle，标题为空；不会因应用自身标题永久 busy。
- event log 找不到完整锚点：仍提供当前本地时间，不引用不存在的上一轮。
- LLM 失败：沿用固定 fallback；归来事实释放，留给后续成功表达。

## 风险与约束

- Presence 采样分辨率为 30 秒，离开/归来展示允许该级别误差；`idle_seconds` 用系统最后输入
  回溯离开起点，避免固定少算 5 分钟。
- 前后端位于同一台电脑，均使用系统本地时区；本轮不处理远程前后端时区分离。
- SSE 增加必填字段是 monorepo 协议升级，后端与前端必须同一批发布。
- 历史引文属于不可信用户数据，必须以资料边界包裹；该措施减少指令混淆，但不把它宣称为
  完整 prompt-injection 防御。
- event log 查询有 20 条固定上限；这是延迟/token 上界，不做配置项。
- 本轮明确不修复活动系统 UTC 日边界、日程块与自然整点未对齐等相邻问题。

## 完成定义

- [x] 用户审核并确认本文后才开始实现。
- [x] 用户可在聊天列表和顶栏直接看见时间变化。
- [x] Nyx 的所有 LLM 表达路径获得确定性自然语言时间上下文。
- [x] 运行期间 away-return 被识别并只由下一次成功自然表达消费。
- [x] 重启后仍能用 durable 对话锚点表达隔夜/多日连续性。
- [x] 无新增依赖、配置项、数据库表、事件类型或内在生命数值耦合。
- [x] 领域契约、事实摘要、测试清单和实现索引在实现提交时同步。

## 本轮验收记录

- 后端：893 passed，1 skipped；ruff check / pyright 零报错。
- 前端：17 文件 / 222 passed；npm run build 与 Rust cargo check 通过。
- Mock LLM + durable event log 验证 19:00→次日 08:00，含 13 小时、昨天晚上、长时间未交谈和双方原话。
- 隔离浏览器 QA：1280×800 / 720×700 截图检查时间分隔与昼夜；390px 顶栏边界无遮挡。
- 自定义底图/色调保留在夜间遮罩之下；未调用真实 LLM，最终措辞质量不作为固定文本测试。
