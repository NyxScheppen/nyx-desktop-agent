"""表达 prompt 拼装：canon + 主动提问指导 + 动态状态 + 记忆 → system / user prompt。

纯函数，无 IO、无 LLM。
"""

from nyx.types import CurrentState, Memory, Message, SelfNarrative, ShortTermDesire


def build_system_prompt(
    canon: str,
    state: CurrentState,
    narrative: SelfNarrative | None = None,
    memories: list[Memory] | None = None,
    ask_guidance: str | None = None,
) -> str:
    """拼 system prompt：角色设定 + 当前状态 + 当前欲望 + 自我认知 + 相关记忆。

    canon 为静态人格注入文本（prompts/canon.md，由 18-api 组合根读入传入）。
    ask_guidance 为主动提问指导（prompts/ask.md），仅慢通道/搭话注入，None 跳过。
    narrative / memories 为 None（或空）时跳过对应段——快通道省略、慢通道补全。
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
