# REST 客户端（`api/client.ts`）

> 薄 fetch 封装：7 个端点函数 + 统一错误契约。被 `chatStore`（`postChat`/`getEventsLog`）、`innerLifeStore`（`getState`）、`usePresence`（`postObserve`）、两个快照 store（`getDesires`/`getActivity`/`getActivityResults`）共享。
> 范围：`api/client.ts` 全部。`BASE_URL` 常量在此定义，SSE 与 REST 共用（01-sse §3）。
> 对齐后端：前端的基础设施独立成 spec，同 `04-db` / `05-event` / `03-llm` 各自独立。

## 1. 函数签名 + 端点映射

```typescript
const BASE_URL = "";   // 空 = 相对路径，走 Vite proxy 同源转发（18-api 不做 CORS，localhost 同源）

async function postChat(message: string): Promise<{ event_id: string }>          // POST /api/chat
async function getState(): Promise<CurrentState>                                 // GET /api/state
async function postObserve(presence: Presence, windowTitle: string): Promise<{ event_id: string }>  // POST /api/observe
async function getDesires(): Promise<DesireState>                                // GET /api/desires
async function getActivity(): Promise<ActivitySnapshot>                          // GET /api/activity
async function getActivityResults(): Promise<Activity[]>                          // GET /api/activity/results
async function getEventsLog(params?): Promise<BackendEvent[]>                    // GET /api/events/log?limit=&event_type=&correlation_id=
```

- 请求体键名 = 后端 tech-ref §4 请求体键（snake_case 零映射）：`postChat` 发 `{message}`、`postObserve` 发 `{presence, window_title}`。
- **请求头**：`Content-Type: application/json`（FastAPI + Pydantic 体，错头会 422）。
- 返回值直接 JSON 反序列化后上抛给调用方，**不包裹** `{ok, data}`。

## 2. 错误契约（统一）

- **成功返回数据、失败 throw**。所有函数：fetch 网络错误（`TypeError`）与非 2xx 响应（读 body 错误信息后 `throw new Error(...)`）都上抛——**不返回 `{ok:false}`、不返回 null**。调用方自行 try/catch：
- **非 2xx 错误体形状**：后端是 FastAPI，错误体 = 默认 `{"detail": str}`（18-api 未自定义错误响应）。client 读 `body.detail`，防御式兜底 `body.detail ?? body.error ?? JSON.stringify(body)`——任何形状都出非空 message，不 `undefined`。
  - `sendMessage`：catch → `sendError = e.message`（02-stores §1；失败时 `isReplying` 本就 false——成功才置 true，无需复位）。
  - `refreshState`：catch → `error = e.message`、`loading=false`（02-stores §2）。
  - `usePresence`：catch → `console.error` + 静默（下次采样重试，不上屏，README §2）。

## 3. 边界

- client.ts 只做「序列化 + fetch + 反序列化 + 错误上抛」，**不含业务逻辑**（不置 isReplying、不写 store、不重试）——那归 store action / hook。
- **不运行时校验响应形状**：`res.json()` 返回后直接 cast（信任零映射，字段名 = 后端 JSON 键）。校验归 store/hook（如 01-sse §4.1 的 `typeof` 收窄）；client 加校验 = 边界越位。
- 不封装重试/超时（核心先行未请求）；`sendMessage` 的 60s 超时兜底在 store action 层做，不进 client。

## 4. 测试（`tests/api.test.ts`）

- 每个函数 mock `fetch`，断言请求 URL/method/请求体键 + 响应解析：
  - `postChat`：请求 `POST /api/chat`、body `{message}` → 返回 `{event_id}` 解析正确。
  - `getState`：`GET /api/state` → 返回 `CurrentState` 解析正确。
  - `postObserve`：`POST /api/observe`、body `{presence, window_title}` → 返回 `{event_id}` 解析正确。
  - `getDesires`：`GET /api/desires` → `DesireState` 解析正确。
  - `getActivity`：`GET /api/activity` → `ActivitySnapshot` 解析正确。
  - `getActivityResults`：`GET /api/activity/results` → `Activity[]` 解析正确。
  - `getEventsLog(params)`：`limit`/`event_type`/`correlation_id` 拼进 query。
- **错误契约**：非 2xx 响应（mock body `{"detail": "..."}`）→ throw（`Error`，message 含 `detail` 内容）；fetch 网络错误（reject `TypeError`）→ 上抛不吞；不返回 `{ok:false}`/null。
- 不依赖真实后端；验证管道正确（端点走对、键零映射、错误上抛），不验证视觉。
