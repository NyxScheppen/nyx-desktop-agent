# 后端指令

适用于 `nyx/`，以及根目录的后端测试 `tests/`、后端配置和启动脚本。与根通用规则共同使用。路径均相对仓库根目录。

修改领域功能前先读取 `docs/AGENTS.md` 中的索引、读取与同步规则；领域对象审计和文档编写也遵守该文件。

技术栈：Python 3.11+ / FastAPI / LangGraph / SQLite。六块 Facade 通过 SSE 推事件给前端，REST 提供状态与操作。

## 名词解释

项目特有名词，贯穿代码与 spec：

| 名词 | 含义 |
|---|---|
| Nyx / 尼克斯·夏本 | 小说《诺斯艾兰》女主人公，本 AI 扮演对象（设定见 `docs/canon.md`） |
| Facade（门面） | 六大模块对外统一入口；三层架构 Facade → 子系统 → 内部类 |
| EventBus（事件总线） | 所有模块通信的管道：`publish` 入队、`run()` 持久化 + 分发 + 广播 |
| correlation_id | 溯源链 ID，贯穿因果链（用户消息 → 回复 → 记忆 → 反思），前端按它分组溯源 |
| 快通道 / 慢通道 | 表达系统两档：快通道只思考一轮、不检索记忆；慢通道完整拼装（检索记忆 + 场景化记忆） |
| 短期 / 长期记忆 | 短期有容量上限 + 新鲜度淘汰；短期"实际用进回复 3 次"升长期 |
| 短期 / 长期欲望 | 短期 = 冲动（产生 → 满足 → 消退）；长期 = 野心（≤5，反思是唯一新增入口） |
| 内在生命 (inner_life) | 数值层：情感 / 性格（Big Five）/ 三观 / 精力 + 自我叙事 |
| 日程块 (schedule block) | 每小时一块的时间网格，grid 派生的临时概念，不建表持久化 |
| 场景化记忆 (scene memory) | 只慢通道生成，一次 LLM 产出「记忆内容 + 标签 + 总结」 |
| 搭话 / 碎碎念 | 跨活动即时行为，不占日程块，可打断任意活动（initiate_chat / mutter） |
| 反思 (reflection) | 唯一改慢变量（性格 / 三观 / 长期欲望 / 自我叙事）的入口 |
| eval 体系 | 对 LLM 产出的评分 + token 记账，纯记录不自动反馈修正 |
| SSE | Server-Sent Events：`GET /api/events` 实时推全部事件给前端 |

## 编码规范

#### Python

- **Python 3.11+**，严格类型注解（所有函数签名必须有完整类型标注）
- **命名**：`snake_case` 变量/函数，`PascalCase` 类，`UPPER_SNAKE` 常量
- **异步**：所有 Facade 方法和 I/O 操作用 `async def`。纯计算函数（`vad_to_category`、`decay_emotion`、`drift_personality` 等）保持同步
- **导入顺序**：标准库 → 第三方 → 本地模块（每组之间空行）
- **枚举**：用 `Enum`，如 `ContextMode`、`TickType`、`EmotionCategory`
- **docstring**：公开方法用 Google style。重点解释 "why" 而非 "what"
- **LLM 客户端**：必须统一调用，不直接使用 httpx
- **禁止**：`*` 导入、`except Exception` 吞异常（不重抛）、模块级可变全局变量。资源清理允许 `except Exception: cleanup; raise`（必须重抛，如 `connect()` 迁移失败关连接）。**豁免（best-effort 旁路）**：LLM/eval/矛盾检测/事件 handler 分发的失败只记日志返默认值或跳过、不重抛、主流程正确性不依赖其结果——如 `eval/evaluator.py:_ooc`、`memory/facade.py:_detect_contradiction`、`events/bus.py` handler 分发——允许 `except Exception` 吞异常

#### 安全

> 基础安全——防常见漏洞和泄露，不追求军工级防护。

- **API Key**：LLM Key 通过 `.env` 注入环境变量，**绝不硬编码**；`.env` 入 `.gitignore`；提供 `.env.example` 模板（空值，不含真实 Key）
- **本地数据**：对话/记忆/偏好存本地 SQLite，库文件在 `~/.nyx/`（或平台等效路径）；用户可随时导出（JSON）或一键删除；数据最小化——只存功能必需
- **网络**：出站请求仅 LLM API 调用；不上传用户数据到任何第三方；LLM 请求走 HTTPS
- **不涵盖**：不做端到端加密（本地应用无传输需求）、用户认证/多账户（单用户桌面应用）、企业级审计日志

### 测试规范

**测试哲学**：验证管道正确性，不验证 LLM 文本质量——确保数据走对流程、结构正确、边界不崩，不测"Nyx 这句话说得好不好"。

**测试层级**：

| 层级 | 范围 | 优先 |
|---|---|---|
| 单元 | 纯函数（计算、解析、矩阵运算） | 最高——有纯逻辑就先测 |
| 集成 | Facade 方法管道（Mock LLM） | 每个 Facade 方法 ≥1 条 |
| E2E | 关键用户路径（发消息 → 收回复） | 每条用户故事 1 条 |

**Mock 原则**：所有 LLM 调用处可注入 mock，返回预设 fixture；测试不依赖真实 LLM、真实桌面、真实文件系统。

**测试写法**：

- 每个 Facade 方法的测试 ≤ 5 个断言
- 纯数学函数优先测且测全（`vad_to_category`、`decay_emotion`、`drift_personality`、`energy_to_state` 等）
- 测试目录：`tests/test_{system}/`（如 `tests/test_event/`、`tests/test_memory/` …）

**覆盖标准**：不追求百分比，追求"改了会不会炸"的信心。

**测试清单更新**：每次改动测试后（新增 / 删除 / 改断言），同步 `docs/test-inventory.md` 的当前快照：更新测试文件数、系统覆盖范围和受影响的关键回归名称；不要把每条测试的断言复制进清单。详细行为以 `tests/` 源文件为准。

### 质量门

提交前（或每次对话产出后）按顺序检查：

0. 查 `docs/LessonsLearned.md`：审查/提交前先找是否有类似类型问题，尤其是本轮改动所属模块和失败模式
1. `ruff check` — 必须零报错
2. `pyright` — 必须零报错
3. `pytest` — 必须全绿
4. 人工抽查：改动的 Facade 签名是否和对应完整 spec 一致？
5. 检查：是否有完整 spec 未定义的新文件或新类？→ 如果有，追问原因
6. 如果审查或修复过程中发现新的错误类型，把教训追加/更新到 `docs/LessonsLearned.md`
7. 对本轮新增或修改的每个领域对象，逐项回答并给出 spec、代码或测试证据：
   - 我增加了新的写入口吗？所有旧消费者都覆盖了吗？
   - 我增加了新的消费者吗？所有旧数据来源都覆盖了吗？
   - 我增加了新状态吗？恢复、删除、重放定义了吗？
   - 我增加了新兜底吗？它会不会在没有证据时替用户做决定？
8. 跨存储同类对象必须验证统一发现路径；特别检查新增上传入口是否会被探索、搜索、调度等
   既有消费者发现，以及无匹配时是否错误回退到“最新一条”。

#### 禁止事项

- **禁止新增抽象层**：Facade → 子系统 → 内部类，已有三层。不要再加 Repository/Service/Manager 等额外层
- **禁止未请求的灵活性**：不要添加完整 spec 未定义的配置项、参数、回调钩子
