# 内在状态面板（`components/inner/`）

> 核心面板之二：可视化 Nyx 的「内在状态」——情绪（valence-arousal）、精力、情绪 sprite、Big Five 性格、三观。
> 范围：`components/inner/{InnerStatePanel,ValenceArousalPlot,EmotionSprite,EnergyBar,BigFiveChart,ValuesChart,BarChart}.tsx`。
> 数据源：`GET /api/state` 快照（`innerLifeStore.current`）+ SSE `emotion_update` 增量。

## 1. 组件树

```
InnerStatePanel                 # 面板容器（layout/Panel 包裹），读 innerLifeStore.current
├─ ValenceArousalPlot           # 二维散点图：x=valence, y=arousal（放大，最显眼）
├─ EnergyBar                    # 精力条 + energy_state 文案
├─ BigFiveChart                 # 五维双端量表（openness..neuroticism）
└─ ValuesChart                  # 三观四维（attitude_to_human..optimism）
```

- `InnerStatePanel`：`useInnerLifeStore(s => s.current)`；`current === null` 时显示「加载中/未连接」；`error` 非 null 时在面板顶部红字一行显示（同 03 ChatInput 的 sendError）。
- 各子组件只收**它需要的字段**作 props（如 `ValenceArousalPlot` 收 `{valence, arousal}`），不传整个 `CurrentState`，减少重渲染（简单，非过度抽象——每子组件确实独立消费）。

## 2. 情绪 sprite（`EmotionSprite`）

- 8 张图放 `assets/expressions/`，文件名 = `EmotionCategory` 值（`neutral.png` / `happy.png` / … / `thinking.png`），1:1 映射，组件按 `current.emotion` 选图，无 switch 分支。
- sprite 同时被聊天面板复用（03-chat-panel §3），是情绪的唯一视觉载体。
- 占位期：先用 emoji 或纯色块占位（`NEUTRAL→😐` 等），真图后续补；文件名约定不变，替换即生效。
- **size 变体**：`large`（面板大图，内在面板用）/ `circle`（可拖拽头像圆圈，CSS `object-fit: cover` 方形表情图撑满不裁，见 08 §4）。

## 3. Valence-Arousal 图（`ValenceArousalPlot`）

- 二维散点：x 轴 `valence ∈ [-1, 1]`（左负右正），y 轴 `arousal ∈ [0, 1]`（下低上高）。单点 + 十字虚线标出当前值。
- 用 `<canvas>` 或轻量 SVG 手绘（核心先行不引图表库——单点定位图，canvas 足够，避免新依赖）。**不引 recharts/d3**（未请求的复杂度）。
- 语义对齐 design §4.2：`valence` 正负 = 情绪正负，`arousal` 高低 = 激活度；图上轻标注 6 档象限区（开心/害羞/悲伤/生气/担忧/平静），经 `EMOTION_LABELS` 与后端 `vad_to_category` 的 6 分类一一对应（右上下=开心/害羞、左上中下=生气/担忧/悲伤、中央带=平静），纯视觉辅助。
- 放大：`va-plot` `max-width: 24rem`（原 16rem），内在面板移除立绘后作为最显眼的可视化元素。

## 4. 精力条（`EnergyBar`）

- 横向进度条：`energy ∈ [0, 100]`；文案经 `ENERGY_LABELS`（`lib/labels.ts`）把 `energy_state` 转中文（`energetic→精力充沛` / `tired→疲惫` …），未知键回退原值。
- 颜色按 `energy_state` 分段（绿→黄→红），纯视觉。

## 5. Big Five 与三观（`BigFiveChart` / `ValuesChart`）

- **Big Five**：五维 `1-10`（`openness`/`conscientiousness`/`extraversion`/`agreeableness`/`neuroticism`），**双端量表**——每行 = 低分端词 + 滑块圆点 + 高分端词（视觉改造：去数值，用两端语义 + 圆点位置表达，不再显示 1-10 数字）。
- **三观**：四维 `1-10`（`attitude_to_human`/`ai_identity_acceptance`/`altruism`/`optimism`），同款双端量表。
- 两者共享内部 `BarChart`（收 `keys: readonly string[]` + `data: Record<string, number>` + `poles: Record<string, {low, high}>`，值 `(v-1)/9 → 0-100%` 映射圆点位置并钳回 `[0,100]`）；`BigFiveChart`/`ValuesChart` 只传各自的 keys 数组 + 数据对象 + `poles`，不重复渲染逻辑。
- **双端语义经 `lib/labels.ts`**：`BigFiveChart` 传 `PERSONALITY_POLES`（`openness→{保守,开放}` …）、`ValuesChart` 传 `VALUES_POLES`（`attitude_to_human→{疏离,亲近}` …）；`BarChart` 渲染 `poles[key] ?? {low:key, high:key}`，未知键回退键名原值。
- 这些是慢变量（无高频事件），只在 `refreshState` 全量刷新时重绘（02-stores §2）。

## 6. 边界

- **纯展示**：本面板只读不写，无任何发往后端的动作；`emotion_update` 由 SSE 自动推，用户不改内在状态（MVP）。
- **`current_activity` / `active_desires`**：核心先行不在本面板展示（活动/欲望面板后续），字段已在类型里占位，忽略即可。
- **未连接**：`current === null` 时面板整体占位「等待核心服务连接…」，不渲染子组件（避免 `undefined` 字段崩）。

## 7. 测试（`tests/stores.test.ts` 已覆盖数据层）

- 组件层（React Testing Library）轻测：`EnergyBar` 按 `energy`/`energy_state` 渲染中文文案、`EmotionSprite` 按 `emotion` 选图文件名、`BigFiveChart`/`ValuesChart` 按 personality/values 渲染双端语义（低端词/高端词，不做数值断言）、`InnerStatePanel` 在 `current=null` 时整体占位不崩（§6 守卫在面板层，不渲染子组件）。
- 图表坐标/像素不做断言（README §6）。
