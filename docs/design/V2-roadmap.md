# V2 路线图（MVP 未实现的功能）

> 本文档汇总 **MVP 未实现、留待 V2** 的功能。
> MVP 文档（`../specs/` + `../tech-reference.md`）已删除这些功能的实现旁注，只保留 MVP 现状；`design.md` 保留完整愿景（含 §3.3 / §5.1 / §5.2 / §7.3 / §7.4 / §8.2 / §8.5 / §8.6 / §9.2 等未实现设计）。
> 每项标「愿景出处」（design.md 章节，前端项标 frontend-design.md 章节）——无设计章节的标「—」；~~删除线~~ = 已落地（移出 V2）。

## 表达（expression）

| 功能 | 愿景出处 | 说明 |
|---|---|---|
| ~~`wait_user`（ask 后等用户回答）~~ ✅ 已落地 | design §5.2 | reply 后按 `result["ask"]` 置 `_waiting_user`；tick 心跳 `check_timeouts` 超时 → `memory.record_no_answer` 记「用户没回答」 |
| ~~搭话被忽略的回增~~ ✅ 已落地 | design §5.2 | `initiate_chat` 记待回应 desire；`check_timeouts` 超时 → `desire.expire`（值立即 +0.3 回灌） |
| ~~表达侧工具调用（`bind_tools`）~~ ✅ 已落地 | design §5.1 | 慢通道 `use_tools` 节点查资料（本地搜索/文件/联网搜索），结果拼进 think/speak prompt（03-llm `complete` 支持 `bind_tools`） |
| ~~语义相关性回溯检测~~ ✅ 已落地 | design §5.1 | 慢通道 `assemble` 调 `build_backtrack_context` 从新到旧截断：满 `max_context_len` / 相邻隔超 `context_time_gap` / 与当前消息零字符重叠（十分不相关，纯启发式）即停；快通道 Nyx 消息跳过继续往前 |

## 活动（activity）

| 功能 | 愿景出处 | 说明 |
|---|---|---|
| ~~活动恢复/续做~~ ✅ 已落地 | design §3.3 | 可续活动（读书/创作/探索）打断置 `PAUSED` 保留记录 + 欲望关联；同日程块内 `_maybe_start_activity` 恢复同一记录（读书从书库刷新 `read_chars` 续读，创作/探索重跑），跨块不恢复留档 |
| 屏幕视觉（截屏+视觉模型） | design §8.5 | `classify_presence` 扩展视觉输入（现只覆盖键盘/鼠标/窗口三输入，窗口标题采 `document.title` 占位，真前台窗口标题等 src-tauri 落地换源） |
| ~~`goal.count` 精确计数~~ ✅ 已落地 | design §7.4 | C3：`_goal_met` 按单位精确判（read→completed / write→title+content / observe→presence）+ desire `goal_progress` 累计 count 次 |
| ~~发呆 `{summary}` 回读 reflect 产出~~ ✅ 已落地 | — | B2：`IDLE_REFLECTION` result 带回反思 summary（直接 await reflect，不再发 REFLECTION 事件） |

## 欲望（desire）

| 功能 | 愿景出处 | 说明 |
|---|---|---|
| ~~主题种子轮转（按「没做过/新鲜度最低」取）~~ ✅ 已落地 | design §7.3 | `_pick_topic_seed` 查记忆 substring：没做过优先、都做过取新鲜度最低；`DesireFacade`/`DesireLifecycle` 注入 `list_memories` 回调 |
| ~~长期欲望「最相关」判定~~ ✅ 已落地 | — | 满足时 `_most_relevant_long_term` 按 `goal.topic` 双向 substring 命中 `subtopics` 者回写，否则第一个 type 匹配 |
| 欲望 `ACTIVE` / `SUPPRESSED` 状态流转 | — | 活动系统用 `ACTIVE` 标记「消费中」，`SUPPRESSED` 纳入流转 |

## eval

| 功能 | 愿景出处 | 说明 |
|---|---|---|
| embedding 相似度 OOC（第 2 档） | design §9.2 | 需 embedding 模型 + Nyx 基准语料 |

## 记忆（memory）

| 功能 | 愿景出处 | 说明 |
|---|---|---|
| per-result 来源标记 | — | `search()` 按层标注来源（`SearchMode` 单层调试） |

## LLM

| 功能 | 愿景出处 | 说明 |
|---|---|---|
| ~~多 provider（OpenAI 兼容映射）~~ ✅ 已落地 | — | `from_config` 用 `_resolve_base_url`：内置 deepseek/openai/ollama 映射 + 可选 `llm.base_url` 覆盖；claude 等非 OpenAI 兼容留后续 |

## 前端（frontend）

| 功能 | 愿景出处 | 说明 |
|---|---|---|
| 语音（TTS） | frontend-design §10 | 订阅 `SPEAK`/`ASK`/`MUTTER`/`INITIATE_CHAT` → GPT-SoVITS 本地推理服务朗读（`THINK` 内心独白不念）；纯消费端语音层，不进核心管道、不反向影响内在生命/记忆/欲望 |

---

## 非 V2（永久不做，保留在原文档）

> 这些是**设计边界**（不是「未来要做」），故不进本清单、保留在 ../how-security.md / 各 spec：
> - 端到端加密 / 用户认证·多账户 / 企业级审计日志（本地单用户应用）
> - eval 结果自动反馈修正（纯记录 + 可视化，不自动反馈修正）
> - 不建「计划」表（design §8.1 临时概念）
