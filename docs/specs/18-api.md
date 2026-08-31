# 服务层（组合根 + REST + SSE）

> 范围：`main.py` 组合根（实例化 + seed + 挂订阅 + tick 循环 + 启动）、REST 端点（薄封装 Facade 读方法）、SSE 广播（全部事件）。
> 组合根 spec：只做「装配 + 启动 + 薄封装」，不含业务逻辑（业务在 09/11/12/14/17 各 Facade）。spec 只定义契约（装配顺序 + 订阅拓扑 + 端点/SSE 契约 + 启动竞速语义）；实现以 `nyx/main.py` 源文件为准。

## 元信息

- **前置依赖**：02-config（`load_config`/`Config`）、03-llm（`LlmClient.from_config`）、04-db（`connect`/`Database`）、05-event（`EventBus`/`ROUTING`/`TICK_ROUTING`）、06-tools（`ToolRegistry`）、08-memory-retrieval（`MemoryRetrieval`/`build_embed`）、09-memory-facade、10-desire-value（`default_value`）、11-desire、12-inner-life、13-activity-scheduler、14-activity、eval（`Evaluator`，OOC 轻量告警）、17-expression（`should_initiate_chat`/`ExpressionFacade`）
- **canon**：原始 prompt 文件（两份：`canon.md` 核心 + `ask.md` 主动提问）由组合根读入（路径见「技术方案」）

## 用户故事

> 作为 Nyx 系统的开发者，我想要一个组合根把六大 Facade 装配起来——构造注入、启动 seed、按 ROUTING/TICK_ROUTING 挂订阅、tick 循环推进内在生命、REST 端点薄封装读方法、SSE 广播全部事件——以便一条 `python -m nyx.main` 起服务后，前端能查询快照/历史、发消息、实时收到 think/speak/ask 等全量事件流。

## 验收标准

- [ ] `main.py` 含 `main()` + `build_app()` + `build_app_context()` + `_seed_inner_life` / `_seed_desire` / `_subscribe` / `_tick_loop` / `_supervise_bus` / `_vision_loop` / `_root_event` / `_load_prompt_files` / `_load_canon` / `_load_ask` / `_interrupt_running` / `_build_tools` / `_run_with_reload`（实现见 `nyx/main.py`）
- [ ] **组合根按各 spec 完成定义装配**：`load_config` → `connect` → `LlmClient.from_config` → 各 store/facade 构造注入（循环依赖 `ActivityFacade↔InnerLifeFacade` 用 `_get_state` 延迟绑定解环；`evaluator` 注入 09/11/12/14/17/21 六个 Facade）
- [ ] **注册内置工具**：`local_search` + `file_io` 恒注册，`web_search` 仅当 `config.exploration.web_enabled` 注册（06-tools 完成定义）；探索无条件调 `local_search`、读书/创作无条件调 `file_io`，缺失会 `KeyError`
- [ ] **seed 幂等**（表空才写）：inner_life 四张单行表（personality 8/8/2/6/7、values 8/6/9/5、energy 100/energetic、narrative 初始 identity）；desire 四类型 `desire_value`（`default_value(t)` + `updated_at=now`）+ 3 个初始长期欲望（canon §4）
- [ ] **订阅覆盖 ROUTING/TICK_ROUTING**：`USER_MESSAGE`→interrupt+reply、`OBSERVATION_STATE`→apply_event+add_value、`DESIRE_GENERATED`→on_desire_generated、`DESIRE_SATISFIED`→apply_event、`ACTIVITY_END`→add_value+apply_event+remember_activity、`REFLECTION`→apply_event、`CLOCK_TICK`→按 tick_type 分发五路
- [ ] **26 个端点**：tech-ref §4 的 25 个 REST + `GET /api/events`（SSE）；除 upload/materials/books/impulse 外每个 REST 端点 = 对应 Facade 读方法的薄封装（无额外业务逻辑）
- [ ] **`POST /api/chat`**：构造 `USER_MESSAGE` 事件（`source=EXTERNAL`、`correlation_id=自身 id`）→ `publish` → 返回 `{event_id}`（回复走 SSE）
- [ ] **请求体校验**：`POST /api/chat`/`/api/export`/`/api/observe` 用 pydantic 请求模型（`_ChatPayload`/`_ExportPayload`/`_ObservePayload`），缺键/类型错 → 422（非 500）；`presence` 仅 `online`/`away`/`busy`（`Literal` 校验，拼写错误 422 而非静默禁用搭话）；`window_title` 可选（默认空串）
- [ ] **`POST /api/upload`**：`UploadFile` + `File(...)`；文件名 `Path(file.filename or "upload.txt").name` 消毒（去路径穿越）、分块读累积（1MB/块）、超 `_MAX_UPLOAD_BYTES` 提前返 400（不整读进内存）；`file_io("write", f"uploads/{name}", text)` 落盘（复用 `_resolve_write` 越界守卫）→ `activity.register_material(path, name, len(text))` 只注册书库（不立即读书、不发事件）→ 返回 `{filename, path}`
- [ ] **`GET /api/materials`**：`app.activity.list_materials()` 返回 `{materials: [Material]}`（含 `read_chars`/`total_chars` 进度，供资料面板展示「读到哪了」）
- [ ] **`POST /api/books`**：`UploadFile` + `File(...)`；扩展名非 `.epub` → 400；分块读累积（1MB/块）超 `_MAX_EPUB_BYTES`（50MB）→ 400「文件过大」；`app.reading.import_book(filename, data)` → 201 `Book`；`DuplicateBookError` → 409（`{existing_book_id, title}`）；`ValueError`（空正文）→ 400；解析失败（含 DRM）→ 500
- [ ] **`POST /api/impulse/evaluate`**：请求模型 `{book_id, paragraph_index, last_paragraph_index}` → `app.reading.evaluate_paragraph(...)` → 200 `{triggered: [ReadingBehavior.value, ...]}`（回翻/缺段返回 `[]` 幂等，不 404/422；缺键/类型错 422；触发分派走 SSE）
- [ ] **6 个笔记端点（22-reading-notes）**：`GET /api/notes/{book_id}` → `list_user_notes` → `UserNote[]`；`POST /api/notes/user`（`_UserNotePayload`：`book_id`/`content` 必填，`paragraph_id`/`selected_text` 可选）→ 201 `UserNote`；`PUT /api/notes/user/{note_id}`（`_UpdateNotePayload`）→ `UserNote`、`NoteNotFoundError` → 404；`DELETE /api/notes/user/{note_id}` → 204、`NoteNotFoundError` → 404；`POST /api/notes/{user_note_id}/show-to-nyx` → `Annotation`、`NoteNotFoundError` → 404；`POST /api/notes/check-chapter-boundary`（`_BoundaryPayload`：`nyx_position` `ge=1`）→ `{is_boundary, book_finished}`（`CHAPTER_END`→is_boundary、`BOOK_FINISHED`→book_finished）、`BookNotFoundError` → 404、缺 `nyx_position` → 422
- [ ] **SSE**：`data` = `event.content` 展开 + `event_id` + `correlation_id`（统一结构，不按 type 特判）；`event:` = `EventType.value`
- [ ] **SSE 背压**：每连接 `asyncio.Queue(maxsize=_SSE_QUEUE_SIZE=100)`；`_broadcast` 队列满时丢最旧保最新（`put_nowait` 捕获 `QueueFull`，慢客户端不拖垮总线）
- [ ] **`_tick_loop`**：定时 publish 五种 `CLOCK_TICK`（`content={"tick_type": ...}`）；`INITIATE_CHAT_CHECK` 走 `should_initiate_chat` 判定 + `initiate_chat`（发话才更新 `last_chat_at`）；`REFLECTION_CHECK` 走 `_check_reflect`（过冷却 + 新记忆达标才 `reflect`）；首个活动块启动即触发（`last_block=0.0`），碎碎念/搭话/反思不立即触发（`last_mutter`/`last_chat`/`last_reflect` 初始为 now，抑制启动洪峰）；每轮结尾 `check_timeouts(now)` 收尾超时问句/搭话
- [ ] **总线监督器**：`main()` 启动 `_supervise_bus(app)` 而非裸 `bus.run()`；`run()` 异常终止 → `logger.exception` + 指数退避（`_BUS_BACKOFF_BASE`→`_BUS_BACKOFF_MAX`）重启；崩溃前连续成功落库达 `_BUS_RECOVERY_STREAK` 视为恢复、计数与退避重置（单次成功不足阈值仍累积，DB 抖动不假自愈）；连续 `_BUS_MAX_FAILURES` 次失败 `logger.critical` + 重抛熔断致命；`CancelledError` 重抛（组合根关闭不重启）
- [ ] **`main()` 竞速**：`asyncio.wait({serve_task, bus_task, tick_task}, FIRST_COMPLETED)` 后对**每个**先完成者 `task.result()`——serve 启动失败（`SystemExit`）/ tick 异常 / bus 熔断都重抛终止进程（非零退出，不静默吞）；`finally` cancel 后 `await asyncio.gather(..., return_exceptions=True)` 让 uvicorn 优雅关停跑完（非 fire-and-forget）
- [ ] `pyright` strict 零报错；无模块级可变全局变量（运行期状态 `last_chat_at`/`last_presence`/`last_window_title`/`last_screen_summary`/`screen_observer` 放 `_App` 实例）

## 技术方案

- **新文件**：`nyx/main.py`（无 Facade、无数据变更；`ROUTING`/`TICK_ROUTING` 已由 05-event 定义）
- **库**：`fastapi` + `uvicorn`（新增 web 栈；依赖 pin 同 03-llm 约定）；`python-multipart`（`POST /api/upload` 的 `UploadFile`/`File(...)` 解析）；`httpx`（测试用 `AsyncClient` + `ASGITransport`，不触网）
- **公开面**：`main.py` 是入口（`python -m nyx.main`；开发自动重载 `python -m nyx.main --reload`），不加 `__all__`；`build_app` 供测试构造
- **开发自动重载（decision，可推翻）**：`--reload` 走 `_run_with_reload`——父进程 `watchfiles.watch` 监听 `nyx/` + `prompts/`（`config.yaml` 存在则一并监听）变更，见变更即 `terminate` + 重启子进程；子进程跑无 `--reload` 的 `python -m nyx.main`（与手动启动同路径，不递归）。复用 uvicorn 的 watchfiles 依赖（`DefaultFilter` 忽略 `__pycache__`/`.git`）；硬杀对 SQLite 安全（WAL 可恢复）。仅 dev 便利，生产仍 `python -m nyx.main`
- **web 框架选 FastAPI**：dataclass 返回值经 `jsonable_encoder` 直接序列化（`StrEnum`→`.value` 字符串），端点不声明 pydantic `response_model`（01-types「不用 pydantic」）；`GET /api/events` 用 `StreamingResponse`（手写 SSE 格式，不引 `sse-starlette`）。请求体用 pydantic `BaseModel` 请求模型（`_ChatPayload`/`_ExportPayload`/`_ObservePayload`）做缺键/取值校验 → 422——请求模型是 web 层校验，与 01-types 的 dataclass 领域类型、`response_model` 序列化互不相干
- **循环依赖解环（decision，可推翻）**：`ActivityFacade` 要 `get_state` 回调、`InnerLifeFacade` 要 `activity_facade` 实例。用 `_get_state` 闭包引用 `state_holder` 可变列表，先构造 `ActivityFacade`（占位回调）→ 再构造 `InnerLifeFacade` → 回填 `state_holder`。运行时才求值，构造期不成环
- **回复阻塞 vs 后台（decision，可推翻）**：`USER_MESSAGE` handler 里**同步 `await reply`**（阻塞事件总线到回复完成）。理由：回复是秒级、用户在等回复；活动是分钟级才后台（14-activity 已定 `create_task`）。用户连发消息顺序排队，符合 05-event「顺序分发」
- **抢占归组合根**：`USER_MESSAGE`/`INITIATE_CHAT_CHECK` 前先 `interrupt` 当前 running 活动（design §3.3）。这是跨模块编排（expression 不依赖 activity，避免耦合成环），归 18-api 组合根
- **工具注册归组合根**：`local_search` + `file_io` 恒注册（探索链 `_search_local`/`_write_note` 无条件依赖），`web_search` 仅当 `config.exploration.web_enabled` 注册（与 14-activity `_search` 同条件、06-tools 完成定义「opt-in 由组合根决定」）
- **观察输入归前端**：`classify_presence` 运行时调用方是前端 Tauri（14-activity `observe.py` 注释已定）。后端 `POST /api/observe` 接收 `{presence, window_title}` → 维护 `app.last_presence` + `app.last_window_title` + publish `observation_state`（content `{presence, window_title}`）。`desire.pressure_from_observation` 固定 +0.15、`inner_life.apply_event` 固定偏移，均不解析 content；`window_title` 经 `_read_observation` 折入观察活动结果（供 `build_observation_summary` 拼装）
- **ROUTING 的 `ACTIVITY_END`**：消费 `desire.add_value` + `inner_life.apply_event` + `memory.remember_activity`（组合根 `_subscribe` 三处订阅，活动记忆归 09）
- **上传落盘复用 file_io 守卫（decision，可推翻）**：`POST /api/upload` 不另写路径校验——文件名 `Path(name).name` 消毒后经 `file_io("write", f"uploads/{name}", text)` 落盘（`_resolve_write` 越界守卫已覆盖）；大小上限 `_MAX_UPLOAD_BYTES=500_000`（decision，可推翻），超限 400。落盘后 `activity.register_material(path, name, len(text))` 只 `upsert` 注册书库、**不发起活动**；读书由欲望驱动的 `_maybe_start_activity` 在活动时按 `find_by_topic`/`next_readable` 选书（14-activity 已定义 `register_material` + `_run_reading_source`）
- **canon 读文件**：`_load_canon` 读 `prompts/canon.md`、`_load_ask` 读 `prompts/ask.md` 各为一段字符串注入 `ExpressionFacade`（共享 `_load_prompt_files` helper）；路径 = `NYX_CANON_DIR` 环境变量 > `prompts/` 默认目录；缺失 `FileNotFoundError`（fail-fast，canon/ask 是核心配置不兜底默认）
- **`_App` 内部 dataclass**：组合根的装配产物（不是新增抽象层——不增 Facade→子系统→内部类之外的层），持有 9 个组件引用 + 4 个运行期状态（`last_chat_at`/`last_presence`/`last_window_title`/`last_screen_summary`）+ 视觉装配 `screen_observer`（`vision.enabled` 时），端点/handler 闭包捕获 `_App` 实例
- **总线监督器（decision，可推翻）**：`main()` 用 `_supervise_bus(app)` 包一层 `bus.run()`。`_persist` 失败会终止 `run()`（05-event 有意让 DB 错误传播、事件放回队首不丢），监督器接住异常 `logger.exception` + 指数退避（`_BUS_BACKOFF_BASE=1.0`→`_BUS_BACKOFF_MAX=30.0`，×2 增长）后重启（重启而非降级：瞬时 `aiosqlite` 错误可自愈）；连续 `_BUS_MAX_FAILURES=8` 次失败 `logger.critical` + 重抛熔断致命（DB 永久挂时干净崩溃，不留「API 收事件但不处理」的僵尸态）；崩溃前连续成功落库达 `_BUS_RECOVERY_STREAK=3`（读 `EventBus.persisted_count` 单调计数，差 ≥ 3）视为恢复、失败计数与退避重置（避免分散瞬时故障累积假熔断）；单次成功不足阈值不重置（DB 抖动「隔一个挂一次」持续累积 → 熔断，不留无限 1s crash-loop 僵尸态）；`CancelledError` 重抛，组合根关闭不重启
- **`main()` 竞速（decision，可推翻）**：`asyncio.wait({serve_task, bus_task, tick_task}, FIRST_COMPLETED)` 把 `tick_task` 纳入监督——`_tick_loop` 异常不再静默丢弃（无 tick = 无活动调度/碎碎念/搭话，僵尸态）。对 `done` 里**每个**先完成者 `task.result()` 重抛（serve 启动失败 `SystemExit(1)` / tick 异常 / bus 熔断都非零退出），而非只取 `bus_task.result()`；`finally` cancel 后 `await asyncio.gather(..., return_exceptions=True)` 让 uvicorn 优雅关停跑完（fire-and-forget cancel 会留 uvicorn cleanup 不完整）
- **SSE 背压（decision，可推翻）**：每连接 sink 是 `asyncio.Queue(maxsize=_SSE_QUEUE_SIZE=100)`，`_broadcast` 满时丢最旧保最新（`put_nowait` 捕获 `QueueFull`）。SSE 允许丢帧、最新事件含最新状态；无界队列 + `put_nowait` 会让慢客户端无界吃内存、队列一满 `QueueFull` 反杀 `run()`
- **明确不做**：`apps/backend` 目录（项目是 `nyx/` 包结构，canon 文件放 `prompts/`）；定时器持久化（重启重置 `last_chat_at`/`last_presence`，同会话历史内存态）；`sse-starlette` / 额外中间件 / CORS（localhost 同源）

## 测试要点

- [ ] 单元测试 `tests/test_api/`（`pytest-asyncio`；`db = await connect(":memory:")`；fake `LlmClient` / fake 各 Facade 按需注入）：
  - [ ] **纯函数/装配**（`test_context.py`）：
    - [ ] `_root_event`：`id == correlation_id`、默认 `source is EXTERNAL`、`timestamp` 非空、`content` 原样；显式 `source=Source.INTERNAL` → `source is INTERNAL`
    - [ ] `_load_canon`：tmp 目录放 `canon.md` → 返回内容；缺失 → `FileNotFoundError`（fail-fast）
    - [ ] `_load_ask`：tmp 目录放 `ask.md` → 返回内容；缺失 → `FileNotFoundError`（fail-fast）
    - [ ] `_seed_inner_life`（真 `InnerLifeStore` + `:memory:`）：空表 seed 四表 → `get_*` 非 None 且值 = canon §2/§3 初始值；**再跑一次幂等**（值不变、不重复行）
    - [ ] `_seed_desire`（真 `DesireStore`）：空表 seed 后 `list_values()` 四类型、`list_long_term()` 3 条；再跑幂等
    - [ ] `_build_tools`（`Config` 的 `exploration.web_enabled=False` / `True`）：`{t["function"]["name"] for t in schema()}` `False` → `{local_search, file_io}`、`True` → 多 `web_search`（工厂构造无 I/O，`roots`/`DDGS` 惰性到 `.call()`）
  - [ ] **端点薄封装**（`httpx.AsyncClient` + `ASGITransport`，fake Facade 返回 fixture）：
    - [ ] `GET /api/state` → `CurrentState` JSON（枚举字段为 `.value` 字符串）
    - [ ] `POST /api/chat` → 返回 `{event_id}`；`bus.list_events()` 含一条 `USER_MESSAGE`（`source=external`、`correlation_id == id`）
    - [ ] `GET /api/memories?tag=&type=` → `Memory[]`（`type` query 转 `MemoryType` 枚举）
    - [ ] `GET /api/memories/search?q=` → `Memory[]`（委托 `memory.search(q)`，三层语义检索）
    - [ ] `POST /api/observe` → 返回 `{event_id}`；`bus.list_events()` 含 `OBSERVATION_STATE`（content `{presence}`）
    - [ ] `POST /api/export` `format=json` / `md` 透传 `memory.export` 结果，返回原始字符串（非 JSON 二次编码：json 以 `[` 开头、md 无外层引号包裹），`content-type` 分别 `application/json` / `text/markdown`；`format=bogus` → `ValueError`（Facade 抛）
    - [ ] 请求体校验：`POST /api/chat` 缺 `message` → 422；`POST /api/observe` `presence=Online`（大小写拼写错误）→ 422（`Literal` 校验，不 publish 事件、不改 `last_presence`）
  - [ ] **tick 循环**（fake `bus.publish` 记录 + `monkeypatch` 常量使间隔→0 + `asyncio.sleep` 立即返回）：跑一个循环 → 收到 `CLOCK_TICK` 且 `tick_type` 覆盖 `SCHEDULE_BLOCK_START`/`DESIRE_EVAL`/`MUTTER_CHECK`/`INITIATE_CHAT_CHECK` 四种（`REFLECTION_CHECK` 间隔 3600s 不 monkeypatch 为 0，故不触发）、每条 `source is INTERNAL`（系统定时器，非外部输入）；`grid_minutes=60` 时首轮只发 `schedule_block_start`/`desire_eval`（首个活动块启动即触发，`last_block=0.0`），碎碎念/搭话/反思不立即触发
  - [ ] **`_check_reflect` 三分支**（`_FakeInnerLife` 记 `reflect_calls`、`_FakeMemory` 记 `list_memories`、monkeypatch `time.time`）：`narrative.updated_at` 距 now < `_REFLECT_MIN_INTERVAL`（冷却内）→ 不触发；已过冷却但新记忆数 < `_REFLECT_MIN_NEW_MEMORIES` → 不触发；过冷却 + 新记忆达标 → `reflect` 调 1 次（correlation 透传）
  - [ ] **订阅一致性**（构建 `_App`（fake Facade 记录 handler 调用）+ `_subscribe` + 真 `EventBus`，`run()` 作 task）：对 `ROUTING` 每个**非空消费者**的 event_type publish 一个事件 → 对应 Facade 方法被调（`OBSERVATION_STATE` → `apply_event`+`add_value` 两 handler；`ACTIVITY_END` → `add_value`+`apply_event`；`USER_MESSAGE` → `reply`；`DESIRE_GENERATED` → `on_desire_generated` 等）
  - [ ] **总线监督器**（fake `bus.run()` 每轮 raise + `monkeypatch _BUS_BACKOFF_BASE/_BUS_BACKOFF_MAX=0`）：`_supervise_bus` 连续 `_BUS_MAX_FAILURES` 次后 `RuntimeError` 重抛熔断（`run()` 调用次数 == `_BUS_MAX_FAILURES`）；崩溃前 `persisted_count` 每次 +`_BUS_RECOVERY_STREAK`（达恢复阈值）→ 计数重置、永不假熔断；崩溃前 `persisted_count` 每次 +1（单次成功不足阈值，DB 抖动）→ 计数不重置、照样熔断（`calls == _BUS_MAX_FAILURES`）；`task.cancel()` → `CancelledError` 重抛、不再重启
  - [ ] **`main()` 竞速**（monkeypatch `uvicorn.Server`/`Config` + `load_config`/`build_app_context`/`build_app`/`_tick_loop` 为 fake）：fake `server.serve()` 抛 `RuntimeError("port in use")`（端口被占；不用 `SystemExit`——它是 BaseException，asyncio 会经 `Handle._run` 直接重抛出事件循环、绕开 `task.result()` 重抛路径，无法被干净断言）→ `main()` 重抛 `RuntimeError`（非零退出，不静默吞）；fake `_tick_loop` 抛 `RuntimeError`（+ 阻塞 serve/bus）→ `main()` 重抛 `RuntimeError`（tick 异常传播）
- [ ] 集成测试：无（真实编排不测 LLM；组合根装配正确性已由端点 + 订阅一致性覆盖）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] ripple 同步：tech-ref §4 REST 表补 `POST /api/observe`（`{presence}` → `{event_id}`）；tech-ref §4 SSE「data 统一 = event.content 展开 + event_id + correlation_id」补一句（替换「按 type 逐条 payload」的示例表表述）；tech-ref §7 `main.py` 注释补「FastAPI 端点 + 组合根 + tick 循环」
- [ ] 下游约定：前端（Tauri）采集活跃度+窗口标题 → `classify_presence` 判定 → `POST /api/observe`；`POST /api/chat` 返回 `event_id` 后通过 `GET /api/events` 收 `think`/`speak`/`ask`
