from enum import StrEnum


class EventType(StrEnum):
    USER_MESSAGE = "user_message"          # 用户消息
    USER_MATERIAL = "user_material"        # 用户投喂资料（上传课本/文档供尼克斯读）
    CLOCK_TICK = "clock_tick"              # 时钟 tick
    OBSERVATION_STATE = "observation_state"  # 观察状态
    SPEAK = "speak"                        # 说出来
    ASK = "ask"                            # 问用户
    THINK = "think"                        # 内心话（仅日志，不路由）
    MUTTER = "mutter"                      # 碎碎念
    INITIATE_CHAT = "initiate_chat"        # 搭话
    EMOTION_UPDATE = "emotion_update"      # 情感更新
    REFLECTION = "reflection"              # 反思
    REFLECTION_DONE = "reflection_done"    # 反思完成（仅广播前端：叙事/欲望刷新+气泡）
    MEMORY_CREATED = "memory_created"      # 记忆生成
    MEMORY_PROMOTED = "memory_promoted"    # 记忆升级（短期→长期）
    DESIRE_GENERATED = "desire_generated"  # 欲望产生
    DESIRE_SATISFIED = "desire_satisfied"  # 欲望满足
    DESIRE_EXPIRED = "desire_expired"      # 欲望淘汰
    ACTIVITY_START = "activity_start"      # 活动开始
    ACTIVITY_END = "activity_end"          # 活动结束
    ACTIVITY_INTERRUPTED = "activity_interrupted"  # 活动打断
    EXPLORATION_STEP = "exploration_step"  # 探索逐步进度（每节点一推，仅广播前端）
    ENCOUNTER_START = "encounter_start"    # 遭遇开始（广播前端：文本 + 可点选项）
    ENCOUNTER_CHOICE = "encounter_choice"  # 用户选选项（广播）
    ENCOUNTER_END = "encounter_end"        # 遭遇结束（结局叙事 + 后果，路由回写）


class EncounterKind(StrEnum):
    """遭遇三类：欲望搭话（重分类 initiate_chat）/ 随机事件 / 成长时刻。"""
    DESIRE_CHAT = "desire_chat"      # 欲望搭话（本 spec 只前端重分类，后端不动）
    RANDOM_EVENT = "random_event"    # 随机事件（块边界掷骰）
    GROWTH_MOMENT = "growth_moment"  # 成长时刻（里程碑，可抢占）


class OptionTone(StrEnum):
    """遭遇选项倾向（4 档），后果由纯函数按 tone 派生。"""
    BOLD = "bold"                    # 勇敢主动
    CAUTIOUS = "cautious"            # 谨慎稳妥
    GENTLE = "gentle"                # 温柔共情
    RECKLESS = "reckless"            # 鲁莽冒险


class Source(StrEnum):
    EXTERNAL = "external"                  # 外部
    INTERNAL = "internal"                  # 内部


class TickType(StrEnum):
    SCHEDULE_BLOCK_START = "schedule_block_start"  # 日程块开始
    DESIRE_EVAL = "desire_eval"            # 欲望评估
    MUTTER_CHECK = "mutter_check"          # 碎碎念检查
    INITIATE_CHAT_CHECK = "initiate_chat_check"  # 搭话检查
    REFLECTION_CHECK = "reflection_check"        # 反思检查（定时+积累门槛）


class ContextMode(StrEnum):
    FAST = "fast"                          # 快通道
    SLOW = "slow"                          # 慢通道


class EmotionCategory(StrEnum):            # 8 档，1:1 对应前端 assets/expressions/（方形头像）
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
    PAUSED = "paused"
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
