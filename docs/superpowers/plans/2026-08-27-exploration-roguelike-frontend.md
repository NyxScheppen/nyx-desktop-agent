# 探索系统重设计（逐层地牢）· 前端实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「探索地图」页内视图从「历史足迹 + 实时节点 + 心愿单」改造成 Darkest Dungeon 风的逐层地牢——HUD（火把精力条 / 欲望目标 / 深度 / 托管开关）、本层 4 槽（真实节点 / 死路 / 安全房）、下楼 / 撤退、展开地图、道具栏占位、内心独白，全部由 `exploration_step` 的决策载荷驱动。

**Architecture:** 后端 `EXPLORATION_STEP` 事件载荷从 `{node}` 改成 `{decision}`（`{kind:"choose", floor, energy, focus, nodes:[FloorNode]}`），前端 `explorationStore` 由「累积 liveNodes」改为「持有当前 decision」；用户 `choose(choice)` POST 决策、新决策经 SSE 推回（SSE 主通道，同 encounter）；托管开关 POST `/api/explore/autopilot`。纯函数（结果文案）在 `lib/activityResult.ts`，枚举中文映射在 `lib/labels.ts`。

**Tech Stack:** React 18 + TypeScript（strict）+ Zustand + Vite + vitest（RTL），SSE 走既有 `useSSE.ts`。

**Spec:** `docs/superpowers/specs/2026-08-27-exploration-roguelike-design.md`（§4.1 结果形状 / §5 有根遭遇 / §6 托管 / §7 前端呈现）

## Global Constraints

> 每个 Task 的需求都隐式包含本节（自 CLAUDE.md 与 design doc 逐字抄录）。

- TypeScript 严格模式（`strict: true`），组件 `PascalCase`、文件 `camelCase.tsx`。
- 键名 = 后端 JSON 键（snake_case 零映射），不转驼峰。
- 全局状态用 Zustand store（每系统一个）；探索只改造既有 `explorationStore`，不新建 store。
- SSE 事件流用 `hooks/useSSE.ts`；事件 → store 路由在 `api/dispatch.ts`。
- **所有 API 端点必须有测试**（`api.test.ts` 补 `chooseExploration`/`setExplorationAutopilot`）。
- 质量门：`npx tsc --noEmit` + `npx vitest run` 全绿。
- **每次写测试后**更新 `docs/test-inventory.md`。
- 不新增抽象层；不新增 Repo/Service；道具栏只做 6 格占位（道具系统 hook，不建实体/事件）。
- 托管整场一个开关，不做逐节点开关。

---

## File Structure

**Modify:**
- `src/types/api.ts` — `ExplorationNode`/`ExplorationStepEvent` 换成 `FloorNode`/`ExplorationDecision`/决策帧；`EncounterKind` 加 `rooted`。
- `src/api/client.ts` — 新增 `chooseExploration` / `setExplorationAutopilot`。
- `src/lib/labels.ts` — `ENCOUNTER_KIND_LABELS` 加 `rooted`。
- `src/lib/activityResult.ts` — `formatResult`/`formatOutputBody` 的 free_exploration 分支读新结果形状。
- `src/stores/explorationStore.ts` — 从「wishlist + liveNodes」改为「decision + history + autopilot + choose」。
- `src/api/dispatch.ts` — `activity_end` 分支加探索终局清理。
- `src/components/exploration/ExplorationMap.tsx` — 重写为逐层地牢视图。
- `src/index.css` — 删 `.map-*`，加 `.dungeon-*` 暗黑地牢样式。

**Test:**
- `tests/activityResult.test.ts` — free_exploration 新结果形状。
- `tests/api.test.ts` — `chooseExploration` / `setExplorationAutopilot`。
- `tests/labels.test.ts` — `rooted` 标签。
- `tests/stores.test.ts` — explorationStore 块重写。
- `tests/explorationMap.test.tsx` — 重写为地牢视图。

**Doc:**
- `docs/frontend/README.md`、`docs/frontend/01-sse.md`、`docs/frontend/06-game-shell.md`、`docs/test-inventory.md`。

---

## Task 1: 结果文案适配（summary/core_discovery/knowledge）

**Files:**
- Modify: `src/lib/activityResult.ts:55-84`（`formatResult`/`formatOutputBody` 的 free_exploration 分支）
- Test: `tests/activityResult.test.ts:55-106,125-131`

**Interfaces:**
- Consumes: 无（纯函数）。
- Produces: `formatResult`/`formatOutputBody` 的 free_exploration 分支改读新结果键 `summary`/`core_discovery`/`knowledge`（design §4.1），弃 `findings`/`notes`。

- [ ] **Step 1: 写失败测试**

改 `tests/activityResult.test.ts` 三个 free_exploration 用例（其余不动）。`formatResult` 的两处：

```ts
  it("free_exploration → summary 与 core_discovery 用 — 连接", () => {
    expect(
      formatResult(
        activity({
          type: "free_exploration",
          progress: { result: { summary: "弄懂了退相干", core_discovery: "环境纠缠抹去相干性" } },
        }),
      ),
    ).toBe("弄懂了退相干 — 环境纠缠抹去相干性");
  });

  it("free_exploration → 无 core_discovery 只留 summary", () => {
    expect(
      formatResult(
        activity({ type: "free_exploration", progress: { result: { summary: "翻了翻量子资料" } } }),
      ),
    ).toBe("翻了翻量子资料");
  });
```

`formatOutputBody` 的两处（替换原「findings/notes 用换行连接」）：

```ts
  it("free_exploration → core_discovery + knowledge 逐条", () => {
    expect(
      formatOutputBody(
        activity({
          type: "free_exploration",
          progress: {
            result: {
              core_discovery: "环境纠缠抹去相干性",
              knowledge: [
                { topic: "退相干", content: "环境纠缠" },
                { topic: "纠错", content: "拓扑保护" },
              ],
            },
          },
        }),
      ),
    ).toBe("核心发现：环境纠缠抹去相干性\n【退相干】环境纠缠\n【纠错】拓扑保护");
  });

  it("free_exploration → 无 knowledge 只留 summary", () => {
    expect(
      formatOutputBody(
        activity({ type: "free_exploration", progress: { result: { summary: "翻了翻" } } }),
      ),
    ).toBe("翻了翻");
  });
```

`activityAnnouncement` 的 free_exploration 用例（替换 `{findings:["a"]}`）：

```ts
  it("free_exploration → 探索收获：…", () => {
    expect(
      activityAnnouncement(
        activity({ type: "free_exploration", progress: { result: { summary: "弄懂了退相干" } } }),
      ),
    ).toBe("探索收获：弄懂了退相干");
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd D:/Desktop/nyx_agent_V1/frontend && npx vitest run tests/activityResult.test.ts`
Expected: 3 个 free_exploration 用例 FAIL（`formatResult` 读 `findings`/`notes`，新 shape 下返回 null → 期望 `— 连接` 却得 null）。

- [ ] **Step 3: 写最小实现**

改 `src/lib/activityResult.ts`。`formatResult` 的 free_exploration 分支（原 55-60 行）：

```ts
  if (a.type === "free_exploration") {
    const parts: string[] = [];
    if (typeof r.summary === "string" && r.summary.length > 0) parts.push(r.summary);
    if (typeof r.core_discovery === "string" && r.core_discovery.length > 0)
      parts.push(r.core_discovery);
    return parts.length > 0 ? parts.join(" — ") : null;
  }
```

`formatOutputBody` 的 free_exploration 分支（原 77-82 行）：

```ts
  if (a.type === "free_exploration") {
    const lines: string[] = [];
    if (typeof r.core_discovery === "string" && r.core_discovery.length > 0)
      lines.push(`核心发现：${r.core_discovery}`);
    if (Array.isArray(r.knowledge)) {
      for (const k of r.knowledge) {
        if (typeof k !== "object" || k === null) continue;
        const kk = k as Record<string, unknown>;
        const topic = typeof kk.topic === "string" ? kk.topic : "";
        const content = typeof kk.content === "string" ? kk.content : "";
        if (content.length === 0) continue;
        lines.push(topic.length > 0 ? `【${topic}】${content}` : content);
      }
    }
    if (lines.length === 0 && typeof r.summary === "string" && r.summary.length > 0)
      lines.push(r.summary);
    return lines.length > 0 ? lines.join("\n") : null;
  }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd D:/Desktop/nyx_agent_V1/frontend && npx vitest run tests/activityResult.test.ts`
Expected: 全绿（3 个新 free_exploration 用例 PASS，其余不变）。

- [ ] **Step 5: 提交**

```bash
git add src/lib/activityResult.ts tests/activityResult.test.ts docs/test-inventory.md
git commit -m "feat(activity-result): 探索产出读 summary/core_discovery/knowledge"
```

> 更新 `docs/test-inventory.md`：追加 activityResult 3 个测试（free_exploration 新结果形状 formatResult / formatOutputBody / activityAnnouncement），检查方向=功能正确，属 activity 系统（前端展示），探索 Roguelike 阶段。

---

## Task 2: 探索契约与 store 数据流改造

**Files:**
- Modify: `src/types/api.ts:105-117,136`（`ExplorationNode`/`ExplorationStepEvent`/`EncounterKind`）
- Modify: `src/api/client.ts:80-88` 之后（新增两个函数）
- Modify: `src/lib/labels.ts:100-104`（加 `rooted`）
- Modify: `src/stores/explorationStore.ts`（全重写）
- Modify: `src/api/dispatch.ts:55-67`（`activity_end` 加清理）
- Test: `tests/api.test.ts`、`tests/labels.test.ts`、`tests/stores.test.ts`

**Interfaces:**
- Consumes: Task 1 无依赖；后端契约（design §4.1/§6/§7）。
- Produces:
  - 类型：`FloorNode`、`ExplorationDecision`、`ExplorationStepEvent`（decision 载荷）、`EncounterKind` 含 `rooted`。
  - client：`chooseExploration(activityId, choice) -> Promise<unknown>`、`setExplorationAutopilot(activityId, on) -> Promise<{activity_id, autopilot}>`。
  - store：`decision/activityId/autopilot/choosing/error/history` + `start/choose/toggleAutopilot/onStep/onActivityEnd`。

> **边界说明**：本 Task 结束后 `ExplorationMap.tsx` 与 `explorationMap.test.tsx` 因引用已删的 `wishlist`/`liveNodes` 而临时编译红，Task 3 一次性重写修复。本 Task 的绿检查 = `tsc` 只看报错列表确认为 explorationMap 两文件、其余无错；vitest 跑 `api.test.ts`/`labels.test.ts`/`stores.test.ts` 全绿。

- [ ] **Step 1: 写失败测试**

(a) `tests/api.test.ts` import 加 `chooseExploration, setExplorationAutopilot`；末尾追加：

```ts
  it("chooseExploration：POST /api/explore/choose body {activity_id, choice}", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ kind: "choose", floor: 1 }));
    vi.stubGlobal("fetch", fetchMock);

    await chooseExploration("a1", "node:0");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/explore/choose");
    expect(init).toMatchObject({ method: "POST" });
    expect(JSON.parse(init.body)).toEqual({ activity_id: "a1", choice: "node:0" });
  });

  it("setExplorationAutopilot：POST /api/explore/autopilot body {activity_id, on}", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ activity_id: "a1", autopilot: true }));
    vi.stubGlobal("fetch", fetchMock);

    const res = await setExplorationAutopilot("a1", true);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/explore/autopilot");
    expect(JSON.parse(init.body)).toEqual({ activity_id: "a1", on: true });
    expect(res).toEqual({ activity_id: "a1", autopilot: true });
  });
```

(b) `tests/labels.test.ts` 的 `ENCOUNTER_KIND_LABELS` describe 内追加：

```ts
  it("rooted 有根遭遇", () => {
    expect(ENCOUNTER_KIND_LABELS.rooted).toBe("有根遭遇");
  });
```

(c) `tests/stores.test.ts`：line 16 的 `ExplorationNode` import 改为 `ExplorationDecision`；`explorationStore` describe（原 733-770 行）整块替换为：

```ts
describe("explorationStore", () => {
  beforeEach(() => {
    useExplorationStore.setState({
      decision: null, activityId: null, autopilot: false,
      choosing: false, error: null, history: [],
    });
  });

  const decision: ExplorationDecision = {
    kind: "choose",
    floor: 1,
    energy: 94,
    focus: "量子",
    nodes: [
      { name: "维基·退相干", url: "", kind: "real", snippet: "…", may_encounter: false },
      { name: "本地·无结果", url: "", kind: "dead_end", snippet: "", may_encounter: false },
    ],
  };

  it("onStep：同 activity 更新 decision，异 activity 清 history", () => {
    useExplorationStore.getState().onStep({
      event: "exploration_step", event_id: "s1", correlation_id: "a1",
      activity_id: "a1", decision,
    });
    expect(useExplorationStore.getState().decision).toEqual(decision);

    useExplorationStore.getState().onStep({
      event: "exploration_step", event_id: "s2", correlation_id: "a2",
      activity_id: "a2", decision,
    });
    expect(useExplorationStore.getState().activityId).toBe("a2");
  });

  it("choose：node:0 记录足迹 + POST /api/explore/choose", async () => {
    useExplorationStore.setState({ decision, activityId: "a1" });
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ kind: "choose" }));
    vi.stubGlobal("fetch", fetchMock);

    await useExplorationStore.getState().choose("node:0");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/explore/choose");
    expect(JSON.parse(init.body)).toEqual({ activity_id: "a1", choice: "node:0" });
    expect(useExplorationStore.getState().history[0]).toMatchObject({ floor: 1, name: "维基·退相干" });
  });

  it("choose：无 decision 不发起 POST", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}));
    vi.stubGlobal("fetch", fetchMock);

    await useExplorationStore.getState().choose("node:0");

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("start：POST /api/explore 后复位 decision/history/autopilot", async () => {
    useExplorationStore.setState({
      decision, activityId: "a1",
      history: [{ floor: 1, name: "x", kind: "real" }],
    });
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ activity_id: "e1" }));
    vi.stubGlobal("fetch", fetchMock);

    await useExplorationStore.getState().start();

    expect(useExplorationStore.getState().activityId).toBe("e1");
    expect(useExplorationStore.getState().decision).toBeNull();
    expect(useExplorationStore.getState().history).toEqual([]);
  });

  it("toggleAutopilot：POST /api/explore/autopilot + 本地镜像", async () => {
    useExplorationStore.setState({ activityId: "a1" });
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ activity_id: "a1", autopilot: true }));
    vi.stubGlobal("fetch", fetchMock);

    await useExplorationStore.getState().toggleAutopilot(true);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/explore/autopilot");
    expect(JSON.parse(init.body)).toEqual({ activity_id: "a1", on: true });
    expect(useExplorationStore.getState().autopilot).toBe(true);
  });

  it("onActivityEnd：匹配 id 清 decision/autopilot", () => {
    useExplorationStore.setState({ decision, activityId: "a1", autopilot: true });
    useExplorationStore.getState().onActivityEnd("a1");
    expect(useExplorationStore.getState().decision).toBeNull();
    expect(useExplorationStore.getState().autopilot).toBe(false);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd D:/Desktop/nyx_agent_V1/frontend && npx vitest run tests/api.test.ts tests/labels.test.ts tests/stores.test.ts`
Expected: FAIL（api.test 找不到 `chooseExploration` 导出；labels.test `rooted` 为 undefined；stores.test `explorationStore` 无 `choose`/`toggleAutopilot`）。

- [ ] **Step 3: 写最小实现**

(a) `src/types/api.ts`：替换 105-117 行的 `ExplorationNode` + `ExplorationStepEvent`：

```ts
/** 探索楼层节点（逐层地牢）：真实节点 / 死路 / 安全房。 */
export type FloorNode = {
  name: string;
  url: string;
  kind: "real" | "dead_end" | "safe_room";
  snippet: string;
  may_encounter: boolean;
};

/** 逐层地牢决策载荷（exploration_step 推送）：本层节点 + 精力 + 深度 + 目标。 */
export type ExplorationDecision = {
  kind: "choose";
  floor: number;
  energy: number;
  focus: string;
  nodes: FloorNode[];
};

/** 探索实时进度帧（exploration_step）：每个决策点推一次决策载荷。 */
export type ExplorationStepEvent = SseBase & {
  event: "exploration_step";
  activity_id: string;
  decision: ExplorationDecision;
};
```

第 136 行 `EncounterKind` 加 `"rooted"`：

```ts
export type EncounterKind = "desire_chat" | "random_event" | "growth_moment" | "rooted";
```

(b) `src/api/client.ts`：在 `postExplore`（80-88 行）之后新增：

```ts
export async function chooseExploration(
  activityId: string,
  choice: string,
): Promise<unknown> {
  return request<unknown>(`${BASE_URL}/api/explore/choose`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ activity_id: activityId, choice }),
  });
}

export async function setExplorationAutopilot(
  activityId: string,
  on: boolean,
): Promise<{ activity_id: string; autopilot: boolean }> {
  return request<{ activity_id: string; autopilot: boolean }>(
    `${BASE_URL}/api/explore/autopilot`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ activity_id: activityId, on }),
    },
  );
}
```

(c) `src/lib/labels.ts`：`ENCOUNTER_KIND_LABELS` 加一行：

```ts
export const ENCOUNTER_KIND_LABELS: Record<EncounterKind, string> = {
  desire_chat: "欲望搭话",
  random_event: "随机事件",
  growth_moment: "成长时刻",
  rooted: "有根遭遇",
};
```

(d) `src/stores/explorationStore.ts` 全重写：

```ts
import { create } from "zustand";
import {
  chooseExploration,
  postExplore,
  setExplorationAutopilot,
} from "../api/client";
import type {
  ExplorationDecision,
  ExplorationStepEvent,
  FloorNode,
} from "../types/api";

// 逐层地牢探索（design §6/§7）：decision = 当前决策载荷（exploration_step 推送），
// 用户 choose 提交决策，新决策经 SSE exploration_step 推回（SSE 主通道，同 encounter）。
// 托管 autopilot：POST /api/explore/autopilot 开关；手动点任意选项 = 随时接管（组件层先关托管再走）。

/** 本地走过的节点（展开地图用）：手动 choose 时记录，托管期间只推进 floor。 */
type VisitRecord = { floor: number; name: string; kind: FloorNode["kind"] };

type ExplorationStoreState = {
  decision: ExplorationDecision | null; // 当前决策载荷；null = 无进行中 run
  activityId: string | null;
  autopilot: boolean;  // 托管开关（本地镜像，POST 后乐观更新）
  choosing: boolean;   // 决策提交中（防连击），新决策到达或终局时复位
  error: string | null;
  history: VisitRecord[];
  start: () => Promise<void>;                 // 出门探索：POST /api/explore（无 topic）
  choose: (choice: string) => Promise<void>;  // 提交决策 node:0/safe_room/descend/retreat
  toggleAutopilot: (on: boolean) => Promise<void>;
  onStep: (e: ExplorationStepEvent) => void;
  onActivityEnd: (activityId: string) => void;
};

export const useExplorationStore = create<ExplorationStoreState>((set, get) => ({
  decision: null,
  activityId: null,
  autopilot: false,
  choosing: false,
  error: null,
  history: [],
  start: async () => {
    set({ error: null });
    try {
      const { activity_id } = await postExplore();
      set({
        activityId: activity_id,
        decision: null,
        history: [],
        autopilot: false,
        choosing: false,
      });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) });
    }
  },
  choose: async (choice) => {
    const { activityId, decision, choosing } = get();
    if (activityId === null || decision === null || choosing) return;
    const record = nodeFromChoice(choice, decision);
    const history = record === null ? get().history : [...get().history, record];
    set({ choosing: true, error: null, history });
    try {
      await chooseExploration(activityId, choice);
      // SSE 主通道：新决策经 exploration_step 的 onStep 推回并解锁 choosing
    } catch (err) {
      set({ choosing: false, error: err instanceof Error ? err.message : String(err) });
    }
  },
  toggleAutopilot: async (on) => {
    const { activityId } = get();
    if (activityId === null) return;
    set({ autopilot: on, error: null });
    try {
      await setExplorationAutopilot(activityId, on);
    } catch (err) {
      set({ autopilot: !on, error: err instanceof Error ? err.message : String(err) });
    }
  },
  onStep: (e) => {
    const d = e.decision;
    if (!Array.isArray(d?.nodes)) return; // 运行时收窄：非法载荷丢弃
    set((s) => {
      const sameRun = s.activityId === e.activity_id;
      return {
        decision: d,
        activityId: e.activity_id,
        choosing: false,
        history: sameRun ? s.history : [],
        autopilot: sameRun ? s.autopilot : false,
      };
    });
  },
  onActivityEnd: (activityId) => {
    set((s) =>
      s.activityId === activityId
        ? { decision: null, activityId: null, autopilot: false, choosing: false }
        : {},
    );
  },
}));

/** 决策字符串 → 本地足迹；非节点类（descend/retreat）不记录。 */
function nodeFromChoice(
  choice: string,
  decision: ExplorationDecision,
): VisitRecord | null {
  if (choice === "safe_room") {
    return { floor: decision.floor, name: "安全房", kind: "safe_room" };
  }
  if (choice.startsWith("node:")) {
    const idx = Number(choice.slice(5));
    const node = Number.isInteger(idx) ? decision.nodes[idx] : undefined;
    if (node !== undefined) {
      return { floor: decision.floor, name: node.name, kind: node.kind };
    }
  }
  return null;
}
```

(e) `src/api/dispatch.ts`：`activity_end` 分支（55-67 行）顶部加一行清理：

```ts
    case "activity_end": {
      useExplorationStore.getState().onActivityEnd(e.activity_id);
      // 完成后主动冒一句：refresh 重拉快照后，按 activity_id 找到刚完成的活动，
      // 有产出就 announce("activity", …)（activityAnnouncement 见 lib/activityResult）。
      void useActivityStore.getState().refresh().then(() => {
        ...
```

（`useExplorationStore` 已在文件顶部 import，无需新增。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd D:/Desktop/nyx_agent_V1/frontend && npx vitest run tests/api.test.ts tests/labels.test.ts tests/stores.test.ts`
Expected: 全绿。`npx tsc --noEmit` 的报错应只剩 `ExplorationMap.tsx` 与 `explorationMap.test.tsx` 两文件（Task 3 修复）；确认无其他文件报错。

- [ ] **Step 5: 提交**

```bash
git add src/types/api.ts src/api/client.ts src/lib/labels.ts src/stores/explorationStore.ts src/api/dispatch.ts tests/api.test.ts tests/labels.test.ts tests/stores.test.ts docs/test-inventory.md
git commit -m "feat(exploration): 决策流 store + 契约 + 托管开关（前端）"
```

> 更新 `docs/test-inventory.md`：追加 api.test 2 个（chooseExploration / setExplorationAutopilot）、labels.test 1 个（rooted）、stores.test 6 个（onStep/choose/无 decision 守卫/start 复位/toggleAutopilot/onActivityEnd），检查方向=功能正确 + 边界鲁棒，属 activity/encounter 系统（前端），探索 Roguelike 阶段。

---

## Task 3: ExplorationMap 暗黑地牢视图 + 样式

**Files:**
- Modify: `src/components/exploration/ExplorationMap.tsx`（全重写）
- Modify: `src/index.css:582-694`（删 `.map-*`，加 `.dungeon-*`）
- Test: `tests/explorationMap.test.tsx`（全重写）

**Interfaces:**
- Consumes: Task 2 的 `useExplorationStore`（`decision/autopilot/choosing/error/history/start/choose/toggleAutopilot`）、`FloorNode`。
- Produces: 无对外接口（叶组件，挂载于 App 的 `view === "explore"`）。

- [ ] **Step 1: 写失败测试**

`tests/explorationMap.test.tsx` 全重写：

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ExplorationMap from "../src/components/exploration/ExplorationMap";
import { useExplorationStore } from "../src/stores/explorationStore";
import type { ExplorationDecision } from "../src/types/api";

const decision: ExplorationDecision = {
  kind: "choose",
  floor: 2,
  energy: 62,
  focus: "量子退相干",
  nodes: [
    { name: "维基·量子退相干", url: "", kind: "real", snippet: "退相干是……", may_encounter: false },
    { name: "arXiv·拓扑纠错", url: "", kind: "real", snippet: "拓扑保护……", may_encounter: true },
    { name: "本地·无结果", url: "", kind: "dead_end", snippet: "", may_encounter: false },
  ],
};

beforeEach(() => {
  useExplorationStore.setState({
    decision, activityId: "a1", autopilot: false, choosing: false, error: null,
    history: [{ floor: 1, name: "维基·量子计算", kind: "real" }],
  });
});

describe("ExplorationMap", () => {
  it("渲染 HUD（目标/深度）+ 节点 + 安全房", () => {
    render(<ExplorationMap />);
    expect(screen.getByText("量子退相干")).toBeTruthy();       // focus
    expect(screen.getAllByText("第 2 层").length).toBeGreaterThan(0); // 深度
    expect(screen.getByText("维基·量子退相干")).toBeTruthy();  // 节点
    expect(screen.getByText("休息整理")).toBeTruthy();          // 安全房
  });

  it("点节点 → choose('node:0')", () => {
    const spy = vi.spyOn(useExplorationStore.getState(), "choose").mockResolvedValue(undefined);
    render(<ExplorationMap />);
    fireEvent.click(screen.getByText("维基·量子退相干"));
    expect(spy).toHaveBeenCalledWith("node:0");
    spy.mockRestore();
  });

  it("无 decision → 渲染「出门探索」+ 点击调 start", () => {
    useExplorationStore.setState({ decision: null, activityId: null });
    const spy = vi.spyOn(useExplorationStore.getState(), "start").mockResolvedValue(undefined);
    render(<ExplorationMap />);
    expect(screen.getByText("出门探索")).toBeTruthy();
    fireEvent.click(screen.getByText("出门探索"));
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });

  it("展开地图显示已走过楼层 + 进过节点", () => {
    render(<ExplorationMap />);
    fireEvent.click(screen.getByText(/展开地图/));
    expect(screen.getByText("维基·量子计算")).toBeTruthy(); // 第 1 层足迹
  });

  it("点「下楼」→ choose('descend')", () => {
    const spy = vi.spyOn(useExplorationStore.getState(), "choose").mockResolvedValue(undefined);
    render(<ExplorationMap />);
    fireEvent.click(screen.getByText(/下楼/));
    expect(spy).toHaveBeenCalledWith("descend");
    spy.mockRestore();
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd D:/Desktop/nyx_agent_V1/frontend && npx vitest run tests/explorationMap.test.tsx`
Expected: FAIL（`ExplorationMap.tsx` 旧实现仍读 `wishlist`/`liveNodes`，编译或渲染失败）。

- [ ] **Step 3: 写最小实现**

(a) `src/components/exploration/ExplorationMap.tsx` 全重写：

```tsx
import { useState } from "react";
import { useExplorationStore } from "../../stores/explorationStore";
import type { FloorNode } from "../../types/api";

// 节点类型标签 + 精力消耗（与后端 enter_cost 常量镜像；仅展示，不驱动逻辑）
const KIND_LABEL: Record<FloorNode["kind"], string> = {
  real: "真实节点",
  dead_end: "死路",
  safe_room: "安全房",
};
const KIND_COST: Record<FloorNode["kind"], string> = {
  real: "-6 精力",
  dead_end: "-4 精力",
  safe_room: "+30 精力",
};

function EnergyBar({ energy }: { energy: number }) {
  const pct = Math.max(0, Math.min(100, energy));
  return (
    <div className="dungeon-hud__energy">
      <div className="dungeon-hud__label">火把 · 精力（燃料）</div>
      <div className="dungeon-hud__energy-track">
        <div className="dungeon-hud__energy-fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="dungeon-hud__energy-num">
        {Math.round(energy)}
        <span className="dim">/100</span>
      </span>
    </div>
  );
}

function NodeCard({
  node,
  disabled,
  onEnter,
}: {
  node: FloorNode;
  disabled: boolean;
  onEnter: () => void;
}) {
  return (
    <button
      type="button"
      className={`dungeon-node dungeon-node--${node.kind}`}
      disabled={disabled}
      onClick={onEnter}
    >
      <span className={`dungeon-node__badge dungeon-node__badge--${node.kind}`}>
        {KIND_LABEL[node.kind]}
      </span>
      <div className="dungeon-node__name">{node.name}</div>
      <div className="dungeon-node__snippet">{node.snippet}</div>
      <div className="dungeon-node__cost">
        {KIND_COST[node.kind]}
        {node.may_encounter ? <span className="dungeon-node__risk"> · 可能触发遭遇</span> : null}
      </div>
    </button>
  );
}

// 逐层地牢探索视图（design §7）：HUD（精力/目标/深度/托管）+ 本层 4 槽 + 下楼/撤退 + 展开地图 + 道具栏占位。
export default function ExplorationMap() {
  const decision = useExplorationStore((s) => s.decision);
  const activityId = useExplorationStore((s) => s.activityId);
  const autopilot = useExplorationStore((s) => s.autopilot);
  const choosing = useExplorationStore((s) => s.choosing);
  const error = useExplorationStore((s) => s.error);
  const history = useExplorationStore((s) => s.history);
  const start = useExplorationStore((s) => s.start);
  const choose = useExplorationStore((s) => s.choose);
  const toggleAutopilot = useExplorationStore((s) => s.toggleAutopilot);

  const [mapOpen, setMapOpen] = useState(false);

  const pick = (choice: string) => {
    if (autopilot) void toggleAutopilot(false); // 随时接管：托管中点任意选项先关托管
    void choose(choice);
  };

  const safeRoom: FloorNode = {
    name: "休息整理", url: "", kind: "safe_room",
    snippet: "+30 精力 · 写进记忆 · 可安全撤退", may_encounter: false,
  };

  return (
    <section className="side-panel dungeon">
      <header className="side-panel__header dungeon__header">
        <span className="side-panel__title">探索地牢</span>
        {decision !== null && (
          <button
            type="button"
            className={`dungeon-autopilot${autopilot ? " dungeon-autopilot--on" : ""}`}
            aria-pressed={autopilot}
            disabled={activityId === null}
            onClick={() => void toggleAutopilot(!autopilot)}
          >
            {autopilot ? "托管中 · 点此接管" : "托管 · 让尼克斯自己走"}
          </button>
        )}
      </header>

      <div className="side-panel__body">
        {decision === null ? (
          <>
            <button type="button" className="dungeon-go" onClick={() => void start()}>
              出门探索
            </button>
            {error !== null && <div className="dungeon-error">{error}</div>}
          </>
        ) : (
          <>
            <div className="dungeon-hud">
              <EnergyBar energy={decision.energy} />
              <div className="dungeon-hud__goal">
                <div className="dungeon-hud__label">欲望（目标）</div>
                <div className="dungeon-hud__value">{decision.focus}</div>
              </div>
              <div className="dungeon-hud__floor">
                <div className="dungeon-hud__label">深度</div>
                <div className="dungeon-hud__value">第 {decision.floor} 层</div>
                <div className="dungeon-hud__hint">越下越险</div>
              </div>
            </div>

            <div className="dungeon-floor">
              {decision.nodes.map((n, i) => (
                <NodeCard key={i} node={n} disabled={choosing} onEnter={() => pick(`node:${i}`)} />
              ))}
              <NodeCard node={safeRoom} disabled={choosing} onEnter={() => pick("safe_room")} />
            </div>

            <div className="dungeon-actions">
              <button
                type="button"
                className="dungeon-descend"
                disabled={choosing}
                onClick={() => pick("descend")}
              >
                下楼 · 追线索往下
              </button>
              <button
                type="button"
                className="dungeon-retreat"
                disabled={choosing}
                onClick={() => pick("retreat")}
              >
                撤退 · 正常结算
              </button>
              <button
                type="button"
                className="dungeon-map-toggle"
                aria-pressed={mapOpen}
                onClick={() => setMapOpen((v) => !v)}
              >
                展开地图 {mapOpen ? "▴" : "▾"}
              </button>
            </div>

            {mapOpen && (
              <div className="dungeon-map">
                <div className="dungeon-map__label">地图 · 已走过的楼层</div>
                {Array.from({ length: decision.floor }, (_, i) => i + 1).map((f) => {
                  const nodes = history.filter((h) => h.floor === f);
                  return (
                    <div key={f} className="dungeon-map__floor">
                      <span
                        className={`dungeon-map__glyph${
                          f === decision.floor ? " dungeon-map__glyph--cur" : ""
                        }`}
                      >
                        ◆
                      </span>
                      <span className="dungeon-map__floor-name">第 {f} 层</span>
                      {nodes.length > 0 && (
                        <span className="dungeon-map__nodes">
                          {nodes.map((n) => n.name).join(" · ")}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            <div className="dungeon-inventory">
              <div className="dungeon-inventory__label">道具（道具系统后续接入）</div>
              <div className="dungeon-inventory__slots">
                {Array.from({ length: 6 }, (_, i) => (
                  <div key={i} className="dungeon-inventory__slot" />
                ))}
              </div>
            </div>

            {error !== null && <div className="dungeon-error">{error}</div>}
          </>
        )}
      </div>
    </section>
  );
}
```

(b) `src/index.css`：删 582-694 行的整个「探索地图」块（`.map-go` 到 `.map-detail p`），替换为：

```css
/* ===== 探索地牢（ExplorationMap 暗黑地牢风，design §7） ===== */
.dungeon {
  background: #0e0b08;
  color: #d8c9a8;
  border-color: #4a3a28;
  font-family: Georgia, "Times New Roman", serif;
}

.dungeon__header {
  border-bottom-color: #4a3a28;
}

.dungeon__header .side-panel__title {
  color: #c9a24a;
}

.dungeon-autopilot {
  padding: 0.35rem 0.8rem;
  border: 1px solid #5a4632;
  border-radius: 2px;
  background: #2a1f14;
  color: #c9a24a;
  cursor: pointer;
  font-family: inherit;
  font-size: 0.85rem;
  letter-spacing: 1px;
}

.dungeon-autopilot--on {
  background: #3a2a12;
  color: #e0b84a;
  border-color: #8a5a1a;
}

.dungeon-go {
  align-self: flex-start;
  padding: 0.55rem 1.1rem;
  border: 2px double #8a5a1a;
  border-radius: 3px;
  background: #1a1512;
  color: #e0b84a;
  cursor: pointer;
  font-family: inherit;
  letter-spacing: 2px;
}

.dungeon-go:hover {
  background: #241c12;
}

.dungeon-error {
  color: #c04a4a;
  font-size: 0.85rem;
}

.dungeon-hud {
  display: flex;
  align-items: stretch;
  gap: 0.9rem;
  border-bottom: 2px solid #4a3a28;
  padding-bottom: 0.8rem;
  flex-wrap: wrap;
}

.dungeon-hud__label {
  font-size: 10px;
  letter-spacing: 2px;
  color: #8a7a5f;
  text-transform: uppercase;
}

.dungeon-hud__value {
  font-size: 0.95rem;
  font-weight: 700;
  margin-top: 0.2rem;
}

.dungeon-hud__hint {
  font-size: 11px;
  color: #8a2f2f;
  margin-top: 0.15rem;
}

.dungeon-hud__energy {
  flex: 1;
  min-width: 190px;
}

.dungeon-hud__energy-track {
  height: 16px;
  background: #241c12;
  border: 1px solid #5a4632;
  border-radius: 2px;
  overflow: hidden;
  margin-top: 0.3rem;
}

.dungeon-hud__energy-fill {
  height: 100%;
  background: linear-gradient(90deg, #8a5a1a, #c9a24a, #e0b84a);
}

.dungeon-hud__energy-num {
  font-size: 0.85rem;
  font-weight: 700;
  color: #e0b84a;
}

.dungeon-hud__energy-num .dim {
  color: #8a7a5f;
}

.dungeon-hud__goal {
  flex: 1;
  min-width: 190px;
}

.dungeon-hud__floor {
  text-align: center;
  min-width: 80px;
}

.dungeon-floor {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0.6rem;
}

.dungeon-node {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  text-align: left;
  padding: 0.7rem;
  border: 2px solid #5a4632;
  border-radius: 3px;
  background: #1a1512;
  cursor: pointer;
  font-family: inherit;
  color: inherit;
}

.dungeon-node:disabled {
  cursor: default;
  opacity: 0.6;
}

.dungeon-node--dead_end {
  border-style: dashed;
  border-color: #3a3024;
  background: #141008;
}

.dungeon-node--safe_room {
  border-color: #4a5a3a;
  background: #141a10;
}

.dungeon-node__badge {
  align-self: flex-start;
  font-size: 10px;
  padding: 2px 7px;
  border-radius: 2px;
  letter-spacing: 1px;
}

.dungeon-node__badge--real {
  background: #3a4a5a;
  color: #c9d4de;
}

.dungeon-node__badge--dead_end {
  background: #3a3024;
  color: #8a7a5f;
}

.dungeon-node__badge--safe_room {
  background: #4a5a3a;
  color: #d8cfa8;
}

.dungeon-node__name {
  font-weight: 700;
  font-size: 0.9rem;
  color: #e8dcc0;
}

.dungeon-node--dead_end .dungeon-node__name {
  color: #7a6a50;
}

.dungeon-node--safe_room .dungeon-node__name {
  color: #c9d4a8;
}

.dungeon-node__snippet {
  font-size: 0.78rem;
  color: #9c8c6a;
  line-height: 1.45;
  min-height: 2.2em;
}

.dungeon-node__cost {
  font-size: 0.75rem;
  color: #8a5a1a;
  margin-top: auto;
}

.dungeon-node__risk {
  color: #8a2f2f;
}

.dungeon-actions {
  display: flex;
  gap: 0.6rem;
  flex-wrap: wrap;
}

.dungeon-descend,
.dungeon-retreat,
.dungeon-map-toggle {
  padding: 0.5rem 0.9rem;
  border: 2px solid #5a4632;
  border-radius: 3px;
  background: #1a1512;
  color: #c9a24a;
  cursor: pointer;
  font-family: inherit;
  font-size: 0.85rem;
  letter-spacing: 1px;
}

.dungeon-retreat {
  color: #c98a6a;
}

.dungeon-descend:disabled,
.dungeon-retreat:disabled {
  cursor: default;
  opacity: 0.6;
}

.dungeon-map {
  padding: 0.7rem;
  border: 2px solid #3a3024;
  border-radius: 3px;
  background: #120e08;
}

.dungeon-map__label {
  font-size: 11px;
  letter-spacing: 2px;
  color: #8a7a5f;
  text-transform: uppercase;
  margin-bottom: 0.4rem;
}

.dungeon-map__floor {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.82rem;
  margin-top: 0.2rem;
}

.dungeon-map__glyph {
  color: #5a4632;
}

.dungeon-map__glyph--cur {
  color: #c9a24a;
}

.dungeon-map__floor-name {
  color: #8a7a5f;
}

.dungeon-map__nodes {
  color: #6b5a3f;
}

.dungeon-inventory {
  padding: 0.7rem;
  border: 2px solid #3a3024;
  border-radius: 3px;
  background: #120e08;
}

.dungeon-inventory__label {
  font-size: 11px;
  letter-spacing: 2px;
  color: #8a7a5f;
  text-transform: uppercase;
  margin-bottom: 0.5rem;
}

.dungeon-inventory__slots {
  display: flex;
  gap: 0.5rem;
}

.dungeon-inventory__slot {
  width: 46px;
  height: 46px;
  border: 1px dashed #4a3a28;
  border-radius: 3px;
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd D:/Desktop/nyx_agent_V1/frontend && npx vitest run tests/explorationMap.test.tsx && npx tsc --noEmit`
Expected: explorationMap 测试全绿；`tsc` 零报错（Task 2 遗留的两文件红已消）。

- [ ] **Step 5: 提交**

```bash
git add src/components/exploration/ExplorationMap.tsx src/index.css tests/explorationMap.test.tsx docs/test-inventory.md
git commit -m "feat(exploration): 暗黑地牢视图（HUD + 节点槽 + 下楼/撤退 + 地图 + 道具栏占位）"
```

> 更新 `docs/test-inventory.md`：追加 explorationMap 5 个测试（HUD 渲染 / 点节点 choose / 无 decision 出门 / 展开地图 / 下楼），检查方向=功能正确 + 交互，属 activity 系统（前端），探索 Roguelike 阶段。

---

## Task 4: 文档同步

**Files:**
- Modify: `docs/frontend/README.md:105,118,177`
- Modify: `docs/frontend/01-sse.md:53,149`
- Modify: `docs/frontend/06-game-shell.md:789`
- Modify: `docs/test-inventory.md`（若前面任务未覆盖则补全）

**Interfaces:**
- Consumes: Task 1–3 的产物。
- Produces: 无。

- [ ] **Step 1: 改 README.md 三处**

- line 105：`explorationStore.ts` 描述改为「探索：decision 决策载荷 + history 足迹 + autopilot 托管 + start/choose（逐层地牢，POST /api/explore + /api/explore/choose + /api/explore/autopilot）」。
- line 118：`ExplorationMap.tsx` 描述改为「探索地牢（HUD + 本层 4 槽 + 下楼/撤退 + 展开地图 + 道具栏占位，读 explorationStore）」。
- line 177 面板表「探索地图」行的「实现」列补「逐层地牢（HUD/节点/托管/地图/道具占位）」。

- [ ] **Step 2: 改 01-sse.md 两处**

- line 53 的 `ExplorationNode` 注释块换成 `FloorNode`（`{name, url, kind: real|dead_end|safe_room, snippet, may_encounter}`）。
- line 149 的路由表 `exploration_step` 行：`onStep` 描述改为「持有决策载荷：`decision` 置帧 `decision`、`activityId` 置帧 `activity_id`（异 activity 清 history）」。

- [ ] **Step 3: 改 06-game-shell.md 一处**

- line 789 的组件级测试清单 `ExplorationMap` 描述：从「渲染历史节点/心愿单 + 出门探索 + 加心愿」改为「渲染 HUD + 节点 + 安全房，点节点/下楼/撤退调 choose，无 decision 显示出门探索，展开地图显示足迹」。

- [ ] **Step 4: 提交**

```bash
git add docs/frontend/README.md docs/frontend/01-sse.md docs/frontend/06-game-shell.md docs/test-inventory.md
git commit -m "docs(frontend): 探索地牢同步 README/01-sse/06-game-shell + test-inventory"
```

---

## Self-Review

**1. Spec 覆盖**（design §4.1/§5/§6/§7 对任务）：

| Spec | 落地任务 |
|---|---|
| §4.1 结果形状（summary/core_discovery/knowledge） | Task 1（activityResult 新映射） |
| §5 有根遭遇（kind=rooted 上屏） | Task 2（`EncounterKind` + `ENCOUNTER_KIND_LABELS`；`EncounterCard` 无需改） |
| §6 托管（一键开关 / 随时接管） | Task 2（`toggleAutopilot` + store）+ Task 3（`pick` 先关托管再走） |
| §7 HUD 顶栏（精力/欲望/深度/托管） | Task 3（`.dungeon-hud`） |
| §7 本层 4 槽（真实/死路/安全房 + 精力消耗 + 险节点标遭遇） | Task 3（`NodeCard` + `KIND_COST` + `may_encounter`） |
| §7 下楼条 | Task 3（`dungeon-descend`） |
| §7 展开地图（来过 ◆ / 当前 ◆） | Task 3（`dungeon-map` + `history`） |
| §7 道具栏 6 格占位 | Task 3（`dungeon-inventory`） |
| §7 少 emoji / 暗黑地牢调性 | Task 3（CSS 近黑暖棕 + 暗金 + serif） |

**2. Placeholder 扫描**：无 TBD/TODO；每个 code step 有真实代码；道具栏明确标注「占位」而非 stub。

**3. 类型一致性**：
- `FloorNode`/`ExplorationDecision`/`ExplorationStepEvent` Task 2 定义，Task 3 组件与测试引用一致（`decision.floor/energy/focus/nodes`、`node.kind/name/snippet/may_encounter`）。
- `chooseExploration(activityId, choice)` / `setExplorationAutopilot(activityId, on)` Task 2 client 定义，store 调用一致。
- `ENCOUNTER_KIND_LABELS.rooted` Task 2 定义，`EncounterCard` 经 `[current.kind]` 索引自动覆盖。
- store 字段 `decision/autopilot/choosing/history/start/choose/toggleAutopilot` Task 2 定义，Task 3 组件与测试读取一致。

**4. 反冗余自查**：无新 store、无新抽象层；`wishlist`/`liveNodes`/`.map-*` orphan 删除；道具栏只占位不建实体；`EncounterCard`/`encounterStore`/`useSSE` 无需改动（复用既有 `rooted` 标签映射 + 事件流）。

---

## 验证（全部任务完成后）

1. `cd D:/Desktop/nyx_agent_V1/frontend && npx tsc --noEmit` 零报错。
2. `npx vitest run` 全绿（含 activityResult / api / labels / stores / explorationMap 的新增与重写）。
3. 人工抽查：`/api/explore` 触发后 SSE 推 `exploration_step`（decision 载荷）→ 地牢视图渲染 HUD + 节点；点节点/下楼/撤退 POST `/api/explore/choose`；托管开关 POST `/api/explore/autopilot`；遭遇卡显示「有根遭遇」；活动完成冒一句「探索收获：…」。
4. `docs/test-inventory.md` 已更新。
