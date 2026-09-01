# 阅读 × 聊天统一布局 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 前端「陪伴感」重构——聊天挪到左栏常驻、读书改真分页、立绘半透明浮层、碎碎念悬浮气泡、读书提问/联想并进对话。

**Architecture:** 复用 `StatusBar`/`MessageList`/`ChatInput` 竖排成左栏 `div.left-dock`（不新增 `LeftChatDock`）；读书事件从 `readerStore` 气泡流重路由进 `chatStore`（`addReadingTurn`）；`ReaderView` 由滚动改为 `overflow:hidden` 整页切换（纯函数 `paginate` + DOM 测量）；立绘 `Avatar` 包进 `.avatar-overlay` 半透明浮层；碎碎念删 `mutterStore`/`MutterCard` 改 `announceStore` 气泡。

**Tech Stack:** React 18 + TypeScript (strict) + Zustand + Vite；测试 Vitest + @testing-library/react。

**Spec:** `docs/frontend/08-reading-chat-layout.md`（本计划的唯一契约来源；每步都从它论证）。对齐后端 `docs/specs/24-reading-chat-turn.md`（后端已另出计划）。

## Global Constraints

- TypeScript `strict: true`——所有新签名完整标注；`types/api.ts` **不改**（`ReadingQuestionEvent`/`ReadingAssociationEvent`/`ReadingMutterEvent`/`QuestionSubtype` 已存在，spec §6）。
- 字段名 = 后端 JSON 键（snake_case 零映射）；`ChatMessage` 在 `chatStore.ts` 非 `types/api.ts`。
- 反冗余：不新增组件/抽象层/未请求的灵活性；`Avatar.tsx` 源码**一字不动**（只改 App 包法 + CSS）。
- 质量门：`npx vitest run` 全绿 + `npx tsc --noEmit` 零报错 + `docs/test-inventory.md` 快照同步。
- 所有命令在 `frontend/` 目录下执行。

---

### Task 1: `chatStore` 扩展（`addReadingTurn` + 类型 + 历史回填）

**Files:**
- Modify: `frontend/src/stores/chatStore.ts`
- Test: `frontend/tests/stores.test.ts`

**Interfaces:**
- Consumes: `QuestionSubtype`, `ReadingQuestionEvent`, `ReadingAssociationEvent`（`types/api.ts` 已有）
- Produces: `ChatMessage` 新增 `kind: "reading_question" | "reading_association"` 与可选字段 `subtype?: QuestionSubtype`、`selectedText?: string | null`、`memoryId?: string`；新 action `addReadingTurn(e: ReadingQuestionEvent | ReadingAssociationEvent): void`（Task 2 的 dispatch 调用它，Task 4 的 MessageBubble 渲染它）。

- [ ] **Step 1: 写失败测试**

在 `tests/stores.test.ts` 的 `chatStore.add*` describe（`63-151` 行）之后新增一个 describe：

```ts
describe("chatStore.addReadingTurn", () => {
  beforeEach(resetChat);

  it("reading_question → kind=reading_question + subtype/selectedText + correlation_id=book_id", () => {
    useChatStore.getState().addReadingTurn({
      event: "reading_question",
      event_id: "e1",
      correlation_id: "b1",
      content: "为什么？",
      subtype: "quote_question",
      book_id: "b1",
      paragraph_index: 4,
      selected_text: "划线句",
    });

    const { messages } = useChatStore.getState();
    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({
      id: "e1",
      role: "nyx",
      kind: "reading_question",
      content: "为什么？",
      correlation_id: "b1",
      subtype: "quote_question",
      selectedText: "划线句",
    });
  });

  it("reading_association → kind=reading_association + memoryId + content=snippet", () => {
    useChatStore.getState().addReadingTurn({
      event: "reading_association",
      event_id: "e2",
      correlation_id: "b1",
      memory_id: "m1",
      snippet: "片段",
      book_id: "b1",
      paragraph_index: 5,
    });

    const { messages } = useChatStore.getState();
    expect(messages[0]).toMatchObject({
      id: "e2",
      role: "nyx",
      kind: "reading_association",
      content: "片段",
      correlation_id: "b1",
      memoryId: "m1",
    });
  });

  it("question content 非 string 丢弃（复用 append 收窄校验）", () => {
    useChatStore.getState().addReadingTurn({
      event: "reading_question",
      event_id: "e3",
      correlation_id: "b1",
      content: 123 as unknown as string,
      subtype: "question_reflective",
      book_id: "b1",
      paragraph_index: 4,
      selected_text: null,
    });
    expect(useChatStore.getState().messages).toHaveLength(0);
  });
});
```

再在 `chatStore.loadHistory` describe（`461-541` 行）末尾（`markTyped 标记` 那条 `it` 之后）追加一条历史回填测试：

```ts
  it("loadHistory：reading_question/association 历史回填（content.content / content.snippet + 回填字段）", async () => {
    vi.stubGlobal(
      "fetch",
      historyFetch({
        reading_question: [
          {
            id: "q1",
            timestamp: 1000,
            source: "internal",
            type: "reading_question",
            content: { content: "为什么？", subtype: "quote_question", selected_text: "划线句" },
            correlation_id: "b1",
          },
        ],
        reading_association: [
          {
            id: "a1",
            timestamp: 1001,
            source: "internal",
            type: "reading_association",
            content: { snippet: "片段", memory_id: "m1" },
            correlation_id: "b1",
          },
        ],
      }),
    );

    await useChatStore.getState().loadHistory();

    const { messages } = useChatStore.getState();
    expect(messages.map((m) => m.kind)).toEqual(["reading_question", "reading_association"]);
    expect(messages[0]).toMatchObject({
      content: "为什么？",
      subtype: "quote_question",
      selectedText: "划线句",
      correlation_id: "b1",
    });
    expect(messages[1]).toMatchObject({ content: "片段", memoryId: "m1" });
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run tests/stores.test.ts -t "addReadingTurn" && npx vitest run tests/stores.test.ts -t "reading_question/association 历史回填"`
Expected: FAIL——`useChatStore.getState().addReadingTurn is not a function`（addReadingTurn 尚未定义）；历史回填那条 `messages.map(...)` 得 `[]`（HISTORY_TYPES 缺 reading 两型）。

- [ ] **Step 3: 写最小实现**

`chatStore.ts` 顶部 import 扩为：

```ts
import type {
  BackendEvent,
  QuestionSubtype,
  ReadingAssociationEvent,
  ReadingQuestionEvent,
  TextEvent,
  TextEventType,
  UserMessageEvent,
} from "../types/api";
```

`ChatMessage` 类型（`10-17` 行）替换为：

```ts
export type ChatMessage = {
  id: string; // event_id
  role: "user" | "nyx";
  kind:
    | "message" | "speak" | "ask" | "think" | "initiate_chat"
    | "reading_question" | "reading_association";
  content: string;
  correlation_id: string;
  preloaded?: boolean; // 历史回填消息：渲染时不逐字
  // 读书 turn 专属（kind==="reading_question" 才有 subtype/selectedText；"reading_association" 才有 memoryId）
  subtype?: QuestionSubtype;
  selectedText?: string | null;
  memoryId?: string;
};
```

`ChatState` 类型（`19-35` 行）在 `addInitiateChat` 行后加一行：

```ts
  addReadingTurn: (e: ReadingQuestionEvent | ReadingAssociationEvent) => void;
```

`HISTORY_TYPES`（`38-44` 行）替换为：

```ts
const HISTORY_TYPES = [
  "user_message",
  "speak",
  "ask",
  "think",
  "initiate_chat",
  "reading_question",
  "reading_association",
] as const;
```

`toChatMessage`（`49-61` 行）替换为：

```ts
function toChatMessage(e: BackendEvent): ChatMessage | null {
  const isUser = e.type === "user_message";
  const isQuestion = e.type === "reading_question";
  const isAssociation = e.type === "reading_association";
  const raw = isUser
    ? e.content.message
    : isAssociation
      ? e.content.snippet
      : e.content.content;
  if (typeof raw !== "string") return null;
  const msg: ChatMessage = {
    id: e.id,
    role: isUser ? "user" : "nyx",
    kind: isUser ? "message" : (e.type as ChatMessage["kind"]),
    content: raw,
    correlation_id: e.correlation_id,
    preloaded: true,
  };
  if (isQuestion) {
    msg.subtype = e.content.subtype as QuestionSubtype;
    msg.selectedText = e.content.selected_text as string | null;
  }
  if (isAssociation) {
    msg.memoryId = e.content.memory_id as string;
  }
  return msg;
}
```

在返回对象的 `addInitiateChat`（`122-125` 行）之后插入新 action：

```ts
    // 读书提问/联想并进对话（08 §2.2）：correlation_id = book_id（后端用 book_id 当 correlation_id），
    // 不过滤当前书（永久聊天消息，关书后仍留转录）；文本字段非 string 丢弃（复用 append 收窄）。
    addReadingTurn: (e) => {
      if (e.event === "reading_question") {
        if (typeof e.content !== "string") return;
        set((s) => ({
          messages: [
            ...s.messages,
            {
              id: e.event_id,
              role: "nyx",
              kind: "reading_question",
              content: e.content,
              correlation_id: e.book_id,
              subtype: e.subtype,
              selectedText: e.selected_text,
            },
          ],
        }));
        return;
      }
      // reading_association
      if (typeof e.snippet !== "string") return;
      set((s) => ({
        messages: [
          ...s.messages,
          {
            id: e.event_id,
            role: "nyx",
            kind: "reading_association",
            content: e.snippet,
            correlation_id: e.book_id,
            memoryId: e.memory_id,
          },
        ],
      }));
    },
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run tests/stores.test.ts`
Expected: PASS（新增 4 条绿，其余不回归）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/stores/chatStore.ts frontend/tests/stores.test.ts
git commit -m "feat(frontend): chatStore 扩 reading_question/association 类型 + addReadingTurn + 历史回填"
```

---

### Task 2: 碎碎念迁移 + dispatch 读书重路由（删 `mutterStore`/`MutterCard`）

> **这是原子迁移任务**：`mutter`/`reading_mutter` → `announceStore`，`reading_question`/`reading_association` → `chatStore.addReadingTurn`（Task 1 已交付），一并删除被替代的 `mutterStore.ts`/`MutterCard.tsx` 及其测试。**注意**：本任务让 `readerStore.addReadingBubble` 变成死代码（dispatch 不再调它），但**暂不删**——`ReaderSidebar` 仍读 `impulseBubbles`、`stores.test.ts` 仍有 `addReadingBubble` 测试，删了会炸；它在 Task 6 一并清除。

**Files:**
- Modify: `frontend/src/api/dispatch.ts`
- Delete: `frontend/src/stores/mutterStore.ts`, `frontend/src/components/shell/MutterCard.tsx`, `frontend/tests/mutterCard.test.tsx`
- Test: `frontend/tests/sse.test.ts`, `frontend/tests/stores.test.ts`

**Interfaces:**
- Consumes: `chatStore.addReadingTurn`（Task 1）
- Produces: dispatch 契约——`mutter`/`reading_mutter`/`reading_question`/`reading_association` 不再有 `addMutter`/`addReadingBubble` 调用（Task 6 依赖此前提删 readerStore 气泡流）。

- [ ] **Step 1: 改 `dispatch.ts`**

`import` 块（`1-10` 行）：删除 `import { useMutterStore } from "../stores/mutterStore";` 与 `import { useReaderStore } from "../stores/readerStore";` 两行（本文件不再用它们）。

`mutter` case（`23-29` 行）替换为：

```ts
    case "mutter":
      // 碎碎念改悬浮气泡（08 §3）：与 reading_mutter/reflection_done 统一走 announce("mutter")。
      return useAnnounceStore.getState().announce("mutter", e.content);
```

`reading_*` case（`67-72` 行）替换为：

```ts
    case "reading_mutter":
      // 读书碎碎念归入悬浮气泡（08 §2.3/§3），与全局 mutter 同一渲染路径。
      return useAnnounceStore.getState().announce("mutter", e.content);
    case "reading_question":
    case "reading_association":
      // 读书提问/联想并进对话（08 §2.3）：不再走 readerStore.addReadingBubble。
      return useChatStore.getState().addReadingTurn(e);
```

`reflection_done` case（`73-86` 行）**不动**（已 `announce("mutter", …)`）。

- [ ] **Step 2: 删被替代文件**

删除 `frontend/src/stores/mutterStore.ts`、`frontend/src/components/shell/MutterCard.tsx`、`frontend/tests/mutterCard.test.tsx`。

- [ ] **Step 3: 改 `sse.test.ts`**

`import`（`12-13` 行）：删 `import { useMutterStore } from "../stores/mutterStore";` 与 `import { useReaderStore } from "../stores/readerStore";`。

`dispatchEvent` describe 的 `beforeEach`（`157-165` 行）：删 `useMutterStore.setState({ mutters: [] });` 与 `useReaderStore.setState(...)`（如有）——实际上只有 mutterStore 一行要删。

`mutter → mutterStore` 测试（`282-295` 行）替换为：

```ts
  it("mutter → announceStore（冒气泡，不进 chatStore）", () => {
    dispatchEvent({
      event: "mutter",
      event_id: "m1",
      correlation_id: "c1",
      content: "在想你",
    });

    expect(useAnnounceStore.getState().items).toHaveLength(1);
    expect(useAnnounceStore.getState().items[0]).toMatchObject({
      kind: "mutter",
      text: "在想你",
    });
    expect(useChatStore.getState().messages).toHaveLength(0);
  });
```

`mutter 非 string content → addMutter 丢弃` 测试（`297-306` 行）**删除**（dispatch 不再做该收窄校验，spec §3 明文删 guard）。

`reading_mutter/question/association → readerStore.addReadingBubble` 测试（`382-423` 行）替换为：

```ts
  it("reading_question/association → chatStore.addReadingTurn；reading_mutter → announce(mutter)", () => {
    const addReadingTurnSpy = vi
      .spyOn(useChatStore.getState(), "addReadingTurn")
      .mockImplementation(() => {});

    const mutter: ReadingMutterEvent = {
      event: "reading_mutter",
      event_id: "e1",
      correlation_id: "b1",
      content: "妙",
      book_id: "b1",
      paragraph_index: 2,
    };
    const question: ReadingQuestionEvent = {
      event: "reading_question",
      event_id: "e2",
      correlation_id: "b1",
      content: "?",
      subtype: "question_reflective",
      book_id: "b1",
      paragraph_index: 3,
      selected_text: null,
    };
    const association: ReadingAssociationEvent = {
      event: "reading_association",
      event_id: "e3",
      correlation_id: "b1",
      memory_id: "m1",
      snippet: "片段",
      book_id: "b1",
      paragraph_index: 4,
    };

    dispatchEvent(question);
    dispatchEvent(association);
    dispatchEvent(mutter);

    expect(addReadingTurnSpy).toHaveBeenCalledTimes(2);
    expect(addReadingTurnSpy).toHaveBeenNthCalledWith(1, question);
    expect(addReadingTurnSpy).toHaveBeenNthCalledWith(2, association);
    expect(useAnnounceStore.getState().items).toHaveLength(1);
    expect(useAnnounceStore.getState().items[0]).toMatchObject({ kind: "mutter", text: "妙" });
  });
```

（`ReadingMutterEvent`/`ReadingQuestionEvent`/`ReadingAssociationEvent` import 仍被上面测试用，**保留**。）

- [ ] **Step 4: 改 `stores.test.ts`**

`import`（`9` 行）：删 `import { useMutterStore } from "../stores/mutterStore";`。

`mutterStore` describe（`153-170` 行）**整块删除**。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: 全绿 + 零报错（`addReadingBubble` 仍存在故其测试不炸，留到 Task 6 删）。

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(frontend): 碎碎念/读书反应重路由——mutter→announce、reading_question/association→chatStore，删 mutterStore/MutterCard"
```

---

### Task 3: 真分页纯函数 `paginate`（`readerStore`）

**Files:**
- Modify: `frontend/src/stores/readerStore.ts`
- Test: `frontend/tests/stores.test.ts`

**Interfaces:**
- Consumes: `Paragraph`（`types/api.ts` 已有）
- Produces: `export const GAP_PX = 12`、`export function paginate(paragraphs: Paragraph[], measureHeight: (index: number) => number, viewportHeight: number): number[][]`（Task 6 的 ReaderView 调用它）。

- [ ] **Step 1: 写失败测试**

在 `tests/stores.test.ts` 的 `readerStore` describe 纯函数区（`catchupDurationMs：非法 speed` 那条 `it`，`703-708` 行之后）新增：

```ts
  // ---- 真分页纯函数（08 §5.1） ----
  it("paginate：长段独占一页、短段一页多段、溢出封页", () => {
    const paras = [para(1, "一"), para(2, "二"), para(3, "三"), para(4, "四")];
    const H = 50; // 每段 offsetHeight 50
    const measure = () => H + GAP_PX; // measureHeight 含段间距 GAP_PX=12 → 62/段
    expect(paginate(paras, measure, 62)).toEqual([[1], [2], [3], [4]]); // 62 装不下第二段 → 每页一段
    expect(paginate(paras, measure, 124)).toEqual([[1, 2], [3, 4]]); // 62*2=124 恰好两段一页
    expect(paginate(paras, measure, 125)).toEqual([[1, 2], [3, 4]]); // 第三段 62 溢出 → 封页
    expect(paginate(paras, measure, 130)).toEqual([[1, 2], [3, 4]]); // 124+62=186>130 仍装不下第三段
  });

  it("paginate：空 paragraphs / viewportHeight<=0 返回 []", () => {
    expect(paginate([], () => 50, 100)).toEqual([]);
    expect(paginate([para(1, "一")], () => 50, 0)).toEqual([]);
    expect(paginate([para(1, "一")], () => 50, -1)).toEqual([]);
  });

  it("paginate：measureHeight 含 GAP_PX 后页界正确（间距计入分页）", () => {
    const paras = [para(1, "一"), para(2, "二")];
    const H = 50;
    // 不含 GAP_PX 时 50+50=100 <= 110 会挤进同一页；含 GAP_PX 后 62+62=124 > 110 → 分两页
    expect(paginate(paras, () => H + GAP_PX, 110)).toEqual([[1], [2]]);
  });
```

并在 `readerStore` 的 import（`11-16` 行）加 `GAP_PX` 与 `paginate`：

```ts
import {
  catchupDurationMs,
  computeWindow,
  GAP_PX,
  nyxStatusOf,
  paginate,
  useReaderStore,
} from "../src/stores/readerStore";
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run tests/stores.test.ts -t "paginate"`
Expected: FAIL——`paginate is not exported`。

- [ ] **Step 3: 写最小实现**

`readerStore.ts` 在 `_BUBBLE_CAP` 常量（`34` 行）附近加 `GAP_PX`：

```ts
export const GAP_PX = 12; // 段间距（px），对齐 CSS .reader-text__pages 的 gap: 0.75rem（08 §5.1）
```

在 `computeWindow` 纯函数之后、`needsWindowRefresh` 之前，新增 `paginate`：

```ts
// 真分页（08 §5.1）：对当前窗口段落贪心填满。measureHeight(i) 返回第 i 段（全局 1-based）
// 渲染高度 + 段间距；累计将溢出 viewportHeight 则封页、下一段开新页。空/<=0 返回 []。
export function paginate(
  paragraphs: Paragraph[],
  measureHeight: (index: number) => number,
  viewportHeight: number,
): number[][] {
  if (paragraphs.length === 0 || viewportHeight <= 0) return [];
  const pages: number[][] = [];
  let current: number[] = [];
  let used = 0;
  for (const p of paragraphs) {
    const h = measureHeight(p.index);
    if (current.length > 0 && used + h > viewportHeight) {
      pages.push(current);
      current = [];
      used = 0;
    }
    current.push(p.index);
    used += h;
  }
  if (current.length > 0) pages.push(current);
  return pages;
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run tests/stores.test.ts`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/stores/readerStore.ts frontend/tests/stores.test.ts
git commit -m "feat(frontend): 真分页纯函数 paginate + GAP_PX"
```

---

### Task 4: `MessageBubble` 渲染契约（提问/联想徽标 + 引文 + 记忆标）

**Files:**
- Modify: `frontend/src/components/chat/MessageBubble.tsx`
- Modify: `frontend/src/index.css`
- Test: `frontend/tests/chatPanel.test.tsx`

**Interfaces:**
- Consumes: `ChatMessage` 的 `reading_question`/`reading_association` kind 与 `subtype`/`selectedText`/`memoryId`（Task 1）
- Produces: 渲染契约——读书 turn **不进** `isNyxText` 白名单（即时全量，不逐字）；`reading_question` → 「提问」徽标 + 非空 `selectedText` 渲染 `.message-bubble__quote`；`reading_association` → 「联想」徽标 + `memoryId` 渲染 `.message-bubble__memory`（Task 6 的测试与最终渲染依赖此）。

- [ ] **Step 1: 写失败测试**

在 `tests/chatPanel.test.tsx` 的 `MessageBubble` describe（`32-78` 行）末尾追加两条：

```tsx
  it("reading_question → 「提问」徽标 + 即时全量 + selectedText 引文行", () => {
    render(
      <MessageBubble
        message={{
          id: "q1",
          role: "nyx",
          kind: "reading_question",
          content: "为什么？",
          correlation_id: "b1",
          subtype: "quote_question",
          selectedText: "划线句",
        }}
        ready
        onTyped={() => {}}
      />,
    );
    // 不逐字：content 即时全量（无需 typeDone）
    expect(screen.getByText("为什么？")).toBeInTheDocument();
    expect(screen.getByText("提问")).toHaveClass("message-bubble__badge");
    expect(screen.getByText("原文：「划线句」")).toHaveClass("message-bubble__quote");
  });

  it("reading_association → 「联想」徽标 + memoryId → 「记忆」标", () => {
    render(
      <MessageBubble
        message={{
          id: "a1",
          role: "nyx",
          kind: "reading_association",
          content: "片段",
          correlation_id: "b1",
          memoryId: "m1",
        }}
        ready
        onTyped={() => {}}
      />,
    );
    expect(screen.getByText("片段")).toBeInTheDocument();
    expect(screen.getByText("联想")).toHaveClass("message-bubble__badge");
    expect(screen.getByText("记忆")).toHaveClass("message-bubble__memory");
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run tests/chatPanel.test.tsx -t "reading_"`
Expected: FAIL——`getByText("提问")` / `getByText("联想")` / `getByText("记忆")` / 引文行均不存在。

- [ ] **Step 3: 写最小实现**

`MessageBubble.tsx` 的 return（`35-43` 行）替换为：

```tsx
  return (
    <div className={`message-bubble message-bubble--${role} message-bubble--${kind}`}>
      {kind === "initiate_chat" && <span className="message-bubble__badge">欲望搭话</span>}
      {kind === "reading_question" && <span className="message-bubble__badge">提问</span>}
      {kind === "reading_association" && <span className="message-bubble__badge">联想</span>}
      <span className="message-bubble__content">
        {text}
        {showCursor && <span className="cursor-blink" />}
      </span>
      {kind === "reading_question" && message.selectedText && (
        <p className="message-bubble__quote">原文：「{message.selectedText}」</p>
      )}
      {kind === "reading_association" && message.memoryId && (
        <span className="message-bubble__memory">记忆</span>
      )}
    </div>
  );
```

> `isNyxText` 白名单（`18-22` 行）**不加** reading 两 kind——读书 turn 不进打字机（spec §2.5），`typewrite=false`、`text=content` 即时全量。`MessageList.NYX_TEXT_KINDS` 同样不加。

`index.css` 在 `.message-bubble__badge`（`346-354` 行）之后追加：

```css
/* 读书 turn 的划线引用（08 §2.5）：复用 .note-item__quote 视觉语言（左金线 + 斜体小字） */
.message-bubble__quote {
  margin: 0.25rem 0 0;
  padding-left: 0.5rem;
  border-left: 2px solid var(--gold);
  font-style: italic;
  font-size: 0.8rem;
  color: var(--ink-soft);
}

/* 读书 turn 的记忆标（08 §2.5）：轻量非交互标记 */
.message-bubble__memory {
  display: inline-block;
  font-size: 0.7rem;
  padding: 0.05rem 0.4rem;
  margin-top: 0.25rem;
  border: 1px solid var(--gold);
  border-radius: 0.2rem;
  color: var(--gold);
  letter-spacing: 1px;
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run tests/chatPanel.test.tsx`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/chat/MessageBubble.tsx frontend/src/index.css frontend/tests/chatPanel.test.tsx
git commit -m "feat(frontend): MessageBubble 渲染读书提问/联想（徽标 + 引文 + 记忆标，即时不逐字）"
```

---

### Task 5: 布局重构 + 立绘浮层 + 删 `ScrollArea`

> 布局无单元测试，验证靠 `tsc --noEmit` + 构建/手动。本任务把 `mutter`/`readerStore` 之外的所有布局改动一次落完（App 装配、RightDock/StatusBar 瘦身、`.left-dock`/`.avatar-overlay`/`.announce-layer` CSS、删 `ScrollArea`）。

**Files:**
- Modify: `frontend/src/App.tsx`, `frontend/src/components/shell/RightDock.tsx`, `frontend/src/components/shell/StatusBar.tsx`, `frontend/src/index.css`
- Delete: `frontend/src/components/shell/ScrollArea.tsx`, `frontend/tests/scrollArea.test.tsx`

**Interfaces:**
- Consumes: `ChatMessage`/`useChatStore`（左栏 MessageList 读 `messages`）；`Avatar`（包进 `.avatar-overlay`）
- Produces: `View = "inner" | "desire" | "activity" | "memory" | "reading"`（删 `null`，默认 `"reading"`）；`.left-dock`/`.avatar-overlay` 结构（Task 6 的 ReaderView 在 `.side-panel` 内渲染）。

- [ ] **Step 1: 改 `App.tsx`**

`import`：删 `import MutterCard from "./components/shell/MutterCard";` 与 `import ScrollArea from "./components/shell/ScrollArea";`；加 `import MessageList from "./components/chat/MessageList";`、`import Avatar from "./components/inner/Avatar";`（按字母序插在 `ChatInput` 之后、`InnerStatePanel` 之前）；加 `import { useChatStore } from "./stores/chatStore";`（插在 `useActivityStore` 之后）。

`view` state（`46-47` 行）替换为：

```tsx
  // 中间内容区当前视图：默认 reading（书架/阅读页）；其余 = 对应面板（RightDock 底部按钮切换）
  const [view, setView] = useState<View>("reading");
```

在 `bookId` selector 附近加：

```tsx
  const messages = useChatStore((s) => s.messages);
```

`<main className="game-shell">` 的 children（`96-116` 行）替换为：

```tsx
      <main className="game-shell" style={shellStyle}>
        <div className="left-dock">
          <StatusBar />
          <MessageList messages={messages} />
          <ChatInput />
        </div>
        <div className="game-main">
          <section className="side-panel">
            <div className="side-panel__body">
              {view === "inner" && <InnerStatePanel />}
              {view === "desire" && <DesiresPanel />}
              {view === "activity" && <ActivityPanel />}
              {view === "memory" && <MemoryPanel />}
              {view === "reading" && (bookId === null ? <BookshelfView /> : <ReaderView />)}
            </div>
          </section>
          <div className="avatar-overlay">
            <Avatar />
          </div>
        </div>
        <RightDock view={view} onSwitch={setView} />
      </main>
```

（顶部 `37-39` 行装配注释顺带更新为「左栏常驻对话 + 中间内容区 + 立绘浮层 + 底部导航」——外科式只改这句，别动其它。）

- [ ] **Step 2: 改 `RightDock.tsx`**

`View` 类型（`2` 行）替换为：

```ts
// 中间内容区当前视图（聊天不再可切换，左栏常驻）：默认 reading。
export type View = "inner" | "desire" | "activity" | "memory" | "reading";
```

`ENTRIES`（`11-18` 行）删 `{ label: "聊天", view: null },` 一行（其余五条顺序不变）。

- [ ] **Step 3: 改 `StatusBar.tsx`**

删 `import Avatar from "../inner/Avatar";`（`5` 行）与 `<Avatar />`（`18` 行）。顶部注释（`8` 行）改为「状态条（左栏顶部）：信息块（名字/心情/精力条/现在状态）。立绘迁到中间浮层（08 §4）。」

- [ ] **Step 4: 改 `index.css` 布局**

`.game-shell`（`98-108` 行）：`grid-template-rows: minmax(0, 1fr) auto auto;` → `grid-template-rows: minmax(0, 1fr) auto;`。

`.game-main`（`110-116` 行）：加一行 `position: relative;`（立绘浮层锚定参照）。

`.status-bar`（`119-132` 行）：删 `grid-column: 1; grid-row: 1;` 与 `overflow-y: auto;`，加 `flex-shrink: 0;`（现在是 `.left-dock` 的 flex 子项）。

`.chat-input`（`437-449` 行）：删 `grid-column: 2; grid-row: 3;`，加 `flex-shrink: 0;`。

`.message-list`（`300-308` 行）：删 `margin-bottom: 0.75rem;`，加面板边框/背景（对齐 StatusBar/ChatInput 面板观感）：

```css
.message-list {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  overflow-y: auto;
  padding: 0.75rem;
  border: 2px double var(--gold);
  box-shadow: inset 0 0 0 1px var(--parchment-deep);
  border-radius: 4px;
  background: var(--parchment);
}
```

新增 `.left-dock`（插在 `.game-shell` 之后）：

```css
/* 左栏对话（08 §1）：StatusBar + MessageList + ChatInput 竖排，占 col1 row1/span 2 */
.left-dock {
  grid-column: 1;
  grid-row: 1 / span 2;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
```

新增立绘浮层（插在 `.avatar` 规则之后）：

```css
/* 立绘半透明浮层（08 §4）：盖在 game-main 右侧上层，不占布局宽、不挡正文点击 */
.avatar-overlay {
  position: absolute;
  top: 12px;
  right: 12px;
  bottom: 12px;
  width: 40%;
  z-index: 1;
  pointer-events: none;
}
.avatar-overlay .avatar {
  pointer-events: auto; /* 只在立绘上恢复点击（戳立绘 + 红点） */
  height: 100%;         /* 让立绘的 height:100% 有确定参照（spec 未写，补上否则立绘塌成 0 高） */
}
.avatar-overlay .emotion-sprite--portrait {
  opacity: 0.22;        /* 调淡 */
  height: 100%;
  object-fit: contain;  /* 整身高立绘 contain 不变形 */
}
.avatar-overlay .avatar-notice {
  opacity: 1; /* 红点不被立绘 0.22 拖淡 */
}
```

`.announce-layer`（`993-1004` 行）：`left: 16px` → `left: auto`；`bottom: 64px` → `top: 72px; bottom: auto`；`align-items: flex-start` → `flex-end`（气泡靠右、贴着立绘浮层上沿，spec §3）。

删 `.mutter-card` 全部规则（`230-278` 行，含 `__title`/`__empty`/`__list`/`__item`）；删 `.scroll-area` 与 `.scroll-area__body`（`279-297` 行，保留其后的「消息列表」注释）。

- [ ] **Step 5: 删 `ScrollArea.tsx` / `scrollArea.test.tsx`**

删除 `frontend/src/components/shell/ScrollArea.tsx` 与 `frontend/tests/scrollArea.test.tsx`。

- [ ] **Step 6: 验证**

Run: `cd frontend && npx tsc --noEmit && npx vitest run`
Expected: 零报错 + 全绿（scrollArea.test.tsx 已删，不再引用 ScrollArea）。

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(frontend): 左栏常驻对话 + 立绘半透明浮层 + 删 ScrollArea/MutterCard 布局"
```

---

### Task 6: `ReaderView` 真分页 + `ReaderSidebar` 拆解 + `readerStore` 删气泡流

> **这是原子迁移任务**：ReaderView 由滚动改真分页、ReaderSidebar 拆解（进度→header、笔记→footer）、`readerStore` 删 `impulseBubbles`/`addReadingBubble`/`ReadingBubble` 类型（清理 Task 2 留下的死代码）。三者耦合——删 `impulseBubbles` 会炸 ReaderSidebar 与相关测试，必须同步拆解。

**Files:**
- Modify: `frontend/src/components/reading/ReaderView.tsx`, `frontend/src/stores/readerStore.ts`, `frontend/src/index.css`
- Delete: `frontend/src/components/reading/ReaderSidebar.tsx`, `frontend/tests/readerSidebar.test.tsx`
- Test: `frontend/tests/readerView.test.tsx`, `frontend/tests/stores.test.ts`

**Interfaces:**
- Consumes: `paginate`/`GAP_PX`（Task 3）、`syncPosition`（既有）
- Produces: ReaderView 真分页组件；readerStore 只留书架/进度/段落/追赶/笔记（无气泡流）。

- [ ] **Step 1: 重写 `ReaderView.tsx`**

全文件替换为：

```tsx
import { useLayoutEffect, useRef, useState } from "react";
import { GAP_PX, paginate, useReaderStore } from "../../stores/readerStore";
import { useSettingsStore } from "../../stores/settingsStore";
import NotePanel from "./NotePanel";

// 阅读页（08 §5 真分页）：正文 overflow:hidden 整页切换，取消滚动/滚轮。
// 页序由纯函数 paginate 从段落实测高度算出；pageIndex 从 userPosition（页首段）反推，
// 翻页调 syncPosition(页首段) 复用既有「前翻逐段补发冲动 + putProgress + 窗口重拉 + 追赶」管线。
export default function ReaderView() {
  const bookId = useReaderStore((s) => s.bookId);
  const books = useReaderStore((s) => s.books);
  const totalParagraphs = useReaderStore((s) => s.totalParagraphs);
  const paragraphs = useReaderStore((s) => s.paragraphs);
  const windowFrom = useReaderStore((s) => s.windowFrom);
  const userPosition = useReaderStore((s) => s.userPosition);
  const nyxPosition = useReaderStore((s) => s.nyxPosition);
  const readCount = useReaderStore((s) => s.readCount);
  const syncPosition = useReaderStore((s) => s.syncPosition);
  const closeBook = useReaderStore((s) => s.closeBook);
  const reread = useReaderStore((s) => s.reread);
  const fontScale = useSettingsStore((s) => s.fontScale); // 字号变化 → 段高变 → 重测重分页

  const viewportRef = useRef<HTMLDivElement | null>(null);
  const paraRefs = useRef<Map<number, HTMLParagraphElement>>(new Map());
  const [viewportHeight, setViewportHeight] = useState(0);
  const [pages, setPages] = useState<number[][]>([]);
  const [pageIndex, setPageIndex] = useState(0);
  const [noteOpen, setNoteOpen] = useState(false);

  // measureHeight(index)：第 index 段（全局 1-based）渲染高度 + 段间距 GAP_PX。
  // 读 ref（稳定），不进 effect deps（加进去反而每次 render 重跑）。
  const measureHeight = (index: number) =>
    (paraRefs.current.get(index)?.offsetHeight ?? 0) + GAP_PX;

  // viewportHeight = .reader-text 的 clientHeight，由 ResizeObserver 维护（08 §5.2）。
  useLayoutEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const update = () => setViewportHeight(el.clientHeight);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // 重测重分页：paragraphs / fontScale / viewportHeight / windowFrom 任一变化（08 §5.2）。
  useLayoutEffect(() => {
    setPages(paginate(paragraphs, measureHeight, viewportHeight));
  }, [paragraphs, fontScale, viewportHeight, windowFrom]);

  // pageIndex 从 userPosition（页首段）反推（08 §5.3）：找不到归 0，再 clamp。
  useLayoutEffect(() => {
    if (pages.length === 0) return;
    const found = pages.findIndex((p) => p.includes(userPosition));
    setPageIndex(Math.max(0, Math.min(found < 0 ? 0 : found, pages.length - 1)));
  }, [pages, userPosition]);

  // 整页切换（08 §5.4）：窗口内 pageIndex 由 userPosition 反推驱动；
  // 窗口首/末页越界时借 syncPosition 的 needsWindowRefresh 触发重拉（§5.5 既有，不改）。
  const goPage = (dir: 1 | -1) => {
    if (dir === 1) {
      if (pageIndex < pages.length - 1) {
        void syncPosition(pages[pageIndex + 1][0]);
      } else if (userPosition < totalParagraphs) {
        // 窗口末页但书未读完：跳到下一窗口首段（窗口末段 +1），needsWindowRefresh 重拉。
        void syncPosition((paragraphs[paragraphs.length - 1]?.index ?? userPosition) + 1);
      }
    } else if (pageIndex > 0) {
      void syncPosition(pages[pageIndex - 1][0]);
    } else if (windowFrom > 1) {
      // 窗口首页但非书首：回上一段，needsWindowRefresh（< windowFrom）重拉上一窗口。
      void syncPosition(windowFrom - 1);
    }
  };

  const title = books.find((b) => b.id === bookId)?.title ?? "阅读中";

  // translateY：当前页前所有段的累计高度（整页视觉切换，08 §5.4）。
  let offset = 0;
  for (let i = 0; i < pageIndex; i++) {
    for (const idx of pages[i]) offset += measureHeight(idx);
  }

  return (
    <div className="reader">
      <header className="reader__header">
        <button type="button" className="reading-btn" onClick={closeBook}>
          返回书架
        </button>
        <span className="reader__title">{title}</span>
        <span className="reader__pos">
          她读到第 {nyxPosition} 段 · 你读到第 {userPosition} / {totalParagraphs} 段
        </span>
      </header>
      <div className="reader__body">
        <div className="reader-text" ref={viewportRef}>
          <div
            className="reader-text__pages"
            style={{ transform: `translateY(-${offset}px)` }}
          >
            {paragraphs.map((p) => {
              const cls = ["reader-text__para"];
              if (p.is_chapter_start) cls.push("reader-text__para--chapter");
              if (p.index === userPosition) cls.push("reader-text__para--current");
              if (p.index === nyxPosition) cls.push("reader-text__para--nyx");
              return (
                <p
                  key={p.id}
                  className={cls.join(" ")}
                  ref={(el) => {
                    if (el) paraRefs.current.set(p.index, el);
                    else paraRefs.current.delete(p.index);
                  }}
                >
                  {p.text}
                </p>
              );
            })}
          </div>
        </div>
      </div>
      <footer className="reader__footer">
        <button
          type="button"
          className="reading-btn"
          onClick={() => goPage(-1)}
          disabled={userPosition <= 1}
        >
          上一页
        </button>
        <button
          type="button"
          className="reading-btn"
          onClick={() => goPage(1)}
          disabled={userPosition >= totalParagraphs}
        >
          下一页
        </button>
        {readCount >= 1 && (
          <button type="button" className="reading-btn" onClick={() => void reread()}>
            重读
          </button>
        )}
        <button type="button" className="reading-btn" onClick={() => setNoteOpen(true)}>
          笔记
        </button>
      </footer>
      {noteOpen && <NotePanel onClose={() => setNoteOpen(false)} />}
    </div>
  );
}
```

> **两个 spec-gap 决策（已选，执行时照此）**：① 窗口首/末页的跨窗口翻页——spec §5.4 只定义窗口内 `pages[pageIndex±1]`，跨窗口分支（`syncPosition(windowFrom-1)` / `syncPosition(末段+1)`）是本计划补的，语义正确（借 `needsWindowRefresh` 重拉）。② header 进度行合并了旧 `{user}/{total}` 与侧栏的「她读到第 K 段」，保留 total（原信息不丢）。

- [ ] **Step 2: 删 `ReaderSidebar.tsx` / `readerSidebar.test.tsx`**

删除 `frontend/src/components/reading/ReaderSidebar.tsx` 与 `frontend/tests/readerSidebar.test.tsx`。

- [ ] **Step 3: `readerStore` 删气泡流**

`readerStore.ts`：
- 删 import `ReadingAssociationEvent`, `ReadingMutterEvent`, `ReadingQuestionEvent`（`18-20` 行）。
- 删 `_BUBBLE_CAP` 常量（`34` 行）与 `ReadingBubbleKind`/`ReadingBubble` 类型（`36-47` 行）。
- `ReaderState` 删 `impulseBubbles`（`60` 行）与 `addReadingBubble`（`72` 行）两行。
- 返回对象删 `impulseBubbles: [],`（`155` 行）与 `addReadingBubble` 实现（`294-332` 行，含其上方 `294` 注释）。
- `closeBook`（`190-204` 行）删 `impulseBubbles: [],`（`200` 行）。
- 顶部注释（`24-25` 行）「冲动气泡 + 笔记见 07」改为「笔记见 07-reading-events」。

- [ ] **Step 4: 改 `index.css` 阅读区**

`.reader-text`（`1150-1159` 行）替换为：

```css
.reader-text {
  flex: 1;
  min-width: 0;
  overflow: hidden; /* 真分页：无滚动条、无滚轮（08 §5） */
  position: relative;
}

.reader-text__pages {
  display: flex;
  flex-direction: column;
  gap: 0.75rem; /* = GAP_PX（12px） */
  transition: transform 0.3s ease;
  will-change: transform;
}
```

`.reader-text__para--chapter`（`1169-1173` 行）：`margin-top: 1.5rem` → `padding-top: 1.5rem`。**必要修正**：spec §5.2 假设「段 margin:0，offsetHeight 即段高」，但章首段带 `margin-top`，`offsetHeight` 不含 margin 会少测 1.5rem、页末裁字；`padding-top` 视觉等价（borderless 段落），且让 `measureHeight = offsetHeight + GAP_PX` 精确。

删 `.reader-sidebar`（`1193-1233` 行）、`.reader-sidebar__bubbles`（`1236-1242` 行）、`.reader-bubble`（`1244-1282` 行）全部规则。

- [ ] **Step 5: 改 `readerView.test.tsx`**

全文件替换为：

```tsx
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ReaderView from "../src/components/reading/ReaderView";
import { useReaderStore } from "../src/stores/readerStore";
import type { Paragraph } from "../src/types/api";

const para = (index: number, text: string): Paragraph => ({
  id: `p${index}`,
  book_id: "b1",
  index,
  text,
  is_chapter_start: index === 1,
});

describe("ReaderView 位置高亮（真分页）", () => {
  beforeEach(() => {
    // jsdom 无 ResizeObserver：stub 空实现（viewportHeight 保持 0，分页返回空页，不影响类名断言）
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        disconnect() {}
        unobserve() {}
      },
    );
    useReaderStore.setState({
      books: [],
      bookId: "b1",
      totalParagraphs: 6,
      paragraphs: [
        para(1, "一"),
        para(2, "二"),
        para(3, "三"),
        para(4, "四"),
        para(5, "五"),
        para(6, "六"),
      ],
      userPosition: 3,
      nyxPosition: 5,
      readCount: 0,
      notes: [],
      notesError: null,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("当前段加 --current、Nyx 段加 --nyx，其余段无", () => {
    const { container } = render(<ReaderView />);
    const paras = Array.from(container.querySelectorAll(".reader-text__para"));
    const byText = (t: string) => paras.find((p) => p.textContent === t);

    expect(byText("三")?.className).toContain("reader-text__para--current");
    expect(byText("三")?.className).not.toContain("reader-text__para--nyx");
    expect(byText("五")?.className).toContain("reader-text__para--nyx");
    expect(byText("五")?.className).not.toContain("reader-text__para--current");
    expect(byText("二")?.className).not.toContain("reader-text__para--current");
    expect(byText("二")?.className).not.toContain("reader-text__para--nyx");
  });

  it("侧栏已拆：笔记入口在 footer、header 显示她/你读到第几段", () => {
    render(<ReaderView />);
    expect(document.querySelector(".reader-sidebar")).toBeNull(); // 侧栏已拆
    expect(screen.getByRole("button", { name: "笔记" })).toBeInTheDocument(); // 笔记入口在 footer
    const pos = document.querySelector(".reader__pos")?.textContent;
    expect(pos).toContain("她读到第 5 段");
    expect(pos).toContain("你读到第 3");
  });
});
```

- [ ] **Step 6: 改 `stores.test.ts` 删气泡测试**

- 删 `addReadingBubble` 三个测试（`995-1050` 行）与其上方三个事件 fixture（`966-993` 行）。
- `beforeEach` 的 `setState`（`662-679` 行）删 `impulseBubbles: [],`（`676` 行）。
- import（`22-24` 行）删 `ReadingAssociationEvent`, `ReadingMutterEvent`, `ReadingQuestionEvent`（现仅 `addReadingBubble` fixture 用，删后 unused）。

- [ ] **Step 7: 验证**

Run: `cd frontend && npx tsc --noEmit && npx vitest run`
Expected: 零报错 + 全绿。

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(frontend): 读书真分页 + 拆 ReaderSidebar + readerStore 删冲动气泡流"
```

---

### Task 7: docs ripple + `test-inventory.md` 快照

**Files:**
- Modify: `docs/frontend/01-sse.md`, `docs/frontend/02-stores.md`, `docs/frontend/03-chat-panel.md`, `docs/frontend/06-reading-panel.md`, `docs/frontend/07-reading-events.md`, `docs/test-inventory.md`

**Interfaces:**
- Consumes: 上述任务的实际契约（dispatch 重路由、chatStore.addReadingTurn、真分页、ReaderSidebar 拆解）。

- [ ] **Step 1: 同步五个前端 spec（按 spec 08 §6 反向修订）**

- `01-sse.md`：分发表 `reading_question`/`reading_association` → `chatStore.addReadingTurn`；`reading_mutter` → `announceStore`；`mutter` → `announceStore`（原 mutterStore）；`reflection_done` 保持 `announce("mutter")`。
- `02-stores.md`：`chatStore` 增 `addReadingTurn` + `ChatMessage` 扩 `reading_*` kind 与 `subtype/selectedText/memoryId`；`readerStore` 删 `impulseBubbles`/`addReadingBubble`/`ReadingBubble`/`ReadingBubbleKind`；`mutterStore` 条目删除。
- `03-chat-panel.md`：聊天主区「中间舞台」→「左栏常驻」；`ScrollArea` 删除；`MessageBubble.isNyxText` 与 `MessageList.NYX_TEXT_KINDS` 明确**不加** reading 两 kind（即时渲染）。
- `06-reading-panel.md`：§5「滚动容器 + onScroll」→「真分页」（`paginate` + 测量/重测/翻页契约）；`ReaderSidebar` 拆解（进度→header、气泡迁走、笔记→footer）；`readerStore` 删 `turnPage`（已由 `syncPosition` 语义承载）。
- `07-reading-events.md`：§2「冲动气泡（ReaderSidebar）」→「并进对话/悬浮气泡」。

- [ ] **Step 2: 更新 `docs/test-inventory.md` 快照**

按本次测试变更，**删**以下行（组件/文件已删或断言已改）：`mutterStore.addMutter`（867）、`addReadingBubble` 三行（917-919）、`ReaderSidebar` 两行（932-933）、`MutterCard` 两行（1016-1017）、`ScrollArea` 一行（1060）、`dispatch > mutter → mutterStore`（1045）、`dispatch > mutter 非 string`（1046）；**改**以下行的断言：824/825/829（dispatch 路由，改为 announce/addReadingTurn 文案）、931（ReaderView 真分页 + 笔记入口）。**增**以下行：`chatStore.addReadingTurn`（question/association 落对）、`chatStore.loadHistory`（reading 回填）、`paginate`（三态 + 空返回）、`MessageBubble` reading 两 kind（徽标 + 引文 + 记忆标）。

> 具体行号以 `grep` 实际结果为准（该文件是快照，随每次提交漂移）；只记现状，不记历史。

- [ ] **Step 3: 验证 + Commit**

Run: `cd frontend && npx tsc --noEmit && npx vitest run`（确认 docs 改动没碰坏代码）
Commit: `git add -A && git commit -m "docs(frontend): 08 布局 ripple 同步 + test-inventory 快照"`

---

## Self-Review 记录

**Spec 覆盖**：§1 布局→Task 5；§2.1/2.2 类型+action→Task 1、§2.3 dispatch→Task 2、§2.4 readerStore 删气泡→Task 6、§2.5 MessageBubble→Task 4；§3 碎碎念→Task 2（announce 重定位 CSS 在 Task 5）；§4 立绘浮层→Task 5；§5.1 paginate→Task 3、§5.2-5.6 真分页+拆侧栏→Task 6；§6 反向修订→Task 7；§7 测试要点→各任务内联。

**已标记的两个 spec-gap 决策**：① 跨窗口翻页分支（Task 6 Step 1 注释）；② `.avatar` 需补 `height:100%`、章首段 `margin-top→padding-top`（Task 5/6 Step 内注释）——均已在任务内写死，非占位。

**类型一致性**：`addReadingTurn`（Task 1 定义签名 `(e: ReadingQuestionEvent | ReadingAssociationEvent) => void`）与 Task 2 的 dispatch 调用、Task 6 删 addReadingBubble 的前提自洽；`paginate`/`GAP_PX`（Task 3 导出）与 Task 6 ReaderView 引用自洽；`View` 类型删 `null`（Task 5）与 App `useState<View>("reading")` 自洽。

**一个刻意取舍**：Task 2 之后 `readerStore.addReadingBubble` 短暂成死代码，Task 6 才删——为保持中间每个任务 tsc/vitest 全绿（避免删气泡流与拆 ReaderSidebar 强耦合进同一 commit）。已在 Task 2 顶部显著标注。
