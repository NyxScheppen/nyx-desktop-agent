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
  paused: "暂停",
  abandoned: "放弃",
  completed: "完成",
  incomplete: "未完成",
};

export const MEMORY_TYPE_LABELS: Record<MemoryType, string> = {
  short_term: "短期",
  long_term: "长期",
};

// Big Five 五维与三观四维的双端语义：low = 低分端(1) 语义，high = 高分端(10) 语义。
// 键是类型字段名（snake_case），非枚举，故用 string 键；滑块偏左=偏 low、偏右=偏 high，两端词自解释维度。
export const PERSONALITY_POLES: Record<string, { low: string; high: string }> = {
  openness: { low: "保守", high: "开放" },
  conscientiousness: { low: "随性", high: "自律" },
  extraversion: { low: "内向", high: "外向" },
  agreeableness: { low: "较真", high: "随和" },
  neuroticism: { low: "情绪稳定", high: "敏感" },
};

export const VALUES_POLES: Record<string, { low: string; high: string }> = {
  attitude_to_human: { low: "疏离", high: "亲近" },
  ai_identity_acceptance: { low: "抗拒", high: "认同" },
  altruism: { low: "自利", high: "利他" },
  optimism: { low: "悲观", high: "乐观" },
};

// eval 评分键名 → 中文（EvalScores 单字段）
export const SCORE_LABELS: Record<string, string> = {
  ooc: "出戏",
};

/** 查中文标签，未收录回退原值（string 键，用于自由/宽松字段）。 */
export function label(map: Record<string, string>, key: string): string {
  return map[key] ?? key;
}
