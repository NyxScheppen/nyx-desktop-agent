# V3 预备路线图（V2 之后的功能）

> 本文档汇总 **V2 完成之后**才排期的功能（backlog），只记方向与关键接入点，不做详细实现旁注。
> V2 项见 [`V2-roadmap.md`](./V2-roadmap.md)；~~删除线~~ = 已落地（移出 V3）。
> 每项标「愿景出处」（design.md 章节）——无设计章节的标「—」。

## 读书/创作借鉴（参考 nyx_desktop_agent）

> 参考项目 `nyx_desktop_agent` 的「读书 → 知识点入记忆」闭环与「创作 → 风格/知识库/屏幕灵感」增强，补齐 Nyx 已读真实文件、但缺知识点沉淀与创作上下文的短板。不借鉴：参考项目的 LLM 日历日程生成器（与「欲望驱动」正交）、分块读文件机制（当前已有）。

| 功能 | 愿景出处 | 说明 |
|---|---|---|
| ~~R1 读书提取知识点入记忆~~ | — | 读完整本那一刻 1 次 LLM 提取 1-5 个客观知识点，`tag="knowledge"` 落长期记忆（复用 `memory` 表，不新建表）；best-effort，提取失败不反噬读书笔记落盘 |
| ~~W1 创作风格随机池~~ | — | `_CREATION_STYLES =（日记体/随笔/微型小说/散文诗/书信体/观察笔记）`，`_pick_creation_style()` 随机抽 |
| ~~W2 创作注入知识库参考~~ | — | 创作时 `list_memories(tag="knowledge")` 取知识点拼进上下文 |
| ~~W3 创作注入观察/屏幕灵感~~ | — | 创作时折入 `_get_observation()`（presence/window_title/screen_summary） |

**关键点**：
- ~~已落地~~：R1/W1/W2/W3 完成——后端 `memory/facade.py` remember_knowledge + `activity/facade.py` 创作上下文注入。✅
- 反冗余：R1 复用 `memory` 表 + `_persist_memory` 入库尾段，不另写写路径；创作上下文复用 `_run_llm_activity` 的 `extra_context` 通道（加 `context_label` 参数），不新增注入回调。

## 文档债（设计文档缺口）

| 缺口 | 说明 |
|---|---|
| 02-config 缺 `VisionConfig` 分段 | `config.py` 已有 `VisionConfig`（`enabled`/`provider`/`model`/`base_url`/`interval_seconds` + `api_key_env`），但 `02-config.md` 的「分段清单」仍只列 7 段 + `ActivityEnergyDelta`、无 VisionConfig。当前以 `tech-reference.md` §8 配置 surface + `03-llm.md` 屏幕视觉为准，待回补 02-config 的 VisionConfig 契约段（列出该分段、字段指向 `nyx/config.py`） |
| ~~09-memory-facade 内联代码漂移~~ ✅ 已解决 | 契约化根治：spec 不再内联代码、指向 `nyx/memory/facade.py`，内联漂移随之消失 |

## 欲望消费接线（deferred）

> 字段与衰减已就位（design 有规划），但消费端 MVP 未接。不删字段（动 schema + 迁移，且 design 有规划），V3 再接线。

| 项 | 现状 | V3 接入点 |
|---|---|---|
| `LongTermDesire.linked_values` | design 规划「关联价值观」（01-types:240），production 唯一写入点 reflection 恒 `[]`，全后端无消费逻辑 | reflection 回填关联价值观，或排序/prompt 参考 |
| `LongTermDesire.strength` | design 规划衰减（11-desire:51 `strength -= 0.02`），lifecycle 也在递减，但递减结果不被任何排序/prompt/决策消费（prompt 读的是 `ShortTermDesire.strength`） | 长期欲望排序/取舍时考虑 `strength` |

## 电脑控制（computer use）

> 给尼克斯「手」：眼睛已有（`screen.py` 抓屏 + `VisionClient` 视觉描述 + Tauri 窗口/活跃度观察），缺口是**输入模拟**与「看到 → 点哪 → 真点」的动作闭环。

| 功能 | 愿景出处 | 说明 |
|---|---|---|
| 打游戏（卡牌策略） | — | 卡牌策略节奏慢、坐标可预测，作首个 computer use 目标。`input` 工具（`pyautogui`：click/type/key/hotkey/move）+ 视觉定位（vision 出坐标 / OCR / 模板匹配）。安全护栏内建：作用域限窗口、高危动作确认、急停热键、动作预算、审计日志。实时动作游戏（FPS/MOBA）不做（文本 LLM 循环延迟不达标） |
