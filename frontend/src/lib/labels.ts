// 枚举值 → 中文显示映射（UI 展示层专用）。
// 键 = 后端 JSON 枚举值（snake_case 原样，零映射），值 = 用户可见中文。
// 单一来源：面板组件不再硬编码英文枚举，统一经 `label()` 取中文，未收录的值回退原值（防后端新增成员时不崩）。

import type {
  ActivityStatus,
  ActivityType,
  AssignedTaskStatus,
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

export const MEMORY_TYPE_LABELS: Record<MemoryType, string> = {
  short_term: "短期",
  long_term: "长期",
};

export const ACTIVITY_TYPE_LABELS: Record<ActivityType, string> = {
  reading: "阅读",
    browser_survey: "填写问卷",
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

export const ASSIGNED_TASK_STATUS_LABELS: Record<AssignedTaskStatus, string> = {
  pending: "排队中",
    running: "进行中",
  completed: "已完成",
  failed: "失败",
};

// eval 记账 output_type → 中文。未收录值经 label() 回退原值。
export const OUTPUT_TYPE_LABELS: Record<string, string> = {
  reply: "回复",
  speak: "对外",
  think: "内心",
  initiate_chat: "搭话",
  mutter: "碎碎念",
  mutter_wander: "走神",
  reflection: "反思",
  desire: "欲望",
  tool: "工具",
};

/** 查中文标签，未收录回退原值（string 键，用于自由/宽松字段）。 */
export function label(map: Record<string, string>, key: string): string {
  return map[key] ?? key;
}

