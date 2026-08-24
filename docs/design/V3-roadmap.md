# V3 预备路线图（V2 之后的功能）

> 本文档汇总 **V2 完成之后**才排期的功能（backlog），只记方向与关键接入点，不做详细实现旁注。
> V2 项见 [`V2-roadmap.md`](./V2-roadmap.md)；~~删除线~~ = 已落地（移出 V3）。
> 每项标「愿景出处」（design.md 章节）——无设计章节的标「—」。

## 可观测（observability）

| 功能 | 愿景出处 | 说明 |
|---|---|---|
| LangSmith 接入（tracing） | — | 三行 env：`LANGCHAIN_TRACING_V2=true` + `LANGCHAIN_API_KEY` + `LANGCHAIN_PROJECT`。项目已基于 `langchain-openai`（`ChatOpenAI`）+ `langgraph`，`langsmith` 是传递依赖，无需新依赖、改代码 |

**关键点**：
- 变量放 `.env`：`nyx/__init__.py` 已在 import 最前 `load_dotenv()`，早于任何 LLM/graph 调用。
- trace 覆盖：每次 `ChatOpenAI` 调用 + 每次 `StateGraph` 运行（节点步骤）。
- 脱敏：`LANGCHAIN_HIDE_INPUTS=true` / `LANGCHAIN_HIDE_OUTPUTS=true`（遮 prompt/回复原文）。
- **隐私**：完整 prompt/回复会上传 LangSmith 云；个人陪伴数据需掂量——可自托管 LangSmith，或不用时置 `LANGCHAIN_TRACING_V2=false`。

## 读书/创作借鉴（参考 nyx_desktop_agent）

> 参考项目 `nyx_desktop_agent` 的「读书 → 知识点入记忆」闭环与「创作 → 风格/知识库/屏幕灵感」增强，补齐 Nyx 已读真实文件、但缺知识点沉淀与创作上下文的短板。不借鉴：参考项目的 LLM 日历日程生成器（与「欲望驱动」正交）、分块读文件机制（当前已有）。

| 功能 | 愿景出处 | 说明 |
|---|---|---|
| ~~R1 读书提取知识点入记忆~~ | — | 读完整本那一刻 1 次 LLM 提取 1-5 个客观知识点，`tag="knowledge"` 落长期记忆（复用 `memory` 表，不新建表）；best-effort，提取失败不反噬读书笔记落盘 |
| ~~W1 创作风格随机池~~ | — | `_CREATION_STYLES =（日记体/随笔/微型小说/散文诗/书信体/观察笔记）`，`_pick_creation_style()` 随机抽 |
| ~~W2 创作注入知识库参考~~ | — | 创作时 `list_memories(tag="knowledge")` 取知识点拼进上下文 |
| ~~W3 创作注入观察/屏幕灵感~~ | — | 创作时折入 `_get_observation()`（presence/window_title/screen_summary） |
| ~~读书笔记 CRUD（含批注）~~ | — | 迁移 v4 两张表 `reading_note` + `annotation`；CRUD 委托在 `ActivityFacade` 上（`ReadingNoteStore` store 层），5 个 REST 端点（清单/删除/批注增删查），不新建 Facade 层 |
| ~~个人空间编排（两级大类导航）~~ | — | 前端 `InnerWorld` 由扁平 7 标签改为两大级导航（内在/空间/记录 三大类 + 子标签），复用 DraggablePanel/`.side-panel` 样式 |

**关键点**：
- ~~已落地~~：R1/W1/W2/W3/读书笔记 CRUD + 个人空间编排全部完成——后端（`db.py` v4、`types.py` ReadingNote/Annotation、`memory/facade.py` remember_knowledge、`activity/facade.py` 接线 + `reading_note_store.py`、`main.py` 组合根 + 5 端点）+ 前端（`types/api.ts` ReadingNote/Annotation、`client.ts` 5 端点、`readingNotesStore`、`ReadingNotesPanel`、`InnerWorld` 两级大类导航）。✅
- 反冗余：R1 复用 `memory` 表 + `_persist_memory` 入库尾段，不另写写路径；创作上下文复用 `_run_llm_activity` 的 `extra_context` 通道（加 `context_label` 参数），不新增注入回调；读书笔记 CRUD 不新建 Facade 层。

## 文档债（设计文档缺口）

| 缺口 | 说明 |
|---|---|
| 02-config 缺 `VisionConfig` 分段 | `config.py` 已有 `VisionConfig`（`enabled`/`provider`/`model`/`base_url`/`interval_seconds` + medium 评审修复轮加的 `api_key_env`），但 `02-config.md` 仍只写「8 分段 dataclass」、无 vision 段与校验规则。当前以 `tech-reference.md` §8 配置 surface + `03-llm.md` 屏幕视觉为准，待回补 02-config 内联代码 |
| 09-memory-facade 内联代码漂移 | facade.py dump 的 `create_scene_memory`/`remember_activity` 仍显示 `_store.add` 内联管道（早于 `_persist_memory` 重构），且缺 `_persist_memory` 方法定义；本次仅同步 `_new_memory` 构造，未整体重同步 |

## 欲望消费接线（deferred）

> 字段与衰减已就位（design 有规划），但消费端 MVP 未接。不删字段（动 schema + 迁移，且 design 有规划），V3 再接线。

| 项 | 现状 | V3 接入点 |
|---|---|---|
| `LongTermDesire.linked_values` | design 规划「关联价值观」（01-types:240），production 唯一写入点 reflection 恒 `[]`，全后端无消费逻辑 | reflection 回填关联价值观，或排序/prompt 参考 |
| `LongTermDesire.strength` | design 规划衰减（11-desire:51 `strength -= 0.02`），lifecycle 也在递减，但递减结果不被任何排序/prompt/决策消费（prompt 读的是 `ShortTermDesire.strength`） | 长期欲望排序/取舍时考虑 `strength` |
