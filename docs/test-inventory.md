# 测试清单（test-inventory）

> 当前测试套件的快照。测试增删时同步更新本表（只记现状：测试名 / 检查方向 / 断言内容，不记变更历史）。

## 01-types（枚举 + 实体类型）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_all_enums_exhaustive` | 功能正确 | 15 个枚举的值集合与 `EXPECTED` 逐枚举相等（防漏成员/多成员/改值） |
| `test_naming_convention` | 回归保护 | 每个成员 `value == name.lower()`（防手滑改值破坏 snake_case 契约） |
| `test_strenum_json_serializable` | 功能正确 | `json.dumps(EventType.USER_MESSAGE) == '"user_message"'` |
| `test_short_term_desire_default_status` | 功能正确 | `status` 默认 `DesireStatus.PENDING`（枚举成员而非裸字符串） |
| `test_memory_aspect_default_factory_isolated` | 边界鲁棒 | `default_factory` 保证两个实例的 `aspect` 互不共享 |
| `test_long_term_desire_linked_values_default_factory_isolated` | 边界鲁棒 | `default_factory` 保证两个实例的 `linked_values` 互不共享 |
| `test_typed_dict_keys` | 功能正确 | 4 个 TypedDict 键集合经 `get_type_hints` 完整 |

## 02-config（配置加载）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_validate_config_accepts_defaults` | 功能正确 | `validate_config(Config())` 合法通过（不抛即通过） |
| `test_validate_config_rejects_invalid`（21 例） | 边界鲁棒 | 越界/非正/错类型改字段后 `validate_config` 报 `ConfigError` |
| `test_validate_config_accepts_temperature_bounds` | 边界鲁棒 | `llm.temperature` 边界 `0.0`/`2.0` 合法通过（`validate_config` 不抛） |
| `test_load_config_fills_defaults` | 功能正确 | 只写 `llm.provider` → 其余字段填默认值 |
| `test_load_config_builds_energy_delta_recursively` | 功能正确 | `energy_delta` 递归构造为 `ActivityEnergyDelta` 实例、未写键用默认 |
| `test_load_config_rejects_unknown_energy_delta_key` | 边界鲁棒 | 嵌套 `energy_delta` 内未知键报 `ConfigError` |
| `test_load_config_rejects_nested_non_dict` | 边界鲁棒 | 嵌套 dataclass 字段给非 dict 值（`memory: 100`）报 `ConfigError`（非裸崩溃） |
| `test_load_config_rejects_unknown_top_level_key` | 边界鲁棒 | 未知顶层键报 `ConfigError` |
| `test_load_config_rejects_unknown_section_key` | 边界鲁棒 | 段内未知键报 `ConfigError` |
| `test_load_config_rejects_missing_file` | 边界鲁棒 | 文件缺失报 `ConfigError` |
| `test_load_config_rejects_bad_yaml` | 边界鲁棒 | 坏 YAML 报 `ConfigError` |
| `test_load_config_env_override` | 功能正确 | `NYX_CONFIG` 环境变量覆盖默认路径生效 |
| `test_load_config_empty_file_defaults` | 边界鲁棒 | 空文件 → `None` → `{}`，返回全默认值（不报错） |
| `test_load_config_rejects_scalar_top_level`（3 例） | 边界鲁棒 | 顶层 falsy 标量（`0`/`""`/`[]`）报 `ConfigError`，不被 `or {}` 吞成全默认 |
| `test_load_config_rejects_mixed_type_unknown_key` | 边界鲁棒 | 混合类型未知键（`1:` int 与 `bogus:` str）报 `ConfigError`，不因 `sorted` 跨类型比较裸崩 `TypeError` |

## 03-llm（LLM 统一客户端）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_to_lc_system` / `test_to_lc_user` / `test_to_lc_assistant` | 功能正确 | `_to_lc` 三种 role 映射到 `SystemMessage`/`HumanMessage`/`AIMessage`，`content` 透传 |
| `test_to_lc_invalid_role` | 边界鲁棒 | 非法 role → `ValueError` |
| `test_complete_fields` | 功能正确 | `module`/`type`/`correlation_id`/`content`/`model` 正确回填进 `LLMOutput` |
| `test_complete_json_mode_on` | 功能正确 | `json_mode=True` → 传给模型的 kwargs 含 `response_format={"type": "json_object"}` |
| `test_complete_json_mode_off` | 功能正确 | `json_mode=False` → kwargs 不含 `response_format` |
| `test_complete_messages_passthrough` | 功能正确 | `messages` 顺序与内容按原序透传为 LangChain 消息（fake 记录收到的消息） |
| `test_complete_non_text_content` | 边界鲁棒 | 非文本 content（`list`）→ `RuntimeError`（非 `str(list)` repr 垃圾） |
| `test_resolve_base_url` | 功能正确 | `resolve_base_url`：显式 `base_url` 优先 / 已知 provider（`openai`）命中映射 / 未知 provider（`claude`）返回 `None` |
| `test_from_config_unknown_provider_rejects` | 边界鲁棒 | `provider="claude"`（无 base_url）→ `ConfigError` |
| `test_from_config_rejects_missing_api_key` | 边界鲁棒 | `api_key_env` 未设（`delenv`）→ `ConfigError` |
| `test_from_config_ok` | 功能正确 | 正常 → 返回 `LlmClient` 且 `_model_name == config.model` |
| `test_from_config_known_provider` | 功能正确 | `provider="openai"` → 返回 `LlmClient` 且 `_model_name == config.model` |
| `test_from_config_base_url_override` | 功能正确 | 自定义 `base_url` → 正常返回 `LlmClient` 且 `_model_name == config.model` |
| `test_from_config_passes_timeout_and_retries` | 功能正确 | `from_config` 把 `config.timeout`/`config.max_retries` 透传给 `ChatOpenAI`（fake 记录构造参数） |
| `test_from_config_passes_temperature` | 功能正确 | `from_config` 把 `config.temperature` 透传给 `ChatOpenAI`（fake 记录构造参数，断言 `temperature` 值） |
| `test_complete_tools_passthrough` | 功能正确 | `complete(tools=[...])` → 传给 fake model 的 kwargs 含 `tools`（透传 schema） |
| `test_complete_tools_off` | 功能正确 | `complete` 不传 `tools` → kwargs 不含 `tools` 键 |
| `test_complete_tool_calls_parsed` | 功能正确 | fake 返回带 `tool_calls` 的 `AIMessage` → `LLMOutput.tool_calls` 正确解析为 `[{name, args}]` |
| `test_complete_no_tools_empty` | 边界鲁棒 | 响应无 `tool_calls` → `LLMOutput.tool_calls == []` |
| `test_describe_returns_text` | 功能正确 | `VisionClient.describe` 多模态：fake 模型收 `HumanMessage` 含 text+image_url 两块、返回文本描述 |
| `test_describe_non_text_raises` | 边界鲁棒 | 非文本 content（`list`）→ `RuntimeError` |
| `test_from_config_unknown_provider_rejects`（VisionClient） | 边界鲁棒 | `provider="claude"` 无 base_url → `ConfigError` |
| `test_from_config_ok`（VisionClient） | 功能正确 | 默认 `VisionConfig` → `VisionClient` 且 `_model_name == "llava"` |
| `test_from_config_requires_key_for_non_ollama`（VisionClient） | 边界鲁棒 | `provider="openai"` 且 `api_key_env` 未设 → `ConfigError`（不再硬编码 "ollama" 静默 401） |
| `test_from_config_reads_api_key`（VisionClient） | 功能正确 | `provider="openai"` 且 `api_key_env` 已设 → 正常返回 `VisionClient`（key 从 env 读） |

## 04-db（SQLite 连接 + 建表 + 迁移）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_migrate_creates_all_tables` | 功能正确 | `sqlite_master` 含硬编码 18 张业务表 + `schema_version`，共 19 张 |
| `test_migrate_creates_five_indexes` | 功能正确 | 显式索引（`sql IS NOT NULL`）恰为 `idx_memory_tag` / `idx_memory_type` / `idx_event_log_corr` / `idx_memory_content_hash` / `idx_books_content_hash` 五个 |
| `test_migrate_books_content_hash_index_unique` | 功能正确 | `idx_books_content_hash` 的 `sqlite_master.sql` 以 `CREATE UNIQUE INDEX` 开头（v8 去重升级唯一索引） |
| `test_migrate_v8_dedupes_duplicate_content_hash` | 边界鲁棒 | 先迁 v7 插两条同 `content_hash` 书 → 完整迁移不抛、重复清到 1、被删书 paragraphs 级联清空、唯一索引就位 |
| `test_migrate_sets_version_to_max` | 功能正确 | `schema_version` 单行 = `_MIGRATIONS` 最高版本 |
| `test_migrate_not_null_alignment` | 边界鲁棒 | 11 列 `notnull=1`（`memory.aspect` / `long_term_desire.linked_values` / `activity.progress` / `event_log.content` / `event_log.correlation_id` / `user_notes.content` / `user_notes.created_at` / `user_notes.updated_at` / `annotations.user_note_id` / `annotations.content` / `annotations.created_at`） |
| `test_migrate_nullable_alignment` | 边界鲁棒 | Optional 列 `notnull=0`（`short_term_desire.goal` / `activity.ended_at` / `memory.embedding` / `memory.content_hash` / `user_notes.book_id` / `user_notes.paragraph_id` / `user_notes.selected_text`） |
| `test_migrate_idempotent` | 回归保护 | 连跑两次不报错，表数不变、版本不变 |
| `test_migrate_version_gating` | 功能正确 | `monkeypatch` 追加「下一版本」后只套该版本，版本=下一版本，旧版本不重复建（动态取 max+1，不再硬编码 v3） |
| `test_migrate_atomic_rollback` | 边界鲁棒 | 迁移含非法 SQL → 抛 `aiosqlite.Error`；`ok` 表回滚不存在；版本仍为 0 |
| `test_connect_returns_database` | 功能正确 | 返回 `Database`；文件创建；`journal_mode=wal`；`foreign_keys=1`；`row_factory` 生效（`row["x"]==1`）；`lock` 是 `asyncio.Lock` |
| `test_connect_explicit_path_priority` | 功能正确 | 显式 path 优先建该文件 |
| `test_connect_env_override` | 功能正确 | `path=None` 时 `NYX_DB` 环境变量覆盖默认 |
| `test_default_db_path_constant` | 功能正确 | `DEFAULT_DB_PATH == "nyx.db"` |
| `test_connect_closes_conn_on_migrate_failure` | 边界鲁棒 | 迁移失败 → `connect` 抛异常且连接被 `close`（spy 记录），不泄漏 |

## 05-event（事件总线 + 路由）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_routing_keys_are_all_event_types_except_clock_tick` | 功能正确 | `set(ROUTING) == set(EventType) - {CLOCK_TICK}`（21 键） |
| `test_tick_routing_keys_are_all_tick_types` | 功能正确 | `set(TICK_ROUTING) == set(TickType)`（5 键） |
| `test_routing_values_are_known_modules` | 功能正确 | 所有路由值 ⊆ `{expression, inner_life, desire, activity, memory}` |
| `test_time_constants` | 功能正确 | `SECONDS_PER_DAY == 86400.0`、`SECONDS_PER_HOUR == 3600.0`（共享常量，防四处 Facade 漂移） |
| `test_internal_event_shape` | 功能正确 | `internal_event` 返回 `Event`：`source is Source.INTERNAL`、`type`/`content`/`correlation_id` 透传、`id` 非空 uuid4、`timestamp` 为 float |
| `test_internal_text_event_wraps_content` | 功能正确 | `internal_text_event` 把纯文本 content 包装成 `{"content": ...}` 载荷 |
| `test_publish_only_enqueues` | 功能正确 | publish 后 handler 未调、`list_events()` 空（未到 run，不落库） |
| `test_run_persists_dispatches_and_broadcasts` | 功能正确 | run 后 handler 收到完整 `Event`、SSE sink 收到同一对象、落库往返相等（含 correlation_id 透传） |
| `test_multiple_handlers_run_in_subscribe_order` | 功能正确 | 多 handler 按订阅序调用 |
| `test_list_events_filter_by_type` | 功能正确 | `event_type=` 只返回该类型事件 |
| `test_list_events_filter_by_correlation` | 功能正确 | `correlation_id=` 只返回该因果链事件 |
| `test_list_events_sorts_desc_and_limits` | 功能正确 | 默认按 `timestamp DESC`、`limit=` 截断 |
| `test_list_events_stable_order_same_timestamp` | 边界鲁棒 | 同 `timestamp` 时按 `id` tiebreaker 稳定排序（不抖动） |
| `test_row_to_event_roundtrip` | 功能正确 | `content` 是 `json.loads` 后 dict、`source`/`type` 从 `.value` 转回枚举成员 |
| `test_add_and_remove_sse_sink` | 功能正确 | add 后收到、remove 后不再收到 |
| `test_remove_sse_sink_is_idempotent` | 边界鲁棒 | 从未加入 / 二次移除均不抛 `ValueError`（幂等） |
| `test_handler_exception_isolated` | 边界鲁棒 | handler 抛异常 → `logger.exception` 记录完整 traceback、后续 handler 照跑、SSE 照广播、run 任务不死 |
| `test_persist_exception_propagates` | 边界鲁棒 | `_persist` 抛异常 → 传播、run 任务终止、事件放回队首不丢（`qsize()==1`） |
| `test_persist_failure_requeues_and_retries` | 边界鲁棒 | `_persist` 首次抛、重试成功 → 事件最终落库 + handler 收到、`calls==2`（队首放回不丢） |
| `test_broadcast_drops_oldest_when_sink_full` | 边界鲁棒 | sink 满（`Queue(maxsize=1)`）→ 丢最旧保最新（只剩最新事件，不抛 `QueueFull`、不杀 `run()`） |
| `test_persist_poison_pill_dead_lettered` | 边界鲁棒 | `_persist` 恒抛 → 前 `_PERSIST_MAX_ATTEMPTS-1` 轮放回队首、第 `_PERSIST_MAX_ATTEMPTS` 轮死信丢弃（`qsize()==0`、run 不死）、`caplog` 含「死信丢弃」+ event.id（毒丸不阻塞整队、不杀进程） |
| `test_persist_rolls_back_on_failure` | 边界鲁棒 | monkeypatch `conn.commit` 抛 `aiosqlite.Error` + spy `conn.rollback` → rollback 被调（失败回滚，不留坏事务给下次重试） |
| `test_persist_serializes_non_json_types` | 功能正确 | content 含 `uuid.uuid4()` → 落库往返为字符串（`json.dumps(..., default=str)`，序列化与 SSE 对称，不抛 `TypeError`） |
| `test_persist_rejects_nan` | 功能正确 | content 含 `float("nan")` → `_persist` 抛 `ValueError`（`allow_nan=False` 拦 NaN/Infinity，`default=str` 不拦 float，不写出非法 `NaN` 字面量） |
| `test_put_left_resets_join` | 边界鲁棒 | `put_left` 后 `wait_for(join(), timeout=0.05)` 抛 `TimeoutError`（`_finished` 被 clear、`_unfinished_tasks` 递增，`join()` 语义对齐 `put_nowait`） |

## 06-tools（工具系统）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_register_and_schema_in_order` | 功能正确 | `register` 后 `schema()` 按注册序返回 `[{"type":"function","function":{name, description, parameters}}]` |
| `test_register_duplicate_raises` | 边界鲁棒 | 重复注册同名工具 → `ValueError` |
| `test_call_invokes_handler_with_kwargs` | 功能正确 | `call` 用 `handler(**args)` 调 handler（fake 记录 kwargs 与返回值） |
| `test_call_unknown_name_raises` | 边界鲁棒 | `call` 未注册名 → `KeyError`（消息含名字） |
| `test_search_hits` | 功能正确 | 命中 → `[{path, snippet}]`，`path` 指向含关键词文件 |
| `test_search_miss` | 功能正确 | 不命中 → `[]` |
| `test_search_empty_roots` | 边界鲁棒 | `roots=[]` → `[]` |
| `test_search_case_insensitive` | 功能正确 | `"DEEP"` 命中 `"deep sea"`（大小写不敏感） |
| `test_search_skips_non_text` | 功能正确 | 非 `.txt`/`.md` 文件跳过 |
| `test_search_caps_results` | 边界鲁棒 | 命中超 `_MAX_RESULTS` → 截断到 50 |
| `test_search_skips_oversized_file` | 边界鲁棒 | 单文件超 `_MAX_FILE_BYTES` → 跳过 |
| `test_full_disk_roots_nonempty_and_exists` | 功能正确 | 非空且每项 `.exists()`；POSIX 下 `== [Path("/")]` |
| `test_web_search_maps_fields` | 功能正确 | fake `DDGS` 的 `title`/`href`/`body` 映射为 `title`/`url`/`snippet`，不触真实网络 |
| `test_web_search_returns_empty_on_error` | 边界鲁棒 | fake `DDGS.text` 抛异常 → 返回 `[]`（best-effort 不冒泡） |
| `test_read` | 功能正确 | `read` 返回文件 content |
| `test_read_non_utf8_replaces` | 边界鲁棒 | 非法 UTF-8 字节 → `�` 替换（不崩溃、不静默丢字节） |
| `test_write` | 功能正确 | `write` 建文件在 `write_root` 内、返回 `written` |
| `test_write_escape_parent` | 边界鲁棒 | `../` 越界 → `ValueError` |
| `test_write_escape_absolute` | 边界鲁棒 | 绝对路径逃逸 `write_root` → `ValueError` |
| `test_write_empty_path` | 边界鲁棒 | 空路径解析到 `write_root` 本身 → `ValueError`（非 `IsADirectoryError` 裸崩） |
| `test_write_escape_symlink` | 边界鲁棒 | `write_root` 内 symlink 指向外部 → `ValueError`（无 symlink 权限环境 skip） |
| `test_list` | 功能正确 | `list` 返回目录条目名 |
| `test_unknown_action` | 边界鲁棒 | 未知 `action` → `ValueError` |
| `test_fetch_url_sync_returns_empty_on_http_error` | 边界鲁棒 | `httpx.get` 抛异常 → `fetch_url` 返回 `""`（best-effort 不冒泡） |
| `test_build_web_fetch_tool_returns_text` | 功能正确 | `web_fetch` handler 抓正文 → 返回 `{text, url}` 纯正文（不写盘、不发事件）；`text == "正文内容"`、`url` 原样回传 |
| `test_build_web_fetch_tool_returns_error_when_empty` | 边界鲁棒 | 正文抓取失败/为空 → 返回 `{"error": ...}`（纯抓取不写盘不 publish） |
| `test_is_public_ip_rejects_internal_ranges` | SSRF 护栏 | 回环/内网/链路本地/组播/未指定 IP（`127.0.0.1`/`10.0.0.1`/`192.168.1.1`/`169.254.1.1`/`::1`）→ `_is_public_ip` 为 `False` |
| `test_is_public_ip_accepts_public` | SSRF 护栏 | 公网 IP（`8.8.8.8`/`1.1.1.1`）→ `_is_public_ip` 为 `True` |
| `test_is_safe_url_rejects_non_http_scheme` | SSRF 护栏 | `file://`/`ftp://`/`javascript:` → `_is_safe_url` 为 `False` |
| `test_is_safe_url_rejects_private_host` | SSRF 护栏 | 主机名解析到内网 IP → `_is_safe_url` 为 `False` |
| `test_is_safe_url_accepts_public_host` | SSRF 护栏 | 主机名解析到公网 IP → `_is_safe_url` 为 `True` |
| `test_is_safe_url_rejects_unresolvable_host` | SSRF 护栏 | 解析失败（`socket.gaierror`）→ `_is_safe_url` 为 `False` |
| `test_fetch_url_sync_rejects_redirect_to_private` | SSRF 护栏 | 重定向到内网 IP 逐跳校验 → 返回 `""`（不跟随到内网） |

## 07-memory-store（记忆存取）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_add_get_roundtrip` | 功能正确 | `add` 多值 `aspect` + 非默认 `recall_count` + `embedding` → `get` 往返全等（`got == mem` 覆盖 aspect JSON / type 枚举 / freshness / embedding） |
| `test_add_get_embedding_none` | 边界鲁棒 | `embedding=None` → `get` 返回 `embedding is None`（SQL NULL 非 `"null"` 字符串） |
| `test_add_duplicate_id_raises` | 边界鲁棒 | 重复 `id` → `aiosqlite.IntegrityError` |
| `test_get_miss_returns_none` | 功能正确 | `get` 未命中 → `None` |
| `test_list_memories_filters_and_sorts` | 功能正确 | `tag` / `type` / 组合过滤 + `freshness DESC` 排序 |
| `test_list_memories_limit` | 功能正确 | `limit=2` 截断（`freshness DESC` 前 2）；`limit` 与 `tag` 组合截断（`tag="a", limit=1` 取最高 freshness 那条） |
| `test_update_fields` | 功能正确 | `update_many`（单条）改各字段 → `get` 验证；`id` / `created_at` 不可变 |
| `test_update_many` | 功能正确 | `update_many` 批量改多条（含 `embedding=None` 与 `embedding=[...]`）→ `get` 逐条验证；空列表 no-op |
| `test_delete_cascades_edges` | 功能正确 | `delete_many`（单条）级联删 `memory_edge`（from/to 双向），其它记忆边保留 |
| `test_delete_many` | 功能正确 | `delete_many` 批量删多条（含关联边）→ `get` 全部 `None`、`list_edges` 无残留；空列表 no-op |
| `test_record_recall_atomic` | 功能正确 | 未达阈值连调两次 → `recall_count==2` 且 type `SHORT_TERM`、返回 False；达阈值 → `LONG_TERM`、返回 True；已 `LONG_TERM` → 只递增、返回 False（加一+条件升型在单锁内原子完成） |
| `test_search_keyword` | 功能正确 | `content` / `summary` 命中、无命中 `[]`、ASCII 大小写不敏感 |
| `test_search_keyword_escapes_wildcards` | 边界鲁棒 | `%` / `_` 作字面量匹配（`ESCAPE '\'` 转义），不误命中通配符匹配 |
| `test_list_edges_and_upsert` | 功能正确 | `upsert_edge` 新建 + 同键重复 `ON CONFLICT` 改 `weight` 不重复建行 |
| `test_upsert_edge_unknown_id_raises` | 边界鲁棒 | `upsert_edge` 引用不存在 id → `IntegrityError`（FK 生效） |
| `test_hash_content_deterministic` | 功能正确 | `hash_content` 同 content 同 hash、不同 content 不同 hash、SHA-256 hex 长度 64（纯函数） |
| `test_find_by_content_hit_and_miss` | 功能正确 | `add` 后按原 content `find_by_content` 命中返回 `Memory`（id 一致）、不同 content 返回 `None` |
| `test_strengthen` | 功能正确 | `add`（`recall_count=0, freshness=0.3`）→ `strengthen(m1, 100.0)` → `recall_count==0`、`freshness==1.0`、`created_at==100.0`（重复写入不涨 recall、锚点刷新） |
| `test_count_new_ignores_strengthened_created_at` | 回归保护 | 读书记忆 `created_at=100` → `strengthen(m1, 200)`（刷新 created_at）→ `count_new("reading", 150)==0`（纯重读不算新增）；真新增（created_at=250）→ `==1`；since 更晚/非目标 tag → `==0`（first_created_at 锚点不被 strengthen 污染） |

## 08-memory-retrieval（三层检索 + 联想图）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_neighbors_empty_and_missing` | 边界鲁棒 | 空 edges → `neighbors([])=[]`；不存在节点 → `[]`（`has_node` 过滤防 `NetworkXError`） |
| `test_neighbors_single_edge` | 功能正确 | 单边 a-b：`neighbors(["a"])=["b"]`；全 seed `["a","b"]` → `[]`（排除 seeds 本身） |
| `test_neighbors_chain_depth` | 功能正确 | 链 a-b-c：depth=1 → `["b"]`、depth=2 → `["b","c"]` |
| `test_neighbors_diamond_dedup` | 功能正确 | 菱形 a-b/a-c/b-d/c-d：depth=2 → `["b","c","d"]`（d 去重只一次） |
| `test_weight_does_not_affect_spread` | 功能正确 | weight 不影响扩散（只按可达性） |
| `test_cosine` | 功能正确 | 正交=0、相同=1、相反=-1、零向量=0、维度不一致=0（纯函数） |
| `test_rank_by_cosine` | 功能正确 | `embedding=None` 跳过、`s<=0` 过滤、按 `s` 降序（纯函数；`_vector_search` 与 09 `_similar` 共用） |
| `test_vector_search_skips_none_and_filters` | 边界鲁棒 | `embedding=None` 跳过、`s<=0` 过滤（cos=-1/0）、cos=1 命中 |
| `test_vector_search_top_k_truncates` | 功能正确 | 7 候选只返回 `_VECTOR_TOP_K=5` |
| `test_vector_search_disabled_when_embed_none` | 功能正确 | `embed=None` → `[]`（向量层禁用） |
| `test_search_merge_order_and_limit` | 功能正确 | keyword→vector→association 编排：A（keyword+vector）、B（association 扩散）→ `[A,B]`；limit=1 → `[A]`；sources：A=`[KEYWORD,VECTOR]`、B=`[ASSOCIATION]` |
| `test_search_dedup` | 功能正确 | keyword 与 vector 命中同一记忆 → 去重只一次 |
| `test_search_empty` | 功能正确 | 无命中 + embed=None + 无边 → `[]` |
| `test_search_blank_query_returns_empty` | 边界鲁棒 | `""`/`" "`/`"   "` 空/空白查询短路 → `[]`（`query.strip()`，不因 `LIKE '%%'`/`'% %'` 误返全量） |
| `test_search_no_edge_no_crash` | 边界鲁棒 | keyword 命中无边记忆 → 不抛 `NetworkXError`（`neighbors` 过滤），返回命中本身 |
| `test_search_sources_keyword_only` | 功能正确 | embed=None（向量层禁用）仅 keyword 命中 → `sources=[KEYWORD]` |
| `test_search_sources_vector_only` | 功能正确 | content 不含 query、embedding 余弦命中 → `sources=[VECTOR]` |

## eval（OOC 轻量告警）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_ooc_score` | 功能正确 | 无命中默认 1.0；黑 1 → 0.5；黑 2 → 0.0；黑 3 → 0.0（封顶）；黑 1 白 1 → 1.0（抵消）；白 2 → 1.0（封顶不越界） |
| `test_is_voice_type` | 功能正确 | `speak`/`initiate_chat`/`think` → True；`tool`/`judge`/`scene_memory` → False |
| `test_build_baseline_len` | 功能正确 | baseline 长度 == `len(NYX_CORPUS)`，逐条嵌入 |
| `test_ooc_embed_score_identical` | 功能正确 | content 与语料同向量 → sim 1.0 越界 clamp 到 1.0 |
| `test_ooc_embed_score_orthogonal` | 功能正确 | 正交向量 → sim 0.0 → 0.0 |
| `test_ooc_embed_score_empty_baseline` | 边界鲁棒 | 空 baseline → 1.0（无语料无信息不惩罚） |

## 09-memory-facade（记忆门面）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_decay_freshness` | 功能正确 | 同刻/倒挂不变；1 天后 `freshness-rate`；负值夹 0（纯函数） |
| `test_parse_scene` | 边界鲁棒 | 合法 → 3 元组；缺 tag / 空 content / JSON 数组 → `ValueError` |
| `test_build_scene_prompt` | 功能正确 | 含 `user_message`/`nyx_think`/`nyx_speak`；缺键 → `KeyError` |
| `test_has_negation` | 功能正确 | `"我不喜欢猫"`→True；`"我喜欢猫"`→False |
| `test_content_preview` | 功能正确 | 短 content 不截断（含 summary）；长 content 截到 60 字 + `…` |
| `test_build_contradiction_prompt` | 功能正确 | 含新记忆 content + 候选 id + 候选预览；否定词 → 含「重点核对」；无否定词 → 无该句 |
| `test_parse_contradiction` | 边界鲁棒 | 字符串→该串；`null`→`None`；数字→`ValueError`；缺键→`None` |
| `test_memory_to_dict` | 功能正确 | `type` 是 `.value` 字符串、`embedding` 透传 |
| `test_memory_to_markdown` | 功能正确 | 含 summary 与 content |
| `test_create_scene_memory_basic` | 功能正确 | 字段正确（content/tag/summary、freshness=1.0、type SHORT_TERM、embedding=None）；`evaluator.evaluate` 调 1 次（scene_memory）；发布 `memory_created`（memory_id/source/correlation 透传） |
| `test_contradiction_gating_under_threshold` | 功能正确 | 正交 embedding → 仅 1 次 LLM 调用、无 contradiction、无 reflection（门控 0 调用） |
| `test_contradiction_detected` | 功能正确 | 过阈值候选 → 第 2 次 `output_type="contradiction"`；`conflicts_with` 命中 → 发布 reflection（含双方 id）；evaluator 再调 1 次 |
| `test_contradiction_null_no_reflection` | 功能正确 | contradiction 返回 null → 不发 reflection |
| `test_contradiction_recall_top_k` | 边界鲁棒 | 6 条高相似旧记忆 → 矛盾 prompt 候选恰 5 条（`_RECALL_TOP_K=5`） |
| `test_contradiction_prompt_negation_hint` | 功能正确 | 新记忆含否定词 → 矛盾 prompt 含「重点核对」句 |
| `test_contradiction_parse_failure_no_crash` | 边界鲁棒 | 矛盾判断返回非法 JSON → 记忆主流程照常入库 + 发布 `memory_created`、无 reflection（矛盾检测 best-effort 不反噬创建） |
| `test_build_edges` | 功能正确 | 新记忆有到旧记忆的 `memory_edge`（`weight>0`） |
| `test_eviction` | 功能正确 | `short_term_capacity=1` → 旧记忆（freshness 更低）被挤掉，只剩新的一条 |
| `test_eviction_tie_break_oldest_first` | 边界鲁棒 | 新鲜度相等（`freshness_decay=0.0`）时按 `created_at` 升序挤掉最旧而非最新，`short_term_capacity=2` 造 3 条 |
| `test_decay_writeback` | 功能正确 | 1 天间隔两次创建 → 旧记忆 freshness 衰减（`<1.0`） |
| `test_dedup_exact_same_content` | 功能正确 | 同 content 二次 `create_scene_memory` → 库内 1 条、`recall_count==0`、仅 1 个 `memory_created`（精确去重合并强化不涨 recall） |
| `test_dedup_semantic_merge` | 功能正确 | 新记忆与旧记忆 embedding 余弦=1.0 → 合并到旧记忆（`recall_count==0`）、不新增、无 `memory_created` |
| `test_dedup_semantic_below_threshold` | 功能正确 | 余弦 < 0.95 → 正常新建入库（`list_memories` 2 条、发 1 个 `memory_created`） |
| `test_dedup_embed_none_skips_semantic` | 边界鲁棒 | `embed=None` 时语义去重跳过（旧记忆带 embedding 也不比较），仅精确去重生效 |
| `test_search_delegates_to_retrieval` | 功能正确 | `search` 委托 fake `MemoryRetrieval`（返回预设 + 记录 query） |
| `test_list_memories_delegates` | 功能正确 | `list_memories(type=)` 委托真 store 过滤 |
| `test_count_new_delegates` | 功能正确 | `facade.count_new` 委托真 store：`since=500` → 1、`since=2000` → 0、非目标 tag → 0 |
| `test_record_recall_below_threshold` | 功能正确 | 未达阈值 → recall_count+1、type 仍 SHORT_TERM、无 `memory_promoted` |
| `test_record_recall_promotes` | 功能正确 | 达阈值 → type LONG_TERM + 发布 `memory_promoted` |
| `test_record_recall_long_term_no_repromote` | 功能正确 | 已 LONG_TERM → 只 recall_count+1，不重复发布 |
| `test_record_recall_concurrent_single_promote` | 回归保护 | `asyncio.gather` 并发两次 → `recall_count==2`、仅 1 条 `memory_promoted`（原子加一+条件升型不重复升级） |
| `test_export_json` | 功能正确 | `json.loads` 还原列表，`type` 为字符串、`embedding` 透传 |
| `test_export_md` | 功能正确 | 含某记忆的 summary 与 content |
| `test_export_unknown` | 边界鲁棒 | `csv` → `ValueError` |
| `test_join_list` | 功能正确 | `str` 原样、`list` 换行拼接、空 `list`/`None`/非 str-list → `""`（纯函数） |
| `test_activity_memory_fields_reading` | 功能正确 | reading result → `(note, book, "reading")` |
| `test_activity_memory_fields_creation` | 功能正确 | creation result → `(content, title, "creation")` |
| `test_activity_memory_fields_exploration` | 功能正确 | free_exploration result → `(summary, core_discovery, "free_exploration")` |
| `test_activity_memory_fields_free_exploration_new_shape` | 功能正确 | free_exploration 新结果形状映射 → `(summary, core_discovery, "free_exploration")`；content 含 summary 文本、summary 含 core_discovery 文本 |
| `test_activity_memory_fields_skip` | 边界鲁棒 | 非目标类型/空 result/空内容/类型非 str/result 非 dict → `None` |
| `test_activity_memory_fields_summary_truncated` | 边界鲁棒 | summary 超 80 字截断为 `x*80 + "…"` |
| `test_remember_activity_reading` | 功能正确 | reading 事件 → 写一条 Memory（content=note/summary=book/tag="reading"/type SHORT_TERM）、发布 `memory_created`、无 LLM 调用 |
| `test_remember_activity_creation_and_exploration` | 功能正确 | creation + free_exploration 各写一条（content/summary 正确、tag 为活动类型值）、无 LLM 调用 |
| `test_remember_activity_skips_empty_or_other_type` | 边界鲁棒 | rest/空 result/observe_user → 不写、无 `memory_created` |
| `test_remember_activity_contradiction` | 功能正确 | 有相似旧记忆 + embed → 门控触发 1 次 `contradiction`（参与矛盾判断，无 scene_memory）；命中 → 发布 reflection |
| `test_remember_user_profile_fields` | 功能正确 | `remember_user_profile` → 写一条 `LONG_TERM`/`tag="user"`/`aspect` 全等的画像记忆、无 LLM 调用、发布 `memory_created`（correlation 透传） |
| `test_record_no_answer` | 功能正确 | 问句未答 → 写一条 `SHORT_TERM`/`tag="interaction"`/summary「用户没有回答我的提问」、content 含问句、无 LLM 调用、发布 `memory_created`（correlation 透传） |
| `test_remember_knowledge` | 功能正确 | 3 项入参 → 落 2 条 `LONG_TERM`/`tag="knowledge"` 记忆（空 content 项跳过）；summary 回退 content；无 LLM 调用；发布 2 条 `memory_created`（correlation 透传） |
| `test_remember_reading` | 功能正确 | 读书入参 → 写 1 条 `LONG_TERM`/`tag="reading"` 记忆（summary 透传）、无 LLM 调用、发布 `memory_created`（correlation 透传） |

## 10-desire-value（欲望值机制）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_decay_value` | 功能正确 | `elapsed_days=0` 不变；`=1` → `value-rate`；衰减到负夹 0；`rate=0` 不变（纯函数） |
| `test_apply_pressure` | 功能正确 | 加正 `delta` 上升；超上限夹 1.0；负 `delta` 夹 0.0（回增与加压同一纯函数） |
| `test_reinforce_weight` | 功能正确 | 默认 `delta`=`+WEIGHT_REINFORCE_DELTA`；显式覆盖；到上限夹 1.0（多次满足不越界） |
| `test_raise_suppression` | 功能正确 | 默认 `delta`=`+SUPPRESSION_RAISE_DELTA`；显式覆盖；到上限夹 1.0 |
| `test_at_peak` | 功能正确 | `value > threshold` → True、`<` → False、`==` → True（含等号） |
| `test_is_expressible` | 功能正确 | 同 `at_peak` 边界（用 `suppression_threshold`） |
| `test_gating_suppression` | 回归保护 | 初始 `suppression=0.5` 达峰即表达；失败 4 次 `suppression=0.9` 达峰但被压抑（越挫越压抑） |
| `test_default_value` | 功能正确 | 四个 `DesireType` 各自 `value==0.0` / `expression_weight==0.7` / `suppression_threshold==0.5` / `updated_at==0.0`、`type` 正确 |
| `test_step_constants` | 功能正确 | `0.0 <= WEIGHT_REINFORCE_DELTA <= SUPPRESSION_RAISE_DELTA`、`REFUND_DELTA > 0` |

## 11-desire（欲望系统：store + 全周期 + 门面）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_add_get_roundtrip` | 功能正确 | `add_desire`+`get_desire` 往返全等（`got == desire` 覆盖 goal JSON / 枚举往返，另证 `goal.topic`） |
| `test_goal_none_roundtrip` | 边界鲁棒 | `goal=None` → `get_desire().goal is None`（SQL NULL 非 `"null"` 字符串） |
| `test_list_pending_filters_and_orders` | 功能正确 | 只返回 pending+active（不含 satisfied/expired），按 `created_at ASC` |
| `test_list_short_term_all_desc` | 功能正确 | 全量（含 satisfied/expired），按 `created_at DESC`（区别于 `list_pending`） |
| `test_update_desire` | 功能正确 | 改 `status`/`retry_count` → `get_desire` 验证 |
| `test_goal_progress_roundtrip` | 功能正确 | `add_desire` 带 `goal_progress=2` → `get` 往返；`update_desire` 改 `goal_progress=3` → 再 `get` 验证（goal 精确计数存储层） |
| `test_upsert_value_new_and_update` | 功能正确 | `upsert_value` 新建 → `list_values` 1 行；同 type 再 upsert 改 `value`/`updated_at`（ON CONFLICT 不重复建行） |
| `test_long_term_roundtrip_and_update` | 功能正确 | `subtopics`/`linked_values` JSON 数组往返、`type` 枚举往返；`update_long_term` 改 `progress`/`strength` |
| `test_parse_desire` | 边界鲁棒 | 合法 JSON→`(description, Goal)`；`goal:null`→`None`；缺/空 description、`goal.action` 非法、`count` 非正/非 int、`topic` 非 str、JSON 数组 → `ValueError`（7 例） |
| `test_subtopics_for` | 功能正确 | `type` 匹配且 `subtopics` 非空 → 返回该 `subtopics`；无匹配 / 空 subtopics → `[]` |
| `test_pick_topic_seed` | 功能正确 | 空池 → `None`；全没做过（无命中记忆）→ 第一个；部分做过 → 取没做过的；都做过 → 取新鲜度最低者 |
| `test_subtopics_for_filters_blank` | 边界鲁棒 | 含 `""`/`"  "` 空白的 subtopics → 过滤掉，只留非空子主题（空串通配符不进池） |
| `test_subtopic_freshness_blank_not_wildcard` | 边界鲁棒 | 空串/纯空白子主题 → `None` 不匹配（`"" in s` 恒 True 通配符兜底）；非空命中 → freshness |
| `test_most_relevant_long_term` | 功能正确 | 无 type 匹配 → `None`；`topic` 双向 substring 命中第二条 → 第二条；漂移仍命中；`topic=None` → 第一个；都不命中 → 第一个 |
| `test_most_relevant_long_term_blank_not_wildcard` | 功能正确 | 空串子主题被跳过（不当作 substring 通配符），`topic="骑士团"` 命中真实子主题的第二条 |
| `test_build_desire_prompt` | 功能正确 | 含类型 `.value` 与种子；`seed=None` → 含「（无）」 |
| `test_pressure_from_observation` | 功能正确 | 互动欲 `value` 0 → `+0.15`；`updated_at` 更新 |
| `test_run_eval_no_peak` | 功能正确 | 四类型都低于 `peak_threshold` → `[]`、无 LLM 调用 |
| `test_run_eval_generates_peak` | 功能正确 | 达峰 → 1 次 LLM（`output_type="desire"`）、`evaluator.evaluate` 1 次、返回 1 个（type/status/strength/description/goal 来自 fixture）、value 重置 0、发布 `desire_generated` |
| `test_run_eval_only_most_urgent` | 功能正确 | 互动 0.95 + 探索 0.92 都达峰 → 只生成互动；探索 `value` 保留 0.92 不重置 |
| `test_run_eval_long_term_pressure` | 功能正确 | 探索长期欲望 → 探索 `value` 额外 `+0.1`（0.5→0.6） |
| `test_run_eval_decay` | 功能正确 | `updated_at` 1 天前 → `value` 衰减 `value_decay × 1`（0.5→0.45） |
| `test_run_eval_suppression_gate` | 功能正确 | 达峰但 `suppression_threshold > value` → 不生成、返回 `[]` |
| `test_run_eval_topic_seed` | 功能正确 | 探索长期 `subtopics=["骑士团", "大学朋友"]` + 记忆命中「骑士团」→ LLM prompt 含「大学朋友」不含「骑士团」（没做过优先）；seed 钉死 goal.topic：LLM 返回「骑士团」被覆盖为「大学朋友」 |
| `test_run_eval_topic_cleared_without_seed` | 功能正确 | 探索欲无长期欲望（无 subtopics）→ seed=None，goal.topic 被清空为 None（杜绝 LLM 漂移主题） |
| `test_run_eval_topic_no_synthesis_goal_none` | 功能正确 | 探索欲有 seed 但 LLM 返回 goal=null → 不合成 goal（goal 保持 None，单次满足语义） |
| `test_run_eval_llm_invalid_json_skips` | 边界鲁棒 | 非法 JSON → `_parse_desire` 抛 `ValueError` → 返回 `[]`、目标 `value` 不重置、无欲望入队 |
| `test_run_eval_evaluator_error_propagates` | 回归保护 | evaluator 抛 `RuntimeError` → 不被 `except ValueError` 吞、上抛给 supervisor（不掩蔽真 bug） |
| `test_run_eval_dedup_discards_similar` | 功能正确 | 已有 PENDING 欲望与新生成 description 向量余弦 ≥ 0.9 → 不入队（`list_pending` 仍 1 条）、不发布 `desire_generated`、value 已归零 |
| `test_run_eval_dedup_keeps_distinct` | 功能正确 | 向量余弦 < 0.9 → 正常入队（`list_pending` 变 2 条）+ 发布 1 条 `desire_generated` |
| `test_run_eval_dedup_disabled_without_embed` | 边界鲁棒 | `embed=None` → 不去重、正常入队（返回 1 个） |
| `test_run_eval_dedup_embed_error_skips` | 边界鲁棒 | embed 抛 `RuntimeError` → 降级不去重、正常入队（返回 1 个）、不崩 |
| `test_satisfy_goal_met` | 功能正确 | `SATISFIED`、表达权重 `+0.05`、长期进度 `+0.1`、发布 `desire_satisfied` |
| `test_satisfy_reinforces_most_relevant_long_term` | 功能正确 | 同类型两条长期欲望 + `goal.topic` 命中第二条 → 只回写第二条 progress（0.1）、第一条不动（0.0） |
| `test_satisfy_goal_progress` | 功能正确 | goal.count=3 时前两次 goal_met → `goal_progress=2` 保持 PENDING；第三次 → SATISFIED + `goal_progress=3`（C3 精确计数累计） |
| `test_satisfy_retry` | 功能正确 | `retry_count+1`、`status` 仍 `PENDING`、无事件 |
| `test_satisfy_retry_exceeds_limit` | 功能正确 | `retry_count > retry_limit` → `EXPIRED`、值回增 `+REFUND_DELTA`、抑制阈值 `+0.1`、发布 `desire_expired` |
| `test_expire` | 功能正确 | `EXPIRED` + 值回增 + 抑制阈值上浮 + 发布 `desire_expired` |
| `test_satisfy_expire_missing` | 边界鲁棒 | `desire_id` 不存在 → 无事件、不抛 |
| `test_satisfy_idempotent` | 回归保护 | 重复 `satisfy(True)` → 表达权重只 `+0.05` 一次、只发 1 条 `desire_satisfied` |
| `test_expire_idempotent` | 回归保护 | 重复 `expire` → 值只回灌一次 `+REFUND_DELTA`、阈值只 `+0.1`、只发 1 条 `desire_expired` |
| `test_add_value_observation` | 功能正确 | `add_value(OBSERVATION_STATE)` → 互动欲加压 |
| `test_add_value_activity_end_satisfies` | 功能正确 | `add_value(ACTIVITY_END)`（content 含 `desire_id`+`goal_met`）→ 满足回写 + 发布 `desire_satisfied` |
| `test_add_value_activity_end_ignores_invalid` | 边界鲁棒 | `ACTIVITY_END` 缺 `goal_met` / `goal_met` 类型错 → 无操作（状态不变） |
| `test_evaluate_and_getters` | 功能正确 | `evaluate` 返回 1 条、`get_pending` 返回该条、`get_all` 返回 `DesireState`（values 4 行） |
| `test_get_all_snapshot` | 功能正确 | `get_all` 三字段非空；`short_term` 含 satisfied 历史、`long_term` 含 seed 的长期欲望 |
| `test_satisfy_expire_delegate` | 功能正确 | `facade.satisfy`/`facade.expire` 委托改 `status`（SATISFIED / EXPIRED） |
| `test_add_long_term_delegates` | 功能正确 | `add_long_term(desire)` → `list_long_term` 多一条、字段全等 |
| `test_list_suppressed_filters_and_orders` | 功能正确 | 只返回 suppressed（不含 pending/active），按 `created_at ASC` 排序 |
| `test_mark_active_pending_to_active` | 功能正确 | `mark_active` 把 PENDING 欲望翻 ACTIVE |
| `test_mark_active_guard` | 边界鲁棒 | SUPPRESSED/SATISFIED/EXPIRED/缺失 id → 不变（no-op） |
| `test_mark_suppressed_active_to_suppressed` | 功能正确 | `mark_suppressed` 把 ACTIVE 欲望翻 SUPPRESSED |
| `test_mark_suppressed_guard` | 边界鲁棒 | PENDING/SATISFIED/EXPIRED/缺失 id → 不变（no-op） |
| `test_satisfy_releases_active` | 功能正确 | ACTIVE 欲望 `satisfy` 未达标 → `PENDING`（不卡 ACTIVE）；达标 → SATISFIED |
| `test_run_eval_releases_suppressed` | 功能正确 | SUPPRESSED 欲望其类型 `value >= suppression` → `run_eval` 后 `PENDING`（不新生成） |
| `test_run_eval_keeps_suppressed` | 边界鲁棒 | SUPPRESSED 欲望其类型 `value < suppression` → 保持 SUPPRESSED |
| `test_mark_active_suppressed_delegate` | 功能正确 | `facade.mark_active`/`mark_suppressed` 委托后 `status` 依次 ACTIVE / SUPPRESSED |

## 12-inner-life（内在生命：情感/精力 + 反思 + 门面）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_clamp` | 功能正确 | `clamp_valence` 夹 `[-1,1]`、`clamp_arousal` 夹 `[0,1]`，越界夹回、界内不变 |
| `test_decay_emotion` | 功能正确 | `elapsed=0`/`rate=0` 不变；`elapsed=1/rate` 衰减到 0；负 valence 同乘 f（不反向） |
| `test_apply_offset` | 功能正确 | 加偏移后 clamp：正偏移超上限夹 1.0、负超下限夹 -1.0（valence）/0.0（arousal） |
| `test_event_offset` | 功能正确 | `DESIRE_SATISFIED`→`(0.2,0.1)`；未登记事件→`(0.0,0.0)` |
| `test_vad_to_category` | 功能正确 | 6 档穷尽：(0.9,0.8)→happy、(0.9,0.2)→shy、(-0.9,0.8)→angry、(-0.9,0.4)→worried、(-0.9,0.2)→sad、(0,0.2)→neutral |
| `test_vad_boundary` | 边界鲁棒 | `valence=±0.2` 含等号归 neutral（中性带含边界） |
| `test_resolve_emotion` | 功能正确 | `DRAINED`→sleepy（压过一切）；`ENERGETIC`+`IDLE_REFLECTION`→thinking；`OKAY`+`READING`→base；`current_activity=None`→base |
| `test_energy_to_state` | 功能正确 | 五档：100→energetic、79→okay、59→tired、39→exhausted、19→drained |
| `test_energy_to_state_boundary` | 边界鲁棒 | 分界 80/60/40/20 含等号归上一档 |
| `test_personality_crud` | 功能正确 | 空表→`None`；upsert 后五维全等；改一维再 upsert（ON CONFLICT 更新不重复建行） |
| `test_values_crud` | 功能正确 | 四维同上（空表→`None`、往返、改一维更新） |
| `test_aesthetic_crud` | 功能正确 | 四维（华丽/抒情/古典/沉重）空表→`None`；upsert 往返四轴全等；改一维再 upsert 单行覆盖 |
| `test_energy_crud` | 功能正确 | `value`+`state` 往返（`EnergyState` 枚举）；空表→`None` |
| `test_narrative_crud` | 功能正确 | `story`/`self_view`/`becoming` JSON 往返 + `identity`/`updated_at`；空表→`None` |
| `test_drift_dim` | 功能正确 | `delta=None` 不变；`+0.3`→base+0.3；`+2`→夹 `+0.5`；`9.8+0.5`→夹 10.0；`1.2-0.5`→夹 1.0 |
| `test_drift_personality_and_values` | 功能正确 | 只改 delta 出现的维、其余维不变；结果夹 `[1,10]` |
| `test_drift_aesthetic` | 功能正确 | 四轴按 delta 逐轴漂移、缺键轴不动；结果夹 `[1,10]` |
| `test_drift_aesthetic_clamps` | 边界鲁棒 | `base=9.9, delta=+0.5` → 10.0；`base=1.1, delta=-0.5` → 1.0（上下界 clamp） |
| `test_drift_aesthetic_empty_delta_unchanged` | 边界鲁棒 | 空 delta → 四轴原值不变 |
| `test_build_reflection_prompt` | 功能正确 | 含记忆摘要/性格/三观数值/叙事身份/长期欲望名；空输入含「（无）」 |
| `test_parse_reflection_ok` | 功能正确 | 合法 JSON → 各字段（story/becoming/self_view/personality_delta/long_term_desires） |
| `test_parse_reflection_missing_story` | 边界鲁棒 | 缺 `story`/`becoming` → `ValueError` |
| `test_parse_reflection_bad_types` | 边界鲁棒 | `self_view` 值非 str、漂移值非数值、`long_term_desires` 非数组、顶层非对象 → `ValueError` |
| `test_parse_reflection_defaults` | 边界鲁棒 | 缺省 `self_view`/`personality_delta`/`values_delta`/`aesthetic_delta`/`long_term_desires` → `{}`/`[]`（不静默吞错类型） |
| `test_parse_reflection_unknown_drift_key` | 边界鲁棒 | 漂移 key 拼错（`openess`）/ 三观 key 拼错（`extroversion`）→ `ValueError`（不静默停滞某维度演化） |
| `test_parse_reflection_aesthetic_delta` | 功能正确 | 合法 `aesthetic_delta` → 返回 dict 含该键、四轴数值透传 |
| `test_parse_reflection_aesthetic_unknown_key_raises` | 边界鲁棒 | `aesthetic_delta` 含未知维度（`foo`）→ `ValueError` |
| `test_parse_reflection_aesthetic_non_numeric_raises` | 边界鲁棒 | `aesthetic_delta` 值非数值（字符串）→ `ValueError` |
| `test_parse_reflection_drops_bad_candidate` | 边界鲁棒 | 好 + 坏候选（`subtopics` 是字符串非数组）→ 只保留好候选、核心字段照常解析（best-effort 跳过单个坏候选） |
| `test_validate_candidate` | 边界鲁棒 | `type` 非法、缺 `name`、`subtopics` 非字符串数组 → `ValueError`；合法不抛 |
| `test_to_long_term` | 功能正确 | `type` 转 `DesireType`、`strength`=`_LONG_TERM_INIT_STRENGTH`、`progress`=0.0、`subtopics`/`created_at` 透传 |
| `test_run_writes_back` | 功能正确 | 1 次 LLM（`output_type="reflection"`、`correlation_id` 透传）、evaluator 1 次；性格/三观按 delta 漂移回写、叙事 story/becoming 各 +1、self_view 合并；`add_long_term` 调 1 次 |
| `test_run_generates_correlation_id` | 功能正确 | `run(None)` → correlation_id 自生成非空 |
| `test_run_long_term_capacity` | 功能正确 | 候选 3 超过 `long_term_capacity=2` → 只新增 2（容量封顶不超） |
| `test_run_survives_bad_candidate` | 边界鲁棒 | 混合好 + 坏候选 → 核心慢变量（叙事/性格）照常回写、只新增好候选（单个坏候选不中断整次反思） |
| `test_run_unseeded_raises` | 边界鲁棒 | 单行表未 seed（personality/values/narrative 任一 `None`）→ `RuntimeError`、未发 LLM |
| `test_run_survives_invalid_json` | 边界鲁棒 | 非法 JSON（`[`）→ `run` 不抛、慢变量不回写（personality/narrative 不变、无欲望新增） |
| `test_apply_event_desire_satisfied` | 功能正确 | valence/arousal 上升（+0.2/+0.1）；发布 `EMOTION_UPDATE`（content 含 valence/arousal/emotion 字符串、source INTERNAL、correlation 透传） |
| `test_apply_event_activity_end` | 功能正确 | content `energy_delta=-25` → energy 100→75、`energy_state` 重算 OKAY |
| `test_apply_event_activity_end_no_delta` | 边界鲁棒 | 无 `energy_delta` 键 → 不崩、energy 不变（缺省 0） |
| `test_apply_event_unseeded_energy` | 边界鲁棒 | 未 seed energy → `DESIRE_SATISFIED`（读 `_publish_emotion`）与 `ACTIVITY_END`（写 `_apply_energy`）均抛 `RuntimeError`（fail-fast 不静默） |
| `test_apply_event_reflection` | 功能正确 | REFLECTION 触发 `reflect`（LLM 1 次、correlation 透传）；情感偏移 -0.1 arousal 生效（0.1→0.0） |
| `test_decay_settlement` | 功能正确 | 两次 `apply_event` 间隔 1 天 → 第二次前情感先衰减（0.2→0.1） |
| `test_get_state` | 功能正确 | 注入 fake `ActivityFacade.get_current` + `DesireFacade.get_pending` → `CurrentState` 各字段正确（current_activity/active_desires/personality/aesthetic/energy/energy_state） |
| `test_get_state_unseeded` | 边界鲁棒 | 未 seed → `get_state` 抛 `RuntimeError` |
| `test_get_narrative` | 功能正确 | store 有→返回；空→`RuntimeError` |
| `test_reflect_delegation` | 功能正确 | `facade.reflect()` → reflection LLM 调 1 次、correlation 透传 |
| `test_build_reflection_prompt_feeds_story` | 功能正确 | 已写故事/认知内容被喂进反思 prompt（而非只喂条数）+ 含「新的、与之不同」指示 |
| `test_build_reflection_prompt_aesthetic_anchor` | 功能正确 | prompt 含「当前审美（1-10）」锚点行 + `华丽 7.0`（delta 需当前值参照） |
| `test_is_duplicate_fragment` | 功能正确 | 片段去重纯函数：strip 后精确相等/高相似度 → True；明显不同/空列表 → False |
| `test_run_dedup_story` | 功能正确 | LLM story 与已有片段重复 → 不追加（`len(story)==1`）；becoming 不同照常追加、慢变量照常回写 |
| `test_run_returns_outcome_new_story` | 功能正确 | story 真新增 → `run` 返回 `ReflectionOutcome(story_is_new=True)`（`story` 字段透传） |
| `test_run_returns_outcome_dedup_story` | 功能正确 | story 与已有片段重复 → `ReflectionOutcome(story_is_new=False)`（返回值结构化，非 `str | None`） |
| `test_run_aesthetic_zero_reading_unchanged` | 功能正确 | 0 条新 reading 记忆 → 审美四轴原值不变（scale=0） |
| `test_run_aesthetic_one_reading_scaled_third` | 功能正确 | 1 条新 reading 记忆 → 按 1/3 缩放（`ornate` 7→7.1、`somber` 6→5.9，`+0.3`/`-0.3` × 1/3） |
| `test_run_aesthetic_three_reading_full` | 功能正确 | ≥3 条新 reading 记忆 → 满额缩放（`ornate` 7→7.3，`+0.3` × 1.0） |
| `test_run_aesthetic_ignores_non_reading` | 边界鲁棒 | 非 reading 记忆（`tag="user"`）不计入新读章数 → 审美不动 |
| `test_reflect_publishes_reflection_done` | 功能正确 | `facade.reflect()` → 发布 `REFLECTION_DONE`（content `{story, story_is_new}`、correlation 透传） |

## 13-activity-scheduler（日程排期纯函数）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_desire_to_activity` | 功能正确 | 四类欲望映射穷尽：`EXPLORATION→READING`、`CREATION→CREATION`、`REST→REST`、`INTERACTION→None` |
| `test_rank_desires` | 功能正确 | 按类型级 `expression_weight` 降序（越愿表达越先消费） |
| `test_rank_desires_stable_fifo` | 功能正确 | 同权两条按 `created_at` 升序（FIFO 稳定） |
| `test_rank_desires_missing_value_defaults_zero` | 边界鲁棒 | 某类型无 `DesireValue` 记录 → 按 0.0 排最后（纯函数防御） |
| `test_rank_desires_empty` | 边界鲁棒 | 空列表 → `[]` |
| `test_build_schedule_empty` | 边界鲁棒 | 空 desires → `[]`（不排空块） |
| `test_build_schedule_enough_energy_preserves_order` | 功能正确 | 精力充足（100）多条探索/创造/休息欲 → 按输入顺序产出、不插休息 |
| `test_build_schedule_inserts_rest_when_low_energy` | 功能正确 | 精力 30 一条探索欲 → 前面先插 `REST`（`[REST, READING]`） |
| `test_build_schedule_multiple_rest_when_exhausted` | 功能正确 | 精力 0 → 连续多个 `REST` 直到恢复（`[REST, REST, READING]`） |
| `test_build_schedule_skips_interaction` | 功能正确 | 互动欲被跳过（`continue`），不产块、不耗精力 |
| `test_build_schedule_rest_nonpositive_no_loop` | 边界鲁棒 | `energy_delta.rest <= 0` → 不死循环，直接产出活动块 |
| `test_format_time_label` | 功能正确 | 块序号 → `"HH:MM"`：`(0,60,9.0)→09:00`、`(1,60,9.0)→10:00`、`(2,60,9.5)→11:30`、`(0,30,0.0)→00:00` |
| `test_format_time_label_rounds_float_minutes` | 边界鲁棒 | 浮点小时 `4.1/8.2/16.4/16.9` → `"04:06"/"08:12"/"16:24"/"16:54"`（`round` 不截断少一分钟，回归保护） |
| `test_rest_energy_threshold` | 边界鲁棒 | `0.0 <= ENERGY_REST_THRESHOLD <= 100.0`（共享常量，从 `nyx.inner_life.emotion` 导入） |

## 14-activity（行为系统：store + facade + 探索链 + 观察）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_insert_get_roundtrip` | 功能正确 | `insert`+`get` 往返全等（`progress` JSON 往返、枚举 `.value` 往返） |
| `test_get_missing_returns_none` | 功能正确 | `get` 未命中 → `None` |
| `test_get_current_only_running` | 功能正确 | `get_current` 只取 running 最新一条（completed/abandoned 排除） |
| `test_get_last_exploration_empty` | 边界鲁棒 | 无 free_exploration 记录 → `0.0` |
| `test_get_last_exploration_max` | 功能正确 | 有 → `MAX(started_at)` |
| `test_list_schedule_filters_and_orders` | 功能正确 | `started_at >= start` 过滤 + ASC |
| `test_update` | 功能正确 | `update` 改 `status`/`progress`/`ended_at` → `get` 验证 |
| `test_get_paused_in_block_latest` | 功能正确 | 当前块内最新一条 PAUSED（按 `started_at DESC`），忽略其他块 |
| `test_get_paused_in_block_none` | 边界鲁棒 | 无当前块 PAUSED → `None` |
| `test_list_results_filters_and_orders` | 功能正确 | `list_results` 只回 completed + reading/free_exploration/creation 三类，按 `ended_at DESC`（observe_user 与 running 被过滤） |
| `test_day_start` | 功能正确 | `now=86400*1.5 → 86400.0` |
| `test_schedule_block_id_aligns_to_grid` | 功能正确 | 同网格块内多个 `now` 返回同标签、跨块返回不同标签、跨小时边界正确进位（`_schedule_block_id` 复用 `format_time_label`，修复 PAUSED 恢复失效） |
| `test_goal_met` | 功能正确 | goal None → True；`read` → `completed`；`write` → 有 `title`+`content`；`observe` → 有 `presence`；free_exploration → `outcome=="won"`；其余 → False（C3 精确版「一本/一篇/一次」+ C2 探索满足接线） |
| `test_sanitize_filename` | 功能正确 | 中文标题原样保留；`a/b:c` → `abc`（剔路径分隔符/非法字符）；空串/纯分隔符 → `untitled`（纯函数） |
| `test_parse_activity_result_valid` | 功能正确 | reading/creation 缺键结构合法 → 返回解析后 dict |
| `test_parse_activity_result_missing_key_raises` | 边界鲁棒 | 缺必需键 → `ValueError`（fail-fast） |
| `test_parse_activity_result_non_dict_raises` | 边界鲁棒 | JSON 顶层非 dict → `ValueError` |
| `test_select_activity_empty` | 功能正确 | 无欲望 → `None` |
| `test_select_activity_exploration` | 功能正确 | 探索欲 → READING，`desire_id`/`description`/`goal` 序列化正确 |
| `test_select_activity_interaction_returns_none` | 功能正确 | 互动欲 → `None`（不可排程） |
| `test_select_activity_rest_desire` | 功能正确 | 休息欲 → REST 且保留 `desire_id` |
| `test_select_activity_low_energy_rest` | 功能正确 | energy=30 + 探索欲 → REST 且 `desire_id` 为 None |
| `test_maybe_start_skips_when_running` | 功能正确 | 已有 running 活动 → 不新起（store 不增） |
| `test_maybe_start_skips_when_task_in_flight` | 回归保护 | 锁内 `self._task` 未完成 → 不新起（并发守卫闭合「insert PENDING → 翻 RUNNING」TOCTOU 窗口，同一时刻仅一个活动） |
| `test_default_idle_reflection_when_tired` | 功能正确 | 空槽 + 低精力 → IDLE_REFLECTION、`desire_id` None |
| `test_default_observe_user_when_energetic` | 功能正确 | 空槽 + 高精力 → OBSERVE_USER、`desire_id` None |
| `test_maybe_start_creation_activity` | 功能正确 | 有欲望 → insert + 发布 activity_start/end（source INTERNAL、desire_id/energy_delta 透传）、evaluator 调 1 次 |
| `test_creation_result_has_path` | 功能正确 | 创作落盘：result 带 `path="workspace/creations/小狐狸的日记.md"`、`file_io` 收到 `creations/小狐狸的日记.md` 与 content（B3 创作产出落盘） |
| `test_idle_reflection_result_has_summary` | 功能正确 | 发呆反思：`reflect` 回带 story 写入 `result.summary`（不发 REFLECTION 事件，直接 await） |
| `test_observe_user_result` | 功能正确 | 观察用户：result 带 `presence`/`window_title` + 确定性 summary `用户（online）正在浏览 编辑器`（0 LLM） |
| `test_observe_user_result_no_window_title` | 边界鲁棒 | `window_title` 空 → summary 省略「正在浏览」仅 `用户（away）` |
| `test_observe_user_result_with_screen_summary` | 功能正确 | `screen_summary` 非空 → result 带 `screen_summary` + summary 追加「，屏幕：写代码」（`用户（busy）正在浏览 编辑器，屏幕：写代码`） |
| `test_execute_failure_marks_incomplete` | 回归保护 | LLM 抛异常 → 活动标 INCOMPLETE + `ended_at` 非空（不卡 RUNNING） |
| `test_upgrade_to_free_exploration` | 功能正确 | 探索欲（goal.topic「骑士团」钉死）+ 精力足 + 频率过 → FREE_EXPLORATION |
| `test_no_upgrade_when_rate_limited` | 功能正确 | 频率未过 → 降级 READING |
| `test_complete_activity` | 功能正确 | COMPLETED + `ended_at` 非空 + activity_end（energy_delta=-20） |
| `test_interrupt_non_resumable_abandons` | 功能正确 | 瞬时活动（休息）打断 → ABANDONED + activity_interrupted（`by=user_message`） |
| `test_interrupt_creation_marks_paused` | 功能正确 | 创作被打断 → PAUSED（保留记录可重跑）+ activity_interrupted，非 ABANDONED |
| `test_interrupt_reading_marks_paused` | 功能正确 | 读书被打断 → PAUSED（material 层 read_chars 已 advance 可续读）+ activity_interrupted，非 ABANDONED |
| `test_interrupt_pauses_in_flight_activity` | 回归保护 | 执行中可续活动（探索）挂起可取消 await 时 interrupt → 终态 PAUSED 而非被 complete 覆盖 |
| `test_interrupt_missing` | 边界鲁棒 | 不存在 → 不发布、不崩溃 |
| `test_resume_paused_creation_reruns` | 功能正确 | 同日程块内 PAUSED 创作恢复 → 复用同一 id 重跑至 COMPLETED（不新建）、evaluator 再调 1 次 |
| `test_resume_paused_reading_refreshes_read_chars` | 功能正确 | 读书恢复 → `progress.read_chars` 从 material 层刷新（旧 0 → 6000）续读，读完 `result.read_chars==7000` |
| `test_resume_skips_different_block` | 边界鲁棒 | 不同日程块 PAUSED 不恢复：旧 PAUSED 保留 + 新起 OBSERVE_USER（共 2 条） |
| `test_get_current_delegates` | 功能正确 | `get_current` 委托 store |
| `test_get_schedule_delegates` | 功能正确 | `get_schedule` 委托 store（按 `_day_start` 过滤） |
| `test_get_results_delegates` | 功能正确 | `get_results` 委托 `store.list_results`（跨天历史产出倒序，供「产出」面板） |
| `test_should_explore_rate_limited` | 边界鲁棒 | `last=1000` + `now-last < 1h*3600` → False（无 energy 入参，精力门已移除） |
| `test_should_explore_ok` | 功能正确 | `last=0.0` + 频率过 → True（无 energy 入参，精力交给 build_schedule 兜底） |
| `test_classify_presence_online` | 功能正确 | 键盘/鼠标活跃 → online |
| `test_classify_presence_busy` | 功能正确 | 无输入 + 有窗口标题 → busy |
| `test_classify_presence_away` | 功能正确 | 无输入无标题 → away |
| `test_build_observation_summary_window_title` | 功能正确 | 有窗口无屏幕 → `用户（online）正在浏览 编辑器` |
| `test_build_observation_summary_no_window` | 边界鲁棒 | 无窗口无屏幕 → `用户（away）` |
| `test_build_observation_summary_with_screen` | 功能正确 | 窗口+屏幕 → `用户（online）正在浏览 编辑器，屏幕：写代码` |
| `test_build_observation_summary_screen_only` | 边界鲁棒 | 无窗口有屏幕 → `用户（busy），屏幕：看视频` |
| `test_sample_once_ok` | 功能正确 | 抓屏+视觉模型各 1 次 → 返回描述文本 `写代码` |
| `test_sample_once_capture_fails` | 边界鲁棒 | capture 抛异常 → 记日志返 `None` 不崩（best-effort） |
| `test_sample_once_describe_fails` | 边界鲁棒 | describe 抛异常 → 记日志返 `None` 不崩（best-effort） |
| `test_reading_completion_aggregates_note` | 功能正确 | `register_material` 存 6 字书 → 探索欲（`GoalAction.READ`）触发 `_maybe_start_activity` 读完一块 → 聚合片段产完整笔记落盘：`completed=True`、`note="完整读书笔记"`、`path="workspace/notes/book.txt-<suffix>.md"`、`file_io` 收到 `notes/book.txt-<suffix>.md`、LLM 调 `["reading","note","knowledge"]` |
| `test_no_material_rate_limited_falls_back_to_default` | 功能正确 | 探索欲（有 topic）+ 无书可读 + 限速中（`prev` FREE_EXPLORATION 刚做）→ 退回默认活动 `OBSERVE_USER`（绝不编造读书内容） |
| `test_no_material_no_topic_falls_back_to_default` | 功能正确 | 探索欲无 goal（无 topic）+ 无书可读 → 不转自由探索，退回默认活动 `OBSERVE_USER`（无 seed 不联网搜主题） |
| `test_desire_reading_reads_latest_material` | 功能正确 | 探索欲 + 已注册 7000 字书 → `READING` 读该书、`progress["result"]["read_chars"]==6000` / `["total_chars"]==7000`、书库 `next_readable().read_chars==6000`（分块推进、下次续读） |
| `test_maybe_start_reading_uses_topic` | 功能正确 | goal.topic「骑士团」命中 `骑士团历史.txt`（更早入库）→ 读该书而非更新的 `other.txt`（C2 读书按 topic 选料） |
| `test_next_readable_picks_latest_unread` | 功能正确 | 两本未读 → `next_readable()` 取 `created_at` 最新的那本 |
| `test_next_readable_skips_completed` | 功能正确 | b 已读完（`advance` 到 total）→ `next_readable()` 跳过 b 返回 a |
| `test_next_readable_none_when_all_read` | 边界鲁棒 | 全部读完 → `next_readable()` 返回 None |
| `test_upsert_resets_progress_on_reupload` | 功能正确 | 同路径重传 → `read_chars` 归零、`total_chars` 更新（`ON CONFLICT` 覆盖） |
| `test_find_by_topic_matches_unread` | 功能正确 | 书名子串命中主题 → 返回该书（优先于「最近一本」的按 topic 选料） |
| `test_find_by_topic_skips_completed` | 功能正确 | 匹配书已读完（`read_chars >= total`）→ 返回 `None` |
| `test_find_by_topic_no_match_returns_none` | 边界鲁棒 | 无书名含主题 → `None`（读书按 topic 选料无果） |
| `test_get_by_path_returns_latest_progress` | 功能正确 | `get_by_path` 按路径取书：upsert+advance 后取到最新 `read_chars`（供读书恢复续读） |
| `test_get_by_path_missing_returns_none` | 边界鲁棒 | 缺路径 → `None` |
| `test_execute_marks_active_desire` | 功能正确 | `_execute` 置 RUNNING 后 `mark_active(desire_id)` 恰 1 次（消费开始标 ACTIVE） |
| `test_execute_failure_marks_suppressed` | 功能正确 | `_execute` 异常 → 标 INCOMPLETE 且 `mark_suppressed(desire_id)` 恰 1 次（`mark_active` 也 1 次） |
| `test_execute_no_desire_no_mark` | 边界鲁棒 | 无关联 desire 的活动（默认观察）→ 不调 `mark_active`/`mark_suppressed` |
| `test_interrupt_marks_suppressed` | 功能正确 | `interrupt` 落 PAUSED/ABANDONED 后 `mark_suppressed(desire_id)` 恰 1 次（`mark_active` 不调） |
| `test_list_all_returns_ordered_by_created_desc` | 功能正确 | `list_all()` 全量读物按 `created_at` 倒序（最近上传在前）、进度随 `advance` 刷新 |
| `test_list_all_empty` | 边界鲁棒 | 空书库 → `list_all()` 返回 `[]` |
| `test_reading_relays_prior_fragments` | 功能正确 | 续读第二块时把「上次读到第 6000 字 + 已读片段笔记」喂给 LLM（`上一块的笔记`/`第 6000 字`/`本次新读` 均在 user 消息里），只发一次 reading 调用（12000 < 13000 未读完不聚合） |
| `test_pick_creation_style` | 功能正确 | 返回值 ∈ `_CREATION_STYLES`（6 风格随机池，纯函数） |
| `test_build_creation_context_full` | 功能正确 | 上下文串含「风格：日记体」/「主题：骑士团」/「知识库参考」+ 知识点正文 /「当前屏幕灵感」（W1/W2/W3 三部分拼装） |
| `test_build_creation_context_empty` | 边界鲁棒 | 无主题/知识/屏幕 → 只剩 `风格：日记体`（空段省略） |
| `test_extract_knowledge_persists_items` | 功能正确 | mock LLM 返回 `{points:[…]}` → `_memory.remember_knowledge` 收到同批 items（2 条） |
| `test_extract_knowledge_best_effort_no_raise` | 边界鲁棒 | mock LLM 抛异常 → 不冒泡（best-effort），`remember_knowledge` 未收到（空列表） |
| `test_extract_knowledge_chunks_long_content` | 功能正确 | 7000 字长正文切成 2 块、每块 `正文` ≤ 6000 字；跨块重复知识点按 content 去重 → `remember_knowledge` 收到 2 条（修复：整本书喂 LLM 绕过分块预算） |
| `test_read_finalizes_and_extracts_on_empty_chunk` | 功能正确 | 读到末尾（文件比注册时短）走 `chunk==""` 分支 → 既聚合笔记也调知识提取（`"note"` 与 `"knowledge"` 均在 LLM 调用里）（修复：完成分支漏调） |
| `test_creation_activity_injects_context` | 功能正确 | 创作活动 → user prompt 含「创作参考」/「风格：」/「知识库参考」/「当前屏幕灵感」（W1/W2/W3 走 `_run_llm_activity` 的 `context_label` 通道） |
| `test_build_creation_system` | 功能正确 | `_build_creation_system(canon, state)` 输出含 canon 原文 + 「此刻心境」段（`emotion.value`/valence/arousal/energy/desires）+ 正向创作指令 + JSON 约束（纯函数拼接） |
| `test_creation_activity_injects_canon_system` | 功能正确 | CREATION 分支 → system prompt 含 canon 人格声音 + 情绪底色（`_CapturingLlm` 捕获，非 `_ACTIVITY_SYSTEM` 默认） |

## 16-expression-prompt（prompt 拼装 + 快慢通道判定）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_build_system_prompt_base` | 功能正确 | `canon in result`（基底透传）；`narrative=None` / `memories` 缺省 → 不含 `[自我认知]` / `[相关记忆]` |
| `test_build_system_prompt_optional_blocks` | 功能正确 | `narrative` 非 None 含 `identity` 与「近期变化」；`memories` 非空含 `m.summary` |
| `test_build_system_prompt_state_fields` | 功能正确 | 状态段含 `valence=0.50` / `arousal=0.40` / `表情=happy` / `精力：80/100（energetic）` / `当前活动：reading` |
| `test_build_system_prompt_personality_values` | 功能正确 | 含 `性格（Big Five` / `三观（` 且数值渲染（`开放性5` / `对人类态度5`） |
| `test_build_system_prompt_aesthetic` | 功能正确 | 含 `审美（1-10）` 且数值渲染（`华丽7` / `沉重6`） |
| `test_state_block_idle` | 边界鲁棒 | `current_activity=None` → `当前活动：空闲` |
| `test_desires_block_empty` | 边界鲁棒 | 空欲望 → `[当前欲望]\n无` |
| `test_desires_block_renders` | 功能正确 | 欲望行含 description / type.value / strength（`读骑士小说（exploration，强度0.8）`） |
| `test_build_user_prompt_empty_context` | 功能正确 | `context=[]` → 原样返回 `message` |
| `test_build_user_prompt_with_context` | 功能正确 | 含 `[对话历史]`、`用户：` / `Nyx：`（按 role）、`[本次消息]`+`message` |
| `test_memory_block_fallback_to_content` | 边界鲁棒 | `summary=""` 回退 `content`（`m.summary or m.content`） |
| `test_build_system_prompt_ask_guidance` | 功能正确 | `ask_guidance=None` 结果不含该内容；非 None 含 `主动提问：合适时问用户。` |
| `test_slow_score_in_range` | 边界鲁棒 | 极端输入 `low < 0.5` / `high ≥ 0.5` 均在 [0,1]；时钟回拨 `last_slow_at > now` → 仍 ≥ 0 |
| `test_slow_score_factors` | 功能正确 | 五因子各生效：长>短、含「吗」>不含、含「难过」>不含、精力足平静>精力低激动、距上次大>小 |
| `test_classify_channel` | 功能正确 | `threshold=0.5`：得分 ≥ 0.5（`在吗`+精力满+2h）→ SLOW；< 0.5（`哦`+精力20+arousal0.9+60s）→ FAST |
| `test_emotion_words_no_single_char_false_positive` | 边界鲁棒 | 同长度中性词对比：「积累」（含「累」）/「麻烦」（含「烦」）不触发情感（`slow_score` 相等）；「烦躁」/「疲惫」正常命中（更高） |
| `test_build_system_prompt_tool_outputs` | 功能正确 | `tool_outputs` 非空 → 结果含 `[工具查询结果]` 段及各条 `- ` 前缀行 |
| `test_build_system_prompt_no_tool_outputs` | 边界鲁棒 | `tool_outputs=None` / `[]` → 结果不含 `[工具查询结果]` |
| `test_backtrack_empty_history` | 边界鲁棒 | 空 history → `[]` |
| `test_backtrack_max_len_and_order` | 功能正确 | 满 `max_len` 截断且返回按时间升序（oldest-first，取最近 2 条） |
| `test_backtrack_time_gap` | 功能正确 | 相邻消息隔超 `time_gap` 即停（更早的不取） |
| `test_backtrack_fast_nyx_skipped_continues` | 功能正确 | 快通道 Nyx（`fast=True`）跳过该条继续往前取更早的用户消息 |
| `test_backtrack_zero_overlap_stops` | 功能正确 | 与当前消息零字符重叠 → `result == []`（「十分不相关」即停） |
| `test_backtrack_short_message_skips_overlap_stop` | 回归保护 | 短确认语（去空白 < `_MIN_OVERLAP_LEN`）零重叠不误清历史、仍累积前文（修复：短确认语清空对话历史） |
| `test_backtrack_relevant_continues` | 功能正确 | 有字符重叠则继续累积（不误停） |
| `test_no_char_overlap` | 功能正确 | 无共同字符 `True`；有共同字符 `False`；空白忽略（`"你 好"` vs `"你好"` → `False`） |

## 17-expression（回复流程 + 碎碎念 + 搭话）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_skeletons_four_categories_nonempty` | 功能正确 | `set(_MUTTER_SKELETONS) == set(MutterCategory)`；每类 `len == 10` 且 `len(set(...)) == 10`（无重复）；每条含 `{subject}` 占位 |
| `test_pick_mutter_category_out_of_range` | 边界鲁棒 | `roll<0` / `roll>=1.0` → `None`（不触发） |
| `test_pick_mutter_category_maps_to_four` | 功能正确 | `roll=0.0/0.25/0.5/0.75` → ACTIVITY/MEMORY/DESIRE/USER |
| `test_pick_mutter_template_out_of_range` | 边界鲁棒 | `roll<0` / `roll>=1.0` → `None`（不触发） |
| `test_pick_mutter_template_bounds_and_membership` | 功能正确 | `roll=0.0` → 第 0 条；`roll=0.999` → 最后一条；`roll=0.37` ∈ 该类骨架池 |
| `test_naturalize_presence_maps_and_never_leaks_raw` | 功能正确 | `"away"`→`"你走开了"`、`"online"`→`"你在电脑前"`、`"busy"`→`"你在忙"`；输出不含 raw 枚举；未知回退原值 |
| `test_clean_fragment_strips_observation_presence` | 功能正确 | `clean_fragment("用户（away）")` 含 `"你走开了"`、不含 `"away"`（观察串润色） |
| `test_clean_fragment_collapses_and_truncates` | 功能正确 | 空白折叠；超 16 字截断（尾带 `…`、`len ≤ 17`） |
| `test_activity_subject_specific_referents` | 功能正确 | READING→`读了《挪威的森林》`、CREATION→`写了《日记》`、探索→`发现「深海鱼会发光」` |
| `test_activity_subject_missing_data_returns_none` | 边界鲁棒 | 缺 `book`/`title`/非三类活动 → `None` |
| `test_activity_subject_exploration_falls_back_to_summary` | 功能正确 | 探索无 `core_discovery` → 回退 `summary` 片段 |
| `test_should_initiate_chat_all_true` | 功能正确 | 互动欲 + 在线 + 不忙 + 精力够 + 间隔够 → `True` |
| `test_should_initiate_chat_each_condition` | 边界鲁棒 | 五条件逐项置反（无互动欲/离线/忙/精力 49/间隔 1000）→ `False` |
| `test_is_question` | 功能正确 | `"你今天好吗？"`/`"你今天怎么样"` → True（含「怎么」）；`"我很好。"` → False |
| `test_rounds_block_empty` | 边界鲁棒 | `([], [])` → `""` |
| `test_rounds_block_single` | 功能正确 | 含「第1轮内心：t1」「第1轮对外：s1」 |
| `test_rounds_block_two` | 功能正确 | 两轮顺序正确（t1 < s1 < t2 < s2） |
| `test_reply_fast` | 功能正确 | 快通道：complete×2（think+speak）、evaluate×2、`search`/`create_scene_memory` 未调、publish `[THINK, SPEAK]` |
| `test_reply_slow_non_question` | 功能正确 | 慢通道非问句：complete×7（`["tool"] + ["think","speak"]×3`）、publish `[THINK, SPEAK]×3`、`search=1`、`create_scene_memory=1`、`nyx_think`/`nyx_speak` 3 轮 `"\n"` 拼接 |
| `test_reply_slow_question` | 功能正确 | 慢通道问句：complete `["tool", "think", "speak"]`、publish `[THINK, ASK]`（非 SPEAK）、`create_scene_memory` 仍调、提前结束（think/speak 各 1） |
| `test_reply_slow_tool_executes_and_flows_into_prompt` | 功能正确 | 工具被调用（`tools.calls` 记录名+args）、结果拼进 think system prompt（含 `[工具查询结果]` 与工具名） |
| `test_reply_slow_no_tool_calls` | 边界鲁棒 | LLM 无 `tool_calls` → `tool_outputs` 空、think system prompt 不含 `[工具查询结果]` |
| `test_reply_slow_tool_failure_fallback` | 边界鲁棒 | 工具 `call` 抛异常 → 回复不崩、prompt 含「工具 {name} 执行失败」降级文案 |
| `test_reply_slow_tool_output_truncated` | 边界鲁棒 | 大工具结果（5000 字符）→ 注入 think system prompt 的工具结果被截断（含 `…`、不含 `TAIL_SENTINEL`） |
| `test_reply_slow_records_recall` | 功能正确 | 慢通道检索命中 2 条记忆 → 每条 `record_recall(m.id)` 调 1 次（短期→长期升级接线） |
| `test_cumulative_prompt` | 功能正确 | 第 2 轮 think user prompt 含第 1 轮 think/speak 文本；第 2 轮 speak 含第 2 轮 think 文本 |
| `test_slow_channel_progressive` | 功能正确 | 慢通道第 1 轮 speak prompt 含「第一句话」、不含「继续往下说」；第 2 轮 speak prompt 含「继续往下说」（递进续写） |
| `test_current_message_not_duplicated` | 回归保护 | `[对话历史]` 段不含当前消息、`[本次消息]` 含且仅一次 |
| `test_history_order` | 功能正确 | 连续两次 reply 后 history 的 role 序列为 `[user, nyx, user, nyx]`（快慢通道都落历史、按序交替） |
| `test_history_fast_channel` | 回归保护 | 两次都走快通道时，第二次回复 prompt 仍含上一轮 `用户：`/`Nyx：` 历史（历史不因快通道丢失） |
| `test_record_message_marks_fast` | 功能正确 | 快通道 nyx 消息 `fast=True`、慢通道 nyx 消息 `fast=False`（回溯截断的依据） |
| `test_reply_slow_backtrack_skips_fast_nyx` | 功能正确 | 慢通道回溯：跳过 `fast=True` 的快通道 nyx 消息（`Nyx：嗯嗯` 不进 prompt）、保留更早的相关用户消息（`用户：我上周去爬山了` 进 prompt） |
| `test_record_proactive_turn_appends_to_history` | 功能正确 | `record_proactive_turn("…")` → `_history` 尾多一条 `role="nyx"`、`content="…"`、`fast is False` |
| `test_reading_turn_slow_backtrack_preserved` | 功能正确 | 读书 turn（fast=False）后慢通道 reply → 回溯 prompt 含 `Nyx：她刚才问：…`（与 fast-skip 各证一半） |
| `test_mutter_skips_when_busy` | 功能正确 | `current_activity` 非 None → 不发 |
| `test_mutter_miss` | 功能正确 | `random.random()` 未命中 → 不发 |
| `test_mutter_activity_fills` | 功能正确 | 命中 + ACTIVITY 类有读书产出 → 发 `mutter`，content 含「读了《挪威的森林》」具体指涉 |
| `test_mutter_memory_fills` | 功能正确 | 命中 + MEMORY 类有最近记忆 → 发 `mutter`，content 含 `content`（优先）/`summary` 片段 |
| `test_mutter_desire_fills` | 功能正确 | 命中 + DESIRE 类有 active_desires → 发 `mutter`，content 含 `description` |
| `test_mutter_user_fills` | 功能正确 | 命中 + USER 类有 tag="user" 记忆 → 发 `mutter`，content 含用户画像文本 |
| `test_mutter_user_naturalizes_presence` | 功能正确 | USER 类画像 summary 是观察串「用户（away）」→ content 含 `"你走开了"`、不含 raw `"away"` |
| `test_mutter_llm_wander` | 功能正确 | `random` 命中 `_LLM_MUTTER_RATE` → `llm.complete(output_type="mutter_wander")`、发 `mutter`（content 即 LLM 产出） |
| `test_mutter_llm_wander_empty_falls_back` | 功能正确 | LLM 即兴空 → 回退模板填空（仍发 `mutter`，content 为模板句） |
| `test_mutter_dedup` | 功能正确 | 连续两次相同文本 → 第二次去重不发（`bus.published` 仅 1 条） |
| `test_mutter_no_data_skips` | 功能正确 | 命中但该类数据源空 → 不发 |
| `test_initiate_chat_empty` | 边界鲁棒 | 空 content → `False` 且不发 |
| `test_initiate_chat_non_empty` | 功能正确 | 非空 → `True` 且发 `initiate_chat`（output_type/correlation 一致）、system prompt 含 `[主动提问指导]` |
| `test_initiate_chat_appends_history` | 功能正确 | 非空发话后 facade 内部 history 含一条 `role="nyx"`、content 为开场白的消息（搭话落历史，后续回复可回溯） |
| `test_reply_ask_guidance_slow_only` | 功能正确 | 慢通道（精力高+平静）system prompt 含 `[主动提问指导]`；快通道（精力低+激动）不含 |
| `test_reply_question_sets_waiting_user` | 功能正确 | 慢通道问句结尾 → `_waiting_user=True`、`_ask_text`/`_ask_cid` 落值（供 tick 超时收尾） |
| `test_reply_fast_question_sets_ask` | 功能正确 | 快通道问句结尾也置 `ask`/`_waiting_user`（快通道绕过 should_ask，问句无人答信号不丢），publish `[THINK, ASK]` |
| `test_reply_clears_pending_state` | 功能正确 | 用户说话即清 `_waiting_user`/`_ask_cid`/`_pending_chat_desire_id`，且回复搭话时 `desire.satisfy("d1", True)` 闭环消费（不做「是否真在答」判断） |
| `test_initiate_chat_sets_pending_desire` | 功能正确 | 搭话发出 → `_pending_chat_desire_id == desire.id`（超时未回则回灌） |
| `test_check_timeouts_records_no_answer` | 功能正确 | wait_user 超时 → `memory.record_no_answer` 调 1 次、清 `_waiting_user`/`_ask_cid` |
| `test_check_timeouts_before_timeout_noop` | 边界鲁棒 | 未到超时点 → 无动作（wait_user 与待回搭话都保持） |
| `test_check_timeouts_expires_ignored_chat` | 功能正确 | 搭话超时未回 → `desire.expire` 调 1 次（值回灌）、清 `_pending_chat_desire_id` |

## 18-api（组合根 + REST + SSE）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_root_event_defaults_external` | 功能正确 | `id == correlation_id`、默认 `source is EXTERNAL`、`type`/`content` 原样透传、`timestamp` 非空 |
| `test_root_event_explicit_internal` | 功能正确 | 显式 `source=Source.INTERNAL` → `source is INTERNAL` |
| `test_load_canon_reads_canon_file` | 功能正确 | 读 `canon.md` 返回原文 |
| `test_load_canon_missing_file_fails` | 边界鲁棒 | 缺失 → `FileNotFoundError`（fail-fast） |
| `test_load_ask_reads_ask_file` | 功能正确 | 读 `ask.md` 返回原文 |
| `test_load_ask_missing_file_fails` | 边界鲁棒 | 缺失 → `FileNotFoundError`（fail-fast） |
| `test_seed_inner_life_idempotent` | 功能正确 | 空表 seed 四表（personality/values/energy/narrative）值 = canon §2/§3 初始值；再跑一遍值不变、不重复行 |
| `test_seed_desire_idempotent` | 功能正确 | `list_values()` 四类型、`list_long_term()` 3 条；再跑幂等（4 行 / 3 条不增） |
| `test_build_tools_web_disabled` | 功能正确 | `web_enabled=False` → `{local_search, file_io}`（工厂构造无 I/O，`roots`/`DDGS` 惰性到 `.call()`） |
| `test_build_tools_web_enabled` | 功能正确 | `web_enabled=True` → 多 `web_search` + `web_fetch` |
| `test_state_endpoint` | 功能正确 | `GET /api/state` → `CurrentState` JSON，枚举字段为 `.value` 字符串（`emotion=neutral`、`energy_state=okay`）、`aesthetic` 四键 7/7/6/6 |
| `test_chat_endpoint` | 功能正确 | `POST /api/chat` → `{event_id}`；bus 收一条 `USER_MESSAGE`（source EXTERNAL、`correlation_id == id`） |
| `test_memories_endpoint` | 功能正确 | `GET /api/memories?tag=&type=` → `Memory[]`；`type` query 转 `MemoryType` 枚举传入 facade |
| `test_memory_search_endpoint` | 功能正确 | `GET /api/memories/search?q=` → `Memory[]`；`q` query 传入 `memory.search`（fake 记 `search_calls`） |
| `test_observe_endpoint` | 功能正确 | `POST /api/observe`（`{presence, window_title}`）→ `{event_id}`；bus 收 `OBSERVATION_STATE`（content `{presence, window_title}`）、`last_presence`/`last_window_title` 更新 |
| `test_export_endpoint` | 功能正确 | `POST /api/export` `json`/`md` 返回原始字符串（非 JSON 二次编码），`content-type` 分别 `application/json`/`text/markdown` |
| `test_export_bogus_raises` | 边界鲁棒 | `format=bogus` → Facade 抛 `ValueError`（端点不吞，透出为 500） |
| `test_tick_loop_emits_four_clock_ticks` | 功能正确 | 跑一个循环 → 4 条 `CLOCK_TICK`，`tick_type` 覆盖四类、每条 `source is INTERNAL`（系统定时器非外部输入） |
| `test_subscription_consistency` | 功能正确 | 对 `ROUTING` 每个非空消费者 publish → 对应 Facade 方法被调（inner_life×4 / desire×2 / activity×1 / expression×1） |
| `test_chat_missing_message_returns_422` | 边界鲁棒 | `POST /api/chat` 缺 `message` → 422（pydantic 请求模型校验，非 500） |
| `test_observe_invalid_presence_returns_422` | 边界鲁棒 | `POST /api/observe` `presence=Online`（拼写错误）→ 422、不 publish、`last_presence` 不变 |
| `test_supervise_bus_breaks_after_max_failures` | 回归保护 | `_supervise_bus` 连续 `_BUS_MAX_FAILURES` 次失败 → `RuntimeError` 重抛熔断（`run()` 调用 == 阈值） |
| `test_supervise_bus_resets_on_recovery` | 边界鲁棒 | 崩溃前 `persisted_count` 每次 +`_BUS_RECOVERY_STREAK`（达恢复阈值）→ 计数重置、超阈值仍不熔断（永不假熔断） |
| `test_supervise_bus_breaks_on_flapping` | 回归保护 | `_FlappingBus` 每次 run 前 `persisted_count += 1`（单次成功不足恢复阈值）→ `calls == _BUS_MAX_FAILURES` 熔断（DB 抖动「隔一个挂一次」不假自愈） |
| `test_first_tick_starts_activity_not_mutter_or_chat` | 功能正确 | `grid_minutes=60` 首轮只发 `schedule_block_start`/`desire_eval`（首个活动块启动即触发），碎碎念/搭话不立即触发 |
| `test_main_propagates_serve_failure` | 功能正确 | fake `server.serve()` 抛 `RuntimeError`（端口被占）→ `main()` 重抛 `RuntimeError`（非零退出，不静默吞） |
| `test_main_propagates_tick_failure` | 功能正确 | fake `_tick_loop` 抛 `RuntimeError` + 阻塞 serve/bus → `main()` 重抛 `RuntimeError`（tick 异常传播，不再静默丢周期事件） |
| `test_materials_endpoint_returns_progress` | 功能正确 | `GET /api/materials` → `{materials: [Material]}`（`read_chars`/`total_chars` 进度），不再是纯文件名；`list_materials` 委托恰 1 次 |
| `test_upload_endpoint_registers_material` | 功能正确 | `POST /api/upload`（multipart 6 字书）→ `file_io` 写 `uploads/book.txt` 后 `register_material(path, name, 6)` 入库，返回 `{filename:"book.txt", path:"workspace/uploads/book.txt"}`；`registered == [("workspace/uploads/book.txt","book.txt",6)]`、`bus.published == []`（只注册不触发读书） |
| `test_check_reflect_skips_within_cooldown` | 边界鲁棒 | `updated_at` 距 now < `_REFLECT_MIN_INTERVAL` → 不触发（`reflect` 不调） |
| `test_check_reflect_skips_below_new_memory_threshold` | 边界鲁棒 | 已过冷却但新记忆 < `_REFLECT_MIN_NEW_MEMORIES` → 不触发（`reflect` 不调） |
| `test_check_reflect_triggers` | 功能正确 | 过冷却 + 新记忆达标 → `reflect` 调 1 次（correlation 透传） |

## 19-reading-content（陪读内容：segmenter + epub + store + facade + POST /api/books）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_heading_plus_paragraph_merges_and_marks_chapter_start` | 功能正确 | `<h2>第一章</h2><p>正文</p>` → `[Segment("第一章\n正文", is_chapter_start=True)]`（h2+p 合并、章首标记） |
| `test_h1_alone_marks_chapter_start` | 功能正确 | `<h1>序章</h1>` → `[Segment("序章", True)]` |
| `test_h3_is_not_chapter_start` | 边界鲁棒 | `<h3>小节</h3>` → `is_chapter_start=False`（仅 h1/h2 章首） |
| `test_consecutive_li_merge_newline_separated` | 功能正确 | 连续 `<li>一/二/三` → `"一\n二\n三"` 合并一段 |
| `test_short_paragraphs_merge` | 功能正确 | 连续短 `<p>`（累计 <100 字符）→ `"短。\n也短。"` 合并 |
| `test_long_paragraph_splits_at_period` | 功能正确 | 单段 4000+ 字符 → 在句号处拆 2 段（`"x"*2000+"。"` / `"y"*2000`） |
| `test_fallback_no_block_tags_whole_text` | 边界鲁棒 | 无块级标签（`<div>`）→ 全文一段 |
| `test_empty_html_returns_empty` | 边界鲁棒 | 空 HTML → `[]` |
| `test_blockquote_independent` | 功能正确 | `blockquote` 独立成段，不与后续 `p` 合并 |
| `test_nested_block_direct_text_preserves_document_order` | 功能正确 | `<li>引言正文 <p>这是列表项</p></li>` → `["引言正文", "这是列表项"]`（外块直接文本在嵌套块前，文档序不乱） |
| `test_nested_block_tail_text_preserves_document_order` | 功能正确 | `<blockquote>引言<p>内文</p>续</blockquote>` → `["引言", "内文", "续"]`（尾随直接文本在嵌套块后） |
| `test_parse_epub_extracts_title_author_and_segments` | 功能正确 | 内存 EPUB → title/author 提取 + 两章两段（章首标记） |
| `test_parse_epub_content_hash_stable` | 功能正确 | 同字节两次 `content_hash` 相等、长度 64（SHA-256 hex） |
| `test_parse_epub_missing_metadata_falls_back_to_empty` | 边界鲁棒 | 缺 title/author → 空串 `""` |
| `test_parse_epub_skips_non_document_spine_items` | 功能正确 | 封面图进 spine → 只读 `ITEM_DOCUMENT`，段数仍 2 |
| `test_parse_epub_non_zip_raises_value_error` | 边界鲁棒 | 非 ZIP 字节 → `ValueError`（无效 EPUB，不裸抛 `BadZipFile`） |
| `test_parse_epub_zip_without_container_raises_value_error` | 边界鲁棒 | 缺 `container.xml` 的 ZIP → `ValueError` |
| `test_parse_epub_rejects_oversized_uncompressed` | 边界鲁棒 | 解压后总量超 `_MAX_UNCOMPRESSED_BYTES`（monkeypatch 到 100）→ `ValueError`（zip 炸弹预检） |
| `test_insert_book_with_paragraphs_returns_new` | 功能正确 | 原子插书+3 段：`created=True`、`title`/`total_paragraphs==3`、`"index"==[1,2,3]`、`is_chapter_start==[1,0,0]`、首段文本正确 |
| `test_insert_book_with_paragraphs_duplicate_returns_existing` | 功能正确 | 同 `content_hash` 二次 → `created=False`、返回同 `id` 书、books 仍 1 行（唯一索引回退） |
| `test_insert_concurrent_same_hash_yields_single_book` | 功能正确 | `asyncio.gather` 并发同哈希 → 恰 1 个 `created=True`、books 1 行（去重 TOCTOU 闭合） |
| `test_insert_atomic_rolls_back_on_paragraphs_failure` | 边界鲁棒 | monkeypatch `_insert_paragraphs` 抛 `aiosqlite.Error` → 上抛且 books 0 行（无空壳书，事务回滚） |
| `test_find_by_hash_hit` | 功能正确 | `find_by_hash` 命中返回同 id 书 |
| `test_find_by_hash_miss_returns_none` | 边界鲁棒 | 未命中 → `None` |
| `test_import_book_inserts_book_and_paragraphs` | 功能正确 | mock `parse_epub` → `import_book` 落书+段落（`total_paragraphs==3`、`"index"==[1,2,3]`） |
| `test_import_book_duplicate_raises` | 功能正确 | 同 `content_hash` 二次导入 → `DuplicateBookError`（`existing_book_id`/`title` 透传） |
| `test_import_book_empty_segments_raises_value_error` | 边界鲁棒 | 空 segments → `ValueError`、books 表 0 行（不插书） |
| `test_import_book_title_falls_back_to_filename` | 功能正确 | title 空 → 回退 filename（`"我的书.epub"`） |
| `test_delete_book_cascades_paragraphs` | 功能正确 | `DELETE FROM books` → paragraphs 级联删空（`ON DELETE CASCADE`） |
| `test_books_success_returns_201` | 功能正确 | `POST /api/books`（multipart）→ 201 `Book`、`import_book(filename, bytes)` 收对参数 |
| `test_books_duplicate_returns_409` | 功能正确 | `DuplicateBookError` → 409 `detail={existing_book_id, title}` |
| `test_books_non_epub_returns_400` | 边界鲁棒 | `.txt` → 400、`import_book` 不调 |
| `test_books_empty_body_returns_400` | 边界鲁棒 | `ValueError("EPUB 无正文")` → 400 |
| `test_books_parse_failure_returns_500` | 边界鲁棒 | 解析抛 `RuntimeError` → 500 |
| `test_books_parse_failure_logs_error` | 边界鲁棒 | 解析失败 → 500 且 `caplog` 记录含「导入 EPUB」的 ERROR（异常不静默吞） |
| `test_books_sanitizes_filename` | 功能正确 | 上传文件名 `../../evil<1>.epub` → `import_book` 收到 `"evil1.epub"`（剥路径 + 去 `<`/`>`） |
| `test_books_too_large_returns_400` | 边界鲁棒 | 超 `_MAX_EPUB_BYTES` → 400、`import_book` 不调（中断不继续读） |

## 20-reading-progress（陪读进度：progress/书架/分页 + 4 端点）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_find_book_hit_and_miss` | 功能正确 | `find_book` 命中返回同 id 书、未命中 `None` |
| `test_list_books_unread_sentinel_and_read_ordering` | 功能正确 | 未读 `user_position=0`/`last_read_at=None`、已读排前（`[read_id, unread_id]`） |
| `test_list_paragraphs_range_ascending_and_bool_restored` | 功能正确 | `list_paragraphs(1,2)` → `"index"==[1,2]` 升序、`is_chapter_start` 1→`True`/0→`False`（bool 还原） |
| `test_get_progress_none_then_value` | 功能正确 | 无行 → `None`；upsert 后回读 `user_position=4`/`nyx_position=3`/`reading_speed=70`/`read_count=0` |
| `test_upsert_progress_insert_then_update` | 功能正确 | 同 book_id 单行、`updated_at` 推进（monkeypatch 时钟）、`reading_speed` 更新 |
| `test_upsert_does_not_reset_read_count` | 边界鲁棒 | `increment` 后 `upsert` → `read_count` 仍 1（进度写回不重置重读计数） |
| `test_increment_read_count_zero_to_one_to_two` | 功能正确 | 两次 `++` → `read_count` 1→2 |
| `test_increment_read_count_creates_default_row` | 功能正确 | 无行 `++` → 建默认行 `read_count=1`、`user_position`/`reading_speed` 走 DDL DEFAULT（1/50）、`nyx_position` 显式落 `total`=3 |
| `test_increment_read_count_persists_nyx_position` | 功能正确 | `increment_read_count(book_id, 5)` → `get_progress().nyx_position==5`（跨重启幂等信号落库） |
| `test_delete_book_cascades_reading_progress` | 功能正确 | 删 book → `reading_progress` 级联删空 |
| `test_list_books_lists_imported_book` | 功能正确 | 导入 1 本 → `list_books` 1 项、`user_position=0`/`last_read_at=None`（未读哨兵） |
| `test_get_progress_default_when_no_row` | 功能正确 | 无进度行 → 默认 `(1,1,50,0,0.0)`（`updated_at=0.0` 从未保存哨兵） |
| `test_save_progress_insert_then_update` | 功能正确 | 首次 INSERT 再次 UPDATE（单行）、`read_count` 不被写回（0） |
| `test_list_paragraphs_range` | 功能正确 | `list_paragraphs(2,4)` → `"index"==[2,3,4]` |
| `test_list_paragraphs_to_idx_exceeds_total_raises_value_error` | 边界鲁棒 | `to=99 > total` → `ValueError`（越界不截断） |
| `test_book_not_found_raises` | 边界鲁棒 | `get_progress`/`save_progress`/`list_paragraphs` 对不存在书抛 `BookNotFoundError` |
| `test_books_list_returns_list` | 功能正确 | `GET /api/books` → `[BookListItem]`、`user_position`/`last_read_at` 透传 |
| `test_progress_get_returns_value` | 功能正确 | `GET /api/progress/{id}` → `ReadingProgress`（position/speed/read_count 透传） |
| `test_progress_get_book_not_found_returns_404` | 边界鲁棒 | `BookNotFoundError` → 404 |
| `test_progress_put_saves_and_returns_ok` | 功能正确 | `PUT /api/progress/{id}` → `{ok:true}`、`save_progress` 收对 `(book_id,4,3,70)` |
| `test_progress_put_missing_reading_speed_returns_422` | 边界鲁棒 | 缺 `reading_speed` → 422、`save_progress` 不调 |
| `test_progress_put_reading_speed_out_of_range_returns_422` | 边界鲁棒 | `reading_speed=9`/`201` → 422 |
| `test_progress_put_book_not_found_returns_404` | 边界鲁棒 | `save_progress` 抛 `BookNotFoundError` → 404 |
| `test_paragraphs_returns_range` | 功能正确 | `GET /api/books/{id}/paragraphs?from=2&to=3` → `"index"==[2,3]`、`is_chapter_start` 透传 |
| `test_paragraphs_missing_from_to_returns_422` | 边界鲁棒 | 缺 `from`/`to` → 422 |
| `test_paragraphs_invalid_range_returns_422` | 边界鲁棒 | `from=0` / `to<from` → 422 |
| `test_paragraphs_book_not_found_returns_404` | 边界鲁棒 | `BookNotFoundError` → 404 |
| `test_paragraphs_to_exceeds_total_returns_422` | 边界鲁棒 | `ValueError` → 422 |

## 21-reading-impulse（陪读冲动：特征提取 + 驱动现算 + 复合 + 阈值冷却 + 端点）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_extract_rich_paragraph_detects_features` | 功能正确 | 富段落（绝望/哭/意义/自由）→ `philosophical`/`negative_emo`/`exclamation_ratio`/`character_mention` > 0、`richness_score ∈ [0,1]` |
| `test_extract_rich_scores_higher_than_flat` | 功能正确 | 富段落 `richness_score` > 平淡段落（「今天天气不错。」） |
| `test_build_drives_energy_and_agreeableness` | 功能正确 | energy=100 → `motivation=1.0`；agreeableness=10 平段 → `empathy_bias=0.6`；`curiosity`/`boredom` 直通（0.5/0.3） |
| `test_build_drives_emotional_paragraph_raises_empathy` | 功能正确 | 情绪段 `empathy_bias` > 平段（情感密度拉高共鸣） |
| `test_compute_composite_weights_spot_check` | 功能正确 | `question_knowledge=0.52`、`associate=0.48`（Σ驱动×权重抽查两档） |
| `test_check_triggers_above_threshold_fires` | 功能正确 | 复合值 0.6 ≥ 阈值 0.55 → 触发 `QUESTION_KNOWLEDGE` |
| `test_check_triggers_within_cooldown_suppressed` | 边界鲁棒 | 冷却内（`last_at == now`）→ 不触发（`[]`） |
| `test_check_triggers_below_threshold_suppressed` | 边界鲁棒 | 0.1 < 阈值 → 不触发（`[]`） |
| `test_evaluate_paragraph_forward_dispatches_events` | 功能正确 | 富段落前翻 → `ASSOCIATE` 触发 + 后台广播 `reading_mutter`/`reading_question`/`reading_association` 三事件 |
| `test_evaluate_paragraph_backtrack_returns_empty` | 边界鲁棒 | `paragraph_index ≤ last` → `[]` 且零广播（回翻幂等） |
| `test_evaluate_paragraph_missing_book_returns_empty` | 边界鲁棒 | 书不存在 → `[]` 且零广播（不 404、幂等） |
| `test_evaluate_paragraph_cooldown_suppresses_repeat` | 功能正确 | 同批行为连续两段 → 第二次 `[]`（冷却抑制重复） |
| `test_evaluate_paragraph_flat_paragraph_no_mutter` | 边界鲁棒 | 平淡段落 → 无 `reading_mutter`（richness 闸门不越过） |
| `test_evaluate_paragraph_associate_searches_and_broadcasts` | 功能正确 | 联想检索 1 次 → 每条记忆广播一条 `reading_association`（`memory_id`/`snippet` 透传） |
| `test_evaluate_paragraph_quote_question_splits_lines` | 功能正确 | `quote_question` 两行 → `content`=首行、`selected_text`=次行（划线拆分） |
| `test_evaluate_paragraph_quote_question_single_line_null_selection` | 边界鲁棒 | `quote_question` 单行 → `selected_text=None` |
| `test_mutter_reading_none_content_skips_without_raise` | 边界鲁棒 | LLM 返回 `content=None` → `.strip()` 不炸、零广播（try 兜住后处理） |
| `test_associate_reading_none_search_skips_without_raise` | 边界鲁棒 | `memory.search` 返回 `None` → `[:3]` 不炸、零广播（try 兜住切片） |
| `test_question_reading_records_proactive_turn` | 功能正确 | `_question_reading`（quote_question）→ 广播 `reading_question` 且 `fake_expression.recorded == [问题正文]`（不含 selected_text） |
| `test_associate_reading_records_proactive_turn_per_memory` | 功能正确 | 2 条记忆 → 2 条 `reading_association` 且 `recorded == [snippet1, snippet2]`（每条一次） |
| `test_mutter_reading_does_not_record_proactive_turn` | 功能正确 | `_mutter_reading` → 广播 `reading_mutter` 且 `recorded == []`（未调 record_proactive_turn） |
| `test_impulse_evaluate_returns_triggered` | 功能正确 | `POST /api/impulse/evaluate` → `{triggered:[associate, question_knowledge]}`、`evaluate_paragraph` 收对 `(book_id,2,1)` |
| `test_impulse_evaluate_missing_last_paragraph_returns_422` | 边界鲁棒 | 缺 `last_paragraph_index` → 422、不调 facade |

## 22-reading-notes（陪读笔记：用户笔记 + Nyx 批注 + 章节边界整合 + 6 端点）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_insert_user_note_with_and_without_paragraph_id` | 功能正确 | 有段/无段两条笔记：`paragraph_id`/`selected_text` 有值或 `None`、`content` 正确 |
| `test_list_user_notes_sorted_desc` | 功能正确 | 同书两笔记 → `list_user_notes` 新在前（`[second.id, first.id]`） |
| `test_update_user_note_hit_and_miss` | 功能正确 | 命中 → 返回更新后笔记（`content=="新"`）；未命中 → `None` |
| `test_update_user_note_same_tick_same_content_returns_note` | 边界鲁棒 | 同 tick 两次同内容更新 → 仍返回笔记（不误判 404） |
| `test_delete_user_note_cascades_annotations` | 功能正确 | 删笔记 → `True` 且批注 FK CASCADE 清空；再删 → `False` |
| `test_get_user_note_and_get_paragraph` | 功能正确 | `get_user_note`/`get_paragraph` 命中返回对象、未命中 `None` |
| `test_list_annotations_for_notes_sorted_desc` | 功能正确 | 两条笔记各插批注 → 批量查按 `created_at` 降序（`[a3,a2,a1]`）、空列表返回 `[]` |
| `test_delete_book_sets_note_fk_null` | 边界鲁棒 | 删书 → 笔记 `book_id`/`paragraph_id` 置 `None`、`content` 保留（SET NULL） |
| `test_parse_reading_note_valid` | 功能正确 | 合法 JSON → `(content, summary)` 二元组 |
| `test_parse_reading_note_non_json_raises` | 边界鲁棒 | 非 JSON → `ValueError` |
| `test_parse_reading_note_missing_key_raises` | 边界鲁棒 | 缺 `summary` → `ValueError` |
| `test_parse_reading_note_wrong_type_raises` | 边界鲁棒 | `content` 非 str → `ValueError` |
| `test_add_and_list_user_notes_with_annotations` | 功能正确 | 加笔记+批注 → `list_user_notes` 返回带 `annotations` 的完整笔记 |
| `test_list_user_notes_batches_annotations` | 功能正确 | 两笔记各一批注 → `list_annotations_for_notes` 只调 1 次、批注归到各自笔记 |
| `test_add_user_note_without_paragraph_id` | 功能正确 | 自由记 → `paragraph_id`/`selected_text` 为 `None` |
| `test_update_user_note_hit_and_miss` | 边界鲁棒 | 命中改 content；未命中 → `NoteNotFoundError` |
| `test_delete_user_note_hit_and_miss` | 边界鲁棒 | 命中删；再删 → `NoteNotFoundError` |
| `test_show_to_nyx_writes_annotation_with_paragraph` | 功能正确 | LLM `reading_annotation` → 写批注、prompt 含原段落、evaluator 记 1 次 |
| `test_show_to_nyx_book_deleted_reads_note_only` | 边界鲁棒 | 书已删 → prompt 只含笔记文字、不含原段落 |
| `test_check_chapter_boundary_chapter_end_integrates` | 功能正确 | 下一段 `is_chapter_start` → CHAPTER_END、LLM `reading_note` json_mode、`remember_reading` 收到 `(content, summary, book_id)` |
| `test_check_chapter_boundary_none_when_next_not_chapter` | 边界鲁棒 | 非章节边界 → NONE、`remembered==[]` |
| `test_check_chapter_boundary_book_finished_integrates` | 功能正确 | 读到末段 → BOOK_FINISHED、`read_count` 0→1 |
| `test_book_finished_persists_nyx_position_total` | 功能正确 | BOOK_FINISHED 后 `progress.nyx_position==total`（跨重启幂等信号落库） |
| `test_check_chapter_boundary_reread_reflects` | 功能正确 | read_count=1（重读）→ `inner_life.reflect(book_id)` 调 1 次 |
| `test_check_chapter_boundary_first_read_no_reflect` | 功能正确 | 首读 → 不 reflect |
| `test_integrate_buffer_empty_skips` | 边界鲁棒 | buffer 空 → 仍返回边界、`remembered==[]` |
| `test_mutter_and_question_record_nyx_output` | 功能正确 | mutter/question 各入 buffer（`source` 集合 `{"mutter","question"}`） |
| `test_book_finished_increments_read_count_even_with_empty_buffer` | 边界鲁棒 | 整本读完 buffer 空 → `read_count` 仍 0→1 |
| `test_book_finished_increments_read_count_even_when_integrate_fails` | 边界鲁棒 | 整本读完 LLM 抛异常 → `read_count` 仍 0→1 |
| `test_check_chapter_boundary_repeat_book_finished_no_double_increment` | 功能正确 | 重复判 BOOK_FINISHED（`nyx_position` 停在 `>= total`）→ `read_count` 仍 1 |
| `test_check_chapter_boundary_repeat_no_second_integration_no_reflect` | 边界鲁棒 | 重复 BOOK_FINISHED（首次整合仍在 LLM 等待中）→ 不 spawn 第二个整合、不误触 reflect |
| `test_check_chapter_boundary_reread_increments_again` | 功能正确 | 回翻/重读（回到 `< total`）后再到末段 → `read_count` 1→2 |
| `test_integrate_preserves_entries_added_during_llm` | 边界鲁棒 | 整合成功只删已消费快照 → LLM 等待期间新 append 的条目保留给下一轮 |
| `test_integrate_failure_preserves_buffer` | 边界鲁棒 | 章末整合失败 → buffer 保留 1 条（供重试） |
| `test_record_nyx_output_caps_buffer` | 边界鲁棒 | 超 `_NYX_BUFFER_MAXLEN` → 丢弃最旧 5 条、保留最新 100 条 |
| `test_add_user_note_missing_book_raises` | 边界鲁棒 | 书不存在 → `BookNotFoundError` |
| `test_add_user_note_missing_paragraph_raises` | 边界鲁棒 | 段落不存在 → `ValueError` |
| `test_add_user_note_cross_book_paragraph_raises` | 边界鲁棒 | 段落属他书 → `ValueError` |
| `test_show_to_nyx_llm_failure_returns_none` | 边界鲁棒 | LLM 抛异常 → 返回 `None`、不落批注 |
| `test_show_to_nyx_none_content_returns_none` | 边界鲁棒 | LLM 返回空 → 返回 `None`、不落批注 |
| `test_notes_list_returns_list` | 功能正确 | `GET /api/notes/{book_id}` → 200 列表、字段透传 |
| `test_notes_add_returns_201` | 功能正确 | `POST /api/notes/user` → 201、`added_notes` 收对 `(book_id, paragraph_id, content, selected_text)` |
| `test_notes_add_missing_content_returns_422` | 边界鲁棒 | 缺 `content` → 422 |
| `test_notes_add_optional_fields_default_none` | 功能正确 | 只传 book_id+content → paragraph_id/selected_text 为 `None` |
| `test_notes_update_returns_note` | 功能正确 | `PUT` → 200、返回笔记 |
| `test_notes_update_not_found_returns_404` | 边界鲁棒 | `NoteNotFoundError` → 404 |
| `test_notes_delete_returns_204` | 功能正确 | `DELETE` → 204 |
| `test_notes_delete_not_found_returns_404` | 边界鲁棒 | `NoteNotFoundError` → 404 |
| `test_notes_show_to_nyx_returns_annotation` | 功能正确 | `POST .../show-to-nyx` → 200 完整批注（`id`/`user_note_id`/`content`） |
| `test_notes_show_to_nyx_not_found_returns_404` | 边界鲁棒 | `NoteNotFoundError` → 404 |
| `test_notes_add_missing_book_returns_404` | 边界鲁棒 | `BookNotFoundError` → 404 |
| `test_notes_add_missing_paragraph_returns_422` | 边界鲁棒 | `ValueError`（段落不存在）→ 422 |
| `test_notes_show_to_nyx_llm_failure_returns_null` | 边界鲁棒 | facade 返回 `None` → 200 `null` |
| `test_boundary_chapter_end` | 功能正确 | `CHAPTER_END` → `{is_boundary:true, book_finished:false}` |
| `test_boundary_book_finished` | 功能正确 | `BOOK_FINISHED` → `{is_boundary:false, book_finished:true}` |
| `test_boundary_none` | 功能正确 | `NONE` → 两者 false |
| `test_boundary_book_not_found_returns_404` | 边界鲁棒 | `BookNotFoundError` → 404 |
| `test_boundary_missing_nyx_position_returns_422` | 边界鲁棒 | 缺 `nyx_position` → 422 |

## frontend-sse（SSE 数据流：useSSE + dispatchEvent 分发表）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `useSSE > 挂载即 new EventSource` | 功能正确 | url == `BASE_URL + "/api/events"`、初始 `connecting` |
| `useSSE > onopen/onerror 三态` | 功能正确 | `onopen` → `open`、`onerror` → `connecting`（原生自动重连） |
| `useSSE > 命名帧解析` | 功能正确 | emit `speak` 帧 → dispatch 收到 `{event,event_id,correlation_id,content}` 展开 |
| `useSSE > 坏 data/缺字段跳过` | 边界鲁棒 | 非法 JSON、缺 `event_id`/`correlation_id` → `console.error` 跳过不崩，仅正常帧 dispatch |
| `useSSE > unmount 调 close()` | 功能正确 | 卸载 cleanup → `source.close()` 被调 |
| `useSSE > EVENT_TYPES 含三型阅读事件` | 功能正确 | `reading_mutter`/`reading_question`/`reading_association` 三帧被 `addEventListener` 监听并各 dispatch 1 次（缺任一 `EVENT_TYPES` 值则该型帧被浏览器静默丢弃，dispatch 不会收到） |
| `dispatchEvent > speak → chatStore` | 功能正确 | `kind=speak`/`role=nyx`/`content` 入 `messages` |
| `dispatchEvent > user_message → chatStore` | 回归保护 | 读 `message` 非 `content` → `kind=message`/`role=user`/`content` 入 `messages`（Finding 1：user_message 裸 `{message}` 曾致用户消息被 `typeof e.content` 拦截静默丢弃） |
| `dispatchEvent > emotion_update → innerLifeStore` | 功能正确 | 覆盖 `valence`/`arousal`/`emotion` 三字段 + 顺带 `refreshState()` 重拉全量快照（能量/性格/三观不随帧下发，补自动刷新） |
| `dispatchEvent > desire_generated → desireStore.refresh()` | 功能正确 | `desire_generated` 触发 `desireStore.refresh()` 恰 1 次 |
| `dispatchEvent > activity_start → activityStore.refresh()` | 功能正确 | `activity_start` 触发 `activityStore.refresh()` 恰 1 次 |
| `dispatchEvent > mutter → announceStore（冒气泡，不进 chatStore）` | 功能正确 | `mutter` → `announce("mutter", e.content)` 入 announceStore `{kind:"mutter",text:"在想你"}`，`chatStore` 空（碎碎念改悬浮气泡，与 reading_mutter/reflection_done 同路径） |
| `dispatchEvent > activity_end → refresh 后按 activity_id 找产出 announce` | 功能正确 | `activity_end` → `refresh()` 后按 `activity_id` 找到完成活动，有产出则 `announce("activity", …)`（无产出静默） |
| `dispatchEvent > reflection_done（story_is_new）→ 欲望 refresh + 气泡` | 功能正确 | `story_is_new=true` → `desireStore.refresh()` + `announceStore` 追加气泡「小狐狸我呀，反思了一下：…」 |
| `dispatchEvent > reflection_done（story_is_new=false）→ 静默 refresh 不气泡` | 功能正确 | `story_is_new=false` → 仍 `desireStore.refresh()` 但 `announceStore` 空（静默刷新） |
| `dispatchEvent > reading_question/association → chatStore.addReadingTurn；reading_mutter → announce(mutter)` | 功能正确 | `reading_question`/`reading_association` 各调 `chatStore.addReadingTurn` 1 次（透传原始事件对象）；`reading_mutter` → `announce("mutter", content)` 入 announceStore（读书提问/联想进对话、读书碎碎念归气泡） |
| `isEmotionCategory > 枚举收窄` | 边界鲁棒 | 合法枚举（`happy`/`neutral`）→ true；非法字符串（`不存在`）/非字符串（`5`/`null`）→ false |

## frontend-client（REST 客户端：api/client.ts）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `postChat > POST /api/chat` | 功能正确 | 请求 URL/method、body `{message}`、`Content-Type: application/json`、解析 `{event_id}` |
| `getState > GET /api/state` | 功能正确 | 请求 URL、解析 `CurrentState` 直返 |
| `postObserve > POST /api/observe` | 功能正确 | 请求 URL/method、body `{presence, window_title}`、解析 `{event_id}` |
| `非 2xx 读 body.detail` | 边界鲁棒 | mock body `{"detail":"校验失败"}` → `throw Error`（message 含 detail） |
| `非 2xx 无 detail 兜底` | 边界鲁棒 | mock body 无 detail/error → `JSON.stringify(body)` 非空 message |
| `非 2xx detail 空串兜底` | 边界鲁棒 | mock body `{"detail":""}` → 兜底 `HTTP status`（防空 message 被 UI `if(sendError)` 误判） |
| `fetch 网络错误上抛` | 边界鲁棒 | reject `TypeError` → 上抛不吞（不返回 `{ok:false}`/null） |
| `getDesires > GET /api/desires` | 功能正确 | 请求 URL、解析 `DesireState` 直返 |
| `getActivity > GET /api/activity` | 功能正确 | 请求 URL、解析 `ActivitySnapshot` 直返 |
| `getActivityResults > GET /api/activity/results` | 功能正确 | 请求 URL、解析 `Activity[]` 直返（跨天历史产出） |
| `getEventsLog > limit/event_type/correlation_id 拼进 query` | 功能正确 | 三参拼进 query（`?limit=20&event_type=speak&correlation_id=c1`） |
| `getBooks > GET /api/books` | 功能正确 | 请求 URL、解析 `BookListItem[]` 直返 |
| `getBookParagraphs > from/to 拼进 query` | 功能正确 | 请求 URL `/api/books/b1/paragraphs?from=1&to=50`、解析 `Paragraph[]` |
| `getProgress > GET /api/progress/{id}` | 功能正确 | 请求 URL `/api/progress/b1`、解析 `Progress`（四键 snake_case） |
| `putProgress > PUT + body 三键` | 功能正确 | `PUT /api/progress/b1`、body `{user_position, nyx_position, reading_speed}`、`Content-Type: application/json` |
| `importBook > POST /api/books FormData` | 功能正确 | `POST /api/books`、`init.body` 为 `FormData` 且 `.get("file")===file`、`init.headers` undefined（不设 json 头，浏览器带 boundary） |
| `evaluateImpulse > POST /api/impulse/evaluate` | 功能正确 | body 三键 snake_case `{book_id, paragraph_index, last_paragraph_index}`、解析 `{triggered}` |
| `checkChapterBoundary > POST /api/notes/check-chapter-boundary` | 功能正确 | body `{book_id, nyx_position}`、解析 `{is_boundary, book_finished}` |
| `getNotes > GET /api/notes/{bookId}` | 功能正确 | 请求 URL `/api/notes/b1`、解析 `UserNoteWithAnnotations[]` 直返 |
| `createUserNote > POST /api/notes/user` | 功能正确 | body 四键 snake_case `{book_id, paragraph_id, content, selected_text}` + `Content-Type: application/json`、解析裸 `UserNote` |
| `updateUserNote > PUT /api/notes/user/{id}` | 功能正确 | 请求 URL `/api/notes/user/n1`、method PUT、body `{content}`、解析 `UserNote` |
| `deleteUserNote > DELETE /api/notes/user/{id}` | 功能正确 | `DELETE` 204 无 body：`json()` 不调（抛「不该调 json」守卫断言）→ resolves undefined |
| `showNoteToNyx > POST /api/notes/{noteId}/show-to-nyx` | 功能正确 | 请求 URL `/api/notes/n1/show-to-nyx`、method POST、解析 `Annotation` 直返 |
| `showNoteToNyx > LLM 空回 null` | 边界鲁棒 | 响应 `null` → 返回 `null`（不抛、不反噬，与 store 的 `ann===null` 分支对齐） |
| `createUserNote > 422 读 body.detail 上抛` | 边界鲁棒 | 422 body `{"detail":"content 不能为空"}` → reject（message 含 detail） |

## frontend-stores（Zustand stores：chatStore + innerLifeStore + desire/activity 快照 + settingsStore + readerStore）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `chatStore.add* > 5 个 action 转 ChatMessage` | 功能正确 | `addSpeak`/`addAsk`/`addThink`/`addInitiateChat`/`addUserMessage` 各断言 role/kind/content/correlation_id 且 append |
| `chatStore.addReadingTurn > reading_question` | 功能正确 | `kind=reading_question` + `subtype`/`selectedText` 落字段 + `correlation_id=book_id`（读书 turn 用 `book_id` 当 correlation_id） |
| `chatStore.addReadingTurn > reading_association` | 功能正确 | `kind=reading_association` + `memoryId` + `content=snippet` |
| `chatStore.addReadingTurn > content 非 string 丢弃` | 边界鲁棒 | question 帧 `content` 非 string → 不进 `messages`（复用 append 收窄校验） |
| `chatStore.addInitiateChat > unreadProactive=true + clearUnreadProactive 复位` | 功能正确 | 搭话入消息时置 `unreadProactive=true`（头像红点未读）；`clearUnreadProactive()` 复位 false |
| `chatStore.reset > 复位 unreadProactive` | 功能正确 | 搭话置 true 后 `reset()` → `unreadProactive` 回 false（新会话清未读） |
| `chatStore.sendMessage > 成功` | 功能正确 | mock fetch 断言 `POST /api/chat`、置 `isReplying=true` + `sendError=null` |
| `chatStore.sendMessage > 失败` | 功能正确 | postChat throw → `sendError=e.message`、`isReplying` 复位 false |
| `chatStore.sendMessage > 重入守卫` | 回归保护 | in-flight（第一次 `sendMessage` 同步置 `isReplying=true` 后挂起）时第二次 `sendMessage` 被 `get().isReplying` 同步守卫拦下，fetch 只调 1 次（串行锁提前到 await 前，防双击并发发送覆盖 pendingId） |
| `chatStore > 60s 超时` | 功能正确 | fake timers：成功后 `advanceTimersByTime(60_000)` → `sendError="回复超时"` + `isReplying=false` |
| `chatStore > addSpeak 取消超时` | 功能正确 | 成功后 `addSpeak` 再 advance 60s → 不触发超时（`clearTimeout` 取消 timer + `isReplying` 复位） |
| `innerLifeStore.refreshState > current 被设置` | 功能正确 | mock fetch 断言 `GET /api/state`、`current` 被设置、`error` 清空 |
| `innerLifeStore.refreshState > 失败` | 功能正确 | getState throw → `error=e.message` |
| `innerLifeStore.updateEmotion > 三字段覆盖` | 功能正确 | 覆盖 `valence`/`arousal`/`emotion`，`personality`/`energy` 不变 |
| `innerLifeStore.updateEmotion > null 安全` | 边界鲁棒 | `current=null` 时不崩（忽略） |
| `chatStore > 迟到回复清 sendError` | 功能正确 | 超时后（`sendError="回复超时"`）`addSpeak` 到达 → `sendError=null`（回复清超时残留） |
| `chatStore > 非匹配 correlation 不清 timer` | 回归保护 | `addSpeak` 的 `correlation_id` ≠ `pendingId` → isReplying 保持 true、消息照常上屏、advance 60s 仍触发超时（防并发误清） |
| `chatStore.reset > 新会话全清` | 功能正确 | `reset()` 复位 `messages/isReplying/sendError/typedIds` + 取消残留 timer（advance 60s 不触发超时） |
| `chatStore.loadHistory > 升序前置 + preloaded + typedIds` | 功能正确 | 七类历史事件合并按 `timestamp` 升序前置、每条 `preloaded=true`、历史 think 入 `typedIds` |
| `chatStore.loadHistory > 已存在 id 去重` | 边界鲁棒 | 与现有 `messages` 撞 id 的历史消息不重复前置（`s1` 仅 1 条） |
| `chatStore.loadHistory > getEventsLog 失败` | 边界鲁棒 | `getEventsLog` reject → best-effort 不抛、`messages` 不变 |
| `chatStore.loadHistory > reading_question/association 历史回填` | 功能正确 | question 读 `content.content`、association 读 `content.snippet` + 回填 `subtype`/`selectedText`/`memoryId` |
| `chatStore > markTyped + reset 清 typedIds` | 功能正确 | `markTyped("x")` 写入 `typedIds["x"]`；`reset()` 清空 `typedIds={}` |
| `desireStore.refresh > GET /api/desires` | 功能正确 | mock fetch 断言端点 + `data` 落 store |
| `activityStore.refresh > 并行 getActivity+getActivityResults` | 功能正确 | `fetch` 恰 2 次（`/api/activity` + `/api/activity/results`）→ `data`/`results` 双字段落 store |
| `desireStore.refresh > 失败 → error` | 边界鲁棒 | `getDesires` reject → `error=e.message` + `data` 保持 null |
| `isReady > think 打完才放行 speak` | 功能正确 | think 未打完 → false；`typedIds` 含该 think → true；无前置 think → true（串行逐字门控核心） |
| `isReady > preloaded / user 恒就绪` | 功能正确 | `preloaded` nyx 文本、user 消息 → true（历史不逐字 / 用户消息不被门控） |
| `isReady > 不同 correlation_id 不阻塞` | 功能正确 | 不同 `correlation_id` 的 nyx 文本不阻塞 speak → true |
| `settingsStore > setTint/setImage 独立落 store` | 功能正确 | `setTint`/`setImage` 各落 `tint`/`image` 字段，可并存 |
| `settingsStore > reset 恢复默认` | 功能正确 | `reset()` 后 `tint`/`image` 均回 null |
| `isReady > think 也受串行门控` | 功能正确 | think2 在 speak1 之后、speak1 未入 `typedIds` → false；speak1 入 → true（每条 nyx 文本等前一条同 correlation_id 打完） |

## frontend-reader-store（阅读 store：readerStore 书架/进度/追赶循环）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `nyxStatusOf > idle/reading/waiting 三态` | 功能正确 | 未开书 → idle；nyx<user → reading；nyx>=user → waiting（含 nyx>user 不超车） |
| `computeWindow > 书首/书尾 clamp 到 [1, total]` | 边界鲁棒 | 书首 `{from:1,to:50}`；书尾非居中 `{from:120,to:120}`；居中 `{from:95,to:120}` 不越界 |
| `catchupDurationMs > clamp 到 [1s, 30s]` | 功能正确 | 100/50→2000；1/50→1000（下限）；10000/50→30000（上限） |
| `catchupDurationMs > 非法 speed 回退最小速度` | 边界鲁棒 | speed≤0 时回退 `MIN_READING_SPEED`(10 字/秒) 而非复用 `MIN_CATCHUP_SEC`（秒）：100/10→10000ms，不再 100/1 顶格 30s |
| `paginate > 长段独占一页、短段一页多段、溢出封页` | 功能正确 | 每段 `H+GAP_PX`(62)：viewport 62 → 每页一段 `[[1],[2],[3],[4]]`；124 恰好 `[[1,2],[3,4]]`；125/130 第三段 62 溢出 → 仍 `[[1,2],[3,4]]`（贪心封页） |
| `paginate > 空 paragraphs / viewportHeight<=0 返回 []` | 边界鲁棒 | `[]`、`viewportHeight=0`、`-1` 均返回 `[]`（不崩） |
| `paginate > measureHeight 含 GAP_PX 后页界正确` | 功能正确 | 两段各 62：`62+62=124 > 110` → `[[1],[2]]`；若不含间距 50+50=100≤110 会误挤同一页（间距计入分页） |
| `loadBooks > GET /api/books → books 落 store` | 功能正确 | `loadBooks()` → `books` 落 fixture、`booksError=null` |
| `loadBooks > getBooks throw → booksError` | 边界鲁棒 | reject → `booksError="fetch failed"`、`books=[]` |
| `openBook > 会话态 + totalParagraphs 从 books 取 + 追赶` | 功能正确 | `totalParagraphs=120`（从 books 列表项取，非 progress）、位置/速度/读次落 store、窗口 `from=3&to=52`、`vi.getTimerCount()>0`（起追赶） |
| `openBook > 书尾窗口 clamp 不越界` | 边界鲁棒 | `user_position=total` → 窗口 `from=120&to=120` 不越界 |
| `syncPosition 前翻跨越 N 段 > 逐段补发 evaluateImpulse` | 功能正确 | 从 `userPosition=3` `syncPosition(6)` → `userPosition=6`；`PUT /api/progress/b1` body 三键 1 次；`POST /api/impulse/evaluate` 3 次 body `{book_id:"b1", paragraph_index:4/5/6, last_paragraph_index:3/4/5}`（逐段保住每段都有机会触发） |
| `syncPosition 后翻 > 只 putProgress 不评估` | 功能正确 | `syncPosition(1)`（回翻）→ `userPosition=1`；fetch 仅 1 次 `/api/progress/b1`（回翻不触发冲动评估） |
| `syncPosition 到同段 > no-op` | 边界鲁棒 | `syncPosition(3)`（== 当前 `userPosition`）→ 不 `putProgress`、不 `evaluateImpulse`（fetch 0 次） |
| `reread > putProgress 复位 + 位置归 1` | 功能正确 | `userPosition=nyxPosition=1`、`PUT /api/progress/b1` body `{user_position:1, nyx_position:1}`（read_count 不碰） |
| `追赶循环 > 按段长推进到 userPosition 停止` | 功能正确 | fake timers 按 `catchupDurationMs(100,50)` 逐段推进 nyxPosition 1→2→3，到 userPosition 后 `getTimerCount()=0`（停止） |
| `advanceNyx 追到 userPosition > 收尾落库` | 回归保护 | 追到 userPosition 时除 `checkChapterBoundary` 外还 `PUT /api/progress/b1`（body `{user_position:120, nyx_position:120, reading_speed:50}`）——防重载后读到陈旧落后值重追、重放 BOOK_FINISHED（22 幂等靠进程内 `_finished_books` 重启即丢） |
| `startCatchup 重入 > 旧 timer 清除不叠加` | 边界鲁棒 | 连续两次 `startCatchup()` → `vi.getTimerCount()=1`（旧 timer 被 clear 不叠加） |
| `stopCatchup > clearTimeout 后 advance 不推进` | 功能正确 | `stopCatchup()` 后 advance → `nyxPosition` 不变、`getTimerCount()=0` |
| `closeBook > 复位 bookId/paragraphs/positions` | 功能正确 | `bookId=null`、`totalParagraphs=0`、`paragraphs=[]`、`userPosition=nyxPosition=1` |
| `addReadingBubble > 非当前书事件丢弃` | 功能正确 | `book_id !== bookId` → `impulseBubbles` 不变（书架切换后旧书气泡不串场） |
| `addReadingBubble > kind 映射 + 字段各落对` | 功能正确 | mutter→`{kind:"mutter",content}`；question→`{kind:"question",subtype,selectedText}`；association→`{kind:"association",content:snippet,memoryId}` |
| `addReadingBubble > cap 到 20 溢出丢最旧` | 边界鲁棒 | 25 条 → 只留 20 条，最旧 5 条被丢（`bubbles[0].id==="e6"`、`bubbles[19].id==="e25"`） |
| `loadNotes > GET /api/notes/{bookId}` | 功能正确 | `notes` 落 fixture（含 `annotations`）、`notesError=null` |
| `addNote > POST 返回裸 UserNote → unshift + 归一` | 功能正确 | 裸 7 键 `UserNote` 归一成 `{...note, annotations:[]}` 后 unshift 到 `notes[0]` |
| `updateNote > 覆盖 7 键、保留 annotations` | 功能正确 | content/updated_at 更新、原 `annotations` 数组保留（后端不回批注，不整表重拉） |
| `deleteNote > 本地移除该条` | 功能正确 | DELETE 204 后 `notes` filter 掉该条（连同其批注） |
| `showToNyx > 成功后 append Annotation（不整表重拉）` | 功能正确 | fetch 恰 1 次（非重拉）、`annotations` append 完整 `Annotation` |
| `showToNyx > LLM 空回 null → 不 append` | 边界鲁棒 | `showNoteToNyx` 返回 `null` → `annotations` 不变 |

## frontend-reading-panel（阅读面板：ReaderView 位置高亮 + ReaderSidebar 气泡 + NotePanel 笔记）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `ReaderView > 当前段 --current、Nyx 段 --nyx、其余无` | 功能正确 | `userPosition=3`、`nyxPosition=5` → 第 3 段 className 含 `reader-text__para--current` 不含 `--nyx`；第 5 段含 `--nyx` 不含 `--current`；第 2 段两者皆无 |
| `ReaderSidebar > 渲染三态气泡（kind 决定样式类）` | 功能正确 | 三态气泡文案上屏 + class 恰为 `reader-bubble reader-bubble--mutter/question/association` |
| `ReaderSidebar > 点「笔记」打开 NotePanel（挂载即 loadNotes）` | 功能正确 | 点「笔记」→ `role=dialog`（name 笔记）出现、`loadNotes` 恰 1 次 |
| `NotePanel > 渲染笔记（content + selected_text + 批注）` | 功能正确 | content/划线原文/批注三文案均上屏 |
| `NotePanel > composer 提交 → addNote（book_id + content）` | 功能正确 | 输入 `"  新笔记  "` 点「记笔记」→ `addNote({book_id:"b1", content:"新笔记"})`（trim） |
| `NotePanel > 空白 composer 提交禁用` | 边界鲁棒 | 空白时「记笔记」按钮 `disabled` |
| `NotePanel > 「给尼克斯看」/「删除」调用对应 action` | 功能正确 | 两按钮分别触发 `showToNyx("n1")` / `deleteNote("n1")` |
| `NotePanel > 点「编辑」进入编辑态，保存 → updateNote(trim 后) + 退出` | 功能正确 | 点「编辑」→ textarea 预填原文；改 `"  改后  "` 点「保存」→ `updateNote("n1","改后")` + 编辑按钮回归 |
| `NotePanel > 取消 → 退出编辑态、不调 updateNote` | 功能正确 | 点「编辑」再「取消」→ `updateNote` 未调、编辑按钮回归 |
| `NotePanel > 编辑态空白 → 保存禁用` | 边界鲁棒 | 编辑态 textarea 置空白 → 「保存」`disabled` |

## frontend-labels（枚举中文化映射：lib/labels.ts）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `labels > 用户示例 exploration → 发现` | 功能正确 | `DESIRE_TYPE_LABELS.exploration === "发现"`（用户点名的翻译） |
| `labels > 各枚举键均有中文映射` | 功能正确 | 6 个枚举映射表的每个值均非空字符串（无 `undefined` 漏译） |
| `labels > Big Five / 三观双端语义均有 low/high 中文` | 功能正确 | `PERSONALITY_POLES`/`VALUES_POLES` 每个 pole 的 `low`/`high` 均非空字符串 |
| `labels > label() 命中键返回中文，未知键回退原值` | 边界鲁棒 | `label(map, "exploration")` → 中文；`label(map, "unknown_key")` → `"unknown_key"`（未知键回退原值不崩） |

## frontend-typewriter（打字机 hook：useTypewriter）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `useTypewriter > 空文本 → displayed 空 + done 立即 true` | 边界鲁棒 | `useTypewriter("")` → `displayed === ""`、`done === true`（空文本短路，不起 timer） |
| `useTypewriter > 逐字：每 tick 增一字，直至 done` | 功能正确 | fake timers：两次 `advanceTimersByTime(35)` → `"你"`→`"你好"`、`done` false→true |
| `useTypewriter > ready=false 不启动` | 功能正确 | `ready=false` 时 `displayed=""`+`done=false` 且推进 timer 不打字；`rerender({ready:true})` 后才从 0 逐字（串行逐字门控） |

## frontend-chat-panel（聊天面板：MessageList + MessageBubble + ChatInput）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `MessageBubble > speak → 左气泡 class + content 上屏` | 功能正确 | speak 气泡带 `message-bubble--speak` class、content 渲染为 `.message-bubble__content` |
| `MessageBubble > ask → 高亮 class` | 功能正确 | ask 气泡带 `message-bubble--ask` class（高亮样式钩子） |
| `MessageBubble > think → 逐字弱化显示（不再折叠）` | 功能正确 | think 气泡带 `message-bubble--think` class、content 经 `typeDone()` 推进 fake timers 后完整上屏 |
| `MessageBubble > initiate_chat → 带「欲望搭话」标记 + 逐字 content` | 功能正确 | initiate_chat 气泡带「欲望搭话」badge；content 经 `typeDone()` 逐字上屏 |
| `MessageBubble > user message → 右气泡 class` | 功能正确 | 用户消息带 `message-bubble--user` class |
| `MessageBubble > reading_question → 「提问」徽标 + 即时全量 + selectedText 引文行` | 功能正确 | `reading_question` 气泡 content 即时全量（`为什么？` 无需 `typeDone()`）；「提问」带 `message-bubble__badge` class；`selectedText` 渲染 `原文：「划线句」` 且带 `message-bubble__quote` class |
| `MessageBubble > reading_association → 「联想」徽标 + memoryId → 「记忆」标` | 功能正确 | `reading_association` 气泡 content 即时全量（`片段`）；「联想」带 `message-bubble__badge` class；`memoryId` 非空时渲染「记忆」且带 `message-bubble__memory` class |
| `MessageList > 全部消息按序渲染，无历史折叠` | 功能正确 | 两条消息 `typeDone()` 后都上屏（微信式：不再只显示一条 / 折叠历史）；`queryByRole("button")` 无历史按钮 |
| `MessageList > 全部气泡渲染即存在（串行门控只延迟内容，不延迟挂载）` | 功能正确 | 两条 nyx 消息渲染即 `.message-bubble` 数量 = 2（打字中 content 渐显但气泡已挂载，串行门控只延迟内容） |
| `MessageList > 串行逐字：内心话先打完、对话才开打` | 功能正确 | think 在前、speak 在后：未推进 timer 两者皆空（think 刚开打、speak 等前置打完）；`typeDone()` 后 think→speak 串行完整上屏 |
| `ChatInput > 点发送 → sendMessage(trimmed) 且成功清空` | 功能正确 | 点发送按钮触发 `sendMessage` 且传入 trim 后文本；成功（返回 true）后 `waitFor` 断言输入框清空 |
| `ChatInput > 回车 → sendMessage 且成功清空` | 功能正确 | 输入框 Enter 触发 `sendMessage`；成功后输入框清空 |
| `ChatInput > 输入法组合态回车不触发` | 回归保护 | `isComposing=true` 的 Enter（拼音选字）不触发 `sendMessage`（防 IME 误发送） |
| `ChatInput > 发送失败保留文本` | 功能正确 | `sendMessage` 返回 false（postChat 失败）→ 输入框保留原文可重试 |
| `ChatInput > 成功清空不误删预打文本` | 回归保护 | 发送在途时用户把输入改成别的 → 成功后仅清「仍是原文」的框，预打文本保留（函数式更新比对 trimmed） |
| `ChatInput > isReplying=true → 禁用 + 回车不触发` | 功能正确 | isReplying 时发送按钮 `disabled` + 文案「…」；填非空值后回车仍不触发 sendMessage（只有 isReplying 守卫能拦） |
| `ChatInput > sendError 非 null → 红字显示` | 功能正确 | `sendError` 非空时渲染 `.chat-input__error` 红字 |

## frontend-inner-state-panel（内在状态面板：InnerStatePanel + ValenceArousalPlot + EmotionSprite + EnergyBar + BigFiveChart + ValuesChart）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `EnergyBar > 按 energy_state 渲染中文文案` | 功能正确 | `energy_state="tired"` 渲染中文 `疲惫`（经 `ENERGY_LABELS`，不再显原值） |
| `EmotionSprite > 按 emotion 选图文件名` | 功能正确 | `emotion="happy"` 时 `<img alt="happy">` 的 `src` 含 `happy`（1:1 文件名映射，无 switch） |
| `BigFiveChart > 按 personality 渲染双端语义` | 功能正确 | 渲染 `保守`/`开放`（openness 两端）+ `情绪稳定`/`敏感`（neuroticism 两端），不做数值断言 |
| `ValuesChart > 按 values 渲染双端语义` | 功能正确 | 渲染 `疏离`/`亲近`（attitude_to_human 两端）+ `悲观`/`乐观`（optimism 两端），不做数值断言 |
| `ValenceArousalPlot > 渲染不崩` | 功能正确 | SVG `.va-plot` 存在（坐标/像素不做断言，README §6） |
| `ValenceArousalPlot > 区域标签对齐后端 6 档` | 功能正确 | 渲染 `开心`/`生气`/`担忧`/`悲伤`/`害羞`/`平静`（经 `EMOTION_LABELS`），旧错误标签 `低落` 不在（对齐 `vad_to_category`） |
| `InnerStatePanel > current=null → 整体占位不崩` | 边界鲁棒 | `current=null` 显示「等待核心服务连接…」且不渲染子组件（`开放` 不在） |
| `InnerStatePanel > current 非 null → 渲染子组件字段` | 功能正确 | `精力充沛`/`开放`/`亲近` 分别经 EnergyBar/BigFiveChart/ValuesChart 上屏 |
| `InnerStatePanel > error 非 null → 红字一行` | 功能正确 | `error` 非空渲染 `.inner-state-panel__error` 红字 |

## frontend-presence（活跃度上报：usePresence + classifyPresence）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `classifyPresence > 键盘/鼠标任一活跃 → online` | 功能正确 | `(true,false)`/`(false,true)`/`(true,true)` 均 `online`；`(true,true,"编辑器")` 仍 `online`（活跃优先于窗口标题） |
| `classifyPresence > 无输入+标题 → busy；全无 → away` | 功能正确 | `(false,false,"编辑器")` → `busy`；`(false,false,"")` → `away`（镜像后端 14-activity observe.py 规则） |
| `usePresence > 首次挂载必报一次（away）` | 功能正确 | 挂载即 `postObserve("away", "")` 恰 1 次（首采样必报，window_title 采 `document.title`，jsdom 默认 `""`） |
| `usePresence > 键盘活动 → 下次采样报 online` | 功能正确 | `keyDown` 后 30s 采样点 `postObserve("online", "")`（活动 20s 前，< 30s 活跃窗口） |
| `usePresence > 鼠标活动 → 下次采样报 online` | 功能正确 | `mouseMove` 后 30s 采样点 `postObserve("online", "")` |
| `usePresence > presence 不变 → 不再上报` | 边界鲁棒 | 无输入 30s 后 `postObserve` 仍 1 次（仅挂载那次 away，不重复上报） |

## frontend-avatar（头像立绘：Avatar 戳立绘 + 红点通知 + 昼夜节律）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `Avatar > isNight 昼夜节律纯函数` | 功能正确 | `isNight(22/0/5)` → true（夜间困倦）；`isNight(6/12/21)` → false（白天回落当前情绪） |
| `Avatar > unreadProactive=true 显示徽标，点击清除` | 功能正确 | `unreadProactive=true` 渲染「小狐狸我有话对你说」徽标；点击 → `clearUnreadProactive` 置 false |
| `Avatar > 戳立绘：戳一下害羞、连戳 5 次生气` | 功能正确 | 点击头像 → `announce("mutter", "呀！")`；连戳第 5 次 → `announce("mutter", "不要再戳了啦！")` |

## frontend-interactivity（交互性：常驻状态条 + 头像旁气泡 + 活动产出）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `activitySubject > 取第一个非空字符串（filename/description/source）` | 功能正确 | progress 里 `filename`/`description`/`source` 任一非空字符串即返回 |
| `activitySubject > 空 progress / 非字符串 / 空串 → null` | 边界鲁棒 | `progress={}`、`description=5`、`filename=""` 均返回 null |
| `formatResult > reading → {book} — {note}` | 功能正确 | `reading` 活动 result 拼 `《小王子》 — 关于驯服` |
| `formatResult > creation → {title} — {content}` | 功能正确 | `creation` 活动 result 拼 `诗 — 正文` |
| `formatResult > free_exploration → summary 与 core_discovery 用 — 连接` | 功能正确 | `summary`/`core_discovery` 拼接 join ` — ` |
| `formatResult > free_exploration → 无 core_discovery 只留 summary` | 功能正确 | 只有 `summary` 无 `core_discovery` 时只返回 summary |
| `formatResult > 未完成 / 无 result / 非 result 类型 → null` | 边界鲁棒 | `status="running"`、无 result、`type="rest"` 均返回 null |
| `formatOutputBody > reading → note` | 功能正确 | reading 产出正文取 `result.note`（与 formatResult 单行摘要互补，正文多行） |
| `formatOutputBody > creation → content` | 功能正确 | creation 产出正文取 `result.content` |
| `formatOutputBody > free_exploration → core_discovery + knowledge 逐条` | 功能正确 | `core_discovery` 加「核心发现：」前缀 + `knowledge` 逐条拼 `【topic】content`，join `\n` |
| `formatOutputBody > free_exploration → 无 knowledge 只留 summary` | 功能正确 | 无 `core_discovery`/`knowledge` 时回退只返回 `summary` |
| `formatOutputBody > 未完成 / 无对应字段 / 非 result 类型 → null` | 边界鲁棒 | `status="running"`、`result={}`（无 note/content）、`type="rest"` 均返回 null |
| `formatTools > 多工具链 → 箭头拼接` | 功能正确 | `[{name:"web_search",args:{query:"骑士团"}},{name:"web_fetch",args:{url:"…"}}]` → `联网搜索「骑士团」 → 抓取网页 …`（`→` 连接） |
| `formatTools > 失败工具 → 标注（失败）` | 功能正确 | `ok:false` 的工具项标注 `（失败）` |
| `formatTools > 空/无 tools → null` | 边界鲁棒 | `tools: []` / 无 `tools` 字段 → 返回 null |
| `activityAnnouncement > reading → 读完啦：…` | 功能正确 | reading 产出前缀 `读完啦：` + formatResult |
| `activityAnnouncement > creation → 创作完成：…` | 功能正确 | creation 产出前缀 `创作完成：` |
| `activityAnnouncement > free_exploration → 探索收获：…` | 功能正确 | free_exploration 产出前缀 `探索收获：` + formatResult（读 `summary`） |
| `activityAnnouncement > 无产出 / 未完成 → null` | 边界鲁棒 | 无 result、未完成活动均 null |
| `announceStore > announce 追加临时气泡（kind/text 落 store、id 唯一）` | 功能正确 | `announce("mutter", …)` append `{kind,text}` 且两次 id 不同 |
| `announceStore > dismiss 摘除指定 id，其余保留` | 功能正确 | `dismiss(id)` 后仅该 id 消失、其余保留 |
| `announceStore > 到时自动 dismiss（按 kind 时长）` | 功能正确 | fake timers 推进 `ANNOUNCE_DURATION[kind]` 后 item 消失 |
| `dispatch > activity_end → refresh 后按 activity_id 找到产出并 announce` | 功能正确 | `activity_end` 触发 `refresh()` 后，从 `data.schedule` 按 `activity_id` 找 completed 活动，`activityAnnouncement` 产出以 `kind="activity"` 进 announceStore |

## frontend-desires-panel（欲望面板：DesiresPanel 短期欲望过滤终态）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `DesiresPanel > 渲染活队列（pending/active/suppressed），过滤 expired/satisfied` | 功能正确 | 短期欲望里 pending/active/suppressed 三条描述上屏，satisfied/expired 两条描述不上屏 |
| `DesiresPanel > 短期欲望全是终态 → 不渲染「短期欲望」空区块` | 边界鲁棒 | 短期欲望全为 satisfied/expired → `liveShortTerm.length===0`，「短期欲望」区块整体不渲染 |

## frontend-scroll-area（书卷区域：ScrollArea 对话主舞台）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `ScrollArea > 渲染对话主舞台：消息列表，无模式切换按钮` | 功能正确 | 渲染 `.message-list` 容器（空消息也渲染）；「记忆/笔记/对话」按钮已移除（`queryByText` 均 null） |

## frontend-settings-view（游戏设置页内面板：SettingsView 字体大小 + 背景外观）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `SettingsView > 渲染标题 / 字体大小三档 / 背景外观` | 功能正确 | 「游戏设置」标题、「字体大小」「背景」两个面板标题（heading）、「小/中/大」三按钮均上屏 |
| `SettingsView > 默认「中」激活，点「大」写 settingsStore.fontScale` | 功能正确 | 默认「中」`aria-pressed=true`；点「大」→ `fontScale==="large"` 且「大」`aria-pressed=true` |
| `SettingsView > 点预设色块「樱粉」写 settingsStore.tint` | 功能正确 | 点「樱粉」色块（aria-label）→ `settingsStore.tint === "#f7e8e0"` |
