# 失败反思与自进化

> 范围：`nyx/enums.py`（`MemoryKind.LESSON`）、`nyx/memory/`（失败信号计数 + 经验沉淀）、
> `nyx/db.py`（迁移 v48）、`nyx/expression/{pipeline,tool_pipeline}.py`（工具失败采集 + 经验注入）、
> `nyx/eval/evaluator.py`（OOC 失败信号回调）。
> 让 Nyx 把失败经验沉淀成可检索的「技能/策略」记忆（`MemoryKind.LESSON`），并在规划与回复时备用。
> 与 06-memory-system 的「记忆反思（沉淀自我）」区分：那条线管 narrative/self_view，这条线管策略经验。
> 本 spec 只定义契约（签名 + 语义 + 决策），不内联完整代码；实现以 `nyx/` 源文件为准。

## 元信息

- **前置依赖**：06-memory-system（`MemoryFacade` / `_persist_memory` / `search`）、
  11-expression（`use_tools` / 慢通道回复）、17-tool-call-observation（工具执行事实）、
  10-eval（`Evaluator` / OOC）、15-discussion-partner（批注纠错）、18-plan-and-execute（plan 注入点）。
- **实现文件**：`nyx/enums.py`、`nyx/memory/facade.py`、`nyx/memory/store.py`、`nyx/db.py`、
  `nyx/expression/{pipeline,tool_pipeline}.py`、`nyx/eval/evaluator.py`、`nyx/app_context.py`（装配回调）。
- **测试文件**：`tests/test_memory/`、`tests/test_expression/test_pipeline.py`、`tests/test_eval/`。

## 用户故事

> 作为 Nyx 的用户，我想让她在客观失败（工具报错、搜不到、表达 OOC）时当场记住教训、
> 在被用户纠正时谨慎地（多次同类才认定）沉淀经验，下次规划或回复时避开同一条走不通的路。

## 验收标准

- [x] `MemoryKind.LESSON` 枚举新增，正式经验记忆的 kind。
- [x] `MemoryFacade.record_failure_signal(kind, topic, summary, correlation_id)` 记录失败信号。
- [x] 触发时机二分：`tool_error` 在重试耗尽后采集，`tool_empty` / `ooc` 当场采集；收到这些信号后直接沉淀 `LESSON`；`user_correction`
  **阈值**（同类累计 `_LESSON_PROMOTE_THRESHOLD` 次）才沉淀。
- [x] 当场类沉淀走 `_persist_memory` 入库尾段，`LONG_TERM` 豁免短期淘汰；同 topic 语义去重合并
  （不因重复失败无限新建）。
- [x] 阈值类用 `lesson_signal` 表（迁移 v48）计数，未达阈值不产生任何记忆；达阈值沉淀后计数归零。
- [x] 用户纠正「双管齐下」：达阈值时既沉淀 `LESSON`（技能/策略），又触发
  `REFLECTION(reason="annotation_correction")`（内心反思）。
- [x] 15 契约变更：`apply_annotation_feedback` 的 `correction=True` 不再当场触发
  `annotation_correction` 反射，改为走 `record_failure_signal(user_correction, …)` 阈值触发。
- [x] 经验注入：plan 步骤（18）与慢通道回复（11）经 `search(kind=LESSON)` 召回正式经验注入 prompt。
- [x] 记录/沉淀 best-effort：失败不重抛、不阻断主流程。
- [x] `ruff check`、`pyright`、`pytest` 全绿；`docs/test-inventory.md` 同步。

## 技术方案

### 分层与边界

- 生产端（本 spec）：失败信号采集 → 分类（当场/阈值）→ 沉淀正式经验。
- 消费端（约定，实现在 18/11）：`search(kind=LESSON)` 召回注入。
- 与记忆反思区分：`REFLECTION → narrative/self_view` 管「沉淀自我」；本 spec 的 `LESSON` 管「沉淀策略」。
  两条线仅在 `user_correction` 达阈值时**同时**触发（双管齐下）。

### 触发时机（核心决策）

| 失败类型 | 时机 | 理由 |
|---|---|---|
| `tool_error` | 重试耗尽后采集并直接沉淀 | 暂时故障重试成功时不学习 |
| `tool_empty` / `ooc` | **当场**沉淀 | 已确认的空结果或表达偏离，不累计阈值 |
| `user_correction` | **阈值**（同类 N 次）沉淀 | 用户信息可能不准（记错/随口/带情绪），减少误判 |

- 当场类指收到信号后直接沉淀，**不计数**；不表示每次中间失败尝试都采集信号。
- 表达工具接入18的有界重试后，`tool_error` 的采集点为调用重试耗尽（最多3次尝试）后的结果接纳；不在重试期间调用学习 LLM，重试成功不为暂时故障沉淀教训。18 的副作用 uncertain 不等于已确认失败，不采集 tool_error。`tool_empty` 不重试，仍直接采集；其它信号时机不变。
- 阈值类用 `lesson_signal` 计数，达阈值才写 `LESSON`。

### 失败信号采集

统一入口 `MemoryFacade.record_failure_signal(kind, topic, summary, correlation_id)`：

- `kind` ∈ `{"tool_error", "tool_empty", "ooc", "user_correction"}`。
- `topic` 是**同类失败聚类键**，采集点构造稳定字符串。

| 失败类型 | 采集点 | topic | summary |
|---|---|---|---|
| 工具报错 | 工具子图捕获异常或显式 error，重试耗尽后接纳 | `tool_error:{name}` | 工具名与有界错误原因 |
| 工具空结果 | 工具子图接纳空结果 | `tool_empty:{name}` | 有界工具结果 |
| OOC | `Evaluator` 命中 OOC（keyword/embed） | `ooc:{module}` | `表达偏离人设` |
| 用户纠正 | `apply_annotation_feedback` 判定 `correction=True` | `user_correction:{correction_topic}` | `用户纠正了回复` |

- **用户纠正主题提取**：复用 `apply_annotation_feedback` 已有的 fact extraction LLM 调用，
  当 `is_correction=true` 时额外输出 `correction_topic`（一句话概括纠正主题）作 topic 聚类键；
  缺失/空则兜底 `user_correction`。不新增 LLM 调用。
  提示同类纠正使用稳定短语、显式纠正主题原样复用，避免次数或措辞差异拆分计数。
- **空结果判定**：工具返回 `None` / `[]` / `{}` / `""` 视为空。
- **OOC 回调**：`Evaluator.__init__` 增注入 `on_failure: Callable[[str, str, str, str], Awaitable[None]] | None = None`，
  组合根注入 `record_failure_signal` 的适配；`None`（测试）不发信号。
- 失败信号是 best-effort 旁路：写失败只记日志不重抛。

### 沉淀：当场 vs 阈值

`record_failure_signal` 内部分类：

- **当场类**（`tool_error`/`tool_empty`/`ooc`）：直接 `_persist_lesson(topic, kind, summary)`。
- **阈值类**（`user_correction`）：`lesson_signal` 计数 → 达 `_LESSON_PROMOTE_THRESHOLD`（默认 3）→
  `_persist_lesson(topic, kind, summary)` + 触发内心反思，并删除该 topic 计数行（归零，重新积累）。

沉淀的正式经验（`_persist_lesson`）：

```text
content = "教训：{topic} 失败（{kind}）。{summary}"
summary_field = "经验：{topic} 失败"
memory = _new_memory(content, MemoryKind.LESSON, summary_field, MemoryType.LONG_TERM, [topic])
```

- 走 `_persist_memory` 的核心入库尾段，`LONG_TERM` 豁免短期淘汰。显式传空事实候选，
  并跳过建边/矛盾检测等旁路：策略经验不是用户事实，技术失败不能因此触发内心反思或递归学习。
- content 模板稳定；语义候选用含完整 topic 的固定 summary 限定，再在同主题内去重合并。
  不同失败主题即使向量接近也不能合并；topics 沿用既有24字符显示限制，不用截断值充当聚类身份。
- 沉淀失败 best-effort：当场类丢弃该次信号；阈值类计数行不删（下次失败信号再尝试），不重抛。

### 双管齐下（仅用户纠正）

- 批注事实与 mediation effect 保持同事务提交；成功提交后才调用 `record_failure_signal`，
  不在批注事务内运行经验入库或 embedding。该提交后学习旁路为 best-effort：提交与信号之间
  中断可能丢失该次学习信号，已完成 effect 的重放不补发；事实修正不受影响。
- 计数先独立提交；经验沉淀成功后，在同一本地事务中追加纠错反思并清除计数，提交后唤醒。
  沉淀或反思提交失败保留计数，下次信号重试；已写经验由既有去重合并。

- 用户纠正达阈值时，除沉淀 `LESSON` 外，触发 `REFLECTION(reason="annotation_correction")`
  （内心反思，更新 narrative/self_view，走 08 既有反射管道）。
- 15 契约变更：`apply_annotation_feedback(correction=True)` 的「当场 publish REFLECTION」改为
  「调 `record_failure_signal(user_correction, …)`」，由 19 阈值判断器统一触发反射与策略沉淀。
  记忆修正（改错记忆）仍当场，不受影响。
- 当场类（工具报错/空结果/OOC）**不**触发内心反思，只沉淀策略（技术失败不「自我怀疑」）。

### 注入（消费端约定）

- plan 步骤（18）：初次 assemble 召回 `kind=LESSON`，重规划构建 prompt 前刷新召回，
  渲染进 system prompt 的经验条目（`[相关记忆]` 中「你记住的经验教训」，展示 content）。
- 慢通道回复（11）：回复 prompt 构建处同样召回 `kind=LESSON` 注入。
- 具体 prompt 文案与渲染归 18/11 实现；本 spec 只约定「召回 `kind=LESSON` 注入」这一契约。

### 常量

```text
_LESSON_PROMOTE_THRESHOLD = 3
```

## 数据变更

### `lesson_signal`（迁移 v48，仅阈值类用）

```sql
CREATE TABLE lesson_signal (
    topic TEXT PRIMARY KEY,       -- 用户纠正的同类聚类键
    kind TEXT NOT NULL,           -- 恒 user_correction（当场类不落本表）
    count INTEGER NOT NULL DEFAULT 0,
    last_at REAL NOT NULL
);
```

## 对象完整性

### 失败信号（lesson_signal 记录，仅 user_correction）

**入口清单**

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| `record_failure_signal(user_correction, …)` | 用户批注纠错 | `lesson_signal` |

**消费者清单**

| 消费者 | 发现方式 | 用途 |
|---|---|---|
| 判断器（`record_failure_signal` 内部） | 计数达阈值 | 沉淀 `LESSON` + 触发 `REFLECTION` |

**状态迁移表**

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| 无记录 | 首次纠正 | `count=1` | 建行 |
| `count=n` | 同类纠正 | `count=n+1` | upsert |
| `count≥3` | 沉淀成功 | 删除行 | 写 LESSON + 触发反射 |
| `count≥3` | 沉淀失败 | 保留行 | best-effort，下次再试 |

**Bad case 表**

| 情况 | 处理 |
|---|---|
| 空 | `summary` 空 → 采集点兜底，不产生空记录 |
| 失败 | 计数写失败 → best-effort 不重抛；沉淀失败 → 计数保留，下次重试 |
| 部分完成 | 写 LESSON 成功、删行失败 → 下次信号可能重复沉淀，语义去重兜底 |
| 乱序 | 同 topic upsert 串行，计数单调 |
| 重放 | 同 topic 重复信号 → 计数正确累加，达阈值才一次沉淀 |
| 删除 | 沉淀后删计数行；正式经验记忆不删除（与记忆同生命周期） |

### 正式 LESSON 记忆（四类沉淀产物）

**入口清单**

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| 当场类：`record_failure_signal` | 工具报错/空结果/OOC 发生 | 记忆表（kind=LESSON，type=LONG_TERM） |
| 阈值类：判断器 | `user_correction` 计数达阈值 | 记忆表（kind=LESSON，type=LONG_TERM） |

**消费者清单**

| 消费者 | 发现方式 | 用途 |
|---|---|---|
| plan 步骤（18） | `search(kind=LESSON)` | 规划时注入经验 |
| 慢通道回复（11） | `search(kind=LESSON)` | 回复时注入经验 |
| 记忆 REST / 前端 Memory | 既有列表、检索和 memory_created 刷新 | 使用新增 `lesson` kind 展示经验 |

**状态迁移表**

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| 不存在 | 当场信号 / 阈值达标 | 创建（LONG_TERM） | 走入库尾段 |
| 已存在（语义相近） | 再次沉淀同 topic | strengthen（不新建） | 语义去重合并 |

**Bad case 表**

| 情况 | 处理 |
|---|---|
| 空 | content 模板保证非空（topic/kind/summary 均有值） |
| 失败 | 沉淀入库失败 → 当场类丢弃、阈值类计数保留重试；不执行建边/矛盾检测 |
| 部分完成 | 记忆写入、MEMORY_CREATED 事件未唤醒 → 走既有 `_persist_memory` 补偿语义 |
| 乱序 | 不适用：创建即终态 |
| 重放 | 语义去重 → strengthen 不新建 |
| 删除 | 不适用：正式经验不删除 |

## 测试要点

- [x] **E2E（agent 驱动，完整用户路径）**：
  - [x] 工具报错/空结果/OOC → 当场沉淀 `kind=LESSON`/`type=LONG_TERM` 记忆，不计数。
  - [x] 同 `correction_topic` 用户纠正累计达 `_LESSON_PROMOTE_THRESHOLD` → 沉淀 LESSON +
    触发 `annotation_correction` 反射；未达阈值不产生记忆、不触发反射。
  - [x] 后续规划/回复 `search(kind=LESSON)` 召回注入。
- [x] **确定性边界（内联判定走上述 E2E，计数使用既有 store 回归）**：
  - [x] 空结果判定：`None`/`[]`/`{}`/`""` 视为空。
  - [x] `correction_topic` 缺失/空 → 兜底 `user_correction`；topic 构造稳定。
  - [x] `lesson_signal` upsert 计数单调、达阈值删除。
- [x] 不测经验「好不好」；验证「坏没坏」（分类对、当场/阈值对、计数对），「好不好」留 `docs/eval/` 真 LLM 评测。

## 完成定义

- [x] `ruff check` 零报错
- [x] `pyright` 零报错
- [x] `pytest` 全绿
- [x] `docs/test-inventory.md` 已更新（快照）
- [x] `docs/tech-reference.md` 源码导航同步（`MemoryKind.LESSON`、`lesson_signal` 表数量 +1）
