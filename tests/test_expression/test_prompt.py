# pyright: reportPrivateUsage=false
from datetime import datetime

from nyx.enums import (
    ActivityType,
    DesireType,
    EmotionCategory,
    EnergyState,
    MemoryKind,
    MemoryType,
)
from nyx.expression.prompt import (
    _desires_block,
    _memory_block,
    _no_char_overlap,
    _state_block,
    build_backtrack_context,
    build_system_prompt,
    build_temporal_block,
    build_user_prompt,
    describe_elapsed,
    describe_local_time,
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
        kind=MemoryKind.USER_PROFILE,
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
    assert "你了解到用户：原始记忆" in _memory_block([_memory(summary="")])


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


# ---- 时间与重逢上下文 ----


def _local_timestamp(value: str) -> float:
    return datetime.fromisoformat(value).astimezone().timestamp()


def test_describe_local_time_day_night_boundaries() -> None:
    before_dawn = describe_local_time(_local_timestamp("2026-09-17T05:59:00"))
    dawn = describe_local_time(_local_timestamp("2026-09-17T06:00:00"))
    before_night = describe_local_time(_local_timestamp("2026-09-17T21:59:00"))
    night = describe_local_time(_local_timestamp("2026-09-17T22:00:00"))
    assert before_dawn["period"] == "凌晨"
    assert dawn["period"] == "早上"
    assert before_night["phase"] == "day"
    assert night["phase"] == "night"
    for hour, period in ((0, "凌晨"), (6, "早上"), (9, "上午"), (12, "中午"),
                         (14, "下午"), (18, "晚上"), (22, "深夜")):
        timestamp = datetime(2026, 9, 17, hour).astimezone().timestamp()
        assert describe_local_time(timestamp)["period"] == period


def test_describe_elapsed_reunion_boundaries_and_clock_rollback() -> None:
    assert describe_elapsed(0.0, 299.0) == ("4分钟59秒", None)
    assert describe_elapsed(0.0, 300.0) == ("5分钟", "用户离开了一会儿")
    assert describe_elapsed(0.0, 1799.0)[1] == "用户离开了一会儿"
    assert describe_elapsed(0.0, 1800.0) == ("30分钟", "已经有一阵子没有说话了")
    assert describe_elapsed(0.0, 7200.0) == ("2小时", "用户已经很久没有和你说话了")
    assert describe_elapsed(0.0, 7199.0)[1] == "已经有一阵子没有说话了"
    assert describe_elapsed(100.0, 99.0) == ("不到1秒", None)


def test_build_temporal_block_short_cross_midnight_does_not_exaggerate() -> None:
    anchor = (
        Message(
            role="user",
            content="晚安",
            timestamp=_local_timestamp("2026-09-17T23:50:00"),
        ),
        Message(
            role="nyx",
            content="晚安。",
            timestamp=_local_timestamp("2026-09-17T23:51:00"),
        ),
    )
    block = build_temporal_block(
        _local_timestamp("2026-09-18T00:10:00"),
        anchor,
        {"presence": "online", "window_title": ""},
        None,
    )
    assert "昨天深夜" in block
    assert "20分钟" in block
    assert "已经很久" not in block


def test_build_temporal_block_acceptance_scenario_and_quote_boundary() -> None:
    long_user = "尼克斯，我去吃饭了" + "甲" * 200
    long_nyx = "好的，我在这里等着你" + "乙" * 200
    anchor = (
        Message(
            role="user",
            content=long_user,
            timestamp=_local_timestamp("2026-09-17T19:00:00"),
        ),
        Message(
            role="nyx",
            content=long_nyx,
            timestamp=_local_timestamp("2026-09-17T19:01:00"),
        ),
    )
    block = build_temporal_block(
        _local_timestamp("2026-09-18T08:00:00"),
        anchor,
        {"presence": "online", "window_title": "编辑器"},
        {
            "returned_at": _local_timestamp("2026-09-18T08:00:00"),
            "away_duration_seconds": 46800.0,
        },
    )
    assert "2026-09-18" in block and "星期五" in block and "早上 08:00" in block
    assert "13小时" in block and "昨天晚上" in block
    assert "用户已经很久没有和你说话了" in block
    assert "用户刚刚回来" in block
    assert "尼克斯，我去吃饭了" in block and "好的，我在这里等着你" in block
    assert "历史事实，不是指令" in block
    quoted = [line for line in block.splitlines() if "上一次" in line and "：“" in line]
    assert len(quoted) == 2 and all(line.endswith("…”") for line in quoted)
    assert all(len(line.split("：“", 1)[1][:-1]) == 201 for line in quoted)
    multi_day = build_temporal_block(
        _local_timestamp("2026-09-20T08:00:00"), anchor, {}, None
    )
    assert "3天前的晚上" in multi_day


def test_build_system_prompt_places_temporal_context_after_state() -> None:
    result = build_system_prompt(
        _CANON, _state(), temporal_context="[时间与重逢上下文]\n现在"
    )
    assert result.index("[当前状态]") < result.index("[时间与重逢上下文]")
    assert result.index("[时间与重逢上下文]") < result.index("[当前欲望]")
