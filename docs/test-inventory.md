# 测试覆盖索引

> 本文件是测试套件的轻量快照，不定义行为，不记录变更历史。
> 详细测试名、fixture 和断言以 `tests/` 源文件为准；契约以 `docs/specs/` 为准。
> 新增、删除测试或改变关键断言后，只更新本表的文件/覆盖范围和关键回归清单，不复制每条测试的说明。

## 当前快照

- 后端测试文件：71
- `pytest --collect-only -q`：1064 tests collected
- 最近一次全量验证：`1062 passed, 2 skipped`
- 后端运行：`pytest -q`
- 前端测试目录：`frontend/tests/`
- 前端运行：`cd frontend; npm test`
- 最近一次前端全量验证：`19 files / 309 passed`。
- 浏览转录回归：`stores.test.ts` 校验 browsing 输出去重、canonical ASK 抑制、选区及记忆引用。
- 浏览后端回归另覆盖 focus/summary 容量上限、eval 失败保持联想、记忆阶段复用 checkpoint
  和 heartbeat 数据库失败回收子任务、迟到认证页不撤销当前页；打包地址回归验证开发/生产共用 REST/SSE base。
- 浏览事件查询拒绝空类型集合及 1..100 之外的 limit，排序/过滤由总线接口拥有。
- 桌面 launcher 回归：后端/Tauri 共享 256-bit secret；8000 已占用时不创建进程；打包资源定位和 sidecar 构建产物；可选 Windows 本地 HTTPS mock IdP spike（临时 CA，默认跳过）。
- 冻结后端生命周期：父管道 EOF 请求正常退出；daemon watcher 不阻塞服务失败后的关停。
- Rust 单元：10 条；覆盖游戏窗口 ID/命令注册、后台枚举契约及既有浏览纯函数；三个 opt-in 桌面 spike，分别覆盖 ACL/DOM、本地 HTTPS mock IdP 与窄窗口激活浏览扩宽（不创建 child）；mock IdP 另覆盖 302 逐跳预检、私网拒绝与慢预检时 UI 响应。
- Windows 桌面 spike：ACL/DOM、HTTPS mock IdP、静态 main 窄窗扩宽均单独通过；真实远程 app/core/plugin invoke 拒绝、公开正文、非表单正文/选区及 password 零正文。
- `browser.test.tsx`：16 条覆盖导航失效/迟到结果、隐私暂停、token 失效清上下文、focus ID 重试、新操作 ID、显隐、普通浏览器降级及提问回复；旧发送完成不清新提问选择，未创建 child 时激活视图按 DPI 扩宽窗口；popup 许可和关闭不假定登录成功；记录按需读取、cursor 续页/防重叠、全量删除清空列表和 cursor。
- `gameCompanion.test.tsx`：durable checkpoint hydrate、旧 observation revision 丢弃、hydrate/SSE 竞态保护、`stale_choice` 重拉并保留提示、当前/废弃 game activity 恢复、迟到 session-start 丢弃、生命周期 expected revision、结束后禁止选择、陪玩面板对白/阶段/选项显示与选择确认、native companion window 打开及错误码映射。
- 浏览 metadata store/API 覆盖有界 cursor 分页与非法 cursor；runtime 覆盖导航/revoke/close 在消息消费前使上下文失效时只发明确 fallback，重放不再调用 reply。
- REST 另覆盖 page/reply_to 转发、浏览 metadata/retry、单页与全量 204 DELETE。
- 浏览提问回复：展示事件的 attempt_id 进入回复选择，ChatInput 转发 reply_to 与当前 page id。

## 后端覆盖

| 系统 | 测试目录 | 文件数 | 主要覆盖 |
|---|---|---:|---|
| 类型 | `tests/test_types/` | 2 | 枚举穷尽、实体默认值、LLM prompt TypedDict |
| 配置 | `tests/test_config/` | 1 | 默认值、嵌套配置、非法输入 |
| LLM | `tests/test_llm/` | 2 | 统一客户端、视觉客户端、token 元数据、最终 prompt 快照 |
| DB | `tests/test_db/` | 1 | 迁移、索引、可空性、eval prompt 表、事务回滚、关闭 |
| 事件总线 | `tests/test_event/` | 3 | durable admission、投递状态、重试、FIFO、SSE |
| API/运行时 | `tests/test_api/` | 7 | 组合根、REST、游戏陪玩 frame bridge 身份/revision/body guard、浏览 bridge/本机 guard/CORS/PNA/body 上限、eval prompt 详情、订阅、tick、恢复重放、presence 原子提交、采样新旧顺序、异常输入与归来 |
| 工具 | `tests/test_tools/` | 5 | 文件、搜索、工具注册和网络抓取 |
| 记忆 | `tests/test_memory/` | 6 | kind/topics、精确与语义去重、episode 保守去重、ANN、融合召回、联想图、Facade、durable 活动/游戏记忆不持锁等待 embedding |
| 欲望 | `tests/test_desire/` | 4 | 值机制、加压、生成、满足、重放 |
| 内在生命 | `tests/test_inner_life/` | 4 | 情感、精力、反思、事务回滚 |
| 活动 | `tests/test_activity/` | 13 | 排期、活动生命周期、探索、观察、读书恢复 |
| 表达 | `tests/test_expression/` | 6 | prompt、快慢通道、回复、搭话、碎碎念、durable interaction attempt、时间/对话锚点/归来消费 |
| 阅读 | `tests/test_reading/` | 7 | EPUB、进度 CAS、冲动、笔记、整合和后台生命周期 |
| 共同浏览 | `tests/test_browsing/` | 4 | checkpoint、封口、fencing、启动恢复、授权污点、companion 幂等、配对 token、结构校验、close→worker→长期记忆、容量压力清理及桌面 launcher；API `test_browsing_api.py` 覆盖 bridge、精确 Host/Origin/媒介、CORS/PNA、声明长度与无长度流式 body 上限、multipart 快照，既有 API fixture 使用真实 loopback Host；相邻套件覆盖总线/记忆/提问事务、枚举及 DB 表快照 |
| 游戏陪玩 | `tests/test_game_companion/`、`tests/test_api/test_game_companion_api.py` | 3 | transient bridge PNG 校验、懒加载 RapidOCR adapter、OCR/crop 预算、frame→tentative/accepted observation 接线、越界框硬拒绝、全局视觉隐私开关、窗口 identity、OCR unavailable 明确 rejected、OCR timeout worker gate、同 session frame single-flight、重复 accepted durable revision、observation 超限 413、checkpoint CAS 并发幂等、幂等索引有界、canonical observation hash、score/evidence 闸门、session/observation/choice/correction 事务与幂等、frame bridge 完整窗口 identity/revision/body 上限 |
| 评估 | `tests/test_eval/` | 4 | OOC、embedding、记账、token、prompt 去重持久化与损坏数据 |

## 前端覆盖

| 范围 | 测试目录 | 主要覆盖 |
|---|---|---|
| REST/SSE | `frontend/tests/api.test.ts`, `sse.test.ts` | 请求封装、开发/打包共享 base URL、eval prompt 懒加载端点、取消信号、错误、事件解析 |
| 状态与交互 | `frontend/tests/stores.test.ts`, `presence.test.ts`, `activityResult.test.ts` | Zustand、eval prompt 逐行缓存/重试、历史/SSE 去重与因果排序、非法帧不改变等待/未读状态、活跃度、异常原生采样、请求超时/取消、网络结果不确定、活动产出 |
| 聊天与设置 | `chatPanel.test.tsx`, `settingsView.test.tsx`, `evalPanel.test.tsx` | 用户输入、时间分隔、设置、评估面板展开详情与安全文本渲染 |
| 内在状态与欲望 | `innerStatePanel.test.tsx`, `desiresPanel.test.tsx`, `labels.test.ts` | 状态显示、过滤、枚举中文化 |
| 阅读 | `readerView.test.tsx`, `notePanel.test.tsx` | 分页、笔记和章节交互 |
| 共同浏览 | `browser.test.tsx` | 宿主状态与 CAS、隐私/token 暂停、focus 幂等 ID、native 显隐、web 降级、提问 attempt 回复 |
| 游戏陪玩 | `gameCompanion.test.tsx` | checkpoint hydrate、SSE 旧 revision 丢弃与竞态恢复、`stale_choice` 重拉、活动状态过滤、迟到 session-start、生命周期 expected revision、结束后禁止选择、对白/阶段/选项显示与选择确认 |
| 视觉与通用 UI | `avatar.test.tsx`, `app.test.tsx`, `time.test.ts`, `useTypewriter.test.tsx` | 统一分钟时钟、休眠恢复校时、昼夜/头像、非法时间标签边界、打字机 |

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
- `test_search_topic_association_limits_after_excluding_direct`
- `test_topic_association_sorts_shared_bucket_once`
- `test_ann_allowed_ids_filter_applies_before_candidate_limit`
- `test_persist_builds_ann_index_once_when_dedup_misses`
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
- `test_prepare_long_term_candidates_filters_before_capacity`
- `test_prepare_long_term_candidates_deduplicates_batch_semantically`
- `test_add_prepared_long_terms_rejects_snapshot_conflict`
- `test_apply_value_delta_preserves_concurrent_increments`
- `test_run_eval_reuses_saved_generation_after_commit_failure`
- `test_run_eval_same_tick_applies_periodic_pressure_once`

### 前端桌面采集

- `presence.test.ts`：Tauri 空闲毫秒/前台标题采样、三态边界、浏览器降级与时钟跳变、失败重试、single-flight A/B/A、采样乱序、Unicode 截断、超时/卸载取消和重连同步
- `readerView.test.tsx`：阅读位置和 Nyx 追赶/等待派生态展示

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
