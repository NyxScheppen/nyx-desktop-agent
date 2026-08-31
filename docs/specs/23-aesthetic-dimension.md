# 审美维度（内在生命新增 4 轴 + 阅读量缩放漂移）

> 范围：`nyx/inner_life/` 新增**审美 4 轴**慢变量——`aesthetic` 单行表 + 与性格/三观同循环漂移，但偏移量按「距上次反思新读章数」缩放（读够章才满额）。落 `CurrentState.aesthetic` 并进 mutter/reply 的 system prompt。不改冲动引擎（21 的 `aesthetic_sensitivity` 驱动仍用段落 `richness_score`）。
> spec 只定义契约（签名 + 语义 + 决策），不内联完整代码；代码唯一事实来源是 `nyx/` 源文件。

## 元信息

- **前置依赖**：01-types（TypedDict 约定）、04-db（`_MIGRATIONS` v10）、09-memory-facade（`list_memories(tag="reading")` 计数）、12-inner-life（`Reflection.run`/`get_state`）、22-reading-notes（`remember_reading` 落 `tag='reading'`）
- **实现文件**：`nyx/types.py`（新增 `Aesthetic` + `CurrentState` 加字段）、`nyx/db.py`（`_MIGRATIONS` 追加 v10）、`nyx/inner_life/store.py`（`get_aesthetic`/`upsert_aesthetic`）、`nyx/inner_life/reflection.py`（`drift_aesthetic` + `_AESTHETIC_KEYS` + `_AESTHETIC_MIN_READING` + `_REFLECTION_SYSTEM` + `_parse_reflection` + `_build_reflection_prompt` + `run`）、`nyx/inner_life/facade.py`（`get_state` 读 aesthetic）、`nyx/expression/prompt.py`（`_state_block` 加审美行）、`nyx/main.py`（`_seed_inner_life` seed）

## 用户故事

> 作为 Nyx，我读了几章书后，我的文风品味（华丽/抒情/古典/沉重）会缓慢漂移，让我之后的碎碎念和回应带上这些偏好——没读书时品味不动，读够三章才满额偏移。

## 验收标准

- [ ] `nyx/db.py` 的 `_MIGRATIONS` 追加 v10（`aesthetic` 单行表，见「数据变更」）
- [ ] `nyx/types.py` 含 `Aesthetic` TypedDict：`ornate/lyrical/classical/somber` 四键（`float`，1-10，10=第一极）
- [ ] `CurrentState` 加 `aesthetic: Aesthetic`
- [ ] `InnerLifeStore` 加 `get_aesthetic() -> Aesthetic | None` / `upsert_aesthetic(a: Aesthetic) -> None`（`async`，`id='self'` 单行，复用 `conn`+`lock`）
- [ ] `reflection.py` 加 `drift_aesthetic(base: Aesthetic, delta: dict[str, float]) -> Aesthetic`（纯函数，复用 `_drift_dim`）
- [ ] `reflection.py` 加 `_AESTHETIC_KEYS = frozenset({"ornate", "lyrical", "classical", "somber"})` + `_AESTHETIC_MIN_READING = 3`
- [ ] `_REFLECTION_SYSTEM` 增加 `aesthetic_delta` 键说明（键 ornate/lyrical/classical/somber，值 [-0.5, 0.5]）
- [ ] `_build_reflection_prompt` 加 `aesthetic: Aesthetic` 参数 + 输出「当前审美（1-10）：华丽{x} / 抒情{x} / 古典{x} / 沉重{x}」锚点行（delta 需当前值作参照，与 personality/values 同款）
- [ ] `_parse_reflection` 校验 `aesthetic_delta`（并入既有漂移白名单循环，未知维度/非数值抛 `ValueError`）；返回值 dict 加 `aesthetic_delta` 键（`run` 读 `parsed["aesthetic_delta"]`）
- [ ] `Reflection.run` 读 `get_aesthetic()` + 计「新读章数」→ 缩放 `aesthetic_delta` → `upsert_aesthetic(drift_aesthetic(...))`
- [ ] `InnerLifeFacade.get_state` 读 `get_aesthetic()` 填入 `CurrentState.aesthetic`
- [ ] `expression/prompt.py` 的 `_state_block` 加审美行（四轴 1-10 拼进 system prompt）
- [ ] `main.py` 的 `_seed_inner_life` 表空时 seed `ornate=7/lyrical=7/classical=6/somber=6`
- [ ] `pyright` strict 零报错

## 技术方案

- **涉及的 Facade / 内部类**：
  - `InnerLifeStore` 追加 `get_aesthetic`/`upsert_aesthetic`（镜像 `get/upsert_personality` 的 `id='self'` 单行 + `ON CONFLICT(id) DO UPDATE` 模式）
  - `reflection.py` 追加 `drift_aesthetic`（纯函数，逐轴 `_drift_dim(base[k], delta.get(k))`，与 `drift_personality`/`drift_values` 同构）；`_build_reflection_prompt` 加 `aesthetic: Aesthetic` 参数 + 输出「当前审美（1-10）：华丽{x} / 抒情{x} / 古典{x} / 沉重{x}」锚点行（delta 必须有当前值作参照，与 personality/values 同款）；`Reflection.run` 内做「读 aesthetic + 计新读章数 + 缩放 + 回写」，其 None 守卫（`personality is None or values is None or narrative is None`）加入 `or aesthetic is None`（未 seed 抛 `RuntimeError`）
  - `InnerLifeFacade.get_state` 读 `get_aesthetic()`，`get_state` 的 None 检查加入 aesthetic（与 personality/values/energy 同组，未 seed 抛 `RuntimeError`）
  - `expression/prompt.py` 的 `_state_block` 加一行「审美（1-10）：华丽{x}、抒情{x}、古典{x}、沉重{x}」
- **关键决策**：
  - **不并入 Big Five/三观**：审美语义独立（对文风的品味），单开 `aesthetic` 单行表（`id='self'`），不往 `personality`/`value_system` 塞第 6/5 列——保 Big Five 5 维、三观 4 维完整（设计文档 §6.3）。
  - **偏移复用 `_drift_dim`**：`drift_aesthetic` 逐轴 `base + clamp(delta, ±0.5)` 再 clamp [1,10]，与性格/三观同款尺度（`_MAX_DRIFT=0.5`、`_SCALE_LO/HI=1.0/10.0` 复用），无新 clamp 逻辑。
  - **阅读量缩放是编排，不是纯函数参数**：`drift_aesthetic` 保持与 `drift_personality`/`drift_values` 同构（`(base, delta) -> Aesthetic`，纯函数可测）；`× min(新读章数 / _AESTHETIC_MIN_READING, 1.0)` 的缩放放在 `Reflection.run`（那里才拿得到「新读章数」），避免把阅读计数塞进纯函数签名。
  - **新读章数 = `tag='reading'` 记忆新增条数**：`list_memories(tag="reading")`（`limit=None` 全量）里 `created_at > narrative.updated_at` 的条数；`narrative.updated_at` 即「上次反思」基准（与 `_check_reflect` 同款判定，`run` 里已拿到 narrative）。一条章末记忆 ≈ 一章（22 的章末整合落 `tag='reading'`），无需读 `reading_progress` 表、无 per-book 计数。**口径注**：「章数」是近似叫法——22 整本读完也落一条 `tag='reading'` 全书记忆，故 n 严格 = 章末记忆条数 + 每读完一本 +1（全书记忆）；缩放因子下这 +1 可忽略。
  - **缩放因子 `min(n / _AESTHETIC_MIN_READING, 1.0)`**：0 章 → 0（审美不动）；1~2 章 → 1/3~2/3；≥3 章 → 1.0 满额。`_AESTHETIC_MIN_READING = 3` 模块常量（同反思参数，不进 config）；公式引用常量、不写死 `3`。
  - **反射 JSON 缺 `aesthetic_delta` → 空 dict 不漂**：与 `personality_delta`/`values_delta` 同款 `parsed.get(...) or {}` 兜底，`drift_aesthetic` 对缺键 `delta.get(k)` 返回 `None` → `_drift_dim` 原值不动（LLM 没输出审美就原地不动，不报错）。
  - **审美进 prompt 需显式改 `_state_block`**：`CurrentState` 加字段**不会**自动进 prompt（`_state_block` 逐段手拼 personality/values）。23 在 `_state_block` 加审美行，21 的 `aesthetic_sensitivity` 驱动（段落 `richness_score`）不受影响、互不依赖。
  - **无新 API 端点**：审美经既有 `GET /api/state`（返回 `CurrentState`）暴露；不单开 `/api/aesthetic`。
  - **无新事件**：审美漂移不单独广播（与性格/三观同，随反思 `REFLECTION_DONE` 前端整体刷新 state 即可）。
- **数据变更**（`_MIGRATIONS` v10，DDL 以 `nyx/db.py` 为准）：
  - `aesthetic`：`id TEXT PRIMARY KEY`（固定 'self'）、`ornate REAL NOT NULL`、`lyrical REAL NOT NULL`、`classical REAL NOT NULL`、`somber REAL NOT NULL`（四轴 1-10，10=第一极）

## 测试要点

- [ ] 单元测试 `tests/test_inner_life/test_inner_life_reflection.py`（纯函数）：
  - [ ] `drift_aesthetic`：`delta` 全 0 → 原值；`ornate +0.5` → `min(10, base+0.5)`；`somber -0.5` → `max(1, base-0.5)`；`delta` 空/缺键 → 原值
  - [ ] `drift_aesthetic` 超界 clamp：`base=9.9, delta=+0.5` → `10`；`base=1.1, delta=-0.5` → `1`
  - [ ] `_parse_reflection` 缺 `aesthetic_delta` → 解析通过、返回空 dict；含未知键（如 `foo`）→ 抛 `ValueError`；`aesthetic_delta` 值非数值 → 抛 `ValueError`
- [ ] 集成测试 `tests/test_inner_life/test_inner_life_store.py`（`:memory:` + 真 `InnerLifeStore`）：
  - [ ] `upsert_aesthetic` → `get_aesthetic` 回读四轴一致；再次 upsert → 同一 `id='self'` 单行覆盖
  - [ ] seed 后 `get_aesthetic` 非 None（初值 7/7/6/6）
- [ ] 集成测试 `tests/test_inner_life/test_inner_life_facade.py`（`:memory:` + fake desire/activity + mock llm）：
  - [ ] `get_state` 返回 `aesthetic` 与落库值一致
  - [ ] `Reflection.run` 阅读量缩放：0 条新 reading 记忆 → `aesthetic` 不变；1 条 → 按 1/3 缩放；≥3 条 → 满额（mock LLM 返回固定 `aesthetic_delta`，比对落库值）
  - [ ] 非 reading 记忆（`tag='user'`）不计入新读章数
- [ ] 契约测试（如适用）：`GET /api/state` 返回体含 `aesthetic`（四键 1-10）

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新（快照）
- [ ] ripple 同步：tech-ref §2 TypedDict 计数 +1（`Aesthetic`）、§3 业务表计数 +1（`aesthetic`）、§5 Facade 清单补 `InnerLifeFacade.get_state`（返回 `aesthetic` 字段）+ `InnerLifeStore.get_aesthetic`/`upsert_aesthetic`、§7 补 `inner_life/store.py`「四张单行表」→「五张」+`reflection.py` `drift_aesthetic`/`_build_reflection_prompt`+`expression/prompt.py` 审美行、01-types TypedDict 计数 +1
- [ ] Nyx 读够章后审美缓慢漂移，没读书不动；碎碎念/回应带上审美偏好
