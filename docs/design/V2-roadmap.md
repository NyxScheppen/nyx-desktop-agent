# V2 路线图（MVP 未实现的功能）

> 本文档汇总 **MVP 未实现、留待 V2** 的功能。
> MVP 文档（`../specs/` + `../tech-reference.md`）已删除这些功能的实现旁注，只保留 MVP 现状；`design.md` 保留完整愿景（含 §3.3 / §5.1 / §5.2 / §7.3 / §7.4 / §8.2 / §8.5 / §8.6 / §9.2 等未实现设计）。
> 每项标「愿景出处」（design.md 章节）——无 design 章节的标「—」。

## 表达（expression）

| 功能 | 愿景出处 | 说明 |
|---|---|---|
| `wait_user`（ask 后等用户回答） | design §5.2 | ask 后等待用户 → 回答则 round 重置 → 超时记「用户未回答」 |
| 搭话被忽略的回增 | design §5.2 | 搭话无回应 → 互动欲值回增 |
| 表达侧工具调用（`bind_tools`） | design §5.1 | think/speak 支持调用工具（需 03-llm `complete` 支持 `bind_tools`） |
| 语义相关性回溯检测 | design §5.1 | 回溯时按「时间隔太久 / 十分不相关」检测并终止 |

## 活动（activity）

| 功能 | 愿景出处 | 说明 |
|---|---|---|
| 活动恢复/续做 | design §3.3 | 打断后同一日程块内恢复，否则未完成可续 |
| 屏幕视觉（截屏+视觉模型） | design §8.5 | `classify_presence` 扩展视觉输入（现只覆盖键盘/鼠标/窗口三输入） |
| `goal.count` 精确计数 | design §7.4 | goal 达成按产出单位数精确判定（现只判「有无产出」） |
| 发呆 `{summary}` 回读 reflect 产出 | — | `IDLE_REFLECTION` 的 result 带回反思 summary |

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

---

## 非 V2（永久不做，保留在原文档）

> 这些是**设计边界**（不是「未来要做」），故不进本清单、保留在 ../how-security.md / 各 spec：
> - 端到端加密 / 用户认证·多账户 / 企业级审计日志（本地单用户应用）
> - eval 结果自动反馈修正（纯记录 + 可视化，不自动反馈修正）
> - 不建「计划」表（design §8.1 临时概念）
