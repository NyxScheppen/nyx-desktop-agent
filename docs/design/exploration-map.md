# 探索升级：联网探索 + 探索地图

> 状态：已实现
> 日期：2026-08-25
> 范围：`nyx/activity/`（探索行为）+ `nyx/events/`（新事件）+ `nyx/enums.py`/`nyx/types.py`（枚举/类型）+ `18-api`（新端点）+ 前端探索地图组件
> 相关 spec：`14-activity`、`05-event`、`01-types`、`18-api`、`frontend/01-sse`、`frontend/02-stores`

## 1. 背景与目标

现状问题：

- 自由探索是「读书的兜底」——探索欲映射成 READING，只有**书库一本书都没有**时才转 FREE_EXPLORATION，还要过「精力 ≥ 60 + 4 小时一次」两道门槛。实际几乎走不到。
- 联网搜索（`exploration.web_enabled=true`）已存在，但只在探索链里轮转，探索本身难触发，所以联网能力形同虚设。
- 前端没有任何可视化：探索只是「活动」时间线里一条记录 + 「产出」面板里的几行文字，无空间感。

目标：

1. **探索欲直接探索**——探索与读书并列成为探索欲的出路（有书读书、无书上网）；频率门槛（≥ 1h）通过且无书时不再退回发呆/观察（频率未到时仍由默认活动兜底）。
2. **联网为主通道**——探索链以联网搜索为主、本地搜索兜底。
3. **探索地图**——把探索过程可视化成一张「游戏小地图」，主界面独立入口，实时看到 Nyx 一步步上网。

## 2. 核心隐喻

**地图上的「地点」= 网页。**

- Nyx「出门探索」= 上网搜索、浏览网页。
- 地图节点 = 访问过的网页（或搜索动作），节点名就是网页名/搜索词（如「新闻」「搜索：深海鱼」）。
- 连线 = 浏览顺序（「搜索：深海鱼」→「新闻」→「搜索：发光生物」→…）。
- 「探索中」= Nyx 在地图上沿路线移动，走到哪个节点哪个节点点亮。

## 3. 后端改动

### 3.1 触发放宽（`activity/facade.py:_maybe_start_activity`）

探索欲映射的 READING 活动，决策改为：

- 有未读完的书 → 读书（书是用户主动投喂的，仍优先）。
- 无未读完的书 → **直接 `FREE_EXPLORATION`（联网）**，不再退回发呆/观察。

`should_explore` 门槛从「精力 ≥ 60 + 频率 4h」放宽为「频率 ≥ 1h」（仅防探索欲高频触发时连续烧 token）；精力不再单独卡，交给 `build_schedule` 的 REST 穿插兜底（探索消耗 -30，精力不足会自动插休息）。

### 3.2 联网主通道 + 节点结构化（`activity/exploration.py`）

- 动作顺序改为 `plan_next → search_web（主）→ read → write_note`；`search_local` 作为联网失败或 `web_enabled=false` 时的兜底。
- 产出从纯 `{findings, notes}` 升级为**带节点序列**。每访问一个网页 / 发一次搜索，记一条节点：
  - `search` 节点：name = `"搜索：<focus>"`，url 为空。
  - `web` 节点：name = 网页标题（或域名），url = 该页地址。
- `Exploration.run` 返回 `{findings, notes, nodes}`，其中 `findings`/`notes` 保持文本（兼容现有产出面板与活动记忆），`nodes` 是新增的结构化数组供地图渲染。

### 3.3 手动端点（`18-api`）

- `POST /api/explore`，可选 body `{topic: string}`：
  - 无 `topic` → Nyx 自己定主题（好奇驱动）。
  - 有 `topic` → 围绕该主题联网探索。
  - 立即发起一次 `FREE_EXPLORATION`（无视欲望/频率门槛），seed = topic 或 Nyx 自定。

### 3.4 实时进度事件（`05-event` + `01-types`）

新增 `EventType.EXPLORATION_STEP = "exploration_step"`，探索链每访问一个节点 publish 一次：

```json
{"activity_id": "…", "node": {"name": "新闻", "url": "https://…", "kind": "web"}}
```

- ROUTING：`EXPLORATION_STEP: []`（仅广播前端，无后端消费者）。
- 前端收到后地图实时点亮节点、移动 Nyx。

## 4. 前端改动

### 4.1 探索地图组件（新 `components/exploration/ExplorationMap.tsx`）

节点列表（本次 MVP 交付为 flat 列表，无连线；网络图渲染留后续迭代）：

- **节点三类**：
  - 🦊 当前探索（Nyx 所在，闪烁高亮）
  - ✦ 已探索（点亮，显示网页名，点开看那次访问的 findings）
  - ◌ 待探索（用户加的或 Nyx 规划好的，灰色虚线）
- **连线** = 浏览顺序（本次 MVP 未渲染连线，flat 列表按浏览顺序从上到下排列）。
- **数据来源**：
  - 历史足迹：`GET /api/activity/results` 里 `free_exploration` 活动的 `result.nodes`。
  - 实时进度：SSE `exploration_step`（新增 dispatch 分支）。
  - 待探索心愿：前端内存（`explorationStore`），MVP 不落库。

### 4.2 主界面独立入口

顶栏加一个「探索」图标（或右侧可折叠浮窗），点开/收起小地图，像游戏里随时可见的 minimap。不占聊天主对话区。

### 4.3 交互

- 「出门探索」按钮：调 `POST /api/explore`（无 topic），Nyx 自己定主题出发。
- 「＋」加节点：输入主题词，追加到「待探索」心愿。**本次 MVP 心愿单仅前端内存展示/可删，尚未接入出发路线（`postExplore(topic)` 未接线）；「下次出门优先纳入路线」留后续迭代。**
- 点已探索节点：弹详情（那次访问的 findings/网页名）。
- 探索进行中：地图实时移动 Nyx、逐个点亮节点（响应 `exploration_step`）。

## 5. 数据契约

| 项 | 形状 | 说明 |
|---|---|---|
| 节点 | `{name: string, url: string, kind: "search" \| "web"}` | `kind=search` 时 url 为空 |
| 探索产出 | `{findings: string[], notes: string[], nodes: Node[]}` | `findings`/`notes` 兼容旧消费方 |
| `exploration_step` content | `{activity_id: string, node: Node}` | 每节点一次 |
| `POST /api/explore` | body `{topic?: string}` → `{activity_id}` | 手动触发 |

## 6. 反冗余自查

- 复用现有 `get_results`/`get_current`/SSE 分发表，不新增检索层或快照端点。
- 节点是 `result.nodes` 的扩展字段，不进 `Activity` dataclass（`progress` 已是 JSON，零 schema 改动）。
- 「待探索心愿」MVP 只存前端内存（`explorationStore`），不建后端表——等有跨会话需求再持久化。
- 探索仍走现有 LangGraph 图，只改动作轮转顺序 + 加节点记录，不重写框架。
- 手动端点复用 `ActivityFacade` 的 `_maybe_start_activity` 决策（新增一个带 seed 的入口），不复制执行逻辑。

## 7. 测试要点（简略）

- 后端：`should_explore` 门槛放宽断言；`_maybe_start_activity` 无书 → 直接 FREE_EXPLORATION；`Exploration.run` 返回 `nodes`（search/web 两类、顺序正确）；`search_web` 为主、`search_local` 兜底；`EXPLORATION_STEP` 每节点发布；`POST /api/explore` 手动触发（含 topic/无 topic）。
- 前端：`explorationStore` 心愿增删；`exploration_step` 分发点亮节点；地图组件渲染三类节点 + 实时移动；手动触发按钮调端点。
