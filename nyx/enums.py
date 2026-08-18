from enum import StrEnum


class EventType(StrEnum):
    USER_MESSAGE = "user_message"          # 用户消息
    CLOCK_TICK = "clock_tick"              # 时钟 tick
    OBSERVATION_STATE = "observation_state"  # 观察状态
    SPEAK = "speak"                        # 说出来
    ASK = "ask"                            # 问用户
    THINK = "think"                        # 内心话（仅日志，不路由）
    MUTTER = "mutter"                      # 碎碎念
    INITIATE_CHAT = "initiate_chat"        # 搭话
    EMOTION_UPDATE = "emotion_update"      # 情感更新
    REFLECTION = "reflection"              # 反思
    MEMORY_CREATED = "memory_created"      # 记忆生成
    MEMORY_PROMOTED = "memory_promoted"    # 记忆升级（短期→长期）
    DESIRE_GENERATED = "desire_generated"  # 欲望产生
    DESIRE_SATISFIED = "desire_satisfied"  # 欲望满足
    DESIRE_EXPIRED = "desire_expired"      # 欲望淘汰
    ACTIVITY_START = "activity_start"      # 活动开始
    ACTIVITY_END = "activity_end"          # 活动结束
    ACTIVITY_INTERRUPTED = "activity_interrupted"  # 活动打断


class Source(StrEnum):
    EXTERNAL = "external"                  # 外部
    INTERNAL = "internal"                  # 内部


class TickType(StrEnum):
    SCHEDULE_BLOCK_START = "schedule_block_start"  # 日程块开始
    DESIRE_EVAL = "desire_eval"            # 欲望评估
    MUTTER_CHECK = "mutter_check"          # 碎碎念检查
    INITIATE_CHAT_CHECK = "initiate_chat_check"  # 搭话检查


class ContextMode(StrEnum):
    FAST = "fast"                          # 快通道
    SLOW = "slow"                          # 慢通道


class EmotionCategory(StrEnum):            # 8 档，1:1 对应前端 sprites/ 与 expressions/
    NEUTRAL = "neutral"                    # 平静：valence≈0、arousal 低
    HAPPY = "happy"                        # 开心：valence+、arousal+
    SAD = "sad"                            # 难过：valence-、arousal 低
    ANGRY = "angry"                        # 生气：valence-、arousal+
    WORRIED = "worried"                    # 担忧：valence-、arousal 中高
    SHY = "shy"                            # 害羞：valence+、arousal 低
    SLEEPY = "sleepy"                      # 困倦：精力低（覆盖）
    THINKING = "thinking"                  # 思考：认知态（覆盖）


class DesireType(StrEnum):
    INTERACTION = "interaction"            # 互动
    EXPLORATION = "exploration"            # 探索
    CREATION = "creation"                  # 创造
    REST = "rest"                          # 休息


class ActivityType(StrEnum):
    READING = "reading"                    # 读书
    FREE_EXPLORATION = "free_exploration"  # 自由探索
    CREATION = "creation"                  # 创作
    OBSERVE_USER = "observe_user"          # 观察用户
    IDLE_REFLECTION = "idle_reflection"    # 发呆(反思)
    REST = "rest"                          # 休息


class MemoryType(StrEnum):
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"


class DesireStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SATISFIED = "satisfied"
    EXPIRED = "expired"
    SUPPRESSED = "suppressed"


class ActivityStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    ABANDONED = "abandoned"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"


class EnergyState(StrEnum):
    ENERGETIC = "energetic"
    OKAY = "okay"
    TIRED = "tired"
    EXHAUSTED = "exhausted"
    DRAINED = "drained"


class SearchMode(StrEnum):                 # 记忆检索三层；内部层标签，不在公开签名暴露
    KEYWORD = "keyword"
    VECTOR = "vector"
    ASSOCIATION = "association"


# 可量化目标动作，完成判定为纯函数须 switch 这些值
class GoalAction(StrEnum):
    READ = "read"
    WRITE = "write"
    OBSERVE = "observe"
