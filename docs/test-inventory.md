# 测试覆盖索引

> 本文件是测试套件的轻量快照，不定义行为，不记录变更历史。
> 详细测试名、fixture 和断言以 `tests/` 源文件为准；契约以 `docs/specs/` 为准。
> 新增、删除测试或改变关键断言后，只更新本表的文件/覆盖范围和关键回归清单，不复制每条测试的说明。

## 当前快照

- 后端测试文件：63
- `pytest --collect-only -q`：873 tests collected
- 最近一次全量验证：`872 passed, 1 skipped`
- 后端运行：`pytest -q`
- 前端测试目录：`frontend/tests/`
- 前端运行：`cd frontend; npm test`
- 最近一次前端全量验证：`15 files / 211 passed`

## 后端覆盖

| 系统 | 测试目录 | 文件数 | 主要覆盖 |
|---|---|---:|---|
| 类型 | `tests/test_types/` | 2 | 枚举穷尽、实体默认值、TypedDict |
| 配置 | `tests/test_config/` | 1 | 默认值、嵌套配置、非法输入 |
| LLM | `tests/test_llm/` | 2 | 统一客户端、视觉客户端、token 元数据 |
| DB | `tests/test_db/` | 1 | 迁移、索引、可空性、事务回滚、关闭 |
| 事件总线 | `tests/test_event/` | 3 | durable admission、投递状态、重试、FIFO、SSE |
| API/运行时 | `tests/test_api/` | 5 | 组合根、REST、订阅、tick、恢复重放 |
| 工具 | `tests/test_tools/` | 5 | 文件、搜索、工具注册和网络抓取 |
| 记忆 | `tests/test_memory/` | 6 | kind/topics、精确与语义去重、episode 保守去重、ANN、融合召回、联想图、Facade |
| 欲望 | `tests/test_desire/` | 4 | 值机制、加压、生成、满足、重放 |
| 内在生命 | `tests/test_inner_life/` | 4 | 情感、精力、反思、事务回滚 |
| 活动 | `tests/test_activity/` | 13 | 排期、活动生命周期、探索、观察、读书恢复 |
| 表达 | `tests/test_expression/` | 6 | prompt、快慢通道、回复、搭话、碎碎念、durable interaction attempt |
| 阅读 | `tests/test_reading/` | 7 | EPUB、进度 CAS、冲动、笔记、整合和后台生命周期 |
| 评估 | `tests/test_eval/` | 4 | OOC、embedding、记账和 token |

## 前端覆盖

| 范围 | 测试目录 | 主要覆盖 |
|---|---|---|
| REST/SSE | `frontend/tests/api.test.ts`, `sse.test.ts` | 请求封装、错误、事件解析 |
| 状态与交互 | `frontend/tests/stores.test.ts`, `presence.test.ts`, `activityResult.test.ts` | Zustand、活跃度、活动产出 |
| 聊天与设置 | `chatPanel.test.tsx`, `settingsView.test.tsx`, `evalPanel.test.tsx` | 用户输入、设置、评估面板 |
| 内在状态与欲望 | `innerStatePanel.test.tsx`, `desiresPanel.test.tsx`, `labels.test.ts` | 状态显示、过滤、枚举中文化 |
| 阅读 | `readerView.test.tsx`, `notePanel.test.tsx` | 分页、笔记和章节交互 |
| 视觉与通用 UI | `avatar.test.tsx`, `useTypewriter.test.tsx` | 头像、打字机 |

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
- `test_integrate_revisit_publishes_reflection_event`
- `test_integrate_keeps_buffer_when_reflection_admission_fails`
- `test_record_recall_rolls_back_when_promoted_event_append_fails`

### 记忆、活动和用户路径

- `test_search_fuses_vector_keyword_and_limits_direct_then_association`
- `test_associate_depth_two_scores_and_excludes_seeds`
- kind-scoped exact/semantic dedup and humanized memory prompt regressions
- schema 19 clears legacy memory/edges and installs kind/topics indexes
- `test_resume_skips_committed_fragment`
- `test_resume_skips_finalized_note_and_knowledge`
- `test_summarize_injects_related_memories`
- `test_chat_endpoint`
- `test_check_reflect_triggers`

### 欲望系统重构

- `test_claim_for_activity_is_single_use`
- `test_trim_pending_keeps_high_expression_weight`
- `test_add_long_term_embed_error_is_strict`
- `test_add_long_term_normalized_name_duplicate_skips`
- `test_apply_value_delta_preserves_concurrent_increments`
- `test_run_eval_reuses_saved_generation_after_commit_failure`
- `test_run_eval_same_tick_applies_periodic_pressure_once`

### 前端桌面采集

- `presence.test.ts`：Tauri 原生输入/前台标题采样、三态判定与去重上报
- `readerView.test.tsx`：阅读位置和 Nyx 追赶/等待派生态展示

## 维护规则

- 测试行为不写入本表；需要查看断言时直接打开对应 `tests/` 文件。
- 测试文件移动或新增系统时，更新上面的文件数和覆盖表。
- 关键回归测试删除或重命名时，同步更新本表。
- 测试快照不是历史记录，不保留旧轮次、旧数量或已删除测试名。
- `docs/LessonsLearned.md` 记录可复用的失败模式；本文件只记录当前覆盖情况。
