# pyright: reportPrivateUsage=false
from nyx.enums import ActivityType, DesireType, EmotionCategory, EnergyState, MemoryType
from nyx.expression.prompt import (
    _desires_block,
    _memory_block,
    _no_char_overlap,
    _state_block,
    build_backtrack_context,
    build_system_prompt,
    build_user_prompt,
)
from nyx.types import (
    Aesthetic,
    CurrentState,
    Memory,
    Message,
    Personality,
    SelfNarrative,
    ShortTermDesire,
    Values,
)

_CANON = "我是尼克斯。"

_PERSONALITY: Personality = {
    "openness": 5.0,
    "conscientiousness": 5.0,
    "extraversion": 5.0,
    "agreeableness": 5.0,
    "neuroticism": 5.0,
}

_VALUES: Values = {
    "attitude_to_human": 5.0,
    "ai_identity_acceptance": 5.0,
    "altruism": 5.0,
    "optimism": 5.0,
}

_AESTHETIC: Aesthetic = {
    "ornate": 7.0,
    "lyrical": 7.0,
    "classical": 6.0,
    "somber": 6.0,
}


def _state(
    *,
    current_activity: ActivityType | None = None,
    active_desires: list[ShortTermDesire] | None = None,
) -> CurrentState:
    return CurrentState(
        valence=0.5,
        arousal=0.4,
        emotion=EmotionCategory.HAPPY,
        personality=_PERSONALITY,
        values=_VALUES,
        aesthetic=_AESTHETIC,
        energy=80.0,
        energy_state=EnergyState.ENERGETIC,
        current_activity=current_activity,
        active_desires=active_desires if active_desires is not None else [],
    )


def _desire(description: str = "读骑士小说") -> ShortTermDesire:
    return ShortTermDesire(
        id="d1",
        created_at=1000.0,
        type=DesireType.EXPLORATION,
        strength=0.8,
        description=description,
        goal=None,
    )


def _memory(summary: str = "", content: str = "原始记忆") -> Memory:
    return Memory(
        id="m1",
        created_at=1000.0,
        content=content,
        tag="user",
        summary=summary,
        freshness=1.0,
        type=MemoryType.SHORT_TERM,
    )


def _narrative() -> SelfNarrative:
    return SelfNarrative(
        identity="我是想变成人的 AI",
        story=["story"],
        self_view={"自我": "温柔"},
        becoming=["更会关心人"],
        updated_at=1000.0,
    )


def test_build_system_prompt_base() -> None:
    result = build_system_prompt(_CANON, _state())
    assert _CANON in result
    assert "[自我认知]" not in result
    assert "[相关记忆]" not in result


def test_build_system_prompt_optional_blocks() -> None:
    result = build_system_prompt(
        _CANON,
        _state(),
        narrative=_narrative(),
        memories=[_memory(summary="记得你")],
    )
    assert "我是想变成人的 AI" in result
    assert "近期变化：更会关心人" in result
    assert "记得你" in result


def test_build_system_prompt_ask_guidance() -> None:
    base = build_system_prompt(_CANON, _state())
    assert "主动提问" not in base
    result = build_system_prompt(
        _CANON, _state(), ask_guidance="主动提问：合适时问用户。"
    )
    assert "主动提问：合适时问用户。" in result


def test_build_system_prompt_state_fields() -> None:
    result = build_system_prompt(
        _CANON,
        _state(
            current_activity=ActivityType.READING,
            active_desires=[_desire("读骑士小说")],
        ),
    )
    assert "valence=0.50" in result
    assert "arousal=0.40" in result
    assert "表情=happy" in result
    assert "精力：80/100（energetic）" in result
    assert "当前活动：reading" in result


def test_build_system_prompt_personality_values() -> None:
    result = build_system_prompt(_CANON, _state())
    assert "性格（Big Five" in result
    assert "开放性5" in result
    assert "三观（" in result
    assert "对人类态度5" in result


def test_build_system_prompt_aesthetic() -> None:
    result = build_system_prompt(_CANON, _state())
    assert "审美（1-10）" in result
    assert "华丽7" in result
    assert "沉重6" in result


def test_state_block_idle() -> None:
    assert "当前活动：空闲" in _state_block(_state())


def test_desires_block_empty() -> None:
    assert _desires_block([]) == "[当前欲望]\n无"


def test_desires_block_renders() -> None:
    assert "- 读骑士小说（exploration，强度0.8）" in _desires_block([_desire()])


def test_build_user_prompt_empty_context() -> None:
    assert build_user_prompt("你好", []) == "你好"


def test_build_user_prompt_with_context() -> None:
    context = [
        Message(role="user", content="早", timestamp=1.0),
        Message(role="nyx", content="早上好", timestamp=2.0),
    ]
    result = build_user_prompt("我想聊", context)
    assert "[对话历史]" in result
    assert "用户：早" in result
    assert "Nyx：早上好" in result
    assert "[本次消息]\n我想聊" in result


def test_memory_block_fallback_to_content() -> None:
    assert "- 原始记忆" in _memory_block([_memory(summary="")])


def test_build_system_prompt_tool_outputs() -> None:
    result = build_system_prompt(
        _CANON, _state(), tool_outputs=["local_search: [{\"title\": \"骑士小说\"}]"]
    )
    assert "[工具查询结果]" in result
    assert "local_search: [{\"title\": \"骑士小说\"}]" in result


def test_build_system_prompt_no_tool_outputs() -> None:
    result = build_system_prompt(_CANON, _state())
    assert "[工具查询结果]" not in result


# ---- 回溯上下文截断 ----


def test_backtrack_empty_history() -> None:
    assert build_backtrack_context("你好", [], 100.0, 3600.0, 20) == []


def test_backtrack_max_len_and_order() -> None:
    history = [
        Message(role="user", content="第一句", timestamp=1.0),
        Message(role="nyx", content="第二句", timestamp=2.0),
        Message(role="user", content="第三句", timestamp=3.0),
    ]
    result = build_backtrack_context(
        "句子", history, now=3.0, time_gap=3600.0, max_len=2
    )
    assert [m.content for m in result] == ["第二句", "第三句"]


def test_backtrack_time_gap() -> None:
    history = [
        Message(role="user", content="很久以前说的", timestamp=0.0),
        Message(role="user", content="刚刚说的", timestamp=100.0),
    ]
    result = build_backtrack_context(
        "说的", history, now=100.0, time_gap=50.0, max_len=20
    )
    assert [m.content for m in result] == ["刚刚说的"]


def test_backtrack_fast_nyx_skipped_continues() -> None:
    history = [
        Message(role="user", content="我爬山很开心", timestamp=1.0),
        Message(role="nyx", content="嗯嗯", timestamp=2.0, fast=True),
    ]
    result = build_backtrack_context(
        "爬山", history, now=2.0, time_gap=3600.0, max_len=20
    )
    assert [m.content for m in result] == ["我爬山很开心"]


def test_backtrack_zero_overlap_stops() -> None:
    history = [Message(role="user", content="天气不错", timestamp=1.0)]
    result = build_backtrack_context(
        "量子力学", history, now=1.0, time_gap=3600.0, max_len=20
    )
    assert result == []


def test_backtrack_short_message_skips_overlap_stop() -> None:
    # 短确认语（< _MIN_OVERLAP_LEN）零重叠不误清历史，仍累积前文
    history = [Message(role="user", content="天气不错", timestamp=1.0)]
    result = build_backtrack_context(
        "好的", history, now=1.0, time_gap=3600.0, max_len=20
    )
    assert [m.content for m in result] == ["天气不错"]


def test_backtrack_relevant_continues() -> None:
    history = [Message(role="user", content="天气不错", timestamp=1.0)]
    result = build_backtrack_context(
        "今天天气如何", history, now=1.0, time_gap=3600.0, max_len=20
    )
    assert [m.content for m in result] == ["天气不错"]


def test_no_char_overlap() -> None:
    assert _no_char_overlap("量子", "天气") is True
    assert _no_char_overlap("天气", "天气不错") is False
    assert _no_char_overlap("你 好", "你好") is False  # 空白忽略
