# 内在状态面板（`components/inner/`）

> 核心面板之二：可视化 Nyx 的「内在状态」——情绪（valence-arousal）、精力、情绪 sprite、Big Five 性格、三观。
> 范围：`components/inner/{InnerStatePanel,ValenceArousalPlot,EmotionSprite,EnergyBar,BigFiveChart,ValuesChart,BarChart}.tsx`。
> 数据源：`GET /api/state` 快照（`innerLifeStore.current`）+ SSE `emotion_update` 增量。

## 1. 组件树

```
InnerStatePanel                 # 面板容器（layout/Panel 包裹），读 innerLifeStore.current
├─ EmotionSprite                # 当前情绪 sprite（大图，最显眼）
├─ ValenceArousalPlot           # 二维散点图：x=valence, y=arousal
├─ EnergyBar                    # 精力条 + energy_state 文案
├─ BigFiveChart                 # 五维条形/雷达（openness..neuroticism）
└─ ValuesChart                  # 三观四维（attitude_to_human..optimism）
```

- `InnerStatePanel`：`useInnerLifeStore(s => s.current)`；`current === null` 时显示「加载中/未连接」；`error` 非 null 时在面板顶部红字一行显示（同 03 ChatInput 的 sendError）。
- 各子组件只收**它需要的字段**作 props（如 `ValenceArousalPlot` 收 `{valence, arousal}`），不传整个 `CurrentState`，减少重渲染（简单，非过度抽象——每子组件确实独立消费）。

## 2. 情绪 sprite（`EmotionSprite`）

- 8 张图放 `assets/sprites/`，文件名 = `EmotionCategory` 值（`neutral.png` / `happy.png` / … / `thinking.png`），1:1 映射，组件按 `current.emotion` 选图，无 switch 分支。
- sprite 同时被聊天面板复用（03-chat-panel §3），是情绪的唯一视觉载体。
- 占位期：先用 emoji 或纯色块占位（`NEUTRAL→😐` 等），真图后续补；文件名约定不变，替换即生效。
- **size 变体**：`small`（气泡内 2rem，当前无调用）/ `large`（面板大图）/ `portrait`（半身像立绘，App 层 `app-stage` 左侧，CSS `object-fit: cover` + `object-position: top center` 裁切全身像下半身，见 01-sse §6）。

## 3. Valence-Arousal 图（`ValenceArousalPlot`）

- 二维散点：x 轴 `valence ∈ [-1, 1]`（左负右正），y 轴 `arousal ∈ [0, 1]`（下低上高）。单点 + 十字虚线标出当前值。
- 用 `<canvas>` 或轻量 SVG 手绘（核心先行不引图表库——单点定位图，canvas 足够，避免新依赖）。**不引 recharts/d3**（未请求的复杂度）。
- 语义对齐 design §4.2：`valence` 正负 = 情绪正负，`arousal` 高低 = 激活度；图上可轻标注象限（高兴/平静/愤怒/低落），纯视觉辅助。

## 4. 精力条（`EnergyBar`）

- 横向进度条：`energy ∈ [0, 100]`；文案经 `ENERGY_LABELS`（`lib/labels.ts`）把 `energy_state` 转中文（`energetic→精力充沛` / `tired→疲惫` …），未知键回退原值。
- 颜色按 `energy_state` 分段（绿→黄→红），纯视觉。

## 5. Big Five 与三观（`BigFiveChart` / `ValuesChart`）

- **Big Five**：五维 `1-10`（`openness`/`conscientiousness`/`extraversion`/`agreeableness`/`neuroticism`），**条形图**（默认；五边雷达可选，手绘 SVG，不引库）。
- **三观**：四维 `1-10`（`attitude_to_human`/`ai_identity_acceptance`/`altruism`/`optimism`），同款条形。
- 两者共享内部 `BarChart`（收 `keys: readonly string[]` + `data: Record<string, number>` + 可选 `labels?: Record<string, string>`，渲染逻辑与越界钳制收敛于此）；`BigFiveChart`/`ValuesChart` 只传各自的 keys 数组 + 数据对象 + `labels`，不重复渲染逻辑。
- **标签经 `lib/labels.ts` 转中文**：`BigFiveChart` 传 `PERSONALITY_LABELS`（`openness→开放性` …）、`ValuesChart` 传 `VALUES_LABELS`（`attitude_to_human→对人类的态度` …）；`BarChart` 渲染 `labels?.[key] ?? key`，未知键回退原值。
- 这些是慢变量（无高频事件），只在 `refreshState` 全量刷新时重绘（02-stores §2）。

## 6. 边界

- **纯展示**：本面板只读不写，无任何发往后端的动作；`emotion_update` 由 SSE 自动推，用户不改内在状态（MVP）。
- **`current_activity` / `active_desires`**：核心先行不在本面板展示（活动/欲望面板后续），字段已在类型里占位，忽略即可。
- **未连接**：`current === null` 时面板整体占位「等待核心服务连接…」，不渲染子组件（避免 `undefined` 字段崩）。

## 7. 测试（`tests/stores.test.ts` 已覆盖数据层）

- 组件层（React Testing Library）轻测：`EnergyBar` 按 `energy`/`energy_state` 渲染中文文案、`EmotionSprite` 按 `emotion` 选图文件名、`BigFiveChart`/`ValuesChart` 按 personality/values 渲染中文标签 + 数值、`InnerStatePanel` 在 `current=null` 时整体占位不崩（§6 守卫在面板层，不渲染子组件）。
- 图表坐标/像素不做断言（README §6）。
