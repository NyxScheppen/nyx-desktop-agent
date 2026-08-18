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
| `test_extract_usage_non_int_value` | 边界鲁棒 | 键值为非数字字符串（`input_tokens="abc"`）→ `{input: 0, ...}` 不抛（`_safe_int` 兜底，不 `int("abc")` 裸崩） |
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

**功能阶段**：03-llm 实现时编写；`test_extract_usage_non_int_value` 于第五轮 review 追加（`_safe_int` 防御非数字 token 值）。

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
| `test_register_and_schema_in_order` | 功能正确 | `register` 后 `schema()` 按注册序返回 `[{name, description, parameters}]` |
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
| `test_read` | 功能正确 | `read` 返回文件 content |
| `test_read_non_utf8_replaces` | 边界鲁棒 | 非法 UTF-8 字节 → `�` 替换（不崩溃、不静默丢字节） |
| `test_write` | 功能正确 | `write` 建文件在 `write_root` 内、返回 `written` |
| `test_write_escape_parent` | 边界鲁棒 | `../` 越界 → `ValueError` |
| `test_write_escape_absolute` | 边界鲁棒 | 绝对路径逃逸 `write_root` → `ValueError` |
| `test_write_empty_path` | 边界鲁棒 | 空路径解析到 `write_root` 本身 → `ValueError`（非 `IsADirectoryError` 裸崩） |
| `test_write_escape_symlink` | 边界鲁棒 | `write_root` 内 symlink 指向外部 → `ValueError`（无 symlink 权限环境 skip） |
| `test_list` | 功能正确 | `list` 返回目录条目名 |
| `test_unknown_action` | 边界鲁棒 | 未知 `action` → `ValueError` |

**功能阶段**：06-tools 实现时编写。

## 07-memory-store（记忆存取）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_add_get_roundtrip` | 功能正确 | `add` 多值 `aspect` + 非默认 `recall_count` + `embedding` → `get` 往返全等（`got == mem` 覆盖 aspect JSON / type 枚举 / freshness / embedding） |
| `test_add_get_embedding_none` | 边界鲁棒 | `embedding=None` → `get` 返回 `embedding is None`（SQL NULL 非 `"null"` 字符串） |
| `test_add_duplicate_id_raises` | 边界鲁棒 | 重复 `id` → `aiosqlite.IntegrityError` |
| `test_get_miss_returns_none` | 功能正确 | `get` 未命中 → `None` |
| `test_list_memories_filters_and_sorts` | 功能正确 | `tag` / `type` / 组合过滤 + `freshness DESC` 排序 |
| `test_update_fields` | 功能正确 | `update` 改各字段 → `get` 验证；`id` / `created_at` 不可变 |
| `test_update_many` | 功能正确 | `update_many` 批量改多条（含 `embedding=None` 与 `embedding=[...]`）→ `get` 逐条验证；空列表 no-op |
| `test_delete_cascades_edges` | 功能正确 | `delete` 级联删 `memory_edge`（from/to 双向），其它记忆边保留 |
| `test_delete_many` | 功能正确 | `delete_many` 批量删多条（含关联边）→ `get` 全部 `None`、`list_edges` 无残留；空列表 no-op |
| `test_record_recall_atomic` | 功能正确 | 未达阈值连调两次 → `recall_count==2` 且 type `SHORT_TERM`、返回 False；达阈值 → `LONG_TERM`、返回 True；已 `LONG_TERM` → 只递增、返回 False（加一+条件升型在单锁内原子完成） |
| `test_search_keyword` | 功能正确 | `content` / `summary` 命中、无命中 `[]`、ASCII 大小写不敏感 |
| `test_search_keyword_escapes_wildcards` | 边界鲁棒 | `%` / `_` 作字面量匹配（`ESCAPE '\'` 转义），不误命中通配符匹配 |
| `test_list_edges_and_upsert` | 功能正确 | `upsert_edge` 新建 + 同键重复 `ON CONFLICT` 改 `weight` 不重复建行 |
| `test_upsert_edge_unknown_id_raises` | 边界鲁棒 | `upsert_edge` 引用不存在 id → `IntegrityError`（FK 生效） |

**功能阶段**：07-memory-store 实现时编写；`test_record_recall_atomic` 于 09 评审修复阶段重写（中3：加一+条件升型原子化进 store 单锁）；`test_update_many` / `test_delete_many` 于第五轮 review 追加（批量写原语，衰减/淘汰 N 次 commit → 2 次）。

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
| `test_search_merge_order_and_limit` | 功能正确 | keyword→vector→association 编排：A（keyword+vector）、B（association 扩散）→ `[A,B]`；limit=1 → `[A]` |
| `test_search_dedup` | 功能正确 | keyword 与 vector 命中同一记忆 → 去重只一次 |
| `test_search_empty` | 功能正确 | 无命中 + embed=None + 无边 → `[]` |
| `test_search_blank_query_returns_empty` | 边界鲁棒 | `""`/`" "`/`"   "` 空/空白查询短路 → `[]`（`query.strip()`，不因 `LIKE '%%'`/`'% %'` 误返全量） |
| `test_search_no_edge_no_crash` | 边界鲁棒 | keyword 命中无边记忆 → 不抛 `NetworkXError`（`neighbors` 过滤），返回命中本身 |

**功能阶段**：08-memory-retrieval 实现时编写；`test_rank_by_cosine` 于 09 评审修复阶段新增（跨模块去重：抽 `rank_by_cosine` 供 facade 复用）。

## 15-eval（三层评分 + token 记账）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_validate_structure` | 功能正确 | 空 `""` / 纯空白 `"   "` / 超长（`_MAX_CONTENT_LEN+1`）→ 0.0；正常 → 1.0 |
| `test_ooc_score` | 功能正确 | 无命中默认 1.0；黑 1 → 0.5；黑 2 → 0.0；黑 3 → 0.0（封顶）；黑 1 白 1 → 1.0（抵消）；白 2 → 1.0（封顶不越界） |
| `test_should_judge` | 功能正确 | `judge` 输出不递归（False）；`roll < sample_rate` 命中/未命中各一例 |
| `test_judge_relevance_returns_score` | 功能正确 | fake 返回 `{"score":4}` → `4.0`；judge 调用 `type=="judge"`、`module=="eval"`、`correlation_id` 透传 |
| `test_judge_relevance_tolerates_bad_json` | 边界鲁棒 | `[`（解析失败）/ `[]`（非 dict）/ `{"score":"abc"}`（非数字）→ 容错 0.0 不 raise |
| `test_judge_relevance_clamps` | 边界鲁棒 | `{"score":100}`→5.0、`{"score":0.5}`→1.0、`{"score":4}`→4.0（clamp [1,5]） |
| `test_judge_relevance_transport_failure` | 边界鲁棒 | fake `complete` 抛异常 → `(0.0, None)` 不 raise（judge 传输失败无产出不记账） |
| `test_judge_relevance_rejects_bool_score` | 边界鲁棒 | `{"score": true}` → 0.0 且 judge_output 非 None（堵 `float(True)==1.0` 的坑） |
| `test_judge_relevance_overflow` | 边界鲁棒 | `{"score": 10**400}`（超大 int）→ 0.0 且 judge_output 非 None（`float()` 溢出不漏出崩 evaluate） |
| `test_evaluate_sampled` | 功能正确 | `judge_sample_rate=1.0` → `relevance==4.0`、1 条 eval_report、2 条 token_usage（judge + 原 output） |
| `test_evaluate_not_sampled` | 功能正确 | `judge_sample_rate=0.0` → `relevance==0.0`、1 条 token_usage（仅原 output） |
| `test_evaluate_judge_transport_failure` | 边界鲁棒 | `judge_sample_rate=1.0` 但 fake `complete` 抛异常 → `relevance==0.0`、仅 1 条 token_usage（judge 无产出不记账），evaluate 不 raise |
| `test_evaluate_persists` | 功能正确 | 落库后重开连接 → `list_reports`/`list_token_usage` 仍各 1 条（持久化往返） |
| `test_list_reports_roundtrip` | 功能正确 | 两条 report；`token_usage` JSON 往返 `{input,output}`；`scores.format` 为 0.0/1.0 |
| `test_list_token_usage_since` | 功能正确 | `since=最新 created_at` → 1 条；`since=+1` → 0 条（`>=` 边界） |

**功能阶段**：15-eval 实现时编写（先于 09-facade，因 09 依赖 Evaluator）；`test_judge_relevance_transport_failure` / `test_judge_relevance_rejects_bool_score` / `test_evaluate_judge_transport_failure` 于 09 评审修复阶段编写（高2：judge LLM 调用移进 try；低5：布尔 score 拒收）；`test_judge_relevance_overflow` 于 15 评审复核阶段编写（补 `float()` 溢出容错）。

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
| `test_search_delegates_to_retrieval` | 功能正确 | `search` 委托 fake `MemoryRetrieval`（返回预设 + 记录 query） |
| `test_list_memories_delegates` | 功能正确 | `list_memories(type=)` 委托真 store 过滤 |
| `test_record_recall_below_threshold` | 功能正确 | 未达阈值 → recall_count+1、type 仍 SHORT_TERM、无 `memory_promoted` |
| `test_record_recall_promotes` | 功能正确 | 达阈值 → type LONG_TERM + 发布 `memory_promoted` |
| `test_record_recall_long_term_no_repromote` | 功能正确 | 已 LONG_TERM → 只 recall_count+1，不重复发布 |
| `test_record_recall_concurrent_single_promote` | 回归保护 | `asyncio.gather` 并发两次 → `recall_count==2`、仅 1 条 `memory_promoted`（原子加一+条件升型不重复升级） |
| `test_export_json` | 功能正确 | `json.loads` 还原列表，`type` 为字符串、`embedding` 透传 |
| `test_export_md` | 功能正确 | 含某记忆的 summary 与 content |
| `test_export_unknown` | 边界鲁棒 | `csv` → `ValueError` |

**功能阶段**：09-memory-facade 实现时编写；`test_contradiction_parse_failure_no_crash` / `test_eviction_tie_break_oldest_first` / `test_record_recall_concurrent_single_promote` 于 09 评审修复阶段编写（高1：矛盾解析失败不再半提交；中4：淘汰平局按 created_at 升序；中3：并发 record_recall 只升一次）。

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
| `test_upsert_value_new_and_update` | 功能正确 | `upsert_value` 新建 → `list_values` 1 行；同 type 再 upsert 改 `value`/`updated_at`（ON CONFLICT 不重复建行） |
| `test_long_term_roundtrip_and_update` | 功能正确 | `subtopics`/`linked_values` JSON 数组往返、`type` 枚举往返；`update_long_term` 改 `progress`/`strength` |
| `test_parse_desire` | 边界鲁棒 | 合法 JSON→`(description, Goal)`；`goal:null`→`None`；缺/空 description、`goal.action` 非法、`count` 非正/非 int、`topic` 非 str、JSON 数组 → `ValueError`（7 例） |
| `test_topic_seed` | 功能正确 | `type` 匹配且 `subtopics` 非空 → `subtopics[0]`；无匹配 / 空 subtopics → `None` |
| `test_build_desire_prompt` | 功能正确 | 含类型 `.value` 与种子；`seed=None` → 含「（无）」 |
| `test_pressure_from_observation` | 功能正确 | 互动欲 `value` 0 → `+0.15`；`updated_at` 更新 |
| `test_run_eval_no_peak` | 功能正确 | 四类型都低于 `peak_threshold` → `[]`、无 LLM 调用 |
| `test_run_eval_generates_peak` | 功能正确 | 达峰 → 1 次 LLM（`output_type="desire"`）、`evaluator.evaluate` 1 次、返回 1 个（type/status/strength/description/goal 来自 fixture）、value 重置 0、发布 `desire_generated` |
| `test_run_eval_only_most_urgent` | 功能正确 | 互动 0.9 + 探索 0.85 都达峰 → 只生成互动；探索 `value` 保留 0.85 不重置 |
| `test_run_eval_long_term_pressure` | 功能正确 | 探索长期欲望 → 探索 `value` 额外 `+0.2`（0.5→0.7） |
| `test_run_eval_decay` | 功能正确 | `updated_at` 1 天前 → `value` 衰减 `value_decay × 1`（0.5→0.48） |
| `test_run_eval_suppression_gate` | 功能正确 | 达峰但 `suppression_threshold > value` → 不生成、返回 `[]` |
| `test_run_eval_topic_seed` | 功能正确 | 探索长期 `subtopics=["骑士团"]` → LLM prompt 含「骑士团」 |
| `test_run_eval_llm_invalid_json_skips` | 边界鲁棒 | 非法 JSON → `_parse_desire` 抛 `ValueError` → 返回 `[]`、目标 `value` 不重置、无欲望入队 |
| `test_run_eval_evaluator_error_propagates` | 回归保护 | evaluator 抛 `RuntimeError` → 不被 `except ValueError` 吞、上抛给 supervisor（不掩蔽真 bug） |
| `test_satisfy_goal_met` | 功能正确 | `SATISFIED`、表达权重 `+0.05`、长期进度 `+0.1`、发布 `desire_satisfied` |
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

**功能阶段**：11-desire 实现时编写（LLM 全 mock、DB `:memory:`、事件经真实 `EventBus` + recording handler；无集成/E2E，与 activity/expression 真实编排归 13/14/17）。

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

**功能阶段**：12-inner-life 实现时编写（LLM 全 mock、DB `:memory:`、事件经真实 `EventBus` + recording handler；`ActivityFacade` 用向前引用 stub/fake、真实编排归 13/14/18）；`test_parse_reflection_unknown_drift_key` / `test_parse_reflection_drops_bad_candidate` / `test_run_survives_bad_candidate` 于 12 评审修复阶段编写（坏候选 best-effort 跳过 + 漂移 key 白名单校验），`internal_event`/时间常量抽到 events/event.py 后的共享测试见 05-event。

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
| `test_day_start` | 功能正确 | `now=86400*1.5 → 86400.0` |
| `test_elapsed_hours` | 功能正确 | `now=5400 → 1.5` |
| `test_goal_met` | 功能正确 | goal None → None；goal 非 None + result 空 → False；result 非空 → True |
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
| `test_execute_failure_marks_incomplete` | 回归保护 | LLM 抛异常 → 活动标 INCOMPLETE + `ended_at` 非空（不卡 RUNNING） |
| `test_upgrade_to_free_exploration` | 功能正确 | 探索欲 + 精力足 + 频率过 → FREE_EXPLORATION |
| `test_no_upgrade_when_rate_limited` | 功能正确 | 频率未过 → 降级 READING |
| `test_complete_activity` | 功能正确 | COMPLETED + `ended_at` 非空 + activity_end（energy_delta=-20） |
| `test_interrupt_running` | 功能正确 | ABANDONED + activity_interrupted（`by=user_message`） |
| `test_interrupt_abandons_in_flight_activity` | 回归保护 | 执行中活动挂起可取消 await 时 interrupt → 终态 ABANDONED 而非被 complete 覆盖 |
| `test_interrupt_missing` | 边界鲁棒 | 不存在 → 不发布、不崩溃 |
| `test_get_current_delegates` | 功能正确 | `get_current` 委托 store |
| `test_get_schedule_delegates` | 功能正确 | `get_schedule` 委托 store（按 `_day_start` 过滤） |
| `test_should_explore_energy_too_low` | 功能正确 | energy=59 → False |
| `test_should_explore_rate_limited` | 功能正确 | energy=60 + `now-last < rate_limit_hours*3600` → False |
| `test_should_explore_ok` | 功能正确 | energy=60 + 频率过 + last=0.0 → True |
| `test_exploration_run_no_web` | 功能正确 | `run` 返回 `{findings, notes}`、步数 == `_MAX_STEPS`、`correlation_id` 传递、每次 complete 后 evaluate、图不含 web_search |
| `test_exploration_run_web` | 功能正确 | `web_enabled=True` → 图含 search_web、`web_search` 被调用（`_route` 可达） |
| `test_exploration_plan_non_dict_raises` | 边界鲁棒 | 规划 JSON 顶层非 dict（数组）→ `ValueError`（fail-fast） |
| `test_classify_presence_online` | 功能正确 | 键盘/鼠标活跃 → online |
| `test_classify_presence_busy` | 功能正确 | 无输入 + 有窗口标题 → busy |
| `test_classify_presence_away` | 功能正确 | 无输入无标题 → away |

**功能阶段**：14-activity 实现时编写（LLM 全 mock、DB `:memory:`、事件经真实 `EventBus` + recording handler；`get_state`/desire/tools 全 fake 注入，无集成/E2E）；`test_execute_failure_marks_incomplete` / `test_exploration_run_web` / `test_maybe_start_skips_when_task_in_flight` 于 14 评审修复阶段编写（高1：执行失败落 INCOMPLETE + 收割异常；中：探索链 `_route` web 可达；高2：并发守卫闭合 TOCTOU）；`test_exploration_plan_non_dict_raises` 于本轮评审修复编写（`_plan_next` 结构校验 fail-fast，配合删除 `recall_memory` 死节点）。

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
| `test_slow_score_in_range` | 边界鲁棒 | 极端输入 `low < 0.5` / `high ≥ 0.5` 均在 [0,1]；时钟回拨 `last_slow_at > now` → 仍 ≥ 0 |
| `test_slow_score_factors` | 功能正确 | 五因子各生效：长>短、含「吗」>不含、含「难过」>不含、精力足平静>精力低激动、距上次大>小 |
| `test_classify_channel` | 功能正确 | `threshold=0.5`：得分 ≥ 0.5（`在吗`+精力满+2h）→ SLOW；< 0.5（`哦`+精力20+arousal0.9+60s）→ FAST |

**功能阶段**：16-expression-prompt 实现时编写（纯函数，无 DB、无 async、无 fake LLM；`CurrentState`/`Memory`/`Message`/`SelfNarrative`/`ShortTermDesire` 全手构，无集成/E2E）。

## 17-expression（回复流程 + 碎碎念 + 搭话）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_templates_len_50_and_unique` | 功能正确 | `len(_MUTTER_TEMPLATES) == 50` 且 `len(set(...)) == 50`（无重复） |
| `test_pick_mutter_out_of_range` | 边界鲁棒 | `roll<0` / `roll>=1.0` → `None`（不触发） |
| `test_pick_mutter_bounds` | 功能正确 | `roll=0.0` → 第 0 条；`roll=0.999` → 最后一条 |
| `test_pick_mutter_membership` | 功能正确 | 返回值 ∈ `_MUTTER_TEMPLATES` |
| `test_should_initiate_chat_all_true` | 功能正确 | 互动欲 + 在线 + 不忙 + 精力够 + 间隔够 → `True` |
| `test_should_initiate_chat_each_condition` | 边界鲁棒 | 五条件逐项置反（无互动欲/离线/忙/精力 49/间隔 1000）→ `False` |
| `test_is_question` | 功能正确 | `"你今天好吗？"`/`"你今天怎么样"` → True（含「怎么」）；`"我很好。"` → False |
| `test_backtrack_short` | 功能正确 | history 短于 `max_len` → 全取 |
| `test_backtrack_long` | 功能正确 | 长于 `max_len` → 只取最后 `max_len` 条 |
| `test_backtrack_empty` | 边界鲁棒 | 空 history → `[]` |
| `test_rounds_block_empty` | 边界鲁棒 | `([], [])` → `""` |
| `test_rounds_block_single` | 功能正确 | 含「第1轮内心：t1」「第1轮对外：s1」 |
| `test_rounds_block_two` | 功能正确 | 两轮顺序正确（t1 < s1 < t2 < s2） |
| `test_reply_fast` | 功能正确 | 快通道：complete×2（think+speak）、evaluate×2、`search`/`create_scene_memory` 未调、publish `[THINK, SPEAK]` |
| `test_reply_slow_non_question` | 功能正确 | 慢通道非问句：complete×6、publish `[THINK, SPEAK]×3`、`search=1`、`create_scene_memory=1`、`nyx_think`/`nyx_speak` 3 轮 `"\n"` 拼接 |
| `test_reply_slow_question` | 功能正确 | 慢通道问句：publish `[THINK, ASK]`（非 SPEAK）、`create_scene_memory` 仍调、提前结束（think/speak 各 1） |
| `test_cumulative_prompt` | 功能正确 | 第 2 轮 think user prompt 含第 1 轮 think/speak 文本；第 2 轮 speak 含第 2 轮 think 文本 |
| `test_current_message_not_duplicated` | 回归保护 | `[对话历史]` 段不含当前消息、`[本次消息]` 含且仅一次 |
| `test_history_order` | 功能正确 | 两次 reply 后 history 为 `[user, nyx, user, nyx]`；第二次 assemble 回溯含 user1/nyx1、不含 user2 |
| `test_mutter_skips_when_busy` | 功能正确 | `current_activity` 非 None → 不发 |
| `test_mutter_hit` | 功能正确 | `random.random()` 命中 → 发 `mutter`（content 来自模板、correlation 透传） |
| `test_mutter_miss` | 功能正确 | `random.random()` 未命中 → 不发 |
| `test_initiate_chat_empty` | 边界鲁棒 | 空 content → `False` 且不发 |
| `test_initiate_chat_non_empty` | 功能正确 | 非空 → `True` 且发 `initiate_chat`（output_type/correlation 一致） |

**功能阶段**：17-expression 实现时编写（mutter/pipeline 纯函数无 DB 无 async；facade 集成 fake LLM/memory/desire/inner_life/evaluator/bus 注入，`cast()` 注入不碰真实 db；无集成/E2E，与 18-api 组合根的编排归 18）。

## 18-api（组合根 + REST + SSE）

| 测试 | 检查方向 | 断言内容 |
|---|---|---|
| `test_root_event_defaults_external` | 功能正确 | `id == correlation_id`、默认 `source is EXTERNAL`、`type`/`content` 原样透传、`timestamp` 非空 |
| `test_root_event_explicit_internal` | 功能正确 | 显式 `source=Source.INTERNAL` → `source is INTERNAL` |
| `test_load_canon_merges_three_files` | 功能正确 | 三份 canon 按序合并（`"\n\n"` 分隔） |
| `test_load_canon_missing_file_fails` | 边界鲁棒 | 缺一份 → `FileNotFoundError`（fail-fast） |
| `test_seed_inner_life_idempotent` | 功能正确 | 空表 seed 四表（personality/values/energy/narrative）值 = canon §2/§3 初始值；再跑一遍值不变、不重复行 |
| `test_seed_desire_idempotent` | 功能正确 | `list_values()` 四类型、`list_long_term()` 3 条；再跑幂等（4 行 / 3 条不增） |
| `test_build_tools_web_disabled` | 功能正确 | `web_enabled=False` → `{local_search, file_io}`（工厂构造无 I/O，`roots`/`DDGS` 惰性到 `.call()`） |
| `test_build_tools_web_enabled` | 功能正确 | `web_enabled=True` → 多 `web_search` |
| `test_state_endpoint` | 功能正确 | `GET /api/state` → `CurrentState` JSON，枚举字段为 `.value` 字符串（`emotion=neutral`、`energy_state=okay`） |
| `test_chat_endpoint` | 功能正确 | `POST /api/chat` → `{event_id}`；bus 收一条 `USER_MESSAGE`（source EXTERNAL、`correlation_id == id`） |
| `test_memories_endpoint` | 功能正确 | `GET /api/memories?tag=&type=` → `Memory[]`；`type` query 转 `MemoryType` 枚举传入 facade |
| `test_observe_endpoint` | 功能正确 | `POST /api/observe` → `{event_id}`；bus 收 `OBSERVATION_STATE`（content `{presence}`）、`last_presence` 更新 |
| `test_export_endpoint` | 功能正确 | `POST /api/export` `json`/`md` 透传 `memory.export` 结果 |
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

**功能阶段**：18-api 实现时编写（fake 各 Facade 注入 + 真 `EventBus` + `:memory:`；`cast()` 注入不碰真实 db/LLM；无集成/E2E，与真实编排的边界即「订阅一致性」）；`test_chat_missing_message_returns_422` / `test_observe_invalid_presence_returns_422` 为首轮 review 追加（请求体 422）；`test_supervise_bus_breaks_after_max_failures` / `test_supervise_bus_resets_on_recovery` / `test_first_tick_starts_activity_not_mutter_or_chat` 为本轮 review 追加（监督器熔断 + 恢复重置 + 首个活动块启动即触发）；`test_supervise_bus_breaks_on_flapping` / `test_main_propagates_serve_failure` / `test_main_propagates_tick_failure` 于第三轮 review 追加（恢复信号改连续成功阈值防抖动假自愈 + main 竞速传播所有先完成者）。

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
| `dispatchEvent > emotion_update → innerLifeStore` | 功能正确 | 覆盖 `valence`/`arousal`/`emotion` 三字段（`current` 非 null 时） |
| `dispatchEvent > 未消费类型 → eventStore` | 功能正确 | `default` 兜底 `record`（`reflection` → `count+1`） |

**功能阶段**：frontend 01-sse 实现时编写（mock `EventSource` stub + 真实 store；验证管道正确——事件走对 store、字段零映射、坏帧跳过不崩，不验证视觉）；`dispatchEvent > user_message → chatStore` 于本轮 review 追加（Finding 1 回归：user_message 裸 `{message}` 曾致用户消息被 `typeof e.content` 拦截静默丢弃）。
