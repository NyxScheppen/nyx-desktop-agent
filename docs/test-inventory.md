# 测试覆盖索引

> 本文件是测试套件的轻量快照，不定义行为，不记录变更历史。
> 详细测试名、fixture 和断言以 `tests/` 源文件为准；契约以 `docs/specs/` 为准。
> 新增、删除测试或改变关键断言后，只更新本表的文件/覆盖范围和关键回归清单，不复制每条测试的说明。

## 当前快照

- 后端测试文件：66
- `pytest --collect-only -q`：1109 tests collected
- 最近一次全量验证：`1108 passed, 1 skipped`（本轮未采集语句覆盖率）
- 后端运行：`pytest -q`
- 前端测试目录：`frontend/tests/`
- 前端运行：`cd frontend; npm test`
- 最近一次前端全量验证：`23 files / 326 passed`。
- 桌面 launcher 回归：原子 lock 防止并发启动；重启会等待并接管旧 launcher/backend；排除 venv wrapper 父进程，避免启动器自杀；未知程序占用 8000 时不创建进程；前端只在后端 ready 后启动。
- 冻结后端生命周期：父管道 EOF 请求正常退出；daemon watcher 不阻塞服务失败后的关停。
- 前端测试覆盖通用 API、SSE、状态、聊天、阅读、设置和桌面 presence。
- SSE 回归：初次连接失败保持 `connecting`，同一连接恢复后转为 `open`，不把启动期失败误报为 `closed`。

## 后端覆盖

| 系统 | 测试目录 | 文件数 | 主要覆盖 |
|---|---|---:|---|
| 类型 | `tests/test_types/` | 2 | 枚举穷尽、实体默认值、LLM prompt TypedDict |
| 配置 | `tests/test_config/` | 1 | 默认值、嵌套配置、非法输入 |
| LLM | `tests/test_llm/` | 2 | 统一客户端、视觉客户端、token 元数据、最终 prompt 快照 |
| DB | `tests/test_db/` | 1 | 新旧默认路径兼容与父目录创建、迁移、活动历史/委派任务 FIFO 索引、书籍/材料原文记忆 checkpoint 列、可空性、旧共同浏览数据清理、eval prompt 表、事务回滚、关闭 |
| 事件总线 | `tests/test_event/` | 3 | durable admission、投递状态、重试、FIFO、SSE |
| API/运行时 | `tests/test_api/` | 7 | 组合根、REST、委派任务创建/列表及 404/409/422、活动结果过滤/有界分页参数、eval prompt 详情、订阅、tick、恢复重放、presence 原子提交、采样新旧顺序、异常输入与归来 |
| 工具 | `tests/test_tools/` | 5 | 文件沙箱、分块判等与同内容幂等写、搜索、工具注册和网络抓取 |
| 记忆 | `tests/test_memory/` | 7 | kind/topics、精确与语义去重、episode 保守去重、原文画像/类别归因/稳定来源 topic、同内容跨来源隔离、来源过滤先于排序、ANN、融合召回、联想图、Facade、通用实体事实抽取/类型与别名索引/极性/多值关系、有效期替代/幂等/冲突折叠/来源过滤/有界召回、观察窗口防伪造事实、知识批量抽取与降级、durable 活动记忆不持锁等待 embedding |
| 欲望 | `tests/test_desire/` | 4 | 值机制、加压、生成、类型/action 钉死、满足、重放 |
| 内在生命 | `tests/test_inner_life/` | 4 | 情感、精力、反思、事务回滚 |
| 活动 | `tests/test_activity/` | 13 | 本地自然日时间线、结果过滤/分页、排期、活动生命周期、委派任务 FIFO/低精力/打断恢复、网页任务 6000 字符 checkpoint、EPUB 与材料跨库模糊选材及分块不结算、无匹配搜索、空来源不沉淀、探索、观察、材料原文沉淀、本地探索读取真实文件、读书恢复、创作结果严格校验、主题/旧作参考、唯一文件名与注册表落盘 |
| 表达 | `tests/test_expression/` | 6 | prompt、快慢通道、回复、搭话、碎碎念、durable interaction attempt、读书提问短回复三层上下文、时间/对话锚点/归来消费 |
| 阅读 | `tests/test_reading/` | 7 | EPUB 受控结构/嵌套块语义/UTF-16 offset/脚本样式过滤、进度 CAS 与后台单调推进、活动阅读不触发陪读冲动、原文 6000 字符/超长段落/余量 flush、pending 恢复、来源内 Top 5 与书籍画像、笔记/划线/书签、整合和后台生命周期 |
| 评估 | `tests/test_eval/` | 4 | OOC、embedding、记账、token、prompt 去重持久化与损坏数据 |

## 前端覆盖

| 范围 | 测试目录 | 主要覆盖 |
|---|---|---|
| REST/SSE | `frontend/tests/api.test.ts`, `sse.test.ts` | 请求封装、委派任务端点与 `task_updated` 分发、创作过滤/分页参数、记忆搜索/事实快照端点、开发/打包共享 base URL、eval prompt 懒加载端点、取消信号、错误、事件解析 |
| 状态与交互 | `frontend/tests/stores.test.ts`, `presence.test.ts`, `activityResult.test.ts`, `activityPanel.test.tsx`, `creationPanel.test.tsx` | Zustand、委派网页/EPUB 表单与段落预览、任务状态/失败原因、普通进度冲突合并与显式重读回退、书签切书乱序回包隔离、创作分批追加与刷新竞态、创作正文折叠/加载更多、记忆搜索与事实图独立降级、eval prompt 逐行缓存/重试、历史/SSE 去重与因果排序、非法帧不改变等待/未读状态、活跃度、异常原生采样、请求超时/取消、网络结果不确定 |
| 聊天与设置 | `chatPanel.test.tsx`, `settingsView.test.tsx`, `evalPanel.test.tsx` | 用户输入、时间分隔、设置、评估面板展开详情与安全文本渲染 |
| 记忆面板 | `memoryPanel.test.tsx` | 关键词查询提交、事实图实体关系渲染、事实加载失败降级 |
| 内在状态与欲望 | `innerStatePanel.test.tsx`, `desiresPanel.test.tsx`, `labels.test.ts` | 状态显示、紧凑布局容器、过滤、枚举中文化 |
| 阅读 | `readerView.test.tsx`, `notePanel.test.tsx` | 真分页与长段可达、受控富文本、UTF-16 同段划线、重叠划线线性端点读取、跨段拒绝、划线查询/删除/定位、书签定位、普通笔记和章节交互 |
| 视觉与通用 UI | `avatar.test.tsx`, `app.test.tsx`, `rightDock.test.tsx`, `desktopWindow.test.ts`, `petShell.test.tsx`, `time.test.ts`, `useTypewriter.test.tsx` | 创作页导航入口、统一分钟时钟、休眠恢复校时、昼夜/头像、完整端与桌宠态拖动模式隔离、桌宠点击/拖动阈值与窗口层级、头像东西南北四项入口/内心摘要/可修改设置入口、状态气泡、桌宠陪读翻页边界、非法时间标签边界、打字机 |

## 关键回归清单

这些测试保护跨模块一致性和最容易回归的失败模式；完整列表直接在测试源码中搜索对应名称。

### 总线、事务与重放

- `test_publish_is_durable_before_return`
- `test_publish_failure_does_not_return_event_id`
- `test_handler_failure_retries_only_failed_consumer`
- `test_delivery_failure_is_recovered_after_expired_lease`
- `test_success_finalization_retries_without_blocking_consumer`
- `test_failure_finalization_retries_without_wedging_delivery`
- `test_effect_marker_skips_duplicate_handler_replay`
- `test_user_message_replay_skips_after_reply_event_exists`
- `test_attempt_insert_rolls_back_with_outer_transaction`
- `test_concurrent_reply_claims_only_claim_once`
- `test_close_rejects_new_events_and_closes_database`
- `test_supervise_bus_returns_when_bus_stops_normally`

### 跨模块状态一致性

- `test_activity_end_transaction_rolls_back_on_derived_event_failure`
- `test_start_activity_rolls_back_when_event_append_fails`
- `test_interrupt_activity_rolls_back_when_event_append_fails`
- `test_run_eval_rolls_back_desire_when_generated_event_append_fails`
- `test_activity_end_transaction_rolls_back_energy_and_emotion`
- `test_reflection_event_rolls_back_slow_variables_when_event_append_fails`
- `test_subscription_consistency`（含 durable consumer id 接线）
- `test_answer_waiting_releases_claim_when_desire_settlement_fails`
- `test_recover_stale_pending_abandons_and_releases_desire`
- `test_compat_apply_event_restores_emotion_when_append_fails`
- `test_get_state_settles_emotion_and_energy`
- `test_run_rejects_invalid_json_for_delivery_retry`
- `test_run_real_desire_facade_commits_without_nested_transaction`
- `test_run_embedding_failure_preserves_all_reflection_state`
- `test_run_commit_failure_rolls_back_long_term_and_slow_variables`
- `test_apply_snapshot_conflict_rolls_back_reflection`
- `test_integrate_revisit_publishes_reflection_event`
- `test_integrate_keeps_buffer_when_reflection_admission_fails`
- `test_record_recall_rolls_back_when_promoted_event_append_fails`

### 记忆、活动和用户路径

- `test_search_fuses_vector_keyword_and_limits_direct_then_association`
- `test_fact_store_replaces_old_valid_fact_and_keeps_history`
- `test_memory_facts_endpoint`、`memoryPanel.test.tsx`：事实快照 API、实体关系图与事实独立降级
- `test_fact_store_duplicate_is_idempotent`
- `test_observation_window_title_does_not_update_fact_graph`
- `test_remember_knowledge_batches_fact_extraction_and_maps_sources`
- `test_build_system_prompt_renders_facts_separately`
- `test_search_topic_association_limits_after_excluding_direct`
- `test_topic_association_sorts_shared_bucket_once`
- `test_ann_allowed_ids_filter_applies_before_candidate_limit`
- `test_persist_builds_ann_index_once_when_dedup_misses`
- `test_associate_depth_two_scores_and_excludes_seeds`
- `test_digest_source_block_updates_profile_and_attributes_fiction`
- `test_same_knowledge_from_two_books_keeps_both_source_scopes`
- `test_source_semantic_dedup_ranks_only_within_same_source`
- `test_search_filters_source_before_ranking`
- `test_each_chunk_persists_profile_and_source_before_advancing`
- `test_pending_digest_is_reused_without_second_extraction`
- `test_sediment_splits_long_paragraph_and_flushes_remainder`
- `test_sediment_reuses_pending_without_digest_call`
- `test_reading_question_reply_injects_context_on_fast_short_reply`
- `test_build_reply_context_contains_three_scoped_layers`（事实层使用正文而非主题 summary）
- `test_script_and_style_content_is_ignored`
- `test_nested_block_direct_text_preserves_document_order`
- `test_nested_block_tail_text_preserves_document_order`
- `test_source_knowledge_keeps_web_url_scope`
- `test_run_empty_source_skips_memory_digest`
- `test_migrate_adds_source_memory_state_columns`
- `test_assigned_task_does_not_preempt_running_activity`
- `test_web_task_sediments_every_6000_character_block`
- `test_interrupted_assigned_task_returns_to_queue`
- `test_maybe_start_reading_can_match_uploaded_epub`
- `test_best_reading_match_does_not_fall_back_to_latest`
- `test_advance_nyx_position_is_monotonic_and_preserves_user_state`
- `activityPanel.test.tsx`：网页/EPUB 委派、目标段前后预览、任务失败原因展示
- `stores.test.ts`：普通进度冲突保留服务端更靠前的 Nyx 位置，显式重读仍可回退
- kind-scoped exact/semantic dedup and humanized memory prompt regressions
- schema 19 clears legacy memory/edges and installs kind/topics indexes
- `test_resume_skips_committed_fragment`
- `test_resume_skips_finalized_note_and_knowledge`
- `test_parse_activity_result_requires_non_empty_strings`
- `test_creation_output_path_is_unique_per_activity`
- `test_creation_activity_injects_context`
- `test_write_same_content_does_not_rewrite`
- `test_same_content_comparison_reads_in_bounded_chunks`
- `test_write_replaces_non_utf8_content`
- `test_connect_creates_parent_directory`
- `test_connect_reuses_legacy_default_database`
- `test_reload_skips_missing_config_path`
- `test_summarize_injects_related_memories`
- `test_chat_endpoint`
- `test_check_reflect_triggers`

### 欲望系统重构

- `test_claim_for_activity_is_single_use`
- `test_trim_pending_keeps_high_expression_weight`
- `test_add_long_term_embed_error_is_strict`
- `test_add_long_term_normalized_name_duplicate_skips`
- `test_prepare_long_term_candidates_filters_before_capacity`
- `test_prepare_long_term_candidates_deduplicates_batch_semantically`
- `test_add_prepared_long_terms_rejects_snapshot_conflict`
- `test_apply_value_delta_preserves_concurrent_increments`
- `test_run_eval_reuses_saved_generation_after_commit_failure`
- `test_run_eval_same_tick_applies_periodic_pressure_once`
- `test_parse_desire_pins_goal_action_to_desire_type`

### 前端桌面采集

- `presence.test.ts`：Tauri 空闲毫秒/前台标题采样、三态边界、浏览器降级与时钟跳变、失败重试、single-flight A/B/A、采样乱序、Unicode 截断、超时/卸载取消和重连同步
- `readerView.test.tsx`：阅读位置和 Nyx 追赶/等待派生态展示、结构化标题/粗体/已有划线、当前书划线搜索、UTF-16 同段选区、跨段拒绝，以及划线/书签持久定位
- `stores.test.ts`：旧书书签请求成功或失败晚到时，均不得覆盖当前书状态
- `readerView.test.tsx`：重叠划线分段保持端点读取为线性次数，不使用耗时阈值

### Eval prompt 可观测

- `test_complete_captures_independent_prompt_messages`
- `test_prompt_round_trip_is_shared_by_call_id`
- `test_prompt_and_eval_record_insert_roll_back_together`
- `test_eval_prompt_legacy_and_missing`
- `test_eval_prompt_corruption_returns_controlled_error`
- `evalPanel.test.tsx`：展开懒加载、旧/空/加载/错误状态、Unicode/换行和 HTML 字面安全渲染

### 时间感知与离开归来

- `test_build_temporal_block_short_cross_midnight_does_not_exaggerate`
- `test_build_temporal_block_acceptance_scenario_and_quote_boundary`
- `test_last_dialogue_anchor_skips_current_and_incomplete_turns`
- `test_reply_recovers_overnight_dialogue_from_durable_log`
- `test_reply_fallback_releases_return_context`
- `test_return_is_consumed_when_normal_reply_precedes_failure`
- `test_stale_observation_cannot_overwrite_user_online`
- `test_observation_and_message_are_serialized_without_false_return`
- `test_clock_rollback_rebuilds_presence_baseline_without_return`
- `test_failed_clock_reset_preserves_existing_return`
- `test_nonfinite_json_observation_returns_validation_error`
- `test_non_utf8_window_title_is_rejected`
- `test_committed_expression_does_not_restore_return_on_announce_failure`
- `test_reply_cancelled_at_publish_return_does_not_restore_durable_claim`
- `test_transaction_cancelled_after_commit_does_not_restore_return`
- `test_committed_observation_cancellation_still_commits_snapshot`
- `test_mutter_publish_failure_does_not_suppress_retry`
- `test_old_user_message_cannot_overwrite_fresh_away`
- `test_new_absence_invalidates_pending_and_claimed_return`
- `test_old_return_is_not_described_as_just_returned`
- `test_return_context_is_not_rendered_for_away_user`
- `test_observe_rejects_adversarial_payload_without_mutation`
- `test_observe_admission_failure_preserves_presence_snapshot`
- `test_release_old_return_claim_does_not_overwrite_new_return`
- `test_user_message_marks_away_user_returned_before_reply`
- `test_first_user_message_only_establishes_presence_baseline`
- `test_sse_frame_uses_backend_event_timestamp`
- `app.test.tsx`：无 SSE/用户操作跨 06:00/22:00，根主题、顶栏时钟和 Avatar 同步

## 维护规则

- 测试行为不写入本表；需要查看断言时直接打开对应 `tests/` 文件。
- 测试文件移动或新增系统时，更新上面的文件数和覆盖表。
- 关键回归测试删除或重命名时，同步更新本表。
- 测试快照不是历史记录，不保留旧轮次、旧数量或已删除测试名。
- `docs/LessonsLearned.md` 记录可复用的失败模式；本文件只记录当前覆盖情况。
