# 委派任务与统一阅读选材

> 本 spec 定义用户给 Nyx 安排阅读任务的完整契约，并定义探索欲在活动书库与 EPUB
> 书架之间选材的修复。任务执行仍属于活动系统，EPUB 内容与进度仍由阅读系统持有；
> 本功能不新增第七个 Facade，也不恢复已经删除的 browsing 系统。

## 元信息

- **前置依赖**：`01-types`、`02-config`、`04-module-bus-system`、`05-tools`、
  `06-memory-system`、`07-desire`、`09-activity`、`12-reading-system`
- **实现文件**：`nyx/types.py`、`nyx/enums.py`、`nyx/db.py`、
  `nyx/activity/store.py`、`nyx/activity/starter.py`、`nyx/activity/facade.py`、
  `nyx/reading/store.py`、`nyx/reading/facade.py`、`nyx/api/routes.py`、
  `nyx/app_context.py`、`frontend/src/api/client.ts`、
  `frontend/src/stores/activityStore.ts`、`frontend/src/stores/readerStore.ts`、
  `frontend/src/components/panels/ActivityPanel.tsx`、`frontend/src/types/api.ts`

## 用户故事

> 作为用户，我想把一个明确网页或书架中的 EPUB 阅读目标交给 Nyx，让任务进入她自己的
> 活动选择并留下事实记忆，以便我私下读得更快时她能正式追上，也能阅读我指定的内容。

> 作为 Nyx，我想在探索欲出现时按主题找到真正相关的本地读物；只有匹配不到时才去探索
> 网络，以免用“最近上传但无关”的材料假装满足探索欲。

## 验收标准

- [ ] 用户可以安排明确 HTTP(S) URL，或选择一本已导入 EPUB 并指定目标段落。
- [ ] 书籍目标选择展示目标段及前后各一段预览；段号仍为 1-based。
- [ ] 任务持久化并进入下一次空闲活动选择，不抢占当前活动；同块暂停活动优先恢复。
- [ ] 待执行任务按创建时间 FIFO，优先于普通欲望；低精力时先休息，不能绕过身体状态。
- [ ] 网页正文与 EPUB 原文都按最多 6000 字符块沉淀 knowledge、滚动摘要、主题与内容类别。
- [ ] EPUB 任务只单调推进 `nyx_position`，不修改 `user_position`，不触发陪读冲动、提问或联想。
- [ ] 探索欲同时检查 EPUB 书架和活动材料书库，按文件名/书名模糊匹配；匹配失败时才升级网络探索，不再读取最近未匹配材料。
- [ ] `exploration.web_enabled=false` 时拒绝新增明确 URL 任务；探索欲匹配失败时沿用禁网配置，不调用网络工具。
- [ ] 任务的排队、执行、完成和失败状态可以通过 REST 快照与 SSE 刷新显示。

## 数据模型

### 枚举与实体

```python
class AssignedTaskType(StrEnum):
    WEB = "web"
    BOOK = "book"

class AssignedTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class AssignedTask:
    id: str
    type: AssignedTaskType
    status: AssignedTaskStatus
    url: str | None
    book_id: str | None
    target_paragraph: int | None
    checkpoint: dict[str, Any]
    error: str | None
    created_at: float
    updated_at: float
```

字段组合固定：`WEB` 只设置 `url`；`BOOK` 只设置 `book_id/target_paragraph`。本轮不增加
任务标题、优先级、定时、截止时间、重复规则、编辑、拖动排序或插件式任务载荷。

### 持久化

新增 `assigned_task` 表：

```sql
CREATE TABLE assigned_task (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    url TEXT,
    book_id TEXT REFERENCES books(id) ON DELETE SET NULL,
    target_paragraph INTEGER,
    checkpoint TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
)
```

建立 `(status, created_at)` 索引支撑 FIFO 领取。任务由既有 `ActivityStore` 管理，不新增
Repository/Service/Manager。删除已安排书籍后 `book_id` 置空，执行时任务明确失败，不保留
无法验证归属的幽灵目标。

## 活动选择与生命周期

空闲选择顺序固定为：

1. 仍在运行的内存 task / `RUNNING` 活动；
2. 同一日程块最近的 `PAUSED` 活动；
3. 有待执行任务且精力低于 `ENERGY_REST_THRESHOLD` 时的 `REST`；
4. 最早创建的 `PENDING` 用户任务；
5. 排序后的短期欲望；
6. 默认观察或发呆反思。

领取任务与插入 `PENDING` activity 必须在同一个本地事务内完成。任务活动继续使用既有
`ActivityType.READING`，并在 `Activity.progress` 保存 `task_id`、任务来源、目标与
`correlation_id=task_id`；不新增只为任务服务的 ActivityType。

- 创建任务后立即尝试 `_maybe_start_activity()`；已有活动时只入队。
- 任务活动完成后置 `COMPLETED`；执行异常置 `FAILED` 并保存简短错误，不自动重试。
- 任务活动被打断时任务回到 `PENDING`；同块优先恢复原活动并重新领取，跨块则创建新的
  activity 从持久化 checkpoint 继续，旧 PAUSED 记录只留档。
- 启动恢复先执行既有 activity 恢复，再把没有活跃执行者的 `RUNNING` 任务重置为
  `PENDING`；不得让进程崩溃永久卡住队列。
- `task_updated` 是无 durable consumer 的广播事件，payload 固定为
  `{task_id, status}`，`correlation_id=task_id`。状态已先落库；SSE 广播失败不得回滚任务。

## 网页任务

- 创建时只接受带主机名的 `http` / `https` URL；网络关闭时返回冲突，不创建任务。
- 执行只调用现有 `web_fetch`，不先搜索；继续复用逐跳公网校验、重定向限制、15 秒请求
  超时和 20 万字符正文上限。
- 空正文、非公网地址、下载或解析失败都使任务 `FAILED`，不得沉淀 snippet 或伪正文。
- `checkpoint` 保存 `cursor/profile/pending`。正文从 cursor 起每次最多 6000 字符调用
  `MemoryFacade.digest_source_block`；先保存 pending，再写 knowledge，最后推进 cursor、
  采纳 profile 并清 pending。
- knowledge 使用稳定的 `web:<normalized-url>` 来源 topic；`source_name` 使用 URL。
  崩溃发生在记忆写入后、cursor 推进前时允许重放，由来源内去重吸收。
- 网页任务读完整个已抽取正文；它不改变自动自由探索“每条搜索结果最多读取 6000 字符”
  的既有语义。

## EPUB 任务与进度并发

阅读 Facade 增加窄入口：

```python
async def read_for_activity(
    book_id: str,
    target_paragraph: int | None,
    correlation_id: str,
) -> dict[str, Any]: ...

async def advance_nyx_position(
    book_id: str,
    nyx_position: int,
) -> ReadingProgress: ...
```

- 明确任务传 `target_paragraph`；普通探索欲匹配 EPUB 时传 `None`，每次从当前 Nyx 位置
  向前选择约一个 6000 字符块的段落范围。
- `read_for_activity` 复用 `ReadingIntegration.sediment(..., flush=True)`，成功后才调用
  store 的单调推进操作。推进使用 `max(current, requested)`，保留服务器当前
  `user_position/reading_speed/read_count` 并递增 revision。
- 目标已不晚于当前 `nyx_position` 时幂等返回完成，不回退、不重复沉淀；只有阅读器显式
  “重读”允许 Nyx 位置回退。
- 目标到书末时沿用既有整本完成幂等语义，首次完成递增 `read_count`；任务不生成陪读
  buffer，因此不凭空生成主观碎碎念整合。
- 前端普通进度写发生 409 后必须重读服务端快照，重试值取
  `nyx_position=max(local, server)` 并更新本地 Nyx 位置；显式重读请求除外。

## 探索欲统一选材修复

探索欲带非空 `goal.topic` 时：

1. 对 topic、EPUB `title/filename` 与 material `filename` 执行相同归一化：Unicode
   `casefold`，去扩展名，移除空白、标点和书名号。
2. 归一化后完全相等优先，其次双向包含，再按 `difflib.SequenceMatcher` 相似度排序；
   相似度低于 `0.6` 不算匹配。同分时取最近导入项。
3. EPUB 和 material 一起参加排序；已完成的候选不参与。命中 EPUB 走上述阅读 Facade
   入口，命中 material 走既有 `ReadingActivityRunner`。
4. 没有匹配时，不调用 `next_readable()`，也不拿最近上传材料兜底；通过既有限速后升级
   `FREE_EXPLORATION`。网络关闭时自由探索沿用现有本地搜索配置，不调用 web 工具。

没有非空 topic 的探索欲不能臆测书名，直接走既有默认活动；不读取最新材料。

## REST 与前端

| 方法 | 路径 | 语义 |
|---|---|---|
| GET | `/api/tasks` | 按 `created_at DESC` 返回最近任务 |
| POST | `/api/tasks/web` | 创建 `{url}` 网页任务；201 |
| POST | `/api/tasks/book` | 创建 `{book_id, target_paragraph}` EPUB 任务；201 |

- URL 形状错误、目标段落越界返回 422；书不存在返回 404；联网关闭返回 409。
- 同一类型、同一目标已有 `PENDING/RUNNING` 任务时幂等返回原任务，不创建重复行。
- 活动页使用“网页 / 书籍”分段控件。书籍模式复用 `GET /api/books` 和现有段落窗口 API，
  目标输入限制在 `[1,total_paragraphs]`，展示前一段、目标段、后一段原文预览。
- 任务列表至少显示类型、目标、状态、创建时间和失败原因；`task_updated` 触发活动 store
  重拉活动与任务快照。

## Bad Cases 与处理

| 情况 | 处理 |
|---|---|
| 当前活动正在运行 | 任务只入队，不抢占 |
| 精力过低 | 先安排 REST，任务保持 PENDING |
| URL 指向内网、抓取失败或正文为空 | 任务 FAILED，不写记忆 |
| 网页超过抓取上限 | 按既有 20 万字符正文上限阅读已抽取内容并完成 |
| 书在排队后被删除 | 任务 FAILED，错误对用户可见 |
| 目标段落越界 | 创建时 422；执行前再次校验，防排队期间数据变化 |
| 目标已经读过 | 幂等完成，不回退进度、不增加读完次数 |
| 活动中断或进程崩溃 | 从 checkpoint 重排/恢复，不永久 RUNNING |
| 记忆已写但 checkpoint 未推进 | 允许重放，来源内去重吸收 |
| 用户与后台同时保存进度 | revision 冲突后合并，非重读路径 Nyx 位置只前进 |
| 模糊匹配到多个候选 | 最高分优先，同分取最近导入；不随机选择 |
| 主题没有本地匹配 | 跳过“读最新”，按限速和联网配置进入探索 |

## 测试要点

- [ ] 单元：文件名归一化、完全匹配/包含/相似度阈值与跨书库排序。
- [ ] Store：任务 CRUD、FIFO 原子领取、重复目标、完成/失败、启动恢复和迁移索引。
- [ ] 活动集成：不抢占、暂停优先、低精力休息、任务优先欲望、无匹配不读最新、EPUB
  命中与网络升级。
- [ ] 网页集成：多块 6000 字符、pending 恢复、空正文失败、禁网拒绝、稳定来源 topic。
- [ ] 阅读集成：目标校验、只推进 Nyx、整本计数幂等、已读目标 no-op、revision 并发。
- [ ] API：三端点 2xx/404/409/422 与重复任务幂等。
- [ ] 前端：任务表单、目标预览、状态/失败展示、SSE 刷新和进度冲突合并。

## 完成定义

- [ ] `ruff check`、`pyright`、`pytest` 全绿。
- [ ] `npm test`、`npm run build` 全绿。
- [ ] `docs/facts/`、`docs/test-inventory.md`、`docs/LessonsLearned.md` 已同步。
- [ ] 变更按契约、后端、前端与文档收尾分批提交。
