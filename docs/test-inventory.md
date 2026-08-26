# 测试清单（test-inventory）

> 每次编写测试后追加。记录：新增测试 / 检查方向 / 所属系统 / 功能阶段。

## 01-types（枚举 + 实体类型）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_all_enums_exhaustive` | 功能正确 | 13 个枚举的值集合与 `EXPECTED` 逐枚举相等（防漏成员/多成员/改值） |
| `test_naming_convention` | 回归保护 | 每个成员 `value == name.lower()`（防手滑改值破坏 snake_case 契约） |
| `test_strenum_json_serializable` | 功能正确 | `json.dumps(EventType.USER_MESSAGE) == '"user_message"'` |
| `test_short_term_desire_default_status` | 功能正确 | `status` 默认 `DesireStatus.PENDING`（枚举成员而非裸字符串） |
| `test_memory_aspect_default_factory_isolated` | 边界鲁棒 | `default_factory` 保证两个实例的 `aspect` 互不共享 |
| `test_long_term_desire_linked_values_default_factory_isolated` | 边界鲁棒 | `default_factory` 保证两个实例的 `linked_values` 互不共享 |
| `test_typed_dict_keys` | 功能正确 | 4 个 TypedDict 键集合经 `get_type_hints` 完整 |

**功能阶段**：01-types 实现时编写；`test_all_enums_exhaustive` 的 `EXPECTED` 于「反思优化」轮追加 `reflection_done`（EventType）/`reflection_check`（TickType）两个成员；「探索升级：联网探索 + 探索地图」轮追加 `exploration_step`（EventType）一个成员。

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

**功能阶段**：02-config 实现时编写；`test_validate_config_rejects_invalid` 两例（`ask_timeout=-1`/`chat_ignore_timeout=0`）于「表达交互闭环」轮追加（V2 问句/搭话超时配置校验）；vision 四例（`interval_seconds=0`/`interval_seconds="60"`/`enabled="yes"`/`provider=""`）于「屏幕视觉」轮追加（`vision` 段校验：非正/错类型/空 provider）；`llm.timeout`/`llm.max_retries` 四例（`timeout=-1.0`/`timeout="60"`/`max_retries="2"`/`max_retries=True`）于「核心 8 项评审修复」轮追加（`LlmConfig` 超时/重试配置校验）；`vision.api_key_env=""` 一例于「medium 评审修复」轮追加（`VisionConfig.api_key_env` 空串校验）；`test_validate_config_rejects_invalid` 追加三例（`temperature=-0.1`/`temperature=2.1`/`temperature=True`）+ `test_validate_config_accepts_temperature_bounds` 于「下载资料+优化写作」轮追加（`LlmConfig.temperature` 采样温度 ∈ [0,2] 校验）。

## 03-llm（LLM 统一客户端）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_to_lc_system` / `test_to_lc_user` / `test_to_lc_assistant` | 功能正确 | `_to_lc` 三种 role 映射到 `SystemMessage`/`HumanMessage`/`AIMessage`，`content` 透传 |
| `test_to_lc_invalid_role` | 边界鲁棒 | 非法 role → `ValueError` |
| `test_extract_usage_dict` | 功能正确 | dict 形状 `{input_tokens, output_tokens}` → `{input, output}` |
| `test_extract_usage_pydantic` | 功能正确 | Pydantic `model_dump()` 形状 → 同上 |
| `test_extract_usage_missing` | 边界鲁棒 | `usage_metadata` 缺失 → `{input: 0, output: 0}` |
| `test_extract_usage_none_value` | 边界鲁棒 | 键存在但值为 `None` → `{input: 0, output: 0}`（宽松兜底，不 `int(None)` 裸崩） |
| `test_extract_usage_unknown_shape` | 边界鲁棒 | 未知形状（非 dict、无 `model_dump`）→ `{input: 0, output: 0}` |
| `test_extract_usage_non_int_value` | 边界鲁棒 | 键值为非数字字符串（`input_tokens="abc"`）→ `{input: 0, ...}` 不抛（`_safe_int` 兜底，不 `int("abc")` 裸崩） |
| `test_complete_fields` | 功能正确 | `id`/`module`/`type`/`correlation_id`/`content`/`model` 正确回填进 `LLMOutput` |
| `test_complete_token_usage` | 功能正确 | `token_usage` 从 `usage_metadata` 抽取为 `{input, output}` |
| `test_complete_token_usage_missing` | 边界鲁棒 | `usage_metadata` 缺失 → `{input: 0, output: 0}` |
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

**功能阶段**：03-llm 实现时编写；`test_extract_usage_non_int_value` 于第五轮 review 追加（`_safe_int` 防御非数字 token 值）；`test_complete_tools_passthrough` / `test_complete_tools_off` / `test_complete_tool_calls_parsed` / `test_complete_no_tools_empty` 于「表达侧工具调用（bind_tools）」阶段追加（`complete` 支持 `tools` + `LLMOutput.tool_calls` 解析）；`test_resolve_base_url` / `test_from_config_unknown_provider_rejects`（原 `test_from_config_rejects_other_provider` 改名）/ `test_from_config_known_provider` / `test_from_config_base_url_override` 于「多 provider（OpenAI 兼容映射）」阶段追加（`LlmConfig.base_url` + `resolve_base_url` 映射）；`test_describe_returns_text` / `test_describe_non_text_raises` / `test_from_config_unknown_provider_rejects`（VisionClient）/ `test_from_config_ok`（VisionClient）于「屏幕视觉」轮追加（`VisionClient` OpenAI 兼容多模态：image_url 块 + 非文本 raise + 复用 `resolve_base_url`）；`test_from_config_passes_timeout_and_retries` 于「核心 8 项评审修复」轮追加（`ChatOpenAI` 超时/重试透传）；`test_from_config_requires_key_for_non_ollama` / `test_from_config_reads_api_key`（VisionClient）于「medium 评审修复」轮追加（`VisionConfig.api_key_env` + `VisionClient.from_config` 从 env 读 key，Ollama 免 key 占位）；`test_from_config_passes_temperature` 于「下载资料+优化写作」轮追加（`ChatOpenAI` 采样温度透传，单一全局旋钮让人格声音更一致）。

## 04-db（SQLite 连接 + 建表 + 迁移）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_migrate_creates_all_tables` | 功能正确 | `sqlite_master` 含硬编码 16 张业务表 + `schema_version`，共 17 张 |
| `test_migrate_creates_five_indexes` | 功能正确 | 显式索引（`sql IS NOT NULL`）恰为 `idx_memory_tag` / `idx_memory_type` / `idx_event_log_corr` / `idx_annotation_target` / `idx_memory_content_hash` 五个 |
| `test_migrate_sets_version_to_max` | 功能正确 | `schema_version` 单行 = `_MIGRATIONS` 最高版本 |
| `test_migrate_not_null_alignment` | 边界鲁棒 | 6 列 `notnull=1`（`memory.aspect` / `long_term_desire.linked_values` / `activity.progress` / `event_log.content` / `event_log.correlation_id` / `eval_report.correlation_id`） |
| `test_migrate_nullable_alignment` | 边界鲁棒 | Optional 列 `notnull=0`（`short_term_desire.goal` / `activity.ended_at` / `token_usage.correlation_id` / `memory.embedding` / `memory.content_hash`） |
| `test_migrate_idempotent` | 回归保护 | 连跑两次不报错，表数不变、版本不变 |
| `test_migrate_version_gating` | 功能正确 | `monkeypatch` 追加「下一版本」后只套该版本，版本=下一版本，旧版本不重复建（动态取 max+1，不再硬编码 v3） |
| `test_migrate_atomic_rollback` | 边界鲁棒 | 迁移含非法 SQL → 抛 `aiosqlite.Error`；`ok` 表回滚不存在；版本仍为 0 |
| `test_connect_returns_database` | 功能正确 | 返回 `Database`；文件创建；`journal_mode=wal`；`foreign_keys=1`；`row_factory` 生效（`row["x"]==1`）；`lock` 是 `asyncio.Lock` |
| `test_connect_explicit_path_priority` | 功能正确 | 显式 path 优先建该文件 |
| `test_connect_env_override` | 功能正确 | `path=None` 时 `NYX_DB` 环境变量覆盖默认 |
| `test_default_db_path_constant` | 功能正确 | `DEFAULT_DB_PATH == "nyx.db"` |
| `test_connect_closes_conn_on_migrate_failure` | 边界鲁棒 | 迁移失败 → `connect` 抛异常且连接被 `close`（spy 记录），不泄漏 |

**功能阶段**：04-db 实现时编写；`material` 表（v2 迁移）于「读书分块读」轮追加——`BUSINESS_TABLES` 补 `material`、表数 14→15、版本门控用例由 v2 改 v3（原 v2 被 `material` 占用）；v3 迁移（`material.note_fragments` + `short_term_desire.goal_progress` 两列）于「活动填实」轮追加——`test_migrate_version_gating` 改为动态取 max+1 不再硬编码版本号；v4 迁移（`reading_note` + `annotation` 两表 + `idx_annotation_target`）于「读书/创作借鉴」轮追加——`BUSINESS_TABLES` 补 `reading_note`/`annotation`、表数 15→16/总 17、`test_migrate_creates_three_indexes` 改名 `test_migrate_creates_four_indexes` 并补第 4 个索引；v6 迁移（`memory.content_hash` + `idx_memory_content_hash`）于「记忆去重」轮追加——`test_migrate_creates_four_indexes` 改名 `test_migrate_creates_five_indexes` 并补第 5 个索引、`NULLABLE_COLUMNS` 补 `memory.content_hash`。

## 05-event（事件总线 + 路由）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_routing_keys_are_all_event_types_except_clock_tick` | 功能正确 | `set(ROUTING) == set(EventType) - {CLOCK_TICK}`（20 键） |
| `test_tick_routing_keys_are_all_tick_types` | 功能正确 | `set(TICK_ROUTING) == set(TickType)`（4 键） |
| `test_routing_values_are_known_modules` | 功能正确 | 所有路由值 ⊆ `{expression, inner_life, desire, activity}` |
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

**功能阶段**：05-event 实现时编写；`test_broadcast_drops_oldest_when_sink_full` 为 review 修复阶段追加（SSE 背压丢帧）；`test_persist_exception_propagates` 改写 + `test_persist_failure_requeues_and_retries` 追加于本轮 review（persist 失败队首放回不丢事件）；`test_persist_poison_pill_dead_lettered` / `test_persist_rolls_back_on_failure` / `test_persist_serializes_non_json_types` / `test_put_left_resets_join` 于第三轮 review 追加（毒丸死信 + 回滚 + `default=str` + `put_left` 补齐 join 语义）；`test_persist_rejects_nan` 于第四轮 review 追加（`allow_nan=False` 拒写非法 NaN 字面量）。

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
| `test_build_web_fetch_tool_writes_and_publishes` | 功能正确 | `web_fetch` handler 抓正文 → `file_io` 写 `uploads/{name}.txt` → `bus.publish(USER_MATERIAL)`（`source=INTERNAL`、content 含 `path`/`filename`/`total_chars`）；返回 `{path, filename, total_chars}` |
| `test_build_web_fetch_tool_returns_error_when_empty` | 边界鲁棒 | 正文抓取失败/为空 → 返回 `{"error": ...}`（不写盘不 publish） |

**功能阶段**：06-tools 实现时编写；`test_fetch_url_sync_returns_empty_on_http_error` / `test_build_web_fetch_tool_writes_and_publishes` / `test_build_web_fetch_tool_returns_error_when_empty` 于「下载资料+优化写作」轮追加（`web_fetch` 抓正文写书库 + publish `USER_MATERIAL` 复用 `read_material`）。

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
| `test_strengthen` | 功能正确 | `add`（`recall_count=0, freshness=0.3`）→ `strengthen` → `recall_count==1` 且 `freshness==1.0` |

**功能阶段**：07-memory-store 实现时编写；`test_record_recall_atomic` 于 09 评审修复阶段重写（中3：加一+条件升型原子化进 store 单锁）；`test_update_many` / `test_delete_many` 于第五轮 review 追加（批量写原语，衰减/淘汰 N 次 commit → 2 次）；`test_list_memories_limit` 于「代码评审修复（5 findings）」轮追加（`list_memories` 加 `limit` 截断，防无界拉取）；`test_hash_content_deterministic` / `test_find_by_content_hit_and_miss` / `test_strengthen` 于「记忆去重」轮追加（精确去重：content 哈希纯函数 + 按哈希查重 + 合并强化原语）。

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

**功能阶段**：08-memory-retrieval 实现时编写；`test_rank_by_cosine` 于 09 评审修复阶段新增（跨模块去重：抽 `rank_by_cosine` 供 facade 复用）。`test_search_sources_*` 与 merge_order 的 sources 断言于 V2「per-result 来源标记」轮新增（`search()` 按层标注 `Memory.sources`，翻转 MVP「不带来源」）。

## 15-eval（OOC 评分 + token 记账）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_ooc_score` | 功能正确 | 无命中默认 1.0；黑 1 → 0.5；黑 2 → 0.0；黑 3 → 0.0（封顶）；黑 1 白 1 → 1.0（抵消）；白 2 → 1.0（封顶不越界） |
| `test_evaluate_persists` | 功能正确 | 落库后重开连接 → `list_reports`/`list_token_usage` 仍各 1 条（持久化往返） |
| `test_list_reports_roundtrip` | 功能正确 | 两条 report；`token_usage` JSON 往返 `{input,output}`；`scores == {"ooc": 1.0}` |
| `test_list_token_usage_since` | 功能正确 | `since=最新 created_at` → 1 条；`since=+1` → 0 条（`>=` 边界） |
| `test_is_voice_type` | 功能正确 | `speak`/`initiate_chat`/`think` → True；`tool`/`judge`/`scene_memory` → False |
| `test_build_baseline_len` | 功能正确 | baseline 长度 == `len(NYX_CORPUS)`，逐条嵌入 |
| `test_ooc_embed_score_identical` | 功能正确 | content 与语料同向量 → sim 1.0 越界 clamp 到 1.0 |
| `test_ooc_embed_score_orthogonal` | 功能正确 | 正交向量 → sim 0.0 → 0.0 |
| `test_ooc_embed_score_empty_baseline` | 边界鲁棒 | 空 baseline → 1.0（无语料无信息不惩罚） |
| `test_evaluate_ooc_embed_combine` | 功能正确 | 注入 mock embed + voice 输出 `speak` → `ooc == min(关键词 1.0, embed 0.0) == 0.0` |
| `test_evaluate_ooc_non_voice_skips_embed` | 边界鲁棒 | 非 voice 输出 `scene_memory` → embed 不触发（调用记录空）、`ooc` 仅关键词 `== 1.0` |

**功能阶段**：15-eval 实现时编写（先于 09-facade，因 09 依赖 Evaluator）；`test_is_voice_type` / `test_build_baseline_len` / `test_ooc_embed_score_*` / `test_evaluate_ooc_*` 于 V2「embedding 相似度 OOC（第 2 档）」轮编写（ooc_embed.py 语料 + 两档合并：max 余弦 / 阈值映射、min 合并、voice 门控）。**「砍三层→单层」轮（接 LangSmith 规划）删除**：`test_validate_structure`（format 结构校验）、`test_should_judge` / `test_judge_relevance_*`（LLM-judge）、`test_evaluate_sampled` / `test_evaluate_not_sampled` / `test_evaluate_judge_transport_failure`（抽样 / judge 记账）。

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
| `test_dedup_exact_same_content` | 功能正确 | 同 content 二次 `create_scene_memory` → 库内 1 条、`recall_count==1`、仅 1 个 `memory_created`（精确去重合并强化） |
| `test_dedup_semantic_merge` | 功能正确 | 新记忆与旧记忆 embedding 余弦=1.0 → 合并到旧记忆（`recall_count+1`）、不新增、无 `memory_created` |
| `test_dedup_semantic_below_threshold` | 功能正确 | 余弦 < 0.95 → 正常新建入库（`list_memories` 2 条、发 1 个 `memory_created`） |
| `test_dedup_embed_none_skips_semantic` | 边界鲁棒 | `embed=None` 时语义去重跳过（旧记忆带 embedding 也不比较），仅精确去重生效 |
| `test_search_delegates_to_retrieval` | 功能正确 | `search` 委托 fake `MemoryRetrieval`（返回预设 + 记录 query） |
| `test_list_memories_delegates` | 功能正确 | `list_memories(type=)` 委托真 store 过滤 |
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

**功能阶段**：09-memory-facade 实现时编写；`test_contradiction_parse_failure_no_crash` / `test_eviction_tie_break_oldest_first` / `test_record_recall_concurrent_single_promote` 于 09 评审修复阶段编写（高1：矛盾解析失败不再半提交；中4：淘汰平局按 created_at 升序；中3：并发 record_recall 只升一次）；`test_join_list` / `test_activity_memory_fields_*` / `test_remember_activity_*` 于「活动记忆」实现阶段编写（活动 result 确定性落记忆，含矛盾检测参与）；`test_remember_user_profile_fields` 于「活动填实（画像记忆）」轮追加（`remember_user_profile` 复用入库尾段、type=LONG_TERM/tag=user）；`test_record_no_answer` 于「表达交互闭环」轮追加（`record_no_answer` 确定性落「用户没回答」SHORT_TERM 记忆、复用入库尾段、无 LLM）；`test_remember_knowledge` 于「读书/创作借鉴」轮追加（`remember_knowledge` 确定性落 `tag="knowledge"` 长期记忆、空 content 跳过、无 LLM）；`test_dedup_exact_same_content` / `test_dedup_semantic_merge` / `test_dedup_semantic_below_threshold` / `test_dedup_embed_none_skips_semantic` 于「记忆去重」轮追加（`_persist_memory` 两层去重）；同轮把受影响的现有测试改到去重阈值外——`test_eviction` / `test_eviction_tie_break_oldest_first` / `test_decay_writeback` 改每次写不同 content（避开精确去重）、`test_contradiction_*` / `test_build_edges` / `test_remember_activity_contradiction` 把旧记忆 embedding `[1.0,0.0]` 改 `[0.8,0.6]`（余弦 0.8 ∈ [0.6,0.95)，仍触发矛盾/建边但不触发语义去重）。`test_activity_memory_fields_free_exploration_new_shape` 于「探索 Roguelike 后端」轮追加（free_exploration 新结果形状映射 `summary`/`core_discovery`）；同轮把 `test_activity_memory_fields_exploration` / `test_remember_activity_creation_and_exploration` 两个旧键断言测试的 `notes`/`findings` 换成 `summary`/`core_discovery`。

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

**功能阶段**：10-desire-value 实现时编写（纯函数，无 DB、无 mock、无集成/E2E；编排归 11-desire）。

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
| `test_build_desire_prompt` | 功能正确 | 含类型 `.value` 与种子；`seed=None` → 含「（无）」 |
| `test_pressure_from_observation` | 功能正确 | 互动欲 `value` 0 → `+0.15`；`updated_at` 更新 |
| `test_run_eval_no_peak` | 功能正确 | 四类型都低于 `peak_threshold` → `[]`、无 LLM 调用 |
| `test_run_eval_generates_peak` | 功能正确 | 达峰 → 1 次 LLM（`output_type="desire"`）、`evaluator.evaluate` 1 次、返回 1 个（type/status/strength/description/goal 来自 fixture）、value 重置 0、发布 `desire_generated` |
| `test_run_eval_only_most_urgent` | 功能正确 | 互动 0.95 + 探索 0.92 都达峰 → 只生成互动；探索 `value` 保留 0.92 不重置 |
| `test_run_eval_long_term_pressure` | 功能正确 | 探索长期欲望 → 探索 `value` 额外 `+0.1`（0.5→0.6） |
| `test_run_eval_decay` | 功能正确 | `updated_at` 1 天前 → `value` 衰减 `value_decay × 1`（0.5→0.45） |
| `test_run_eval_suppression_gate` | 功能正确 | 达峰但 `suppression_threshold > value` → 不生成、返回 `[]` |
| `test_run_eval_topic_seed` | 功能正确 | 探索长期 `subtopics=["骑士团", "大学朋友"]` + 记忆命中「骑士团」→ LLM prompt 含「大学朋友」不含「骑士团」（没做过优先） |
| `test_run_eval_llm_invalid_json_skips` | 边界鲁棒 | 非法 JSON → `_parse_desire` 抛 `ValueError` → 返回 `[]`、目标 `value` 不重置、无欲望入队 |
| `test_run_eval_evaluator_error_propagates` | 回归保护 | evaluator 抛 `RuntimeError` → 不被 `except ValueError` 吞、上抛给 supervisor（不掩蔽真 bug） |
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

**功能阶段**：11-desire 实现时编写（LLM 全 mock、DB `:memory:`、事件经真实 `EventBus` + recording handler；无集成/E2E，与 activity/expression 真实编排归 13/14/17）；`test_goal_progress_roundtrip` / `test_satisfy_goal_progress` 于「活动填实（goal 精确计数）」轮追加（`goal_progress` 列读写往返 + `satisfy` 按 count 累计达标才满足）；`test_topic_seed` 改 `test_subtopics_for` + `test_pick_topic_seed`、`test_run_eval_topic_seed` 改查记忆，于「主题种子轮转（没做过/新鲜度最低）」轮追加（`_pick_topic_seed` 查记忆 substring 取种子）；`test_most_relevant_long_term` / `test_satisfy_reinforces_most_relevant_long_term` 于「长期欲望最相关判定」轮追加（`_most_relevant_long_term` 按 `goal.topic` 双向 substring 命中 `subtopics` 者回写，否则第一个 type 匹配）；`test_list_suppressed_filters_and_orders` / `test_mark_active_pending_to_active` / `test_mark_active_guard` / `test_mark_suppressed_active_to_suppressed` / `test_mark_suppressed_guard` / `test_satisfy_releases_active` / `test_run_eval_releases_suppressed` / `test_run_eval_keeps_suppressed` / `test_mark_active_suppressed_delegate` 于「欲望 ACTIVE/SUPPRESSED 状态流转」轮追加（五态流转：消费标 ACTIVE、非满足停车 SUPPRESSED、run_eval 可表达即释放回 PENDING）；`test_subtopics_for_filters_blank` / `test_subtopic_freshness_blank_not_wildcard` 于「medium 评审修复」轮追加（空串子主题通配符：`_subtopics_for` 过滤空白 + `_subtopic_freshness` 空串返回 None）。

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
| `test_energy_crud` | 功能正确 | `value`+`state` 往返（`EnergyState` 枚举）；空表→`None` |
| `test_narrative_crud` | 功能正确 | `story`/`self_view`/`becoming` JSON 往返 + `identity`/`updated_at`；空表→`None` |
| `test_drift_dim` | 功能正确 | `delta=None` 不变；`+0.3`→base+0.3；`+2`→夹 `+0.5`；`9.8+0.5`→夹 10.0；`1.2-0.5`→夹 1.0 |
| `test_drift_personality_and_values` | 功能正确 | 只改 delta 出现的维、其余维不变；结果夹 `[1,10]` |
| `test_build_reflection_prompt` | 功能正确 | 含记忆摘要/性格/三观数值/叙事身份/长期欲望名；空输入含「（无）」 |
| `test_parse_reflection_ok` | 功能正确 | 合法 JSON → 各字段（story/becoming/self_view/personality_delta/long_term_desires） |
| `test_parse_reflection_missing_story` | 边界鲁棒 | 缺 `story`/`becoming` → `ValueError` |
| `test_parse_reflection_bad_types` | 边界鲁棒 | `self_view` 值非 str、漂移值非数值、`long_term_desires` 非数组、顶层非对象 → `ValueError` |
| `test_parse_reflection_defaults` | 边界鲁棒 | 缺省 `self_view`/`personality_delta`/`values_delta`/`long_term_desires` → `{}`/`[]`（不静默吞错类型） |
| `test_parse_reflection_unknown_drift_key` | 边界鲁棒 | 漂移 key 拼错（`openess`）/ 三观 key 拼错（`extroversion`）→ `ValueError`（不静默停滞某维度演化） |
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
| `test_get_state` | 功能正确 | 注入 fake `ActivityFacade.get_current` + `DesireFacade.get_pending` → `CurrentState` 各字段正确（current_activity/active_desires/personality/energy/energy_state） |
| `test_get_state_unseeded` | 边界鲁棒 | 未 seed → `get_state` 抛 `RuntimeError` |
| `test_get_narrative` | 功能正确 | store 有→返回；空→`RuntimeError` |
| `test_reflect_delegation` | 功能正确 | `facade.reflect()` → reflection LLM 调 1 次、correlation 透传 |
| `test_build_reflection_prompt_feeds_story` | 功能正确 | 已写故事/认知内容被喂进反思 prompt（而非只喂条数）+ 含「新的、与之不同」指示 |
| `test_is_duplicate_fragment` | 功能正确 | 片段去重纯函数：strip 后精确相等/高相似度 → True；明显不同/空列表 → False |
| `test_run_dedup_story` | 功能正确 | LLM story 与已有片段重复 → 不追加（`len(story)==1`）；becoming 不同照常追加、慢变量照常回写 |
| `test_run_returns_outcome_new_story` | 功能正确 | story 真新增 → `run` 返回 `ReflectionOutcome(story_is_new=True)`（`story` 字段透传） |
| `test_run_returns_outcome_dedup_story` | 功能正确 | story 与已有片段重复 → `ReflectionOutcome(story_is_new=False)`（返回值结构化，非 `str | None`） |
| `test_reflect_publishes_reflection_done` | 功能正确 | `facade.reflect()` → 发布 `REFLECTION_DONE`（content `{story, story_is_new}`、correlation 透传） |

**功能阶段**：12-inner-life 实现时编写（LLM 全 mock、DB `:memory:`、事件经真实 `EventBus` + recording handler；`ActivityFacade` 用向前引用 stub/fake、真实编排归 13/14/18）；`test_parse_reflection_unknown_drift_key` / `test_parse_reflection_drops_bad_candidate` / `test_run_survives_bad_candidate` 于 12 评审修复阶段编写（坏候选 best-effort 跳过 + 漂移 key 白名单校验），`internal_event`/时间常量抽到 events/event.py 后的共享测试见 05-event；`test_run_survives_invalid_json` 于反思 JSON 解析容错修复阶段追加（非法 JSON 跳过回写、不抛给事件总线，对齐 11-desire run_eval）；`test_build_reflection_prompt_feeds_story` / `test_is_duplicate_fragment` / `test_run_dedup_story` 于「叙事去重」修复追加（story/becoming 重复：prompt 喂旧内容治本 + 回写前相似度去重兜底）；`test_run_returns_outcome_new_story` / `test_run_returns_outcome_dedup_story` / `test_reflect_publishes_reflection_done` 于「反思优化」轮追加（`run` 返回值由 `str | None` 改结构化 `ReflectionOutcome(story, story_is_new)`、`reflect` 发布 `REFLECTION_DONE` 供前端叙事高亮/欲望刷新）。

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

**功能阶段**：13-activity-scheduler 实现时编写（纯函数，无 DB、无 mock、无 async；与 `ActivityFacade` 的编排归 14）。

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
| `test_goal_met` | 功能正确 | goal None → None；`read` → `completed`；`write` → 有 `title`+`content`；`observe` → 有 `presence`；free_exploration → `outcome=="won"`；其余 → False（C3 精确版「一本/一篇/一次」+ C2 探索满足接线） |
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
| `test_upgrade_to_free_exploration` | 功能正确 | 探索欲 + 精力足 + 频率过 → FREE_EXPLORATION |
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
| `test_read_material_reads_real_file` | 功能正确 | 写真实文件（7000 字）→ `read_material(path, filename, total_chars, cid)` 起一条 `READING` 活动，`progress["source"]` 指向源文件、`result.read_chars==6000`/`total_chars==7000`（一块读 6000 字符不超本），事件序 `[ACTIVITY_START, ACTIVITY_END]` |
| `test_reading_completion_aggregates_note` | 功能正确 | 6 字书一块读尽 → 聚合片段产完整笔记落盘：`completed=True`、`note="完整读书笔记"`、`path="workspace/notes/book.txt.md"`、LLM 调 `["reading","note"]`（C1 读完一本 = 一篇笔记） |
| `test_read_material_skips_when_busy` | 边界鲁棒 | 已有 in-flight 活动（`_task` 未 done）时 `read_material` 直接 return 不新建（`list_schedule` 仍 1 条）——并发守卫镜像 `_maybe_start_activity` |
| `test_no_material_rate_limited_falls_back_to_default` | 功能正确 | 探索欲 + 无书可读 + 限速中（`prev` FREE_EXPLORATION 刚做）→ 退回默认活动 `OBSERVE_USER`（绝不编造读书内容） |
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
| `test_list_notes_counts_annotations` | 功能正确 | 一条笔记挂两条批注 → `list_notes` 的 `annotation_count == 2`（LEFT JOIN 子查询算出） |
| `test_delete_cascades_annotations` | 功能正确 | 删笔记 → `list_notes` 空、`list_annotations` 空（同一事务级联删批注） |
| `test_list_annotations_ordered_asc` | 功能正确 | 批注按 `created_at` 升序（`a1` 在 `a2` 前） |
| `test_delete_annotation` | 功能正确 | 删单条批注 `a1` → 剩 `a2`（其余保留） |
| `test_upsert_by_path_insert_then_update` | 功能正确 | 同 path 二次 `upsert_by_path` → 1 条、content 更新、note id 不变（批注仍挂原 id 下） |
| `test_upsert_by_path_distinct_paths_same_filename` | 功能正确 | 不同 path 同名书 → 2 条互不删（path 是去重键，book 仅展示） |
| `test_pick_creation_style` | 功能正确 | 返回值 ∈ `_CREATION_STYLES`（6 风格随机池，纯函数） |
| `test_build_creation_context_full` | 功能正确 | 上下文串含「风格：日记体」/「主题：骑士团」/「知识库参考」+ 知识点正文 /「当前屏幕灵感」（W1/W2/W3 三部分拼装） |
| `test_build_creation_context_empty` | 边界鲁棒 | 无主题/知识/屏幕 → 只剩 `风格：日记体`（空段省略） |
| `test_extract_knowledge_persists_items` | 功能正确 | mock LLM 返回 `{points:[…]}` → `_memory.remember_knowledge` 收到同批 items（2 条） |
| `test_extract_knowledge_best_effort_no_raise` | 边界鲁棒 | mock LLM 抛异常 → 不冒泡（best-effort），`remember_knowledge` 未收到（空列表） |
| `test_extract_knowledge_chunks_long_content` | 功能正确 | 7000 字长正文切成 2 块、每块 `正文` ≤ 6000 字；跨块重复知识点按 content 去重 → `remember_knowledge` 收到 2 条（修复：整本书喂 LLM 绕过分块预算） |
| `test_read_finalizes_and_extracts_on_empty_chunk` | 功能正确 | 读到末尾（文件比注册时短）走 `chunk==""` 分支 → 既聚合笔记也调知识提取（`"note"` 与 `"knowledge"` 均在 LLM 调用里）（修复：完成分支漏调） |
| `test_finalize_reading_replaces_duplicate_note` | 回归保护 | `_finalize_reading` 二次调用同 path → `upsert_by_path` 原地更新（保留 note id → 批注仍挂原 id），`list_reading_notes()` 只剩 1 条（修复：重读同书累积重复笔记、跨路径同名误删） |
| `test_creation_activity_injects_context` | 功能正确 | 创作活动 → user prompt 含「创作参考」/「风格：」/「知识库参考」/「当前屏幕灵感」（W1/W2/W3 走 `_run_llm_activity` 的 `context_label` 通道） |
| `test_build_creation_system` | 功能正确 | `_build_creation_system(canon, state)` 输出含 canon 原文 + 「此刻心境」段（`emotion.value`/valence/arousal/energy/desires）+ 正向创作指令 + JSON 约束（纯函数拼接） |
| `test_creation_activity_injects_canon_system` | 功能正确 | CREATION 分支 → system prompt 含 canon 人格声音 + 情绪底色（`_CapturingLlm` 捕获，非 `_ACTIVITY_SYSTEM` 默认） |
| `test_start_exploration_returns_id` | 功能正确 | 手动触发 `start_exploration("深海鱼")` → 返回 `activity_id`、落一条 `FREE_EXPLORATION`、`progress["description"]=="深海鱼"`（无视欲望/频率门槛，复用 `_execute` 管线） |
| `test_start_exploration_busy_raises` | 边界鲁棒 | 已有 in-flight 活动时 `start_exploration` → `RuntimeError`（锁内 `self._task` 未完成守卫，端点转 409） |
| `test_start_exploration_none_topic_picks_topic` | 功能正确 | `start_exploration(None)` → 调 `Exploration.pick_topic`，返回 `activity_id`、落一条 `FREE_EXPLORATION`、`progress["description"]` 被 pick_topic 结果覆盖（非 None） |
| `test_pick_topic_returns_topic` | 功能正确 | `_FakeLlm` 返回 `{"topic":"深海鱼"}` → `pick_topic("corr-1") == "深海鱼"`、LLM `output_type=="exploration_topic"` |
| `test_pick_topic_non_dict_raises` | 边界鲁棒 | `_FakeLlm` 返回 `[1,2,3]`（非对象）→ `pick_topic` 抛 `ValueError`（fail-fast） |
| `test_pick_topic_fallback` | 边界鲁棒 | `_FakeLlm` 返回 `{"other":"x"}`（无 `topic` 键）→ 兜底 `"有趣的新鲜事"` |
| `test_fill_dead_ends_pads_to_target` | 功能正确 | 槽位不足补死路：1 个真实节点 → 补到 `_NODE_SLOTS=3`，首槽保持真实、后续为死路 |
| `test_enter_cost_by_kind` | 功能正确 | 进节点精力：真实 `_ENTER_NODE_COST` 6.0 / 死路 `_DEAD_END_COST` 4.0 / 安全房 0.0 |
| `test_descent_cost_increases_with_floor` | 功能正确 | 下楼消耗随深度递增：`descent_cost(1) < descent_cost(3)`（基础 + 逐层递增） |
| `test_restore_energy_caps_at_max` | 边界鲁棒 | 安全房回精力 30 封顶 `_MAX_ENERGY`：80→100、40→70 |
| `test_determine_outcome_three_ways` | 功能正确 | run 结局三分：核心发现→won、精力耗尽→exhausted、主动撤退→retreated |
| `test_parse_choice_routes` | 功能正确 | 决策字符串路由：`node:i` 越界→retreat 兜底、safe_room / retreat / visit 正确映射 |
| `test_search_nodes_fills_real_results` | 功能正确 | `_search_nodes` 真实搜索 → 首槽真实节点（`kind==_KIND_REAL`）、浅楼层（floor=1）不标险（`may_encounter` False） |
| `test_search_nodes_deep_floor_marks_encounter` | 边界鲁棒 | 深楼层（floor=3）→ 节点 `may_encounter` True（险节点标险，进后由 facade 触发有根遭遇） |
| `test_start_interrupts_at_first_decision` | 功能正确 | `start` 首次 `ainvoke` 在首决策点 `interrupt` 暂停：`pending` True、`decision.kind=="choose"`、`nodes` 3 槽 |
| `test_resume_choice_visits_node_and_interrupts_again` | 功能正确 | `resume("node:0")` 进一真实节点后精力降 6（100→94）并再次在决策点中断（`pending` True） |
| `test_resume_retreat_finalizes` | 功能正确 | `resume("retreat")` 走 finalize：`pending` False、`result.type=="free_exploration"`、`outcome=="retreated"` |
| `test_finalize_judges_won` | 管道正确 | 终局 LLM 判定：retreat 触发 finalize，LLM 返回 core_discovery 命中 → outcome 覆盖为 won、`core_discovery`/`knowledge` 落进 result（终局 LLM 输出结构落进 result） |
| `test_choose_exploration_retreat_completes` | 管道正确 | `start_exploration` 停在首决策点 → `choose_exploration("retreat")` 触发终局结算 → `result["outcome"]=="retreated"` 且活动 COMPLETED（交互式探索终局结算） |
| `test_exploration_finalize_writes_long_term_and_knowledge` | 管道正确 | `_finalize_exploration_sink`：`strong_new_topics` → `add_long_term`（长期欲望 `name` 回写）、`knowledge` → `remember_knowledge`（`topic` 回写）（探索终局回写接线：强烈新兴趣→长期欲望、知识→长期记忆） |
| `test_pick_choice_returns_valid_action` | 管道正确 | `pick_choice`：mock LLM 返回 `{"choice":"retreat"}` → 返回 `"retreat"`（托管决策 LLM 输出落成合法动作 + 非法动作兜底） |
| `test_assemble_result_includes_strong_new_topics` | 管道正确 | `assemble_result(state)` 的 `strong_new_topics` 透传（C1 接缝：结果形状补上强烈新兴趣，供 `_finalize_exploration_sink` 落长期欲望） |
| `test_safe_room_resets_last_node` | 回归保护 | 进安全房后 `_last_node` 复位 `None`（I1：非 visit 节点不残留险节点，避免重复触发有根遭遇） |
| `test_descend_resets_last_node` | 回归保护 | 下楼后 `_last_node` 复位 `None`（I1：同上） |
| `test_execute_free_exploration_failure_marks_incomplete` | 回归保护 | FREE_EXPLORATION 启动异常 → 并入 fail-fast：活动标 INCOMPLETE + `ended_at` 非空 + `mark_suppressed` 被调 + 异常上抛（I3：启动失败不留 RUNNING 僵尸 + ACTIVE 欲望） |
| `test_choose_exploration_disables_autopilot` | 功能正确 | `choose_exploration` 先以 `(activity_id, False)` 调 `set_exploration_autopilot` 再 resume（I4：手动选 = 接管，消除与托管循环并发 resume） |

**功能阶段**：14-activity 实现时编写（LLM 全 mock、DB `:memory:`、事件经真实 `EventBus` + recording handler；`get_state`/desire/tools 全 fake 注入，无集成/E2E）；`test_execute_failure_marks_incomplete` / `test_exploration_run_web` / `test_maybe_start_skips_when_task_in_flight` 于 14 评审修复阶段编写（高1：执行失败落 INCOMPLETE + 收割异常；中：探索链 `_route` web 可达；高2：并发守卫闭合 TOCTOU）；`test_exploration_plan_non_dict_raises` 于本轮评审修复编写（`_plan_next` 结构校验 fail-fast，配合删除 `recall_memory` 死节点）；`test_read_material_reads_real_file` / `test_read_material_skips_when_busy` 于「喂资料/上传课本」轮追加（上传 → `USER_MATERIAL` → `read_material` 读真实文件产 `{book,note}` + 忙时跳过）；「读书分块读」轮追加 `MaterialStore` 单测（`test_material_store.py` 4 条：最近未读、跳过读完、全读完 None、重传归零）+ `test_desire_reading_reads_latest_material`（探索欲读最近那本、分块推进度）+ `test_no_material_rate_limited_falls_back_to_default`（无书可读限速退回默认，禁编造），并把 `test_read_material_reads_real_file` 断言补上 `read_chars`/`total_chars` 与 `total_chars` 入参。；`test_find_by_topic_*` 于「活动填实（读书按 topic 选料）」轮追加（`MaterialStore.find_by_topic` 按书名子串选未读完的书）。`test_goal_met`（精确版）/ `test_sanitize_filename` / `test_creation_result_has_path` / `test_idle_reflection_result_has_summary` / `test_observe_user_result` / `test_observe_user_result_no_window_title` / `test_reading_completion_aggregates_note` / `test_maybe_start_reading_uses_topic` / `test_interrupt_reading_marks_paused` 同属「活动填实」轮（B3 创作落盘、B2 发呆回带 story、B1 观察 result 带 presence/window_title、C1 读书聚合完整笔记、C2 读书按 topic 选料、C3 `_goal_met` 精确计数、D 读书打断置 PAUSED）；`test_read_material_reads_real_file` 同轮由 6 字改 7000 字（一块读 6000 不读完，适配「读完整本才 completed」的完成判定）；`test_interrupt_non_resumable_abandons`（原 `test_interrupt_running` 改名）+ `test_interrupt_creation_marks_paused` + `test_interrupt_pauses_in_flight_activity`（原 `test_interrupt_abandons_in_flight_activity` 改名）+ `test_resume_paused_creation_reruns` / `test_resume_paused_reading_refreshes_read_chars` / `test_resume_skips_different_block` + `test_get_paused_in_block_*` / `test_get_by_path_*` 于「活动恢复/续做」轮追加（可续活动打断置 PAUSED 保留记录 + 同日程块内恢复同一记录续读/重跑）；`test_execute_marks_active_desire` / `test_execute_failure_marks_suppressed` / `test_execute_no_desire_no_mark` / `test_interrupt_marks_suppressed` 于「欲望 ACTIVE/SUPPRESSED 状态流转」轮追加（活动消费/中断/异常三处接线：消费标 ACTIVE、非满足释放 SUPPRESSED）；`test_observe_user_result_with_screen_summary` / `test_build_observation_summary_*` / `test_sample_once_*` 于「屏幕视觉」轮追加（观察 summary 折入屏幕描述 + `ScreenObserver` 抓屏→视觉→可选摘要，失败 best-effort 返 None）；`test_list_results_filters_and_orders` / `test_get_results_delegates` 于「产出面板」轮追加（跨天历史产出端点：store 过滤 completed+三类按 `ended_at` 倒序、facade 委托）；`test_list_all_*` 于「读书连贯+进度」轮追加（资料面板进度：`MaterialStore.list_all` 全量读物倒序），`test_reading_relays_prior_fragments` 同轮追加（读书滚动摘要接力：续读时把「上次读到哪里 + 已读片段笔记」喂给 LLM，断言 user 消息含已读片段与位置、未读完只发一次 reading 调用）；`test_reading_note_store.py` 4 条（`test_list_notes_counts_annotations` / `test_delete_cascades_annotations` / `test_list_annotations_ordered_asc` / `test_delete_annotation`）与 `test_pick_creation_style` / `test_build_creation_context_full` / `test_build_creation_context_empty` / `test_extract_knowledge_persists_items` / `test_extract_knowledge_best_effort_no_raise` / `test_creation_activity_injects_context` 于「读书/创作借鉴」轮追加（读书笔记 CRUD store + R1 知识点提取 + W1/W2/W3 创作上下文）；`test_extract_knowledge_chunks_long_content` / `test_read_finalizes_and_extracts_on_empty_chunk` / `test_finalize_reading_replaces_duplicate_note` / `test_delete_by_book_cascades_annotations` 于「代码评审修复（5 findings）」轮追加（知识提取分块、完成分支漏调、重读同书去重、同名删书）；`test_upsert_by_path_insert_then_update` / `test_upsert_by_path_distinct_paths_same_filename`（替代 `test_delete_by_book_cascades_annotations`）/ `test_schedule_block_id_aligns_to_grid` 于「核心 8 项评审修复」轮追加（读书笔记 path 去重键 + 日程块网格对齐）；`test_insert_and_list_notes_ordered_desc` 删除、其余 4 测 setup 由 `insert` 改 `upsert_by_path` 于「第二批清理」轮（`ReadingNoteStore.insert` orphan 清理，插入分支覆盖由 `test_upsert_by_path_distinct_paths_same_filename` 承接）；`test_build_creation_system` / `test_creation_activity_injects_canon_system` / `test_search_web_downloads_first_result` / `test_search_web_no_crash_when_download_fails` 于「下载资料+优化写作」轮追加（创作注入 canon 人格声音 + 情绪底色；探索搜索顺手下第一条正文入书库）。`test_should_explore_rate_limited` / `test_should_explore_ok` 于「探索升级：联网探索 + 探索地图」轮改写（`should_explore` 去精力门、频率上限 4h→1h：`rate_limited` 边界鲁棒 / `ok` 功能正确），`test_should_explore_energy_too_low` 同轮删除（精力门已移除，无 energy 入参）；`test_exploration_run_returns_nodes_and_publishes_steps` / `test_search_web_falls_back_to_local` 同轮追加（联网主通道 + 探索节点记录 + EXPLORATION_STEP 发布）；`test_start_exploration_returns_id` / `test_start_exploration_busy_raises` / `test_start_exploration_none_topic_picks_topic` 同轮追加（手动触发 `start_exploration`：返回 id / busy raise `RuntimeError` / `topic=None` 调 `pick_topic` 覆盖 description，复用 `_execute` 管线不复制执行逻辑）；`test_pick_topic_returns_topic` / `test_pick_topic_non_dict_raises` / `test_pick_topic_fallback` 同轮追加（`Exploration.pick_topic` 选题：返回 topic / 非对象 fail-fast / 无 topic 键兜底）。`test_fill_dead_ends_pads_to_target` / `test_enter_cost_by_kind` / `test_descent_cost_increases_with_floor` / `test_restore_energy_caps_at_max` / `test_determine_outcome_three_ways` / `test_parse_choice_routes` 于「探索 Roguelike（逐层地牢）」轮追加（`exploration.py` 顶部逐层地牢纯函数：死路补位 / 进节点精力 / 下楼递增消耗 / 安全房回精力封顶 / run 结局三分 / 决策字符串路由）；`test_search_nodes_fills_real_results` / `test_search_nodes_deep_floor_marks_encounter` 同轮 Task 2 追加（`Exploration._search_nodes` / `_node_from_result` 本层真实搜索 → 节点填充：管道正确 / 深楼层标险边界）；`test_start_interrupts_at_first_decision` / `test_resume_choice_visits_node_and_interrupts_again` / `test_resume_retreat_finalizes` 同轮 Task 3 追加（`Exploration.start`/`resume` 逐层 run 决策循环：interrupt/resume 机制管道正确）；`test_finalize_judges_won` 同轮 Task 4 追加（`Exploration._finalize` 终局 LLM 判定：一次 LLM 产出 summary/core_discovery/knowledge/新话题 + 深度兜底，核心发现命中时 won 覆盖 retreat）；同轮删除旧固定链测试 `test_exploration_run_no_web` / `test_exploration_run_web` / `test_exploration_plan_non_dict_raises` / `test_exploration_run_returns_nodes_and_publishes_steps` / `test_exploration_never_reads_focus_as_file` / `test_search_web_downloads_first_result` / `test_search_web_no_crash_when_download_fails` / `test_search_web_falls_back_to_local`（8 条，测已删的 `run`/`_search_web`）+ 3 个 orphan helper（`_web_state` / `_CrashOnReadTools` / `_EmptyWebTools`）。`test_choose_exploration_retreat_completes` 同轮 Task 5 追加（ActivityFacade 交互式执行改造：`start_exploration` 到首决策点即返 RUNNING、`choose_exploration("retreat")` 续跑图至终局结算 → 活动 COMPLETED）。`test_exploration_finalize_writes_long_term_and_knowledge` 同轮 Task 7 追加（探索终局回写接线：`_finalize_exploration_sink` 把 `strong_new_topics` 写长期欲望 `add_long_term`、`knowledge` 写长期记忆 `remember_knowledge`，接入 `choose_exploration` 终点分支）。`test_pick_choice_returns_valid_action` 同轮 Task 8 追加（`Exploration.pick_choice` 托管决策：mock LLM 返回 `{"choice":"retreat"}` → 合法动作落成 + 非法动作兜底 retreat）。`test_assemble_result_includes_strong_new_topics` / `test_safe_room_resets_last_node` / `test_descend_resets_last_node` / `test_execute_free_exploration_failure_marks_incomplete` / `test_choose_exploration_disables_autopilot` 于「全分支审查修复（C1/C2/I1/I2/I3/I4）」轮追加（C1：`assemble_result` 补 `strong_new_topics`；C2：`test_goal_met` 补 free_exploration 两断言；I1：`_safe_room`/`_descend` 复位 `_last_node`；I3：FREE_EXPLORATION 启动并入 fail-fast；I4：`choose_exploration` 先关托管）。

## 16-expression-prompt（prompt 拼装 + 快慢通道判定）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_build_system_prompt_base` | 功能正确 | `canon in result`（基底透传）；`narrative=None` / `memories` 缺省 → 不含 `[自我认知]` / `[相关记忆]` |
| `test_build_system_prompt_optional_blocks` | 功能正确 | `narrative` 非 None 含 `identity` 与「近期变化」；`memories` 非空含 `m.summary` |
| `test_build_system_prompt_state_fields` | 功能正确 | 状态段含 `valence=0.50` / `arousal=0.40` / `表情=happy` / `精力：80/100（energetic）` / `当前活动：reading` |
| `test_build_system_prompt_personality_values` | 功能正确 | 含 `性格（Big Five` / `三观（` 且数值渲染（`开放性5` / `对人类态度5`） |
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

**功能阶段**：16-expression-prompt 实现时编写（纯函数，无 DB、无 async、无 fake LLM；`CurrentState`/`Memory`/`Message`/`SelfNarrative`/`ShortTermDesire` 全手构，无集成/E2E）；`test_build_system_prompt_ask_guidance` 于「主动提问段按需注入」阶段追加（ask_guidance 注入/省略）；`test_build_system_prompt_tool_outputs` / `test_build_system_prompt_no_tool_outputs` 于「表达侧工具调用（bind_tools）」阶段追加（tool_outputs 段拼装/省略）；`test_backtrack_*` / `test_no_char_overlap` 于「语义相关性回溯检测」阶段追加（`build_backtrack_context` 三停条件纯函数 + `_no_char_overlap` 零字符重叠保守判定）；`test_emotion_words_no_single_char_false_positive` 于「medium 评审修复」轮追加（`_EMOTION_WORDS` 去单字「累」「烦」改「疲惫」「烦躁」，防子串误判）。

## 17-expression（回复流程 + 碎碎念 + 搭话）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_templates_four_categories_nonempty_and_unique` | 功能正确 | `set(_MUTTER_TEMPLATES) == set(MutterCategory)`；每类 `len == 10` 且 `len(set(...)) == 10`（无重复） |
| `test_pick_mutter_category_out_of_range` | 边界鲁棒 | `roll<0` / `roll>=1.0` → `None`（不触发） |
| `test_pick_mutter_category_maps_to_four` | 功能正确 | `roll=0.0/0.25/0.5/0.75` → ACTIVITY/MEMORY/DESIRE/USER |
| `test_pick_mutter_template_out_of_range` | 边界鲁棒 | `roll<0` / `roll>=1.0` → `None`（不触发） |
| `test_pick_mutter_template_bounds_and_membership` | 功能正确 | `roll=0.0` → 第 0 条；`roll=0.999` → 最后一条；`roll=0.37` ∈ 该类模板池 |
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
| `test_mutter_skips_when_busy` | 功能正确 | `current_activity` 非 None → 不发 |
| `test_mutter_miss` | 功能正确 | `random.random()` 未命中 → 不发 |
| `test_mutter_activity_fills` | 功能正确 | 命中 + ACTIVITY 类有最近活动 → 发 `mutter`，content 含 `_ACTIVITY_LABEL` 标签（读书/探索/创作） |
| `test_mutter_memory_fills` | 功能正确 | 命中 + MEMORY 类有最近记忆 → 发 `mutter`，content 含 `summary` |
| `test_mutter_desire_fills` | 功能正确 | 命中 + DESIRE 类有 active_desires → 发 `mutter`，content 含 `description` |
| `test_mutter_user_fills` | 功能正确 | 命中 + USER 类有 tag="user" 记忆 → 发 `mutter`，content 含用户画像文本 |
| `test_mutter_no_data_skips` | 功能正确 | 命中但该类数据源空 → 不发 |
| `test_initiate_chat_empty` | 边界鲁棒 | 空 content → `False` 且不发 |
| `test_initiate_chat_non_empty` | 功能正确 | 非空 → `True` 且发 `initiate_chat`（output_type/correlation 一致）、system prompt 含 `[主动提问指导]` |
| `test_initiate_chat_appends_history` | 功能正确 | 非空发话后 facade 内部 history 含一条 `role="nyx"`、content 为开场白的消息（搭话落历史，后续回复可回溯） |
| `test_reply_ask_guidance_slow_only` | 功能正确 | 慢通道（精力高+平静）system prompt 含 `[主动提问指导]`；快通道（精力低+激动）不含 |
| `test_reply_question_sets_waiting_user` | 功能正确 | 慢通道问句结尾 → `_waiting_user=True`、`_ask_text`/`_ask_cid` 落值（供 tick 超时收尾） |
| `test_reply_fast_question_sets_ask` | 功能正确 | 快通道问句结尾也置 `ask`/`_waiting_user`（快通道绕过 should_ask，问句无人答信号不丢），publish `[THINK, ASK]` |
| `test_reply_clears_pending_state` | 功能正确 | 用户说话即清 `_waiting_user`/`_ask_cid`/`_pending_chat_desire_id`（不做「是否真在答」判断） |
| `test_initiate_chat_sets_pending_desire` | 功能正确 | 搭话发出 → `_pending_chat_desire_id == desire.id`（超时未回则回灌） |
| `test_check_timeouts_records_no_answer` | 功能正确 | wait_user 超时 → `memory.record_no_answer` 调 1 次、清 `_waiting_user`/`_ask_cid` |
| `test_check_timeouts_before_timeout_noop` | 边界鲁棒 | 未到超时点 → 无动作（wait_user 与待回搭话都保持） |
| `test_check_timeouts_expires_ignored_chat` | 功能正确 | 搭话超时未回 → `desire.expire` 调 1 次（值回灌）、清 `_pending_chat_desire_id` |

**功能阶段**：17-expression 实现时编写（mutter/pipeline 纯函数无 DB 无 async；facade 集成 fake LLM/memory/desire/inner_life/evaluator/bus 注入，`cast()` 注入不碰真实 db；无集成/E2E，与 18-api 组合根的编排归 18）；`test_reply_ask_guidance_slow_only` 于「主动提问段按需注入」阶段追加（慢通道注入 ask 指导、快通道省略），`test_initiate_chat_non_empty` 同轮补注入断言；`test_slow_channel_progressive` 与 `test_initiate_chat_appends_history` 于「慢通道递进续写 + 搭话落历史」阶段追加（三段递进而非并列、主动搭话后用户回复能回溯开场白）；`test_reply_question_sets_waiting_user` / `test_reply_clears_pending_state` / `test_initiate_chat_sets_pending_desire` / `test_check_timeouts_records_no_answer` / `test_check_timeouts_before_timeout_noop` / `test_check_timeouts_expires_ignored_chat` 于「表达交互闭环」轮追加（V2 wait_user 等待 + 搭话被忽略回灌的待回应态与 tick 超时收尾）；`test_reply_slow_tool_executes_and_flows_into_prompt` / `test_reply_slow_no_tool_calls` / `test_reply_slow_tool_failure_fallback` 于「表达侧工具调用（bind_tools）」轮追加（use_tools 节点执行工具 + 结果进 prompt + 失败降级），`test_reply_slow_non_question` / `test_reply_slow_question` 同轮改断言（complete 序列前置 `tool`、complete 数 +1）；`test_record_message_marks_fast` / `test_reply_slow_backtrack_skips_fast_nyx` 于「语义相关性回溯检测」轮追加（快通道 nyx 落库标 fast + 慢通道回溯跳过快通道 nyx），`test_history_order` 同轮简化为只断言 role 序列、`test_current_message_not_duplicated` 同轮改为 seed 相关历史后断言当前消息只进 `[本次消息]`；`test_reply_slow_tool_output_truncated` / `test_reply_slow_records_recall` 于「核心 8 项评审修复」轮追加（工具结果截断 + `record_recall` 接线）；`test_reply_fast_question_sets_ask` 于「medium 评审修复」轮追加（快通道 `speak` 节点检测问句置 `ask`）。

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
| `test_state_endpoint` | 功能正确 | `GET /api/state` → `CurrentState` JSON，枚举字段为 `.value` 字符串（`emotion=neutral`、`energy_state=okay`） |
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
| `test_reading_notes_endpoint` | 功能正确 | `GET /api/reading-notes` → `ReadingNote[]`（含 `annotation_count`） |
| `test_delete_reading_note_endpoint` | 功能正确 | `DELETE /api/reading-notes/{note_id}` → `{deleted}` 且 `fake.deleted_notes` 记到该 id |
| `test_annotations_endpoint` | 功能正确 | `GET /api/annotations?target_id=` → `Annotation[]`（`author` 透传） |
| `test_add_annotation_endpoint` | 功能正确 | `POST /api/annotations` body `{target_id, content}` → 返回新批注 + `fake.added_annotations` 记 `(target_id, content)` |
| `test_delete_annotation_endpoint` | 功能正确 | `DELETE /api/annotations/{annotation_id}` → `{deleted}` 且 `fake.deleted_annotations` 记到该 id |
| `test_check_reflect_skips_within_cooldown` | 边界鲁棒 | `updated_at` 距 now < `_REFLECT_MIN_INTERVAL` → 不触发（`reflect` 不调） |
| `test_check_reflect_skips_below_new_memory_threshold` | 边界鲁棒 | 已过冷却但新记忆 < `_REFLECT_MIN_NEW_MEMORIES` → 不触发（`reflect` 不调） |
| `test_check_reflect_triggers` | 功能正确 | 过冷却 + 新记忆达标 → `reflect` 调 1 次（correlation 透传） |
| `test_explore_endpoint_no_topic` | 功能正确 | `POST /api/explore` 空 body → `{activity_id:"exp-1"}`、`fake.explore_topics == [None]` |
| `test_explore_endpoint_with_topic` | 功能正确 | `POST /api/explore` `{topic:"深海鱼"}` → `{activity_id:"exp-1"}`、`fake.explore_topics == ["深海鱼"]` |
| `test_explore_endpoint_busy_returns_409` | 边界鲁棒 | `start_exploration` raise `RuntimeError` → 端点转 409（已有活动在跑） |
| `test_explore_choose_endpoint` | 功能正确 | `POST /api/explore/choose` body `{activity_id, choice}` → 200、`fake.explore_choices` 记 `(activity_id, choice)`（选择透传 Facade） |
| `test_explore_choose_busy_returns_409` | 边界鲁棒 | `choose_exploration` raise `RuntimeError` → 端点转 409（无进行中的探索） |
| `test_explore_autopilot_endpoint` | 功能正确 | `POST /api/explore/autopilot` body `{activity_id, on}` → 200 `{activity_id, autopilot}`、`fake.autopilot_calls` 记 `(activity_id, on)` |

**功能阶段**：18-api 实现时编写（fake 各 Facade 注入 + 真 `EventBus` + `:memory:`；`cast()` 注入不碰真实 db/LLM；无集成/E2E，与真实编排的边界即「订阅一致性」）；`test_chat_missing_message_returns_422` / `test_observe_invalid_presence_returns_422` 为首轮 review 追加（请求体 422）；`test_supervise_bus_breaks_after_max_failures` / `test_supervise_bus_resets_on_recovery` / `test_first_tick_starts_activity_not_mutter_or_chat` 为本轮 review 追加（监督器熔断 + 恢复重置 + 首个活动块启动即触发）；`test_supervise_bus_breaks_on_flapping` / `test_main_propagates_serve_failure` / `test_main_propagates_tick_failure` 于第三轮 review 追加（恢复信号改连续成功阈值防抖动假自愈 + main 竞速传播所有先完成者）；`test_load_ask_reads_ask_file` / `test_load_ask_missing_file_fails` 于「主动提问段按需注入」阶段追加（`_load_ask` 读 ask.md / 缺失 fail-fast）。`test_observe_endpoint` 于「活动填实（观察填实）」轮改为收可选 `window_title`（`_ObservePayload.window_title`、`OBSERVATION_STATE` content 加 `window_title`、`app.last_window_title` 落组合根）；`test_materials_endpoint_returns_progress` 于「读书连贯+进度」轮追加（`/api/materials` 由纯文件名改为书库进度：`list_materials` 委托 `MaterialStore.list_all`，响应 `{materials: [Material]}`）；`test_reading_notes_endpoint` / `test_delete_reading_note_endpoint` / `test_annotations_endpoint` / `test_add_annotation_endpoint` / `test_delete_annotation_endpoint` 于「读书/创作借鉴」轮追加（读书笔记 5 端点：清单/删除/批注增删查）；`test_memory_search_endpoint` 于「记忆前端搜索/显示优化」轮追加（`/api/memories/search` 委托 `memory.search` 三层检索）；`test_check_reflect_skips_within_cooldown` / `test_check_reflect_skips_below_new_memory_threshold` / `test_check_reflect_triggers` 于「反思优化」轮追加（tick 循环新增 `REFLECTION_CHECK` 类型，`_check_reflect` 三分支：冷却/新记忆门槛/触发）；`test_explore_endpoint_no_topic` / `test_explore_endpoint_with_topic` / `test_explore_endpoint_busy_returns_409` 于「探索升级：联网探索 + 探索地图」轮追加（`POST /api/explore` 手动触发端点：空 body / 带 topic / busy 409）。`test_explore_choose_endpoint` / `test_explore_choose_busy_returns_409` / `test_explore_autopilot_endpoint` 于「探索 Roguelike 后端」轮（Task 11）追加（`POST /api/explore/choose` 决策点选择 + 无进行中探索转 409 + `POST /api/explore/autopilot` 托管开关）。

## 19-encounter（遭遇：规则纯函数 + 门面）

| 测试 | 检查方向 | 说明 |
|---|---|---|
| `test_start_rooted_broadcasts_start` | 功能正确 | `start_rooted(snippet, theme, activity_id)` 生成 `kind=="rooted"` 遭遇并广播一条 `ENCOUNTER_START`（有根遭遇入口） |
| `test_start_rooted_guarded_when_encounter_in_progress` | 回归保护 | `_current` 非空时 `start_rooted` 直接返回：进行中遭遇不被撞掉、不新增 `ENCOUNTER_START`（I2） |
| `test_should_encounter_removed` | 回归保护 | 块边界随机入口已删：`rules` 模块不再导出 `should_encounter` |

**功能阶段**：本段于 Task 9 新增（有根遭遇 `start_rooted` + 删块边界随机入口）。同轮删除 `test_should_encounter_*`（7 条）、`test_constants_sane`、`test_try_block_boundary_*`（3 条）共 11 条旧测试（随机入口已删）；其余 encounter 测试（`_parse_encounter` / `choose` / `on_activity_end` / `consequence_for` / `ending_for` / `growth_milestone_key` / `growth_memory`）此前未单列于清单，属既有缺口，不在本 Task 补齐。`test_start_rooted_guarded_when_encounter_in_progress` 于「全分支审查修复（I2）」轮追加（`start_rooted` 加 `_current` 守卫，防撞掉进行中的遭遇）。

## frontend-sse（SSE 数据流：useSSE + dispatchEvent 分发表）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `useSSE > 挂载即 new EventSource` | 功能正确 | url == `BASE_URL + "/api/events"`、初始 `connecting` |
| `useSSE > onopen/onerror 三态` | 功能正确 | `onopen` → `open`、`onerror` → `connecting`（原生自动重连） |
| `useSSE > 命名帧解析` | 功能正确 | emit `speak` 帧 → dispatch 收到 `{event,event_id,correlation_id,content}` 展开 |
| `useSSE > 坏 data/缺字段跳过` | 边界鲁棒 | 非法 JSON、缺 `event_id`/`correlation_id` → `console.error` 跳过不崩，仅正常帧 dispatch |
| `useSSE > unmount 调 close()` | 功能正确 | 卸载 cleanup → `source.close()` 被调 |
| `dispatchEvent > speak → chatStore` | 功能正确 | `kind=speak`/`role=nyx`/`content` 入 `messages` |
| `dispatchEvent > user_message → chatStore` | 回归保护 | 读 `message` 非 `content` → `kind=message`/`role=user`/`content` 入 `messages`（Finding 1：user_message 裸 `{message}` 曾致用户消息被 `typeof e.content` 拦截静默丢弃） |
| `dispatchEvent > emotion_update → innerLifeStore` | 功能正确 | 覆盖 `valence`/`arousal`/`emotion` 三字段 + 顺带 `refreshState()` 重拉全量快照（能量/性格/三观不随帧下发，补自动刷新） |
| `dispatchEvent > desire_generated → desireStore.refresh()` | 功能正确 | `desire_generated` 触发 `desireStore.refresh()` 恰 1 次 |
| `dispatchEvent > memory_created → memoryStore.refresh()` | 功能正确 | `memory_created` 触发 `memoryStore.refresh()` 恰 1 次 |
| `dispatchEvent > activity_start → activityStore.refresh()` | 功能正确 | `activity_start` 触发 `activityStore.refresh()` 恰 1 次 |
| `isEmotionCategory > 枚举收窄` | 边界鲁棒 | 合法枚举（`happy`/`neutral`）→ true；非法字符串（`不存在`）/非字符串（`5`/`null`）→ false |
| `dispatchEvent > reflection_done（story_is_new）→ 欲望/叙事 refresh + 高亮 + 气泡` | 功能正确 | `story_is_new=true` → `desireStore.refresh`/`narrativeStore.refresh` 各 1 次 + `highlightedStory` 置为 story + `announceStore` 追加气泡「小狐狸我呀，反思了一下：…」 |
| `dispatchEvent > reflection_done（story_is_new=false）→ 静默 refresh 不高亮不气泡` | 功能正确 | `story_is_new=false` → 仍双 refresh 但 `highlightedStory` 为 null、`announceStore` 空（静默刷新） |
| `dispatchEvent > exploration_step → explorationStore.onStep` | 功能正确 | 推送决策载荷：`decision` 落 store、`activityId` 置为帧 `activity_id` |
| `dispatchEvent > activity_end → 清空匹配 activity 的探索状态` | 功能正确 | 路由集成：`activity_end` 帧 `activity_id` 匹配探索中活动 → `decision`/`activityId` 置 null、`autopilot` 关 false |

**功能阶段**：frontend 01-sse 实现时编写（mock `EventSource` stub + 真实 store；验证管道正确——事件走对 store、字段零映射、坏帧跳过不崩，不验证视觉）；`dispatchEvent > user_message → chatStore` 于本轮 review 追加（Finding 1 回归：user_message 裸 `{message}` 曾致用户消息被 `typeof e.content` 拦截静默丢弃）；`isEmotionCategory > 枚举收窄` 于本轮 review 追加（emotion 枚举值运行时收窄）；`desire_generated` / `memory_created` / `activity_start` → refresh 于前端面板落地轮追加（快照 store 路由）；`emotion_update → 顺带 refreshState` 于「前端不自动刷新」bug 修复轮改写（根因：emotion_update 载荷只带情绪，能量/性格/三观不随帧下发，EnergyBar/BigFiveChart/ValuesChart 停在初始快照；改为 dispatch 顺带重拉全量 state）；`dispatchEvent > reflection_done` → 欲望/叙事 refresh + 高亮 + 气泡于「反思优化」轮追加（`reflection_done` 事件路由：双快照刷新 + 新故事高亮 + 头像旁气泡）；`dispatchEvent > exploration_step` 于「探索升级：联网探索 + 探索地图」轮追加（`exploration_step` 事件路由：点亮探索地图实时节点）；「探索 Roguelike 前端」轮改为推送决策载荷（`decision` 落 store，弃 `node`/`liveNodes`）。

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
| `getMemories > query 拼装` | 功能正确 | `tag`/`type` 拼进 query（`?tag=user&type=long_term`） |
| `getMemories > 无参数不带 query` | 边界鲁棒 | 无参 → 请求 `/api/memories`（不带 `?`） |
| `searchMemories > query 拼进 URL` | 功能正确 | `searchMemories("猫")` → 请求 `/api/memories/search?q=%E7%8C%AB`（`encodeURIComponent`） |
| `getEval > 可选 limit 拼进 query` | 功能正确 | `getEval(5)` → `/api/eval?limit=5` |
| `getTokens > 可选 since 拼进 query` | 功能正确 | `getTokens(1000)` → `/api/tokens?since=1000` |
| `getEventsLog > limit/event_type/correlation_id 拼进 query` | 功能正确 | 三参拼进 query（`?limit=20&event_type=speak&correlation_id=c1`） |
| `getNarrative > GET /api/narrative` | 功能正确 | 请求 URL、解析 `SelfNarrative` 直返 |
| `exportMemories > POST /api/export 返回文本` | 功能正确 | `POST /api/export` body `{format}`；响应非 JSON 走 `text()` 返回裸字符串（不走 `request` 的 `.json()`） |
| `uploadFile > POST /api/upload FormData` | 功能正确 | `POST /api/upload` body 为 `FormData`（含 `file` 字段）、不设 `Content-Type`（浏览器自动 multipart 边界）、解析 `{event_id, filename, path}` |
| `getMaterials > GET /api/materials` | 功能正确 | 请求 URL、解析 `{materials: Material[]}` 直返 |
| `getReadingNotes > 可选 limit 拼进 query` | 功能正确 | `getReadingNotes(50)` → `/api/reading-notes?limit=50`；解析 `ReadingNote[]` 直返 |
| `getReadingNotes > 无参数不带 query` | 边界鲁棒 | 无参 → 请求 `/api/reading-notes`（不带 `?`） |
| `deleteReadingNote > DELETE /api/reading-notes/{id}` | 功能正确 | 请求 URL/method、解析 `{deleted}` 直返 |
| `getAnnotations > GET /api/annotations?target_id=` | 功能正确 | 请求 URL（`?target_id=n1`）、解析 `Annotation[]` 直返 |
| `addAnnotation > POST /api/annotations` | 功能正确 | 请求 URL/method、body `{target_id, content}`、解析 `Annotation` 直返 |
| `deleteAnnotation > DELETE /api/annotations/{id}` | 功能正确 | 请求 URL/method、解析 `{deleted}` 直返 |
| `postExplore > POST /api/explore` | 功能正确 | 无 topic → body `{}`；有 topic → body `{topic}`（`POST /api/explore`、解析 `{activity_id}`） |
| `chooseExploration > POST /api/explore/choose` | 功能正确 | body `{activity_id, choice}`（`POST /api/explore/choose`、`method: POST`） |
| `setExplorationAutopilot > POST /api/explore/autopilot` | 功能正确 | body `{activity_id, on}`、解析 `{activity_id, autopilot}` |

**功能阶段**：frontend 05-client 实现时编写（mock `fetch` 断言端点/方法/请求体键 + 错误契约；验证管道正确——键零映射、错误上抛，不验证视觉）；`非 2xx detail 空串兜底` 于本轮 review 追加（Finding B：空串 detail 致 `Error.message=""` 被 UI 误判为无错误）；六个新端点（`getDesires`/`getActivity`/`getMemories`/`getEval`/`getTokens`/`getEventsLog`）于前端面板落地轮追加（快照/事件日志端点 query 拼装 + 解析）；`getNarrative` / `exportMemories` / `uploadFile` / `getMaterials` 于「喂资料/上传课本」轮追加（自我叙事快照 + 记忆导出裸文本 + 上传 FormData + 资料清单）。`postObserve` 于「活动填实（观察填实）」轮改为两参 `postObserve(presence, windowTitle)`（body 加 `window_title`）；`getActivityResults` 于「产出面板」轮追加（跨天历史产出端点）；`getMaterials` 于「读书连贯+进度」轮改为解析 `{materials: Material[]}`（书库进度，不再 `string[]`）。`getReadingNotes` / `deleteReadingNote` / `getAnnotations` / `addAnnotation` / `deleteAnnotation` 于「读书/创作借鉴」轮追加（读书笔记 CRUD + 批注 5 端点）；`searchMemories > query 拼进 URL` 于「记忆前端搜索/显示优化」轮追加（`/api/memories/search` 端点函数 `encodeURIComponent` 拼装）；`postExplore` 于「探索升级：联网探索 + 探索地图」轮追加（`POST /api/explore` 端点函数：无 topic 发空 body、有 topic 发 `{topic}`）。`chooseExploration` / `setExplorationAutopilot` 于「探索 Roguelike 前端」轮追加（决策流端点：提交选择 + 托管开关）。

## frontend-stores（Zustand stores：chatStore + innerLifeStore + 四个快照 store + settingsStore）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `chatStore.add* > 6 个 action 转 ChatMessage` | 功能正确 | `addSpeak`/`addAsk`/`addThink`/`addMutter`/`addInitiateChat`/`addUserMessage` 各断言 role/kind/content/correlation_id 且 append |
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
| `chatStore.loadHistory > 升序前置 + preloaded + typedIds` | 功能正确 | 六类历史事件合并按 `timestamp` 升序前置、每条 `preloaded=true`、历史 think 入 `typedIds` |
| `chatStore.loadHistory > 已存在 id 去重` | 边界鲁棒 | 与现有 `messages` 撞 id 的历史消息不重复前置（`s1` 仅 1 条） |
| `chatStore.loadHistory > getEventsLog 失败` | 边界鲁棒 | `getEventsLog` reject → best-effort 不抛、`messages` 不变 |
| `chatStore > markTyped + reset 清 typedIds` | 功能正确 | `markTyped("x")` 写入 `typedIds["x"]`；`reset()` 清空 `typedIds={}` |
| `desireStore.refresh > GET /api/desires` | 功能正确 | mock fetch 断言端点 + `data` 落 store |
| `activityStore.refresh > 并行 getActivity+getActivityResults` | 功能正确 | `fetch` 恰 2 次（`/api/activity` + `/api/activity/results`）→ `data`/`results` 双字段落 store |
| `memoryStore.refresh > GET /api/memories` | 功能正确 | 同上（`data` 落 store） |
| `evalStore.refresh > 并行 getEval+getTokens` | 功能正确 | `fetch` 恰 2 次 → `reports`/`tokens` 落 store |
| `desireStore.refresh > 失败 → error` | 边界鲁棒 | `getDesires` reject → `error=e.message` + `data` 保持 null |
| `isReady > think 打完才放行 speak` | 功能正确 | think 未打完 → false；`typedIds` 含该 think → true；无前置 think → true（串行逐字门控核心） |
| `isReady > preloaded / user 恒就绪` | 功能正确 | `preloaded` nyx 文本、user 消息 → true（历史不逐字 / 用户消息不被门控） |
| `isReady > 不同 correlation_id 不阻塞` | 功能正确 | 不同 `correlation_id` 的 nyx 文本不阻塞 speak → true |
| `settingsStore > setTint/setImage 独立落 store` | 功能正确 | `setTint`/`setImage` 各落 `tint`/`image` 字段，可并存 |
| `settingsStore > reset 恢复默认` | 功能正确 | `reset()` 后 `tint`/`image` 均回 null |
| `isReady > think 也受串行门控` | 功能正确 | think2 在 speak1 之后、speak1 未入 `typedIds` → false；speak1 入 → true（每条 nyx 文本等前一条同 correlation_id 打完） |
| `narrativeStore.refresh > GET /api/narrative` | 功能正确 | mock fetch 断言端点 + `data` 落 store（`SelfNarrative`）|
| `materialsStore.refresh > GET /api/materials` | 功能正确 | mock fetch 断言端点 + `materials` 落 store |
| `materialsStore.upload > 上传后重拉 materials` | 功能正确 | `upload(file)` → `POST /api/upload`（fetch 恰 2 次：upload + 重拉 `getMaterials`）+ `materials` 更新 + `uploading` 复位 |
| `readingNotesStore.refresh > GET /api/reading-notes?limit=50` | 功能正确 | mock fetch 断言端点 + `notes` 落 store（`ReadingNote[]`）+ `loading=false` |
| `readingNotesStore.remove > DELETE 后本地摘除` | 功能正确 | `remove(id)` → `DELETE /api/reading-notes/{id}`（fetch 恰 1 次）+ 从 `notes` 本地摘除该条（不重拉） |
| `narrativeStore.setHighlightedStory > 记录待高亮故事（reflection_done 高亮用）` | 功能正确 | `setHighlightedStory("x")` → `highlightedStory` 落 store（叙事面板据此标「新」徽标） |
| `explorationStore.onStep > 同 activity 更新 decision / 异 activity 清 history` | 功能正确 | 同 `activity_id` → `decision` 落 store 且 `history` 保留；异 `activity_id` → `decision`/`activityId` 更新 + `history` 清空 + `autopilot` 复位 |
| `explorationStore.choose > node:0 记录足迹 + POST /api/explore/choose` | 功能正确 | mock fetch 断言 `POST /api/explore/choose` body `{activity_id, choice}`；`history[0]` 记 `{floor, name}` |
| `explorationStore.choose > 无 decision 不发起 POST` | 边界鲁棒 | `activityId`/`decision` 任一为 null → fetch 不调（守卫短路） |
| `explorationStore.start > POST /api/explore 后复位 decision/history/autopilot` | 功能正确 | mock fetch 断言 `POST /api/explore`；`activityId` 置为返回 `activity_id`、`decision=null`、`history=[]` |
| `explorationStore.toggleAutopilot > POST /api/explore/autopilot + 本地镜像` | 功能正确 | mock fetch 断言 body `{activity_id, on}`；`autopilot` 本地镜像置 `on` |
| `explorationStore.onActivityEnd > 匹配 id 清 decision/autopilot` | 功能正确 | `activityId` 匹配 → `decision=null`/`activityId=null`/`autopilot=false`/`choosing=false` |

**功能阶段**：frontend 02-stores 实现时编写（mock `fetch`/fake timers + 真实 store；验证管道正确——action 转消息正确、isReplying 生命周期 + 60s 超时兜底、快照+增量、内存上限，不验证视觉）；`chatStore > 迟到回复清 sendError`、`chatStore.reset > 新会话全清` 于上轮 review 追加（Finding 2/3：回复到达清「回复超时」残留 + reset 全清）；`chatStore > 非匹配 correlation 不清 timer` 于本轮 review 追加（Finding A：存 postChat 返回 event_id 到 pendingId，addSpeak/addAsk 按 correlation_id 匹配后才清 timer）；`chatStore.sendMessage > 重入守卫` 于 03-chat-panel 后 review 追加（串行锁提前到 await 前 + get() 同步守卫，防 in-flight 重复发送覆盖 pendingId）；`chatStore.loadHistory` / `markTyped`+`reset` / 四个快照 store `refresh` / `isReady` 于前端面板落地轮追加（聊天历史加载 + 快照 store + 串行逐字门控纯函数）；`settingsStore` 于视觉改造轮追加（背景色调/背景图纯前端 UI 状态）；`isReady` 于「开头打字机」轮追加并随后改为**全串行门控**（每条 nyx 文本等前一条同 correlation_id 打完，删去 `isFirstTypewriter` 开头打字机）；`narrativeStore.refresh` / `materialsStore.refresh` / `materialsStore.upload` 于「喂资料/上传课本」轮追加（自我叙事快照 + 资料清单 + 上传即重拉）；`activityStore.refresh` 于「产出面板」轮改为双字段并发拉取（`data` + `results`）；`materialsStore.refresh` / `materialsStore.upload` 于「读书连贯+进度」轮改为落 `materials: Material[]`（书库进度，不再 `files`）。`chatStore.addInitiateChat > unreadProactive` / `chatStore.reset > 复位 unreadProactive` 于「内心世界弹窗化 + 借鉴」轮追加（头像红点未读：搭话置未读、`clearUnreadProactive`/发消息/`reset` 清）。`readingNotesStore.refresh` / `readingNotesStore.remove` 于「读书/创作借鉴」轮追加（读书笔记清单快照 + 删除本地摘除）；`narrativeStore.setHighlightedStory` 于「反思优化」轮追加（`highlightedStory` 字段 + `setHighlightedStory` action，供 `reflection_done` 新故事高亮）；`explorationStore`（`addWish`/`removeWish`/`onStep`/`start`）于「探索升级：联网探索 + 探索地图」轮追加（探索地图 store：心愿单内存增删 + 实时节点累积/重置 + `POST /api/explore` 出门探索）。「探索 Roguelike 前端」轮全重写：旧四测替换为 `onStep`（decision 落 store）/`choose`（+无 decision 守卫）/`start` 复位/`toggleAutopilot`/`onActivityEnd` 六测（决策流 store：`decision` + `history` + `autopilot` + `choose`）。

## frontend-labels（枚举中文化映射：lib/labels.ts）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `labels > 用户示例 exploration → 发现` | 功能正确 | `DESIRE_TYPE_LABELS.exploration === "发现"`（用户点名的翻译） |
| `labels > 各枚举键均有中文映射` | 功能正确 | 6 个枚举映射表的每个值均非空字符串（无 `undefined` 漏译） |
| `labels > Big Five / 三观双端语义均有 low/high 中文` | 功能正确 | `PERSONALITY_POLES`/`VALUES_POLES` 每个 pole 的 `low`/`high` 均非空字符串 |
| `labels > label() 命中键返回中文，未知键回退原值` | 边界鲁棒 | `label(map, "exploration")` → 中文；`label(map, "unknown_key")` → `"unknown_key"`（未知键回退原值不崩） |
| `labels > ENCOUNTER_KIND_LABELS.rooted → 有根遭遇` | 功能正确 | `ENCOUNTER_KIND_LABELS.rooted === "有根遭遇"`（`EncounterKind` 新增 `rooted` 成员的中文映射） |

**功能阶段**：frontend 视觉改造轮编写（枚举值中文化映射 + `label()` 回退纯函数；验证 `lib/labels.ts` 单一真源——各枚举值都映射到非空中文、未知键回退原值，不验证视觉）；`PERSONALITY_POLES`/`VALUES_POLES`（双端语义 low/high）于「双端量表」轮替代 `PERSONALITY_LABELS`/`VALUES_LABELS`，新增双端语义断言。`ENCOUNTER_KIND_LABELS.rooted` 于「探索 Roguelike 前端」轮追加（`EncounterKind` 加 `rooted` 成员）。

## frontend-typewriter（打字机 hook：useTypewriter）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `useTypewriter > 空文本 → displayed 空 + done 立即 true` | 边界鲁棒 | `useTypewriter("")` → `displayed === ""`、`done === true`（空文本短路，不起 timer） |
| `useTypewriter > 逐字：每 tick 增一字，直至 done` | 功能正确 | fake timers：两次 `advanceTimersByTime(35)` → `"你"`→`"你好"`、`done` false→true |
| `useTypewriter > ready=false 不启动` | 功能正确 | `ready=false` 时 `displayed=""`+`done=false` 且推进 timer 不打字；`rerender({ready:true})` 后才从 0 逐字（串行逐字门控） |

**功能阶段**：frontend 视觉改造（Galgame 打字机）时编写（`renderHook` + fake timers；验证渲染层逐字推进——`displayed` 按 tick 递增、空文本短路，纯渲染层不碰 store/数据流，不验证视觉）；`ready=false 不启动` 于前端面板落地轮追加（串行逐字：speak/ask 等前置 think 打完才开打）。

## frontend-chat-panel（聊天面板：ChatPanel + MessageList + MessageBubble + ChatInput）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `MessageBubble > speak → 左气泡 class + content 上屏` | 功能正确 | speak 气泡带 `message-bubble--speak` class、content 渲染为 `.message-bubble__content` |
| `MessageBubble > ask → 高亮 class` | 功能正确 | ask 气泡带 `message-bubble--ask` class（高亮样式钩子） |
| `MessageBubble > think → 逐字弱化显示（不再折叠）` | 功能正确 | think 气泡带 `message-bubble--think` class、content 经 `typeDone()` 推进 fake timers 后完整上屏 |
| `MessageBubble > initiate_chat → 「搭话」标记` | 功能正确 | initiate_chat 气泡带「搭话」badge |
| `MessageBubble > user message → 右气泡 class` | 功能正确 | 用户消息带 `message-bubble--user` class |
| `MessageList > 全部消息按序渲染，无历史折叠` | 功能正确 | 两条消息 `typeDone()` 后都上屏（微信式：不再只显示一条 / 折叠历史）；`queryByRole("button")` 无历史按钮 |
| `MessageList > 全部气泡渲染即存在（串行门控只延迟内容，不延迟挂载）` | 功能正确 | 两条 nyx 消息渲染即 `.message-bubble` 数量 = 2（打字中 content 渐显但气泡已挂载，串行门控只延迟内容） |
| `MessageList > 串行逐字：内心话先打完、对话才开打` | 功能正确 | think 在前、speak 在后：未推进 timer 两者皆空（think 刚开打、speak 等前置打完）；`typeDone()` 后 think→speak 串行完整上屏 |
| `ChatPanel > 订阅 messages 透传给 MessageList` | 功能正确 | store 里 messages 经 ChatPanel 订阅透传 → MessageList 渲染上屏 |
| `ChatPanel > 头部「设置」按钮触发 onOpenSettings` | 功能正确 | 点「设置」→ `onOpenSettings` 恰调 1 次（App 层 view 切到设置面板） |
| `ChatPanel > 头部「内在/空间/记录」三按钮触发 onOpenInner` | 功能正确 | 3 个分类按钮（内在/空间/记录）都渲染；点「空间」→ `onOpenInner` 恰调 1 次且传 `1`（App 层开对应分类卡片） |
| `ChatInput > 点发送 → sendMessage(trimmed) 且成功清空` | 功能正确 | 点发送按钮触发 `sendMessage` 且传入 trim 后文本；成功（返回 true）后 `waitFor` 断言输入框清空 |
| `ChatInput > 回车 → sendMessage 且成功清空` | 功能正确 | 输入框 Enter 触发 `sendMessage`；成功后输入框清空 |
| `ChatInput > 输入法组合态回车不触发` | 回归保护 | `isComposing=true` 的 Enter（拼音选字）不触发 `sendMessage`（防 IME 误发送） |
| `ChatInput > 发送失败保留文本` | 功能正确 | `sendMessage` 返回 false（postChat 失败）→ 输入框保留原文可重试 |
| `ChatInput > 成功清空不误删预打文本` | 回归保护 | 发送在途时用户把输入改成别的 → 成功后仅清「仍是原文」的框，预打文本保留（函数式更新比对 trimmed） |
| `ChatInput > isReplying=true → 禁用 + 回车不触发` | 功能正确 | isReplying 时发送按钮 `disabled` + 文案「…」；填非空值后回车仍不触发 sendMessage（只有 isReplying 守卫能拦） |
| `ChatInput > sendError 非 null → 红字显示` | 功能正确 | `sendError` 非空时渲染 `.chat-input__error` 红字 |

**功能阶段**：frontend 03-chat-panel 实现时编写（RTL + 真实 store；验证管道正确——按 role/kind 渲染、think 逐字弱化、nyx 文本消息走 useTypewriter 需 `typeDone()` 推进 fake timers、isReplying 锁输入、sendError 上屏，不验证视觉样式）；本表 MessageBubble/MessageList/ChatPanel 于视觉改造轮追加 fake timers（nyx 文本逐字，`typeDone()` 多轮推进覆盖逐字）；`MessageList > 全部消息按序渲染，无历史折叠` / `全部消息渲染即存在（不串行）` 于视觉改造轮追加（微信式全量列表：全部消息按序渲染、上滑看历史、不串行等前一条打完）；`ChatInput > 输入法组合态回车不触发`、`ChatInput > 发送失败保留文本` 于 03-chat-panel 后 review 追加（Finding 1/2：IME 回车误发送 + 清空太早致失败丢文本）；`makeMsg` 自增 id、`isReplying` 用例填非空值 于同轮修正（假绿：空输入提前 return / 同 kind 撞 key）；`ChatInput > 成功清空不误删预打文本` 于下一轮 review 追加（异步无条件清空会误删回复期间预打文本，改函数式更新比对 trimmed）；`MessageList > 打字机只在第一条 nyx 文本生效` 于「开头打字机」轮追加（打字机只在第一条 nyx 文本消息生效，其余即时显示）；`ChatPanel > 头部「内心」按钮触发 onToggleInner` 于「内心世界」轮追加（观测面板移入右侧抽屉，头部新增「内心」按钮）；本轮「读书/创作借鉴 + 三按钮」把它改为 `头部「内在/空间/记录」三按钮触发 onOpenInner`（「内心」单按钮换 3 个分类按钮，点哪个开哪个分类卡片，断言点「空间」传 index `1`）。

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

**功能阶段**：frontend 04-inner-state-panel 实现时编写（RTL + 真实 store；验证管道正确——子组件按需收字段、`current=null` 整体占位不崩，图表坐标/像素不断言）；`EmotionSprite` 已在 03-chat-panel 由 MessageBubble 复用，此处补其文件名映射的独立断言。枚举值中文化（`lib/labels.ts`）于视觉改造轮追加——原「枚举值显原值不转中文」约定被用户要求推翻（`exploration → 发现`），EnergyBar/BigFiveChart/ValuesChart 断言由英文原值改为中文标签；BigFiveChart/ValuesChart 于「双端量表」轮由条形图+数值改为双端量表（去数值、两端语义 + 圆点位置表达，`PERSONALITY_LABELS`/`VALUES_LABELS` 改 `PERSONALITY_POLES`/`VALUES_POLES`，断言改为两端词）。`ValenceArousalPlot > 区域标签对齐后端 6 档` 于「读书/创作借鉴 + 三按钮」轮追加（象限硬编码「高兴/平静/愤怒/低落」改为对齐 `vad_to_category` 6 档经 `EMOTION_LABELS`，修右下角「平静」实为「害羞」的错位）。

## frontend-presence（活跃度上报：usePresence + classifyPresence）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `classifyPresence > 键盘/鼠标任一活跃 → online` | 功能正确 | `(true,false)`/`(false,true)`/`(true,true)` 均 `online`；`(true,true,"编辑器")` 仍 `online`（活跃优先于窗口标题） |
| `classifyPresence > 无输入+标题 → busy；全无 → away` | 功能正确 | `(false,false,"编辑器")` → `busy`；`(false,false,"")` → `away`（镜像后端 14-activity observe.py 规则） |
| `usePresence > 首次挂载必报一次（away）` | 功能正确 | 挂载即 `postObserve("away", "")` 恰 1 次（首采样必报，window_title 采 `document.title`，jsdom 默认 `""`） |
| `usePresence > 键盘活动 → 下次采样报 online` | 功能正确 | `keyDown` 后 30s 采样点 `postObserve("online", "")`（活动 20s 前，< 30s 活跃窗口） |
| `usePresence > 鼠标活动 → 下次采样报 online` | 功能正确 | `mouseMove` 后 30s 采样点 `postObserve("online", "")` |
| `usePresence > presence 不变 → 不再上报` | 边界鲁棒 | 无输入 30s 后 `postObserve` 仍 1 次（仅挂载那次 away，不重复上报） |

**功能阶段**：frontend usePresence（README §2）实现时编写（mock `postObserve` + fake timers + `renderHook`；验证管道正确——采集→判定→上报的节奏与去重，fetch 细节归 frontend-client；窗口标题核心先行恒传 `""`，故 hook 不测 busy 分支，busy 由 `classifyPresence` 纯函数覆盖）。`usePresence` 于「活动填实（观察填实）」轮改为采样 `document.title` 并上报 `postObserve(presence, windowTitle)`（jsdom `document.title` 默认 `""`，故断言第二参为 `""`）；真正的「前台应用窗口标题」留到 src-tauri 落地换源。

## frontend-side-panel（布局：SidePanel 标签页）— 已删除

> **已删除**：`SidePanel` 组件及其测试于「替换式切视图」轮移除（设置职责由 `SettingsView` 接管，Eval 走独立调试页），`tests/sidePanel.test.tsx` 一并删除。以下为历史记录。

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `SidePanel > 渲染 2 个标签，默认激活「背景」` | 功能正确 | 2 个 tab 按钮（背景/Eval）都渲染；默认「背景」`aria-pressed=true` + 面板标题「背景」上屏 |
| `SidePanel > 点击「Eval」切换面板，未激活面板卸载` | 功能正确 | 点「Eval」后 `aria-pressed` 移到「Eval」、面板标题「Eval」上屏、背景面板标题卸载（仅挂载当前 tab，规避旧抽屉 flex 子项被压缩裁掉无法滚动） |
| `SidePanel > 「返回对话」按钮触发 onBack` | 功能正确 | 点「返回对话」→ `onBack` 恰调 1 次（设置面板退回聊天，App 层 view 切换） |

**功能阶段**：frontend 视觉改造（右侧滑出抽屉 → 右侧常驻标签页）时编写（RTL + 真实 store；验证管道正确——标签切换当前面板、仅挂载 active tab，fetch stub 永不 resolve 以隔离数据加载，不验证视觉样式）；本轮视觉改造把 SidePanel 从「常驻右侧标签页」改为「设置模式下替换对话框」，`SidePanel` 需 `onBack` prop + 首 tab 由「内在」改为「背景」——原 6 标签断言改为 7 标签、默认激活断言由「内在」改「背景」、补 `onBack` 用例；`渲染 7 个标签` 于「喂资料/上传课本」轮改为 `渲染 9 个标签`（新增「叙事」「资料」两 tab，标签清单同步补入）；本轮移除「溯源」tab（`渲染 8 个标签`）；`渲染 9 个标签` 于「产出面板」轮改为 9 标签（新增「产出」tab，标签清单同步补入）；`渲染 9 个标签` 于「内心世界」轮改为 `渲染 3 个标签`（内在/欲望/活动/产出/叙事/资料 6 个观测面板移入右侧 `InnerWorld` 抽屉，SidePanel 只留背景/记忆/Eval，切换用例由「欲望」改「记忆」）。本轮「记忆」移入内心世界：`渲染 3 个标签` 改为 `渲染 2 个标签`（SidePanel 只留背景/Eval），切换用例由「记忆」改「Eval」。

## frontend-inner-world（布局：InnerWorld 内心世界页内面板）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `InnerWorld > categoryIndex=0 渲染「内在」子标签，默认激活「内在状态」` | 功能正确 | 传 `categoryIndex=0` 上屏分类标题「内在」+ 三个子标签按钮（内在状态/欲望/叙事），默认「内在状态」`aria-pressed=true` + heading 上屏（点标签切面板，不再竖排堆叠） |
| `InnerWorld > categoryIndex=1 渲染「空间」子标签，默认激活「读书笔记」` | 功能正确 | 传 `categoryIndex=1` 上屏「空间」标题 + 读书笔记/产出/资料三标签，默认「读书笔记」`aria-pressed=true` + heading 上屏 |
| `InnerWorld > categoryIndex=2 渲染「记录」子标签，默认激活「活动」` | 功能正确 | 传 `categoryIndex=2` 上屏「记录」标题 + 活动/记忆两标签，默认「活动」`aria-pressed=true` + heading 上屏 |
| `InnerWorld > 点子标签「欲望」切到欲望面板，内在状态面板卸载` | 功能正确 | 点「欲望」→ `aria-pressed` 移到「欲望」、heading「欲望」上屏、「内在状态」heading 卸载（仅挂载活动 tab） |

**功能阶段**：frontend「内心世界」轮编写（观测面板从「设置」移入右侧滑出抽屉；RTL + 真实 store，验证管道正确——标签切换、仅挂载 active tab、open/onClose 接线，fetch stub 永不 resolve 以隔离数据加载，不验证视觉样式）。本轮「记忆」从「设置」移入内心世界：`渲染 6 个标签` 改为 `渲染 7 个标签`。「内心世界弹窗化 + 借鉴」轮：InnerWorld 由右侧滑出抽屉（`<aside className="inner-world">` + `open` prop）改为可拖拽弹窗（DraggablePanel 包裹、App 层条件渲染 `{innerOpen && <InnerWorld/>}`），移除 `open` prop、`收起` 按钮改 `×`（aria-label「关闭」）、删 `open=false` 修饰类用例；标签/内容结构不变（仍复用 `.side-panel__tabs/__body`）。「读书/创作借鉴」轮：扁平 7 标签改为「内在/空间/记录」三大类两级导航（子标签：内在=内在状态/欲望/叙事、空间=读书笔记/产出/资料、记录=活动/记忆），用例由「渲染 7 标签」改为「渲染 3 大类 + 默认内在状态」+ 大类切换 + 子标签切换。「读书/创作借鉴 + 三按钮」轮：三大类导航从 InnerWorld 内部上移到对话框头部三按钮（ChatPanel），InnerWorld 改为**单分类卡片**——收 `categoryIndex` prop，去掉顶部大类导航只留子标签，标题=分类名；用例由「渲染 3 大类 + 大类切换」改为「按 categoryIndex 渲染对应子标签 + 分类内切子标签」。「替换式切视图」轮：InnerWorld 从可拖拽弹窗（DraggablePanel 包裹）改为页内面板（`.side-panel` 容器），`onClose`/`×` 关闭按钮改 `onBack`/「返回对话」按钮（对应用例由「关闭按钮触发 onClose」改为「返回对话按钮触发 onBack」），App 层由「条件渲染弹窗」改为「替换书卷区三向视图」；标签/内容结构不变。「底两条常驻」轮：`InnerWorld` 移除 `onBack` prop 与「返回对话」按钮（切回聊天统一走底部工具条「聊天」入口），对应「返回对话按钮触发 onBack」用例删除，标题栏只留分类名。「词条收敛·竖排单分类」轮：`InnerWorld` 由「单分类子标签」改为**竖排单分类**——`categoryIndex` 直接渲染该类全部面板（滚动查看），删除 `OVERVIEW_INDEX` 全分类总览 + 内部 `activeCategory`/`activeTab` 状态（修「空间点开是内在」的跨视图状态残留）；用例由「按 categoryIndex 渲染子标签 + 分类内切子标签 + 全分类总览」改为「按 categoryIndex 竖排渲染该类全部面板 heading」（0/1/2 三分类各一例），fetch stub 上移到 `beforeEach` 统一隔离（竖排渲染多面板、多面板挂载即 refresh）。「横板点网页」轮：`InnerWorld` 竖排堆叠改回**横向子标签条**（网页 tab 感，点标签切活动面板、默认激活第一项），重新引入 `activeTab` 本地 state（跨分类切换由 App 层 `key={view}` 强制重挂载归零，修状态残留），补 `.side-panel__tabs/__tab` 横向样式；用例由「三分类竖排渲染全部面板」改为「三分类各渲染子标签 + 默认激活第一项 + 点子标签切面板」（4 例），fetch stub 保留 `beforeEach` 统一隔离。

## frontend-reading-notes（读书笔记面板：ReadingNotesPanel）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `ReadingNotesPanel > 渲染清单：书名 + 预览 + 批注数徽标` | 功能正确 | mock fetch 返回笔记 → 请求 `/api/reading-notes?limit=50`；书名《三体》、内容预览、`💬3` 批注数徽标上屏 |
| `ReadingNotesPanel > 点卡片展开详情 + 批注` | 功能正确 | 点卡片 → 正文 Markdown 上屏 + `← 返回` 按钮 + 批注（`getAnnotations` 结果）上屏 |
| `ReadingNotesPanel > 新增批注` | 功能正确 | 输入 + 点「添加批注」→ `POST /api/annotations`（body `{target_id, content}`）后重拉批注上屏 + 再拉清单（`refresh`，`annotation_count` 徽标跟随） |
| `ReadingNotesPanel > 删除批注` | 功能正确 | 点批注「删除」→ `DELETE /api/annotations/{id}` 后该批注摘除 + 再拉清单（`refresh`，徽标跟随） |
| `ReadingNotesPanel > 删除笔记` | 功能正确 | `confirm` true + 点「🗑 删除」→ `DELETE /api/reading-notes/{id}` 后清单摘除 |
| `ReadingNotesPanel > 切换笔记 A→B 丢弃 A 的陈旧批注响应` | 边界鲁棒 | A 的 `getAnnotations` 手动延迟 resolve、晚于 B 返回 → 序号守卫丢弃 A，界面仍显示 B 的批注（修复：陈旧响应竞态） |

**功能阶段**：frontend「读书/创作借鉴」轮编写（RTL + 真实 store；验证管道正确——清单/详情/批注增删，笔记清单走 `readingNotesStore`、选中笔记与批注走组件本地 `useState`，mock fetch 断言端点/方法/请求体，不验证视觉）；「切换笔记 A→B 丢弃陈旧批注响应」于「代码评审修复（5 findings）」轮追加（`useRef` 序号守卫防陈旧响应竞态）；`新增批注`/`删除批注` 于「medium 评审修复」轮补断言（增/删批注后 `refresh()` 再拉清单，`annotation_count` 徽标不再陈旧）。

## frontend-avatar（头像立绘：Avatar 戳立绘 + 红点通知 + 昼夜节律）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `Avatar > isNight 昼夜节律纯函数` | 功能正确 | `isNight(22/0/5)` → true（夜间困倦）；`isNight(6/12/21)` → false（白天回落当前情绪） |
| `Avatar > unreadProactive=true 显示徽标，点击清除` | 功能正确 | `unreadProactive=true` 渲染「小狐狸我有话对你说」徽标；点击 → `clearUnreadProactive` 置 false |
| `Avatar > 戳立绘：戳一下害羞、连戳 5 次生气` | 功能正确 | 点击头像 → `announce("mutter", "呀！")`；连戳第 5 次 → `announce("mutter", "不要再戳了啦！")` |

**功能阶段**：frontend「内心世界弹窗化 + 借鉴」轮编写（借鉴 nyx_desktop_agent 的 DockAvatar 三项：戳立绘/红点通知/昼夜节律；RTL + 真实 store，验证管道正确——`isNight` 纯函数、红点徽标点击清除、戳立绘经 announceStore 冒短语，不验证视觉）。

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
| `activityAnnouncement > reading → 读完啦：…` | 功能正确 | reading 产出前缀 `读完啦：` + formatResult |
| `activityAnnouncement > creation → 创作完成：…` | 功能正确 | creation 产出前缀 `创作完成：` |
| `activityAnnouncement > free_exploration → 探索收获：…` | 功能正确 | free_exploration 产出前缀 `探索收获：` + formatResult（读 `summary`） |
| `activityAnnouncement > 无产出 / 未完成 → null` | 边界鲁棒 | 无 result、未完成活动均 null |
| `announceStore > announce 追加临时气泡（kind/text 落 store、id 唯一）` | 功能正确 | `announce("mutter", …)` append `{kind,text}` 且两次 id 不同 |
| `announceStore > dismiss 摘除指定 id，其余保留` | 功能正确 | `dismiss(id)` 后仅该 id 消失、其余保留 |
| `announceStore > 到时自动 dismiss（按 kind 时长）` | 功能正确 | fake timers 推进 `ANNOUNCE_DURATION[kind]` 后 item 消失 |
| `dispatch > mutter → chatStore + announceStore（头像旁气泡）` | 功能正确 | `mutter` 事件同时进 chatStore（历史气泡）+ announceStore（`kind="mutter"` 头像旁临时气泡） |
| `dispatch > mutter 非 string content → addMutter 丢弃且不 announce` | 回归保护 | `content=123` 的 mutter 帧：chatStore 与 announceStore 均不 append（与 addMutter 收窄一致，announce 不崩） |
| `dispatch > activity_end → refresh 后按 activity_id 找到产出并 announce` | 功能正确 | `activity_end` 触发 `refresh()` 后，从 `data.schedule` 按 `activity_id` 找 completed 活动，`activityAnnouncement` 产出以 `kind="activity"` 进 announceStore |

**功能阶段**：frontend「增强交互性」轮编写（常驻状态条 + 头像旁气泡 + 活动产出三件套；验证管道正确——`activityResult` 纯函数拼装、`announceStore` 追加/到时摘除、dispatch 把 `mutter` 与 `activity_end` 额外路由到 announceStore，不验证视觉淡出样式）。`formatResult` 从 ActivityPanel 本地函数抽提为共享库（`lib/activityResult.ts`），供活动产出气泡与状态条复用；`formatOutputBody` 于「产出面板」轮追加（独立产出面板的完整正文，与单行摘要 `formatResult` 互补）。「探索 Roguelike 前端」轮：free_exploration 五个结果文案用例改读新结果键 `summary`/`core_discovery`/`knowledge`（弃 `findings`/`notes`），与后端 §4.1 结果形状对齐。

## frontend-memory-panel（记忆面板：MemoryPanel 搜索/筛选/排序/展开）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `渲染清单：摘要 + 召回次数 + 时间` | 功能正确 | mock fetch 返回记忆 → 请求 `/api/memories`；摘要上屏 + `召回×N` + `YYYY-MM-DD` 时间 |
| `输入搜索词 → 调后端语义搜索并替换列表` | 功能正确 | 输入「猫」→ 300ms 防抖后请求 `/api/memories/search?q=%E7%8C%AB`，列表替换为搜索结果 |
| `tag 筛选：只显示匹配标签` | 功能正确 | 选 `user` 标签 → 只显示 tag=user 的记忆（本地过滤） |
| `类型筛选：只显示匹配类型` | 功能正确 | 选 `long_term` → 只显示 long_term 记忆（本地过滤） |
| `排序：按召回次数降序` | 功能正确 | 选「按召回次数」→ 列表按 `recall_count` 降序（首项召回多者） |
| `点击展开完整内容` | 功能正确 | 点摘要 → `content` 完整上屏；再点收起（`expandedId` 切换） |

**功能阶段**：frontend「记忆前端搜索/显示优化」轮编写（RTL + 真实 store + mock fetch；验证管道正确——搜索走后端语义检索、tag/类型筛选与排序对已拉取列表本地处理、点击展开完整内容，不验证视觉）。`searchMemories` 端点函数本身由 frontend-client 覆盖（`searchMemories > query 拼进 URL`）。

## frontend-narrative-panel（叙事面板：NarrativePanel 新故事高亮）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `NarrativePanel > highlightedStory 命中 → 该故事条目标「新」徽标` | 功能正确 | `highlightedStory` 命中某条 story → 该 `<li>` 内渲染「新」徽标 |
| `NarrativePanel > highlightedStory 未命中 → 不渲染徽标` | 边界鲁棒 | `highlightedStory` 为空 → 不渲染「新」徽标（`queryByText` 空） |

**功能阶段**：frontend「反思优化」轮编写（RTL + 真实 store；验证管道正确——`highlightedStory` 命中则标「新」徽标、未命中不标，不验证视觉）。

## frontend-desires-panel（欲望面板：DesiresPanel 短期欲望过滤终态）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `DesiresPanel > 渲染活队列（pending/active/suppressed），过滤 expired/satisfied` | 功能正确 | 短期欲望里 pending/active/suppressed 三条描述上屏，satisfied/expired 两条描述不上屏 |
| `DesiresPanel > 短期欲望全是终态 → 不渲染「短期欲望」空区块` | 边界鲁棒 | 短期欲望全为 satisfied/expired → `liveShortTerm.length===0`，「短期欲望」区块整体不渲染 |

**功能阶段**：frontend「欲望终态过滤」轮编写（RTL + 真实 store，`vi.spyOn(refresh)` 阻断 fetch；验证展示层过滤——`expired`/`satisfied` 终态不出现在队列列表，全终态时连「短期欲望」标题都不渲染；后端 /api/desires 全量快照不动、只前端过滤）。

## frontend-exploration-map（探索地牢组件：ExplorationMap 暗黑地牢视图）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `ExplorationMap > 渲染 HUD（目标/深度）+ 节点 + 安全房` | 功能正确 | decision 载荷渲染：focus「量子退相干」/深度「第 2 层」/节点「维基·量子退相干」/安全房「休息整理」上屏 |
| `ExplorationMap > 点节点 → choose('node:0')` | 功能正确 + 交互 | 点节点「维基·量子退相干」→ `choose('node:0')`（第 0 个节点） |
| `ExplorationMap > 无 decision → 渲染「出门探索」+ 点击调 start` | 功能正确 + 交互 | `decision=null` 时渲染「出门探索」，点击调 `start` |
| `ExplorationMap > 展开地图显示已走过楼层 + 进过节点` | 功能正确 + 交互 | 点「展开地图」→ 第 1 层足迹「维基·量子计算」上屏（`history` 按 floor 过滤） |
| `ExplorationMap > 点「下楼」→ choose('descend')` | 功能正确 + 交互 | 点「下楼」→ `choose('descend')` |

**功能阶段**：「探索 Roguelike 前端」轮全重写（暗黑地牢视图：HUD 精力/欲望/深度/托管 + 本层 4 槽节点 + 下楼/撤退 + 展开地图 + 道具栏占位，全部由 `exploration_step` 的 `decision` 载荷驱动；RTL + 真实 store，`vi.spyOn(choose/start)` 验证交互管道，不验证视觉）。旧「历史足迹 + 实时节点 + 心愿单」视图 4 用例（渲染历史节点/出门探索 POST/409 忙碌/加心愿）随组件重写删除。

## frontend-scroll-area（书卷区域：ScrollArea 对话主舞台）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `ScrollArea > 渲染对话主舞台：消息列表 + 遭遇卡片，无模式切换按钮` | 功能正确 | 渲染 `.message-list` 容器；无未决遭遇时 `.encounter-card` 为 null；「记忆/笔记/对话」按钮已移除（`queryByText` 均 null） |

**功能阶段**：「多入口收敛」轮编写（原「三模式切换」测试随 ScrollArea 从 对话/记忆/笔记 三模式收敛为单一对话主舞台而重写：记忆/笔记移入左面板 InnerWorld 入口，ScrollArea 不再有模式切换按钮；RTL + 真实 store，验证渲染不崩 + 无模式按钮，不验证视觉）。

## frontend-right-dock（右侧底部工具条：聊天 / 内在 / 空间 / 记录 / 出门 / 游戏设置）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `RightDock > 渲染聊天 / 内在 / 空间 / 记录 / 出门 / 游戏设置六个入口` | 功能正确 | 六个按钮「聊天/内在/空间/记录/出门/游戏设置」均上屏 |
| `RightDock > 当前视图对应入口高亮（aria-pressed）` | 功能正确 | `view=null` 时「聊天」`aria-pressed=true`；`view=0/1/2/"explore"/"settings"` 时「内在/空间/记录/出门/游戏设置」各自 `true` 且「聊天」false |
| `RightDock > 点入口触发 onSwitch 对应视图` | 功能正确 | `onSwitch` 依次被调 `null`、`0`、`1`、`2`、`"explore"`、`"settings"` |

**功能阶段**：「三区布局再收敛」轮编写（左面板瘦身为 心情/精力 + 她现在的念头 + 正在做什么；空间/内心世界总览/游戏设置迁到右侧底部工具条 `RightDock`；`InnerWorld` 加 `OVERVIEW_INDEX(-1)` 全分类总览模式——面板顶部三大类导航 + 点大类切子标签。RTL + 真实 store，验证入口传参正确 + 总览导航切换管道，不验证视觉）。「替换式切视图」轮：`RightDock` 的「游戏设置」从折叠字体条改为切视图入口（`onOpenSettings`），字体大小迁至 `SettingsView`，第三条用例随之由「展开点大写 fontScale」改为「点游戏设置触发 onOpenSettings」。「底两条常驻」轮：`RightDock` 从三入口（`onOpenInner`/`onOpenSettings`）改为四入口导航条（统一 `onSwitch(view)` + `view` 高亮当前），新增「聊天」入口切回对话；用例由「三入口 + 传参」改为「四入口 + 高亮 + onSwitch 传参」。「词条收敛·六词条」轮：`RightDock` 词条由「聊天/空间/内心世界总览/游戏设置」改为**六词条**「聊天/内在/空间/记录/出门/游戏设置」——去掉 `OVERVIEW_INDEX`（内心世界总览删除），新增「内在(0)」「记录(2)」直达（左面板摘要与工具条同源分类）、「出门("explore")」挂载探索地图页内视图；用例由「四入口 + OVERVIEW_INDEX」改为「六入口 + onSwitch(null/0/1/2/"explore"/"settings")」，全分类总览两例随 `OVERVIEW_INDEX` 删除。

## frontend-settings-view（游戏设置页内面板：SettingsView 字体大小 + 背景外观）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `SettingsView > 渲染标题 / 字体大小三档 / 背景外观` | 功能正确 | 「游戏设置」标题、「字体大小」「背景」两个面板标题（heading）、「小/中/大」三按钮均上屏 |
| `SettingsView > 默认「中」激活，点「大」写 settingsStore.fontScale` | 功能正确 | 默认「中」`aria-pressed=true`；点「大」→ `fontScale==="large"` 且「大」`aria-pressed=true` |
| `SettingsView > 点预设色块「樱粉」写 settingsStore.tint` | 功能正确 | 点「樱粉」色块（aria-label）→ `settingsStore.tint === "#f7e8e0"` |

**功能阶段**：「替换式切视图」轮编写（把 `RightDock` 折叠字体条 + orphan `BackgroundPanel` 收进页内设置面板 `SettingsView`；复用 `Panel` + `BackgroundPanel` + `settingsStore`。RTL + 真实 store，验证字体档位/背景色调写 store + 返回对话接线，不验证视觉）。「底两条常驻」轮：`SettingsView` 移除 `onBack` prop 与「返回对话」按钮（切回聊天走工具条「聊天」），对应「返回对话」用例与首条中的「返回对话」断言删除。
