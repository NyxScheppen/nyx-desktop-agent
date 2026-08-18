// 后端契约 TS 镜像：字段名 = 后端 JSON 键（snake_case 零映射），见 frontend/README §4
export type EmotionCategory =
  | "neutral"
  | "happy"
  | "sad"
  | "angry"
  | "worried"
  | "shy"
  | "sleepy"
  | "thinking";

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

/** SSE 帧：event: 行 + data: 行解析结果，其余键 = event.content 展开。 */
export type SseEvent = {
  event: string; // EventType 值（snake_case）
  event_id: string;
  correlation_id: string;
} & Record<string, unknown>;

export type ConnectionState = "connecting" | "open" | "closed";
