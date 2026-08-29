# prompt 拼装 + 快慢通道判定

> 范围：`expression/prompt.py`（canon + 记忆 + 状态 → prompt 文本）+ `expression/classifier.py`（5 因子加权 → 0-1 vs slow_threshold）。两者都是纯函数。
> 纯函数 spec：只做「格式化文本」+「判定」，不含 Facade、不含 I/O（不读文件、不调 LLM、不碰 db）、不含 API。think/speak 的节点编排与任务指令归 17-expression。
> spec 只定义契约（签名 + 分段/因子语义 + 阈值常量）；实现以 `nyx/expression/prompt.py` / `nyx/expression/classifier.py` 源文件为准。

## 元信息

- **前置依赖**：01-types（`CurrentState` / `Memory` / `Message` / `SelfNarrative` / `ShortTermDesire` / `ContextMode` / 各枚举）。输入数据由 09-memory-facade（`search`）、11-desire（`get_pending`，装配进 `CurrentState.active_desires`）、12-inner-life（`get_state` / `get_narrative`）生产，经 17-expression 编排传入本 spec 的纯函数；`canon` 文本来自 `prompts/canon.md`、`ask` 文本来自 `prompts/ask.md`（见「技术方案」）

## 用户故事

> 作为 Nyx 系统的开发者，我想要一套纯函数把静态人格 + 动态状态 + 记忆拼成回复用的 prompt 文本、并按启发式规则判定快慢通道，以便 17 的回复流程节点只做编排，prompt 拼装和通道判定可独立单测（不依赖 Facade / LLM / 文件系统）。

## 验收标准

- [ ] `prompt.py` 含 `build_system_prompt` + `build_user_prompt` + `build_backtrack_context`；`classifier.py` 含 `slow_score` + `classify_channel`（实现见 `nyx/expression/prompt.py` / `nyx/expression/classifier.py`）
- [ ] 四个函数全是**同步纯函数**：无 `async`、无 I/O、无 LLM、无 db，仅字符串拼装 + 数值计算
- [ ] `build_system_prompt` 分段：canon（基底）→ 当前状态 → 当前欲望 → 主动提问指导（可选）→ 自我认知（可选）→ 相关记忆（可选）→ 工具查询结果（可选）；`ask_guidance` / `narrative` / `memories` / `tool_outputs` 为空（None）时跳过对应段
- [ ] `classify_channel`：`slow_score(...) >= threshold` → `ContextMode.SLOW`，否则 `ContextMode.FAST`
- [ ] `pyright` strict 零报错；无模块级可变全局变量（词表/常量均为不可变 `tuple` / `float`）

## 技术方案

- **新文件**：`nyx/expression/prompt.py`、`nyx/expression/classifier.py`（无 Facade、无 API、无数据变更）
- **库**：无新库（标准库即可；类型从 `nyx.types` / `nyx.enums` 拿）
- **公开面**：`from nyx.expression.prompt import build_system_prompt, build_user_prompt, build_backtrack_context`；`from nyx.expression.classifier import slow_score, classify_channel`（不加 `__all__`）
- **定位**：两个模块都是内部类（非 Facade），被 17 的 `classify_channel` / `think` / `speak` 节点调用
- **canon / ask 来源**：`build_system_prompt` 接收 `canon: str`（静态人格注入文本）+ `ask_guidance: str | None`（主动提问指导）。canon 来自 `prompts/canon.md`、ask 来自 `prompts/ask.md`（见 `docs/canon.md` 指针），由 18-api 组合根读入为字符串传入——**本 spec 不读文件**（保持纯函数可单测、测试不碰文件系统）；`ask_guidance=None` 时跳过该段
- **think/speak 任务指令归 17**：`build_user_prompt` 只拼「对话历史 + 本次消息」，不含「内心思考 / 说给用户」指令；那是 17 节点的活（think 与 speak 各拼自己的指令后接在 user prompt 上）
- **数值直接拼，不转中文标签**：情感 valence/arousal、精力、性格/三观 1-10、枚举 `.value`（`happy`/`energetic` 等）直接格式化进 prompt。LLM 能读；不额外维护「数值→中文描述」映射（反冗余）。前端展示经 `lib/labels.ts` 转中文（`exploration → 发现`），但 prompt 仍用枚举原值——两处各自独立，不互相反噬
- **回溯截断（纯函数）**：`build_backtrack_context(message, history, now, time_gap, max_len)` 从新到旧累积，命中「满 max_len / 相邻隔超 time_gap / 与当前消息零字符重叠（`_no_char_overlap`，十分不相关的保守判定）」即停；快通道 Nyx 消息（`Message.fast`）跳过该条继续往前（浅层回复不占上下文，但不断深聊线程）；返回按时间升序。这是 design §5.1 回溯检测的纯函数落地，**编排**（何时调、context 重截断、state 装配）归 17 的 `assemble_context`
- **明确不做**：回溯上下文**检测/截断的编排**（`assemble_context` 节点的活，归 17）；`canon` / `ask` 文件读取（归 18-api）；think/speak 指令（归 17）；记忆检索（归 09）

## 测试要点

- [ ] 单元测试 `tests/test_expression/`（纯函数，无 DB、无 async、无 fake LLM）：
  - [ ] **prompt**（`test_prompt.py`）：
    - [ ] `build_system_prompt`：`canon in result`（基底透传）；`narrative=None`、`memories=[]` 时结果**不含** `[自我认知]` / `[相关记忆]`（段被跳过）；`narrative` 非 None 含 `identity` 与「近期变化」；`memories` 非空含 `m.summary`；`ask_guidance=None` 时结果**不含**该内容、非 None 时含其内容；`tool_outputs` 非空含 `[工具查询结果]`、空则不拼
    - [ ] `build_system_prompt` 状态段：`state` 构造含非默认值，断言结果含 `valence=`、`arousal=`、`表情=`、`精力：`、`当前活动：`、`性格（Big Five`、`三观（`、当前欲望描述
    - [ ] `_state_block`：`current_activity=None` → `当前活动：空闲`
    - [ ] `_desires_block`：空欲望 → `[当前欲望]\n无`
    - [ ] `build_user_prompt`：`context=[]` → 原样返回 `message`；`context` 非空 → 含 `[对话历史]`、`用户：` / `Nyx：`（按 role）、`[本次消息]` + `message`
    - [ ] `_memory_block`：`summary=""` 时回退 `content`（`m.summary or m.content`）
    - [ ] `build_backtrack_context`：空 history → `[]`；满 `max_len` 截断且返回按时间升序（oldest-first）；相邻消息隔超 `time_gap` 即停（更早的不取）；快通道 Nyx 消息（`fast=True`）跳过该条继续往前取更早的用户消息；与当前消息零字符重叠的消息即停（`result == []`，但当前消息去空白 < `_MIN_OVERLAP_LEN` 时禁用该停条件、仍累积）；有字符重叠则继续累积
    - [ ] `_no_char_overlap`：无共同字符 → `True`（`"量子"` vs `"天气"`）；有共同字符 → `False`；空白被忽略（`"你 好"` vs `"你好"` → `False`）
  - [ ] **classifier**（`test_classifier.py`）：
    - [ ] `slow_score` ∈ `[0, 1]`（构造极端输入：空消息 + 精力 0 + arousal 1 + 刚慢通道过 → 接近 0；长消息含问句含情感词 + 精力 100 + arousal 0 + 2 小时没慢通道 → 接近 1；`last_slow_at > now`（时钟回拨）→ 仍 ≥ 0）
    - [ ] `slow_score` 五因子各生效：长消息 > 短消息（其余同）；含「吗」> 不含；含「难过」> 不含；`energy=100, arousal=0` > `energy=0, arousal=1`；`now-last_slow_at` 大 > 小
    - [ ] `classify_channel`：`threshold=0.5`，得分 ≥ 0.5 的输入 → `ContextMode.SLOW`；得分 < 0.5 → `ContextMode.FAST`（用两个可心算的例子，如「在吗」+ 精力满 + 2h → 慢，「哦」+ 精力 20 + arousal 0.9 + 60s → 快）
    - [ ] `_EMOTION_WORDS` 无单字子串误判：「积累」（含「累」）/「麻烦」（含「烦」）不触发情感；「烦躁」/「疲惫」正常命中
- [ ] 集成测试：无（纯函数，无 Facade 管道；编排在 17）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] 下游约定：17-expression 的 `classify_channel` 节点调 `classifier.classify_channel(message, state, now, last_slow_at, config.expression.slow_threshold)`；`think`/`speak` 节点调 `prompt.build_system_prompt`（慢通道传 ask_guidance+narrative+memories+tool_outputs，快通道省略）+ `prompt.build_user_prompt`，再拼各自任务指令后 `await llm.complete(...)`（tech-ref §6.1 已锁节点名；无新文件，tech-ref §7 已列 `prompt.py` / `classifier.py`）
