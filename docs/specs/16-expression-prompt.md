# prompt 拼装 + 快慢通道判定

> 范围：`expression/prompt.py`（canon + 记忆 + 状态 → prompt 文本）+ `expression/classifier.py`（5 因子加权 → 0-1 vs slow_threshold）。两者都是纯函数。
> 纯函数 spec：只做「格式化文本」+「判定」，不含 Facade、不含 I/O（不读文件、不调 LLM、不碰 db）、不含 API。think/speak 的节点编排与任务指令归 17-expression。
> **本文件自包含**：两个文件的完整代码内联在下文。

## 元信息

- **前置依赖**：01-types（`CurrentState` / `Memory` / `Message` / `SelfNarrative` / `ShortTermDesire` / `ContextMode` / 各枚举）。输入数据由 09-memory-facade（`search`）、11-desire（`get_pending`，装配进 `CurrentState.active_desires`）、12-inner-life（`get_state` / `get_narrative`）生产，经 17-expression 编排传入本 spec 的纯函数；`canon` 文本来自 `prompts/canon.md`、`ask` 文本来自 `prompts/ask.md`（见「技术方案」）

## 用户故事

> 作为 Nyx 系统的开发者，我想要一套纯函数把静态人格 + 动态状态 + 记忆拼成回复用的 prompt 文本、并按启发式规则判定快慢通道，以便 17 的回复流程节点只做编排，prompt 拼装和通道判定可独立单测（不依赖 Facade / LLM / 文件系统）。

## 验收标准

- [ ] `prompt.py` 含 `build_system_prompt` + `build_user_prompt` + `build_backtrack_context`；`classifier.py` 含 `slow_score` + `classify_channel`，与各自「（完整）」段代码逐字一致
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

### `nyx/expression/prompt.py`（完整）

```python
"""表达 prompt 拼装：canon + 主动提问指导 + 动态状态 + 记忆 → system / user prompt。

纯函数，无 IO、无 LLM。
"""

from nyx.types import CurrentState, Memory, Message, SelfNarrative, ShortTermDesire

_MIN_OVERLAP_LEN = 4  # 短于此（去空白）的消息禁用零重叠停条件（短确认语不误清历史）


def build_system_prompt(
    canon: str,
    state: CurrentState,
    narrative: SelfNarrative | None = None,
    memories: list[Memory] | None = None,
    ask_guidance: str | None = None,
    tool_outputs: list[str] | None = None,
) -> str:
    """拼 system prompt：角色设定 + 状态 + 欲望 + 自我认知 + 记忆 + 工具结果。

    canon 为静态人格注入文本（prompts/canon.md，由 18-api 组合根读入传入）。
    ask_guidance 为主动提问指导（prompts/ask.md），仅慢通道/搭话注入，None 跳过。
    narrative / memories 为 None（或空）时跳过对应段——快通道省略、慢通道补全。
    tool_outputs 为 use_tools 节点查到的工具结果（慢通道专属），空则跳过。
    """
    parts: list[str] = [
        canon,
        _state_block(state),
        _desires_block(state.active_desires),
    ]
    if ask_guidance is not None:
        parts.append(ask_guidance)
    if narrative is not None:
        parts.append(_narrative_block(narrative))
    if memories:
        parts.append(_memory_block(memories))
    if tool_outputs:
        parts.append(_tool_outputs_block(tool_outputs))
    return "\n\n".join(parts)


def build_user_prompt(message: str, context: list[Message]) -> str:
    """拼 user prompt：对话历史（按时间升序的回溯上下文）+ 本次用户消息。

    不含 think/speak 任务指令——那是 17 节点的活
    （think 说「内心思考」、speak 说「说给用户」）。
    """
    if not context:
        return message
    lines = ["[对话历史]"]
    for m in context:
        speaker = "用户" if m.role == "user" else "Nyx"
        lines.append(f"{speaker}：{m.content}")
    lines.append(f"[本次消息]\n{message}")
    return "\n".join(lines)


def build_backtrack_context(
    message: str,
    history: list[Message],
    now: float,
    time_gap: float,
    max_len: int,
) -> list[Message]:
    """回溯上下文截断（慢通道）：从新到旧累积，命中停条件即止。

    停条件：满 max_len / 相邻消息隔超 time_gap / 与当前消息零字符重叠（十分不相关，
    短于 _MIN_OVERLAP_LEN 的当前消息禁用该停条件，避免短确认语误清历史）。
    快通道 Nyx 消息跳过该条继续往前（浅层回复不占用上下文，但不断深聊线程）。
    返回按时间升序（oldest-first），对齐 build_user_prompt 的「按时间升序」。
    """
    out: list[Message] = []
    prev_ts = now
    for m in reversed(history):
        if len(out) >= max_len:
            break
        if prev_ts - m.timestamp > time_gap:
            break
        prev_ts = m.timestamp
        if m.role == "nyx" and m.fast:
            continue
        if (
            len(message.strip()) >= _MIN_OVERLAP_LEN
            and _no_char_overlap(message, m.content)
        ):
            break
        out.append(m)
    out.reverse()
    return out


# ---- 内部 ----

def _state_block(state: CurrentState) -> str:
    """当前状态段：情感 / 精力 / 活动 / 性格 / 三观（数值直接拼，LLM 能读）。"""
    p = state.personality
    v = state.values
    activity = (
        state.current_activity.value
        if state.current_activity is not None
        else "空闲"
    )
    return (
        "[当前状态]\n"
        f"情感：valence={state.valence:.2f}，arousal={state.arousal:.2f}，表情={state.emotion.value}\n"
        f"精力：{state.energy:.0f}/100（{state.energy_state.value}）\n"
        f"当前活动：{activity}\n"
        f"性格（Big Five 1-10）：开放性{p['openness']:.0f}、"
        f"尽责性{p['conscientiousness']:.0f}、"
        f"外向性{p['extraversion']:.0f}、宜人性{p['agreeableness']:.0f}、神经质{p['neuroticism']:.0f}\n"
        f"三观（1-10）：对人类态度{v['attitude_to_human']:.0f}、AI身份接纳{v['ai_identity_acceptance']:.0f}、"
        f"利他{v['altruism']:.0f}、乐观{v['optimism']:.0f}"
    )


def _desires_block(desires: list[ShortTermDesire]) -> str:
    """当前欲望段：无欲望返回「无」。"""
    if not desires:
        return "[当前欲望]\n无"
    lines = ["[当前欲望]"]
    lines += [
        f"- {d.description}（{d.type.value}，强度{d.strength:.1f}）"
        for d in desires
    ]
    return "\n".join(lines)


def _narrative_block(narrative: SelfNarrative) -> str:
    """自我认知段：identity + 近期变化（becoming）。"""
    becoming = "、".join(narrative.becoming) if narrative.becoming else "无"
    return f"[自我认知]\n{narrative.identity}\n近期变化：{becoming}"


def _memory_block(memories: list[Memory]) -> str:
    """相关记忆段：优先 summary，无 summary 用 content。"""
    lines = ["[相关记忆]"]
    lines += [f"- {m.summary or m.content}" for m in memories]
    return "\n".join(lines)


def _tool_outputs_block(outputs: list[str]) -> str:
    """工具查询结果段：use_tools 节点查到的结果（慢通道专属）。"""
    lines = ["[工具查询结果]"]
    lines += [f"- {o}" for o in outputs]
    return "\n".join(lines)


def _no_char_overlap(a: str, b: str) -> bool:
    """a 与 b 是否无共同非空白字符——「十分不相关」的保守判定。"""
    ca = {c for c in a if not c.isspace()}
    cb = {c for c in b if not c.isspace()}
    return not (ca & cb)
```

### `nyx/expression/classifier.py`（完整）

```python
"""快慢通道判定：5 因子加权 → 0-1，与 slow_threshold 比较。纯函数。"""

from nyx.enums import ContextMode
from nyx.types import CurrentState

# 消息长度归一化：≥50 字符视为长消息（可推翻）
_LONG_MSG_LEN = 50.0
# 距上次慢通道归一化：≥3600 秒（1 小时）视为满（可推翻）
_RECENCY_WINDOW = 3600.0
QUESTION_MARKS = ("?", "？", "吗", "呢", "怎么", "为什么", "什么", "如何", "哪")
_EMOTION_WORDS = (
    "难过", "伤心", "生气", "愤怒", "开心", "高兴", "焦虑", "担心",
    "害怕", "委屈", "烦", "累", "孤独",
)


def slow_score(
    message: str, state: CurrentState, now: float, last_slow_at: float
) -> float:
    """慢通道倾向得分 0-1，越高越该走慢通道（design §5.2）。

    5 因子（权重和=1）：消息长度 0.25 + 含问句 0.25 + 情感词 0.20
    + 精力/情感 0.15 + 距上次慢通道 0.15。
    「精力/情感」= 精力足且情绪平静 → 倾向慢（有力气深聊）；精力低或激动 → 倾向快。
    """
    length = min(1.0, len(message) / _LONG_MSG_LEN)
    question = 1.0 if any(m in message for m in QUESTION_MARKS) else 0.0
    emotion = 1.0 if any(w in message for w in _EMOTION_WORDS) else 0.0
    # 不夹：energy/arousal 已在上游 clamp 到 [0,100]/[0,1]
    vigor = 0.5 * (state.energy / 100.0) + 0.5 * (1.0 - state.arousal)
    # 上下限都夹：last_slow_at>now（时钟回拨）也不为负
    recency = max(0.0, min(1.0, (now - last_slow_at) / _RECENCY_WINDOW))
    return (
        0.25 * length + 0.25 * question + 0.20 * emotion
        + 0.15 * vigor + 0.15 * recency
    )


def classify_channel(
    message: str,
    state: CurrentState,
    now: float,
    last_slow_at: float,
    threshold: float,
) -> ContextMode:
    """判定快/慢通道：slow_score ≥ threshold → 慢，否则快。"""
    return (
        ContextMode.SLOW
        if slow_score(message, state, now, last_slow_at) >= threshold
        else ContextMode.FAST
    )
```

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
- [ ] 集成测试：无（纯函数，无 Facade 管道；编排在 17）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] 下游约定：17-expression 的 `classify_channel` 节点调 `classifier.classify_channel(message, state, now, last_slow_at, config.expression.slow_threshold)`；`think`/`speak` 节点调 `prompt.build_system_prompt`（慢通道传 ask_guidance+narrative+memories+tool_outputs，快通道省略）+ `prompt.build_user_prompt`，再拼各自任务指令后 `await llm.complete(...)`（tech-ref §6.1 已锁节点名；无新文件，tech-ref §7 已列 `prompt.py` / `classifier.py`）
