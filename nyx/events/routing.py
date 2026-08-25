from nyx.enums import EventType, TickType

# ROUTING：EventType → 内部消费者模块名（空 = 仅广播前端，无内部消费者）。
# 值取 {"expression", "inner_life", "desire", "activity", "memory"}。
# CLOCK_TICK 不在此表（走 TICK_ROUTING）。
ROUTING: dict[EventType, list[str]] = {
    EventType.USER_MESSAGE:        ["expression"],
    EventType.USER_MATERIAL:       ["activity"],               # 上传资料 → 触发读书活动
    EventType.OBSERVATION_STATE:   ["inner_life", "desire"],   # 情感 + 互动欲加压
    EventType.DESIRE_GENERATED:    ["activity"],
    # 欲望→内在生命唯一耦合点（走事件防成环）
    EventType.DESIRE_SATISFIED:    ["inner_life"],
    EventType.DESIRE_EXPIRED:      [],
    EventType.ACTIVITY_START:      [],
    EventType.ACTIVITY_END:        ["desire", "inner_life", "memory"],  # 满足+情感+记忆
    EventType.ACTIVITY_INTERRUPTED: [],
    EventType.SPEAK:               [],
    EventType.ASK:                 [],
    EventType.THINK:               [],
    EventType.MUTTER:              [],
    EventType.INITIATE_CHAT:       [],
    EventType.EMOTION_UPDATE:      [],
    # 协调器，内部调 memory/desire
    EventType.REFLECTION:          ["inner_life"],
    # 反思完成：仅广播前端（叙事/欲望刷新 + 高亮），无内部消费者
    EventType.REFLECTION_DONE:     [],
    EventType.MEMORY_CREATED:      [],
    EventType.MEMORY_PROMOTED:     [],
}

# TICK_ROUTING：clock_tick 按 content.tick_type 二次路由
# （1 个 tick_type → 1 个消费者，非广播）。
TICK_ROUTING: dict[TickType, list[str]] = {
    TickType.SCHEDULE_BLOCK_START: ["activity"],
    TickType.DESIRE_EVAL:          ["desire"],
    TickType.MUTTER_CHECK:         ["expression"],
    TickType.INITIATE_CHAT_CHECK:  ["expression"],
    TickType.REFLECTION_CHECK:     ["inner_life"],
}
