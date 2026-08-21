# V2 路线图（MVP 未实现的功能）

> 本文档汇总 **MVP 未实现、留待 V2** 的功能。
> MVP 文档（`../specs/` + `../tech-reference.md`）已删除这些功能的实现旁注，只保留 MVP 现状；`design.md` 保留完整愿景（含 §3.3 / §5.1 / §5.2 / §7.3 / §7.4 / §8.2 / §8.5 / §8.6 / §9.2 等未实现设计）。
> 每项标「愿景出处」（design.md 章节，前端项标 frontend-design.md 章节）——无设计章节的标「—」；~~删除线~~ = 已落地（移出 V2）。

## 表达（expression）

| 功能 | 愿景出处 | 说明 |
|---|---|---|
| ~~`wait_user`（ask 后等用户回答）~~ ✅ 已落地 | design §5.2 | reply 后按 `result["ask"]` 置 `_waiting_user`；tick 心跳 `check_timeouts` 超时 → `memory.record_no_answer` 记「用户没回答」 |
| ~~搭话被忽略的回增~~ ✅ 已落地 | design §5.2 | `initiate_chat` 记待回应 desire；`check_timeouts` 超时 → `desire.expire`（值立即 +0.3 回灌） |
| 表达侧工具调用（`bind_tools`） | design §5.1 | think/speak 支持调用工具（需 03-llm `complete` 支持 `bind_tools`） |
| 语义相关性回溯检测 | design §5.1 | 回溯时按「时间隔太久 / 十分不相关」检测并终止 |

## 活动（activity）

| 功能 | 愿景出处 | 说明 |
|---|---|---|
| 活动恢复/续做（读书已落地） | design §3.3 | 读书打断置 `PAUSED` + `read_chars` 断点续读已落地（D）；「同日程块内恢复同一记录」「创作/探索 LLM 中途暂停」仍 V2 |
| 屏幕视觉（截屏+视觉模型） | design §8.5 | `classify_presence` 扩展视觉输入（现只覆盖键盘/鼠标/窗口三输入，窗口标题采 `document.title` 占位，真前台窗口标题等 src-tauri 落地换源） |
| ~~`goal.count` 精确计数~~ ✅ 已落地 | design §7.4 | C3：`_goal_met` 按单位精确判（read→completed / write→title+content / observe→presence）+ desire `goal_progress` 累计 count 次 |
| ~~发呆 `{summary}` 回读 reflect 产出~~ ✅ 已落地 | — | B2：`IDLE_REFLECTION` result 带回反思 summary（直接 await reflect，不再发 REFLECTION 事件） |

## 欲望（desire）

| 功能 | 愿景出处 | 说明 |
|---|---|---|
| 主题种子轮转（按「没做过/新鲜度最低」取） | design §7.3 | 需查记忆，现只取 `subtopics[0]` |
| 长期欲望「最相关」判定 | — | 满足时回写最匹配的长期欲望（现只回写第一个） |
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
| 多 provider（claude 等） | — | `from_config` 支持非 deepseek |

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
