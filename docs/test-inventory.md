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

**功能阶段**：01-types 实现时编写。

## 02-config（配置加载）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_validate_config_accepts_defaults` | 功能正确 | `validate_config(Config())` 合法通过（不抛即通过） |
| `test_validate_config_rejects_invalid`（7 例） | 边界鲁棒 | 越界/非正/错类型改字段后 `validate_config` 报 `ConfigError` |
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

**功能阶段**：02-config 实现时编写。

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
| `test_complete_fields` | 功能正确 | `id`/`module`/`type`/`correlation_id`/`content`/`model` 正确回填进 `LLMOutput` |
| `test_complete_token_usage` | 功能正确 | `token_usage` 从 `usage_metadata` 抽取为 `{input, output}` |
| `test_complete_token_usage_missing` | 边界鲁棒 | `usage_metadata` 缺失 → `{input: 0, output: 0}` |
| `test_complete_json_mode_on` | 功能正确 | `json_mode=True` → 传给模型的 kwargs 含 `response_format={"type": "json_object"}` |
| `test_complete_json_mode_off` | 功能正确 | `json_mode=False` → kwargs 不含 `response_format` |
| `test_complete_messages_passthrough` | 功能正确 | `messages` 顺序与内容按原序透传为 LangChain 消息（fake 记录收到的消息） |
| `test_complete_non_text_content` | 边界鲁棒 | 非文本 content（`list`）→ `RuntimeError`（非 `str(list)` repr 垃圾） |
| `test_from_config_rejects_other_provider` | 边界鲁棒 | `provider="claude"` → `ConfigError` |
| `test_from_config_rejects_missing_api_key` | 边界鲁棒 | `api_key_env` 未设（`delenv`）→ `ConfigError` |
| `test_from_config_ok` | 功能正确 | 正常 → 返回 `LlmClient` 且 `_model_name == config.model` |

**功能阶段**：03-llm 实现时编写。

## 04-db（SQLite 连接 + 建表 + 迁移）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_migrate_creates_all_tables` | 功能正确 | `sqlite_master` 含硬编码 13 张业务表 + `schema_version`，共 14 张 |
| `test_migrate_creates_three_indexes` | 功能正确 | 显式索引（`sql IS NOT NULL`）恰为 `idx_memory_tag` / `idx_memory_type` / `idx_event_log_corr` 三个 |
| `test_migrate_sets_version_to_max` | 功能正确 | `schema_version` 单行 = `_MIGRATIONS` 最高版本 |
| `test_migrate_not_null_alignment` | 边界鲁棒 | 6 列 `notnull=1`（`memory.aspect` / `long_term_desire.linked_values` / `activity.progress` / `event_log.content` / `event_log.correlation_id` / `eval_report.correlation_id`） |
| `test_migrate_nullable_alignment` | 边界鲁棒 | Optional 列 `notnull=0`（`short_term_desire.goal` / `activity.ended_at` / `token_usage.correlation_id` / `memory.embedding`） |
| `test_migrate_idempotent` | 回归保护 | 连跑两次不报错，表数不变、版本不变 |
| `test_migrate_version_gating` | 功能正确 | `monkeypatch` 追加 v2 后只套 v2，版本=2，v1 表不重复建 |
| `test_migrate_atomic_rollback` | 边界鲁棒 | 迁移含非法 SQL → 抛 `aiosqlite.Error`；`ok` 表回滚不存在；版本仍为 0 |
| `test_connect_returns_database` | 功能正确 | 返回 `Database`；文件创建；`journal_mode=wal`；`foreign_keys=1`；`row_factory` 生效（`row["x"]==1`）；`lock` 是 `asyncio.Lock` |
| `test_connect_explicit_path_priority` | 功能正确 | 显式 path 优先建该文件 |
| `test_connect_env_override` | 功能正确 | `path=None` 时 `NYX_DB` 环境变量覆盖默认 |
| `test_default_db_path_constant` | 功能正确 | `DEFAULT_DB_PATH == "nyx.db"` |
| `test_connect_closes_conn_on_migrate_failure` | 边界鲁棒 | 迁移失败 → `connect` 抛异常且连接被 `close`（spy 记录），不泄漏 |

**功能阶段**：04-db 实现时编写。

## 05-event（事件总线 + 路由）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_routing_keys_are_all_event_types_except_clock_tick` | 功能正确 | `set(ROUTING) == set(EventType) - {CLOCK_TICK}`（17 键） |
| `test_tick_routing_keys_are_all_tick_types` | 功能正确 | `set(TICK_ROUTING) == set(TickType)`（4 键） |
| `test_routing_values_are_known_modules` | 功能正确 | 所有路由值 ⊆ `{expression, inner_life, desire, activity}` |
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
| `test_persist_exception_propagates` | 边界鲁棒 | `_persist` 抛异常 → 传播、run 任务终止、`task_done()` 照走 |

**功能阶段**：05-event 实现时编写。
