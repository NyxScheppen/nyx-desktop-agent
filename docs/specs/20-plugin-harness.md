# 插件化 Harness（装配契约）

> spec 只定义**契约**（签名 + 语义 + 决策），不内联完整代码。代码的唯一事实来源是 `nyx/` 源文件；spec 指向它、不改写它。
> 概念与动机（为什么、六层架构、D1–D13 决策、分阶段路线）见 `docs/design/plugin-harness-architecture.md`。
> 本 spec 定义**阶段 1（装配重构）**的契约；阶段 2+（传输适配器 / 云端 / 框架化）只定边界，契约留后续 spec。
> 阶段五的独立包与当前实现位置见 [24-harness-framework](24-harness-framework.md)：通用 `PluginContext`、排序、构建与启动已移至 `packages/nyx-harness`；Nyx 业务声明和窄回调继续在 `nyx/plugins.py`。以下装配行为契约不变。

## 元信息

- **前置依赖**：04-module-bus-system（EventBus / RouteSpec）、05-tools（ToolRegistry）、
  14-character-cards（角色卡）、各 Facade 契约（06 记忆、07 欲望、08 内在生命、09 活动、
  11 表达、12 阅读、10 评估）。
- **实现文件**：`nyx/plugins.py`（`FacadePlugin` Protocol、`PluginContext`、装配器 + 7 组件插件声明）、
  `nyx/app_context.py`（`build_app_context` 改为调用装配器）、`nyx/subscriptions.py`（handler 绑定收敛到
  插件声明）。`nyx/events/routing.py` 的 `ROUTE_SPECS` 保留为等价基准，不改。
- **测试文件**：`tests/test_plugins.py`（装配器纯函数单测）；E2E 仓库外驱动脚本（验收时跑，不落地 `tests/`）。

## 用户故事

> 作为 Nyx 的开发者，我想把 `app_context.build_app_context()` 里手工的 Facade 装配（构造顺序 +
> 十几个 late-bound 闭包）收敛成可声明、可校验的插件契约，以便新增/替换 Facade 或传输时不必手写
> 依赖顺序和晚绑定占位，并且装配错误在启动即失败，而不是运行到一半才炸。

## 验收标准

- [ ] `FacadePlugin` Protocol 定义：`name` / `provides` / `requires` / `optional` / `tools()` /
  `routes()` / `start()` / `quiesce()`。
- [ ] `requires`（强依赖）缺失或成环 → 装配器启动 fail-fast，不产生半装配 `_App`。
- [ ] `optional`（弱依赖）缺失 → 插件降级；或对「必须绑定的弱边」由装配器在 `start` 前统一校验并 fail-fast。
- [ ] 装配器两阶段：先按 `requires` 拓扑排序构造，再注入 `optional` 能力。
- [ ] 三处循环依赖（expression↔reading、activity↔inner_life、evaluator↔memory）都在 `optional` 边闭合。
- [ ] 装配器产出的 `_App` 与现状手工装配行为**完全一致**（零行为变化）。
- [ ] 插件 `routes()` 汇总后的 `RouteSpec` 集合与现状 17 条**完全一致**（`bus.validate_routes()` 等价断言）。
- [ ] 评测集约定落地：插件化各阶段落地后跑对应 `docs/eval/` 场景（见 §技术方案「评测集入计划」）。
- [ ] `ruff check`、`pyright`、`pytest` 全绿；`docs/test-inventory.md` 同步。

## 技术方案

### 现状锚点（契约的依据，非实现细节）

当前 `build_app_context` 是唯一组合根，手工按依赖顺序构造 7 个域组件，中间穿插晚绑定闭包。
本 spec 的契约就是把这段手工装配抽象掉，但**不改变任何构造参数的语义**。

7 个域组件的强 / 弱依赖（强 = 缺则无法构造，弱 = 缺则降级或后注入）：

| 组件 | 强依赖 | 弱依赖 |
|---|---|---|
| `memory` | `bus` `llm` `evaluator` `embed` + `memory_store` `retrieval` `fact_store` | — |
| `eval`（Evaluator） | `embed` `eval_store` | `on_failure`（→ memory） |
| `desire` | `bus` `llm` `evaluator` `embed` + `desire_store` + `memory.list_memories` | — |
| `tools`（ToolRegistry） | `config` `ToolCallStore` + `memory.ingest_source` | — |
| `activity` | `bus` `llm` `evaluator` `tools` `desire` `memory` `canon` + `activity_store` | `get_state`（→ inner_life）、`get_observation`（→ app）、reading 四闭包 |
| `inner_life` | `activity` `desire` `memory` `bus` `llm` `evaluator` + `inner_life_store` | — |
| `expression` | `bus` `llm` `evaluator` `memory` `activity` `desire` `inner_life` `tools` `skills` `canon` `ask` `checkpointer` + `interaction_store` | `get_observation`（→ app）、`claim/finish/release_return`（→ app）、`reading_context_reader`（→ reading） |
| `reading` | `inner_life` `desire` `memory` `bus` `llm` `evaluator` `canon` `expression` + `ReadingStore` | — |

三处循环依赖（强边必须无环，环上至少一条 `optional` 边）：

| 环 | 强边 | 弱边（后注入） | 现状断法 |
|---|---|---|---|
| `expression ↔ reading` | `reading → expression` | `expression → reading_context_reader` | reading 构造后回填 `reading.build_reply_context`；未绑定降级 `""` |
| `activity ↔ inner_life` | `inner_life → activity` | `activity → get_state` | inner_life 构造后回填 `inner_life.get_state`；未绑定 raise |
| `evaluator ↔ memory` | `memory → evaluator` | `evaluator → on_failure` | 先定义 `on_failure` 闭包，memory 构造后闭包生效 |

late-bound 闭包清单（`optional` 依赖在现状里的具体形态，装配器要替换的对象）：

| 占位 | 绑定目标 | 未绑定行为 |
|---|---|---|
| `state_reader` | `inner_life.get_state` | `get_state` raise |
| `observation_reader` | `app` 的 `read_observation` | `get_observation` raise |
| `reading_context_reader` | `reading.build_reply_context` | `read_reading_context` 降级 `""` |
| `reading_owner` | `reading` | `list/read/get` raise |
| `return_context_owner` | `app` | `claim/finish/release_return` raise |
| `on_failure` | `memory.record_failure_signal` | 无（Evaluator 可选参数） |
| `ingest_web` | `memory.ingest_source` | 无（tools 构造参数） |

底座能力（`db` `bus` `llm` `embed` `config` `eval_store`）由 harness 提供，不在插件
`requires` 里声明——它们是装配器的固定输入，不是插件间依赖。`evaluator` 由eval插件提供，
其强依赖声明遵循上表，`on_failure` 作为弱边闭合与memory的环。

### FacadePlugin 契约

```python
class FacadePlugin(Protocol):
    name: str                                  # "memory" | "desire" | ...
    provides: tuple[str, ...]                  # 暴露的能力名，如 ("memory",)
    requires: tuple[str, ...]                  # 强依赖能力名（缺失 fail-fast）
    optional: tuple[str, ...]                  # 弱依赖能力名（缺失降级 / 后注入）

    def tools(self) -> list[Tool]: ...                       # tool 面（ToolRegistry 语义）
    def routes(self) -> list[RouteSpec]: ...                 # lifecycle 面（复用 04 RouteSpec）
    async def start(self, ctx: PluginContext) -> None: ...   # 装配后、跑 loop 前（restore/recover）
    async def quiesce(self) -> None: ...                     # 关停收尾
```

语义与决策：

- `tools()` / `routes()` / `start()` / `quiesce()` 默认空实现（无生命周期组件用 no-op）。
- 只有 memory / desire / activity / inner_life / expression / reading 真正需要 `start`（activity 的
  `recover_stale_running`、expression 的 `restore_history` 归入 `start`）；eval、tools、角色卡不产生 routes。
- **构造参数语义与现状完全一致**：本契约只改变装配顺序从手写变成声明驱动，不改任何 Facade 行为。
- 不允许 `holder[0]` 式可变占位；`_App` 仍是组合根内部对象，不传入 Facade（沿用 04 既有边界）。

### PluginContext

```python
class PluginContext:
    def get(self, capability: str) -> object | None: ...   # 同步引用（装配期已解析）
```

- `PluginContext` 是装配器的辅助对象，不是业务 Facade；只读视图，无持久状态。
- Nyx 的 `_get_state(ctx)` 窄回调从 `ctx.get("state_reader")` 取得异步状态能力；通用上下文不定义业务方法。
- `optional` 能力未绑定时：`ctx.get(...)` 返回 `None` 由插件降级（对应现状「未绑定降级 `""`」）；
  对「必须绑定的弱边」由装配器在 `start` 前统一校验（对应现状「未绑定 raise RuntimeError」）。

### 装配器两阶段

装配器读 7 个插件的 `provides` / `requires` / `optional`：

1. **强依赖拓扑排序**：只走 `requires` 边；`requires` 图必须无环，否则启动 fail-fast。排序结果
   即构造顺序，等价于现状手工顺序去掉晚绑定。
2. **optional 能力注入**：对每个 `optional` 声明，在目标组件构造后回填引用；三处环的弱边在此闭合。

三个环的注入点（精确对应现状回填位置）：

| 环 | 弱边声明 | 注入点 |
|---|---|---|
| `expression ↔ reading` | `expression.optional += ("reading_context",)` | expression 构造后 → reading 构造后注入 `reading.build_reply_context` |
| `activity ↔ inner_life` | `activity.optional += ("state_reader",)` | activity 构造后 → inner_life 构造后注入 `inner_life.get_state` |
| `evaluator ↔ memory` | `eval.optional += ("on_failure",)` | evaluator 构造后 → memory 构造后注入 `memory.record_failure_signal` |

**fail-fast 语义**：装配器在 `start` 前校验「所有非降级类 `optional` 边已注入」，缺失即启动抛错，
不产生半装配 `_App`（对齐现状 `except: db.close(); raise` 的资源释放）。

**稳定序**：拓扑不唯一时按插件声明序稳定化，保证可复现（幂等装配）。

### 路由声明统一

现状 `routing.py`（17 条 `ROUTE_SPECS`）+ `subscriptions.py`（handler 硬编码 if 映射）两处，收敛为：

- 每个 Facade 插件的 `routes()` 返回它订阅的 `RouteSpec` 列表；装配器汇总成 `ROUTE_SPECS` 等价物。
- `RouteSpec.consumer_id` 唯一标识消费者；handler 绑定从 `subscriptions.py` 的 if 分支收敛为声明驱动
  （插件声明哪个方法消费它）。
- **等价性约束**：装配器产出的 `ROUTE_SPECS` 集合必须与现状 17 条完全一致，用 `bus.validate_routes()`
  的 `expected == registered` 断言。

**tick handler 归属决策**：`runtime.py` 的 tick 触发消费者按事件归到对应 Facade 声明——
`on_mutter_check` / `on_initiate_chat_check` → expression，`on_reflection_check` → inner_life，
`on_schedule_block_start` → activity，`on_desire_eval` → desire；`on_user_message` → expression。
handler 物理位置可在实现期保留在 `runtime.py`（作为模块函数被插件声明引用），本 spec 只定「归属」。

### 传输适配器边界（阶段 2 预告，本文不实现）

阶段2完整契约现见 [21-onebot-transport](21-onebot-transport.md)，以下只保留阶段1边界形状。

统一模型：所有输入 = 某处采集 → 传输适配器 → `Source.EXTERNAL` 事件 → 单脑。

```python
class TransportAdapter(Protocol):
    name: str
    async def on_external(self, payload: dict) -> None: ...   # 入向 → 既有事件构造
    async def emit(self, event: Event) -> None: ...           # 出向 → 外部通道
```

- 入向复用 `EventBus.publish` / `POST /api/observe` 的 durable admission（04 已有）。
- 出向复用 per-recipient + lease + retry 心智（`event_delivery` / `presence_delivery` 的既有模式），
  新建 `transport_outbox`（D13），不复用 `event_delivery`。
- 首个真实传输：OneBot（QQ）；不引入 NoneBot2 宿主，只做协议适配器（D5）。

> 阶段 1 不实现任何传输改动；TransportAdapter 的完整契约、`transport_outbox` 表、OneBot 认证/续发
> 留阶段 2 的 spec。

### 关键决策

- **D1–D13**：见 `docs/design/plugin-harness-architecture.md`（已定决策）。本 spec 只落其中与装配
  相关的 D1（Facade 级边界）、D3（tool 面 / lifecycle 面分开）、D11（requires/optional 两档）。
- **零行为变化**（阶段 1 目标）：装配器只是替换装配方式，任何 Facade 的构造参数、事件路由、工具
  注册、回复图接线都不变。
- **不重造底座**：EventBus / RouteSpec / ToolRegistry / load_skills / character_cards 全部复用，
  只在第 3 层之上补「插件身份 + 依赖装配」。
- **阶段 1 不引入 MCP**：进程内插件直连 Python 接口（D2），MCP 留给跨进程/远端（阶段 5）。

### 数据变更

阶段 1 **无新表 / 无迁移**。阶段 2 的 `transport_outbox` 留阶段 2 spec。

### 评测集入计划

`docs/eval/README.md` 是「效果评测」（测「好不好」），与管道 E2E（测「坏没坏」）区分。本 spec 落地后：

- 阶段 1（零行为变化）重跑现有 R1–R3 / P1–P3 做**效果回归**——确认重构没悄悄改变行为。
- 后续阶段新增场景随各自 spec 写入 `docs/eval/README.md`（阶段 2 QQ 往返/断线续发、阶段 3 双 reader
  不串、阶段 4 跨端一致、阶段 5 跨角色隔离）。
- **不引入自动打分模型**（反冗余，项目约定「不测 LLM 文本质量只测管道」）。

## 对象完整性

### FacadePlugin 声明（provides / requires / optional / routes / tools / start / quiesce）

**入口清单**

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| 装配器读插件声明 | 装配期 | 装配器内存（不持久化） |
| 插件 `routes()` / `tools()` | 装配期汇总 | 装配器内存 → `bus` / `ToolRegistry` |

**消费者清单**

| 消费者 | 发现方式 | 用途 |
|---|---|---|
| 装配器 | 读 `provides` / `requires` / `optional` | 拓扑排序 + optional 注入 |
| `bus.validate_routes()` | 读汇总后的 RouteSpec 集合 | 等价性校验 |

**状态迁移表**

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| 声明读取中 | 缺 `requires` | 装配失败 | fail-fast，不产生 `_App` |
| 声明读取中 | `requires` 成环 | 装配失败 | fail-fast |
| 装配完成 | `optional` 已注入 / 可降级 | 装配成功 | 产出 `_App` |

**Bad case 表**

| 情况 | 处理 |
|---|---|
| 空 | `routes()` / `tools()` 默认空元组（eval / tools / 角色卡不产生 routes） |
| 失败 | 缺 `requires` → 启动报错；`optional` 未注入 → 插件降级或装配器 fail-fast |
| 部分完成 | 装配中断 → 不产生 `_App`，释放已构造资源（对齐现状 `db.close()`） |
| 乱序 | 拓扑序由声明序稳定化，可复现 |
| 重放 | 装配幂等：同一配置 + 角色产出等价 `_App` |
| 删除 | 插件声明移除 → 装配器不再构造该组件，无残留 |

### PluginContext（能力解析）

**入口清单**

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| 装配器构造 | 每个插件 `start` 前 | 装配器内存 |

**消费者清单**

| 消费者 | 发现方式 | 用途 |
|---|---|---|
| 插件的 `start()` / 惰性能力访问 | `ctx.get(capability)`；Nyx 状态由 `_get_state(ctx)` 适配 | 解析已注入能力 |

**状态迁移表**

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| 构造完成 | 请求已解析能力 | 返回引用 | 无副作用 |
| 构造完成 | 请求未绑定 `optional` | 返回 `None` | 插件降级 |

**Bad case 表**

| 情况 | 处理 |
|---|---|
| 空 | 请求未声明能力 → 报错 |
| 失败 | `optional` 未绑定 → `get` 返回 `None` 由插件降级 |
| 部分完成 | 不适用（只读视图，无状态） |
| 乱序 | 不适用（只读视图，无状态） |
| 重放 | 不适用（只读视图，无状态） |
| 删除 | 不适用（随装配器生命周期结束） |

### 装配器（组合根）

**入口清单**

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| `build_app_context` 等价入口 | 启动 / 角色装载 | 内存 → `_App` |

**消费者清单**

| 消费者 | 发现方式 | 用途 |
|---|---|---|
| `_Host` | 每角色各装配一份 | 多角色运行 |
| `main.py` 启动 | 调用装配入口 | 启动 loop |

**状态迁移表**

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| 装配中 | 全部强依赖构造完成 | 注入 optional | 回填弱边 |
| 装配中 | 强依赖有环 / 缺失 | 装配失败 | fail-fast，关闭已打开 db |
| 注入完成 | `start` 前校验通过 | 产出 `_App` | 可跑 loop |

**Bad case 表**

| 情况 | 处理 |
|---|---|
| 空 | 无插件 → 无 Facade（不适用：7 插件固定） |
| 失败 | 强依赖有环 → fail-fast |
| 部分完成 | 装配中断 → 关闭已打开 db，不泄漏 |
| 乱序 | 拓扑不唯一 → 按声明序稳定化 |
| 重放 | 装配幂等 |
| 删除 | 角色卸载 → 对应 `_App` 生命周期结束（`_Host` 既有） |

### TransportAdapter（阶段 2 边界，本文只定契约形状）

**入口清单**

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| 外部传输 `on_external` | 外部消息 / 采集上报 | 事件日志（入向） |
| 出向 `emit` | SPEAK / ASK 等事件 | `transport_outbox`（阶段 2） |

**消费者清单**

| 消费者 | 发现方式 | 用途 |
|---|---|---|
| EventBus | 入向 publish | durable admission |
| 外部通道 | 出向发送 | 投递消息 |

**状态迁移表**

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| 入向接收 | publish 成功 | 入向 durable | 事件可回放 |
| 出向 pending | 发送成功 | sent | at-least-once |
| 出向 pending | 发送失败 | retry | 有界重试 |

**Bad case 表**

| 情况 | 处理 |
|---|---|
| 空 | 无事件 → 无投递 |
| 失败 | 出向至少一次，MVP 接受极端单次重复（D13，OneBot 无幂等键） |
| 部分完成 | 留阶段 2 spec 定（本文只定边界） |
| 乱序 | 留阶段 2 spec 定（本文只定边界） |
| 重放 | 留阶段 2 spec 定（本文只定边界） |
| 删除 | 留阶段 2 spec 定（本文只定边界） |

## 测试要点

- [ ] **E2E（agent 驱动，完整用户路径）**：发消息 → 工具 / 规划 / 反思 → 收回复 → 查表落库，
  断言装配器产出的 `_App` 与手工装配行为一致（零行为变化）。
- [ ] **纯函数单测（仅确定性逻辑）**：
  - [ ] 拓扑排序正确性：给定 `requires` 断言顺序；有环 / 缺失 → fail-fast。
  - [ ] `optional` 注入缺失检测：必须绑定的弱边未注入 → `start` 前 fail-fast。
  - [ ] `routes()` 汇总等价性：与现状 17 条 `ROUTE_SPECS` 完全一致。
- [ ] 不测 LLM 文本质量；验证「装配没坏」（拓扑对、注入对、路由等价），「好不好」留 `docs/eval/`。
- [ ] 重跑 `docs/eval/README.md` R1–R3 / P1–P3 做效果回归。

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿（含 17/18/19 的确定性回归 + 装配器纯函数单测）
- [ ] 零行为变化：agent E2E 通过 + `bus.validate_routes()` 等价断言通过
- [ ] `docs/test-inventory.md` 已更新（快照）
- [ ] `docs/tech-reference.md` 源码导航同步（`nyx/plugins.py`、`FacadePlugin` / `PluginContext` / 装配器）
- [ ] `docs/eval/README.md` 已补插件化评测场景（阶段 1 效果回归说明）

阶段1实现与验证记录见 [效果记录](../eval/README.md#插件装配阶段1回归2026-10-29)
及 [测试快照](../test-inventory.md)。既有宿主卸载超时已用HEAD原装配复现，后续修复将测试的
短drain预算限制在阻塞阶段；最终回复执行叙述修复与真实模型复测见 [效果记录](../eval/README.md)。
当前质量门结果以测试快照为准。
