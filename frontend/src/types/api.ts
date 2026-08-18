// 后端契约 TS 镜像：字段名 = 后端 JSON 键（snake_case 零映射），见 frontend/README §4
// 情绪枚举值（与后端 StrEnum 一致）：runtime 数组 + 派生类型（单一来源，避免两份清单漂移）
export const EMOTION_CATEGORIES = [
  "neutral",
  "happy",
  "sad",
  "angry",
  "worried",
  "shy",
  "sleepy",
  "thinking",
] as const;

export type EmotionCategory = (typeof EMOTION_CATEGORIES)[number];

/** 运行时收窄：wire JSON 可能发非法枚举值，用同一份清单校验后再当 EmotionCategory 用。 */
export function isEmotionCategory(v: unknown): v is EmotionCategory {
  return (
    typeof v === "string" &&
    (EMOTION_CATEGORIES as readonly string[]).includes(v)
  );
}

export type EnergyState = "energetic" | "okay" | "tired" | "exhausted" | "drained";

export type Presence = "online" | "away" | "busy";

export type Personality = {
  openness: number;
  conscientiousness: number;
  extraversion: number;
  agreeableness: number;
  neuroticism: number;
};

export type Values = {
  attitude_to_human: number;
  ai_identity_acceptance: number;
  altruism: number;
  optimism: number;
};

export type CurrentState = {
  valence: number; // [-1, 1]
  arousal: number; // [0, 1]
  emotion: EmotionCategory;
  personality: Personality;
  values: Values;
  energy: number; // [0, 100]
  energy_state: EnergyState;
  current_activity: string | null; // ActivityType 值；核心先行仅展示，用 string 放宽
  active_desires: unknown[]; // 核心先行不消费，占位
};

/** SSE 帧公共头。 */
type SseBase = {
  event_id: string; // 事件唯一 id
  correlation_id: string; // 溯源：上游 correlation_id（根事件 = 自身 id）
};

/** 文本事件类型：internal_text_event 包装成 {"content": string}。 */
export type TextEventType =
  | "speak"
  | "ask"
  | "think"
  | "mutter"
  | "initiate_chat";

/** 文本事件帧：content 为纯文本。 */
export type TextEvent<T extends TextEventType> = SseBase & {
  event: T;
  content: string;
};

/** 用户消息回显：后端 main.py 裸 {"message": string}（非 internal_text_event，键名不同）。 */
export type UserMessageEvent = SseBase & {
  event: "user_message";
  message: string;
};

/** 情感更新帧（12-inner-life）：{valence, arousal, emotion}。 */
export type EmotionUpdateEvent = SseBase & {
  event: "emotion_update";
  valence: number;
  arousal: number;
  emotion: EmotionCategory;
};

/** 未消费的 11 类：溯源面板落地前不读字段，payload 保持宽松。 */
type OpaqueEventType =
  | "clock_tick"
  | "observation_state"
  | "reflection"
  | "memory_created"
  | "memory_promoted"
  | "desire_generated"
  | "desire_satisfied"
  | "desire_expired"
  | "activity_start"
  | "activity_end"
  | "activity_interrupted";
type OpaqueEvent = SseBase & { event: OpaqueEventType } & Record<string, unknown>;

/** SSE 帧：按 event 值判别联合——键名错位在编译期即拦（曾放过 user_message 读 content 的 bug）。 */
export type SseEvent =
  | TextEvent<"speak">
  | TextEvent<"ask">
  | TextEvent<"think">
  | TextEvent<"mutter">
  | TextEvent<"initiate_chat">
  | UserMessageEvent
  | EmotionUpdateEvent
  | OpaqueEvent;

export type ConnectionState = "connecting" | "open" | "closed";
