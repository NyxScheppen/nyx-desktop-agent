# 前端设计（galgame 方向）

> 本文记录前端视觉/交互/语音的决策，作为日后 frontend spec 的依据。
> 技术栈：TS/React（strict）+ Tauri 薄壳，SSE over localhost 实时推送。

## 1. 视觉方向

- galgame 形态：**背景 + 立绘 + 头像 + 对话框**。

## 2. 窗口形态（双模式）

- **迷你模式**（always-on-top 常驻）：头像 + 状态条 + 碎碎念气泡。
- **全屏 galgame 模式**：背景 + 立绘 + 对话框 + 底部状态条 + 观测标签页。
- 一键切换。

## 3. 面板布局

- galgame 表层为主 + 底部常驻状态条 + 常驻观测标签页。

## 4. 表情（8 档）

- `assets/sprites/`（立绘）与 `assets/expressions/`（头像）各 8 张，同键。
- 键 = `EmotionCategory` 8 个成员：`neutral / happy / sad / angry / worried / shy / sleepy / thinking`。
- 选择优先级：**困倦 > 思考 > 情绪**。
- SSE `emotion_update` 的 `emotion` 字段直接 = sprite 文件名，前端零映射换图。

## 5. 背景（7 张）

- 6 活动背景 + 1 对话默认背景。
- 时间色调渐变**只叠背景**，立绘色调不变。
- 对话中固定用"对话默认背景"。

## 6. 状态条

- 精力条 + 当前活动文字。心情由立绘/头像表情承担，不重复。

## 7. 头像用法（3 处）

- 迷你模式 / 对话框名字牌 / 系统通知（toast）。

## 8. 碎碎念呈现

- 气泡（头像旁冒出，几秒后淡出）；上语音后同时念出。

## 9. 观测标签页（6 面板 = 作品集展示）

| 面板 | 展示 | 端点 |
|---|---|---|
| 内在 | valence/arousal 图、Big Five、三观、精力、自我叙事 | `get_state` / `get_narrative` |
| 记忆 | 记忆列表 + 检索 + 联想图（networkx） | `list_memories` / `search` |
| 欲望 | 短期 + 长期 + 值 | `get_pending` / `get_all` |
| 活动 | 时间轴 + 当前活动 | `get_schedule` / `get_current` |
| eval | 评分报告 + token 台账 | `list_reports` / `list_token_usage` |
| 事件 | 事件流（correlation_id 溯源） | SSE 实时 + `GET /api/events/log` |

## 10. 语音（TTS）

- **范围**：只 TTS（尼克斯说），不做 STT（输入保持打字）。
- **引擎**：GPT-SoVITS（本地、离线、few-shot 克隆/微调）。
- **架构**：纯消费端语音层——订阅 `SPEAK` / `ASK` / `MUTTER` / `INITIATE_CHAT` → TTS → 播放。不进核心管道、不反向影响内在生命/记忆/欲望。`THINK` 是内心独白，不念。
- **部署**：GPT-SoVITS 作为独立本地推理服务（自带 API），nyx 语音层调它；不把 torch/模型塞进 nyx 核心。
- **待定**：尼克斯的参考音色（few-shot 需要参考音频）。
