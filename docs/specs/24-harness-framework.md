# Harness 框架化

> 本契约覆盖阶段五：将已稳定的插件装配核心作为独立 Python 包供 Nyx 应用使用，并以第二张角色卡验证复用。MCP 与 supervisor 不在本阶段范围。

## 元信息

- **前置依赖**：20-plugin-harness、14-character-cards。
- **实现文件**：`packages/nyx-harness/nyx_harness/`、`nyx/plugins.py`、`nyx/app_context.py`。
- **测试文件**：`tests/test_harness.py`、`tests/test_plugins.py`、`tests/test_character_host.py`。

## 用户故事

作为 Nyx 的维护者，我希望 Nyx 的角色领域插件通过独立可安装的 harness 装配和启动，以便第二张角色卡复用同一运行框架。

## 验收标准

- [x] `nyx-harness` 是可独立构建/安装的本地 Python 包，不依赖 `nyx`。
- [x] 包提供受声明限制的 `PluginContext`、稳定拓扑排序、能力绑定校验、构建和启动调度。
- [x] Nyx 的领域实现、路由、工具及其业务能力留在 `nyx` 应用包；现有 17 条路由等价。
- [x] 第二张角色卡通过同一 `_Host` 和 harness 装配运行；其角色叙事与存档和内置 Nyx 隔离。
- [x] 不新增数据库对象、迁移、角色卡字段、网络 API 或运行时配置。
- [x] Ruff、Pyright、全量 pytest、前端全量测试与生产构建通过；所有现有端到端测试及新增多角色端到端测试全部重跑。

## 技术方案

- **包 API**：`PluginContext`、`sort_plugins`、`build_plugins`、`validate_optional`、`start_plugins`。上下文只允许访问声明过的能力；装配器按现有强依赖稳定排序，构造后完成弱能力绑定检查，再依序启动。
- **插件协议**：`nyx_harness.core.Plugin` 声明 `name/provides/requires/optional/context`；`factory(ctx, /) -> Awaitable[Mapping[str, object]]`、`tools() -> Sequence[object]`、`async start(ctx) -> None`。`sort_plugins` 保留传入插件类型；排序不调用插件的相等比较。
- **能力绑定**：基础能力与本插件声明的能力可读，读取未声明名称抛出 `RuntimeError`；工厂返回值更新共享映射。沿用既有工具聚合约定：声明提供 `<name>_tools` 时绑定 `tools()` 的结果。弱边在构建完成后可见；`start_plugins(..., degradable=())` 默认要求全部弱边存在，Nyx 显式放行 `reading_context/on_failure` 两个既有可降级能力。
- **Nyx 接入**：`nyx.plugins` 保留 Facade 构造、路由/工具声明和 Nyx 专属回调；通用上下文与装配执行转由 `nyx_harness` 提供。
- **本次边界**：仅抽出稳定装配核心。传输、EventBus、持久化、角色宿主和调度仍在应用侧；六层设计中的完整运行时拆包留待后续，不预留接口。
- **分发**：独立包元数据位于 `packages/nyx-harness/pyproject.toml`；主项目以本地依赖运行和测试，不发布到外部索引。
- **冻结分发**：`dev.py --build-sidecar` 显式将独立包源码目录传给 PyInstaller `--paths`，不能依赖 editable 安装的动态导入器。冻结包必须实际启动验收。
- **可选能力**：MCP 与 supervisor 不实现。
- **数据变更**：无。

## 对象完整性

### 插件装配

**入口清单**

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| Nyx `build_app_context` | 启动内置角色或已注册角色 | 进程内能力映射，无持久化 |

**消费者清单**

| 消费者 | 发现方式 | 用途 |
|---|---|---|
| Nyx 插件工厂 | 宿主显式传入声明列表 | 构造 Facade、工具和路由 |
| Nyx 启动流程 | 同一声明列表 | 校验并运行生命周期钩子 |

**状态迁移表**

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| 未构建 | 强依赖有效 | 已构建 | 注入能力；失败由组合根关闭已打开数据库并抛出 |
| 已构建 | 必要弱能力存在 | 已启动 | 按声明序恢复插件 |
| 已构建 | 必要弱能力缺失 | 失败 | 任何启动钩子执行前失败 |

**Bad case 表**

| 情况 | 处理 |
|---|---|
| 空 | 空声明允许排序；Nyx 路由校验仍要求完整路由集合 |
| 失败 | 缺强依赖或强依赖成环时 fail-fast；组合根关闭已打开 DB |
| 部分完成 | 工厂失败不返回部分 `_App`；组合根关闭 DB |
| 乱序 | 同时可构造时按原声明顺序稳定选择 |
| 重放 | 不适用；装配是每次启动的内存操作，业务恢复继续由既有插件处理 |
| 删除 | 不适用；装配声明不持久化 |

### 角色卡与角色运行

角色卡的全部入口、消费者、状态迁移及 Bad case 继续遵守 `docs/specs/14-character-cards.md`；本阶段不改变该对象契约或格式。第二角色 E2E 使用现有卡包注册路径，核验该角色叙事在自身表达组件中、独立数据库中，且不覆盖 Nyx 数据。

## 测试要点

- [x] 独立包核心：声明访问边界、稳定排序、缺失强依赖/成环、弱能力校验先于启动。
- [x] 宿主 E2E：注册第二张卡、通过 harness 构建并启动、验证角色身份及 DB 隔离、卸载并恢复。
- [x] 重跑全量后端端到端与单元/集成测试、前端全量测试和生产构建。

验收证据见 [测试快照](../test-inventory.md) 与 [效果记录](../eval/README.md)。当前CI仅pytest覆盖率87%，不满足已有90%门槛；本机与外部E2E合并后Nyx91.09%。外部验收接入CI及阶段四公网/手机实机项目统一见 [待办清单](../TODO.md)，未降低阈值。
