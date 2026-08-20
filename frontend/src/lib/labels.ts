// 枚举值 → 中文显示映射（UI 展示层专用）。
// 键 = 后端 JSON 枚举值（snake_case 原样，零映射），值 = 用户可见中文。
// 单一来源：面板组件不再硬编码英文枚举，统一经 `label()` 取中文，未收录的值回退原值（防后端新增成员时不崩）。

import type {
  ActivityStatus,
  ActivityType,
  DesireStatus,
  DesireType,
  EmotionCategory,
  EnergyState,
  MemoryType,
} from "../types/api";

export const EMOTION_LABELS: Record<EmotionCategory, string> = {
  neutral: "平静",
  happy: "开心",
  sad: "悲伤",
  angry: "生气",
  worried: "担忧",
  shy: "害羞",
  sleepy: "困倦",
  thinking: "思考",
};

export const ENERGY_LABELS: Record<EnergyState, string> = {
  energetic: "精力充沛",
  okay: "尚可",
  tired: "疲惫",
  exhausted: "筋疲力尽",
  drained: "枯竭",
};

export const DESIRE_TYPE_LABELS: Record<DesireType, string> = {
  interaction: "互动",
  exploration: "发现",
  creation: "创作",
  rest: "休息",
};

export const DESIRE_STATUS_LABELS: Record<DesireStatus, string> = {
  pending: "待定",
  active: "进行中",
  satisfied: "已满足",
  expired: "已过期",
  suppressed: "被抑制",
};

export const ACTIVITY_TYPE_LABELS: Record<ActivityType, string> = {
  reading: "阅读",
  free_exploration: "自由探索",
  creation: "创作",
  observe_user: "观察用户",
  idle_reflection: "静默反思",
  rest: "休息",
};

export const ACTIVITY_STATUS_LABELS: Record<ActivityStatus, string> = {
  pending: "待定",
  running: "进行中",
  abandoned: "放弃",
  completed: "完成",
  incomplete: "未完成",
};

export const MEMORY_TYPE_LABELS: Record<MemoryType, string> = {
  short_term: "短期",
  long_term: "长期",
};

// Big Five 五维键名（Personality）与三观四维键名（Values）→ 中文。
// 键是类型字段名（snake_case），非枚举，故用 string 键 + 可选回退。
export const PERSONALITY_LABELS: Record<string, string> = {
  openness: "开放性",
  conscientiousness: "尽责性",
  extraversion: "外向性",
  agreeableness: "宜人性",
  neuroticism: "神经质",
};

export const VALUES_LABELS: Record<string, string> = {
  attitude_to_human: "对人类的态度",
  ai_identity_acceptance: "AI 身份认同",
  altruism: "利他",
  optimism: "乐观",
};

// eval 评分键名 → 中文（EvalScores 三字段）
export const SCORE_LABELS: Record<string, string> = {
  format: "格式",
  ooc: "出戏",
  relevance: "相关性",
};

/** 查中文标签，未收录回退原值（string 键，用于自由/宽松字段）。 */
export function label(map: Record<string, string>, key: string): string {
  return map[key] ?? key;
}
