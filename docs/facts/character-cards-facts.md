# 角色卡与多角色运行事实表

> 快速源码摘要；唯一完整契约见 `docs/specs/14-character-cards.md`。

- 所有角色的 `build_app_context` 复用独立 `nyx-harness` 装配核心。`test_second_character_runs_through_harness_and_keeps_isolated_state` 通过真实 HTTP/SSE 验证导入第二张卡、角色叙事进入 prompt、聊天与 Nyx 数据隔离及卸载恢复；卡包格式不变。

- OneBot按配置绑定一个角色，与桌面选择独立。桌面或已启用QQ绑定的RUNNING角色允许主动表达准入，
  仍受原presence/精力/冷却限制。传输任务随角色启动、监督、停止；卸载超时保留DB，恢复续发出向队列。

- `nyx/character_cards.py` 负责原生 YAML/Markdown/图片 ZIP 校验与固定卡片快照；内置卡位于 `nyx/cards/nyx/`。压缩及解压量分别限 50 MiB，无新增第三方依赖。
- `nyx/app_context.py:_Host` 管理最多四个注册角色和当前选择；STARTING/FAILED/STOPPING 占名额，ARCHIVED 保留文件但不自动运行。每角色复用独立 `_App`，共享延迟 SDK/embedding 和唯一屏幕观察器。
- 宿主 `data/characters/host.db` 独立迁移；新角色位于 `data/characters/<id>/`。旧 Nyx 使用解析后的原 DB/workspace，不迁移旧文件，其他角色显式指定路径。
- 阶段四首个切片在宿主库保存配对码摘要和设备令牌摘要；配对码一次性、十分钟过期，设备令牌可由根凭证撤销，未撤销设备令牌可访问既有 API。
- `nyx/bootstrap.py:initialize_character` 原子登记一次初始化；重启/恢复不覆盖成长，也不复活已清空的长期欲望。
- `nyx/runtime.py` 按角色运行、监督和停止；切换不重建。卸载超时保留实例/连接/名额；干净停止全部 writer 后释放残留 processing lease。宿主 close 持续收尾后关闭全部连接。
- `nyx/api/routes.py` 复用业务路由到角色命名空间；写请求校验 revision，捕获 runtime，未知/过期/不可运行分别 404/409/503。当前失败不静默切换，后端拒绝卸载当前角色。
- 公共 presence 先宿主持久受理，再每角色 FIFO 独立补投（1/2/4/8/30 秒）；稳定 sample ID、事件 ID 与角色 checkpoint 防重放。私有观察绑定选择版本，过期不转交新角色。
- `nyx/api/routes.py` 的设备端点只在 `NYX_AUTH_TOKEN` 启用时提供；根凭证管理配对与设备，手机以配对换得的 Bearer 令牌访问业务。
- `frontend/src/api/characterScope.ts` 和 `stores/characterStore.ts` 管理请求归属与切换代数；领域 Store 只展示当前角色。SSE、success/error/finally/timer/retry 都检查 scope，防止 A→B→A 污染。
- SSE 使用带根 Bearer 凭证的 fetch reader；角色图片通过认证 fetch 读取为 blob URL，凭证失败会卸载活动 UI 并让迟到的角色列表响应失效。浏览器生产端同源访问，Tauri 仍走回环地址；完整传输契约见22。
- 完整端标题与桌宠菜单提供角色选择；设置提供导入、卸载、恢复、失败重试和额度。无资产角色显示名称占位；外观设置全局，草稿/最近书籍按角色保存。
- 关键回归位于 `tests/test_character_cards.py`、`tests/test_character_host.py` 和 `frontend/tests/characterScope.test.ts`、`characterStore.test.ts`，另有总线、工具、API、presence 和 SSE 回归。
