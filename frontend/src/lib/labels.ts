import inner_lifePolicy from "../../../nyx/settings/inner_life.json";
import desirePolicy from "../../../nyx/settings/desire.json";
import memoryPolicy from "../../../nyx/settings/memory.json";
import activityPolicy from "../../../nyx/settings/activity.json";
import expressionPolicy from "../../../nyx/settings/expression.json";
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

export const EMOTION_LABELS: Record<EmotionCategory, string> = inner_lifePolicy.frontend.labels.EMOTION_LABELS as Record<EmotionCategory, string>;

export const ENERGY_LABELS: Record<EnergyState, string> = inner_lifePolicy.frontend.labels.ENERGY_LABELS as Record<EnergyState, string>;

export const DESIRE_TYPE_LABELS: Record<DesireType, string> = desirePolicy.frontend.labels.DESIRE_TYPE_LABELS as Record<DesireType, string>;

export const DESIRE_STATUS_LABELS: Record<DesireStatus, string> = desirePolicy.frontend.labels.DESIRE_STATUS_LABELS as Record<DesireStatus, string>;

export const MEMORY_TYPE_LABELS: Record<MemoryType, string> = memoryPolicy.frontend.labels.MEMORY_TYPE_LABELS as Record<MemoryType, string>;

export const ACTIVITY_TYPE_LABELS: Record<ActivityType, string> = activityPolicy.frontend.labels.ACTIVITY_TYPE_LABELS as Record<ActivityType, string>;

export const ACTIVITY_STATUS_LABELS: Record<ActivityStatus, string> = activityPolicy.frontend.labels.ACTIVITY_STATUS_LABELS as Record<ActivityStatus, string>;

export const ASSIGNED_TASK_STATUS_LABELS: Record<AssignedTaskStatus, string> = activityPolicy.frontend.labels.ASSIGNED_TASK_STATUS_LABELS as Record<AssignedTaskStatus, string>;

// eval 记账 output_type → 中文。未收录值经 label() 回退原值。
export const OUTPUT_TYPE_LABELS: Record<string, string> = expressionPolicy.frontend.labels.OUTPUT_TYPE_LABELS as Record<string, string>;

/** 查中文标签，未收录回退原值（string 键，用于自由/宽松字段）。 */
export function label(map: Record<string, string>, key: string): string {
  return map[key] ?? key;
}

