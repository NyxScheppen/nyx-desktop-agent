# V3 预备路线图（V2 之后的功能）

> 本文档汇总 **V2 完成之后**才排期的功能（backlog），只记方向与关键接入点，不做详细实现旁注。
> V2 项见 [`V2-roadmap.md`](./V2-roadmap.md)；~~删除线~~ = 已落地（移出 V3）。
> 每项标「愿景出处」（design.md 章节）——无设计章节的标「—」。

## 可观测（observability）

| 功能 | 愿景出处 | 说明 |
|---|---|---|
| LangSmith 接入（tracing） | — | 三行 env：`LANGCHAIN_TRACING_V2=true` + `LANGCHAIN_API_KEY` + `LANGCHAIN_PROJECT`。项目已基于 `langchain-openai`（`ChatOpenAI`）+ `langgraph`，`langsmith` 是传递依赖，无需新依赖、改代码 |

**关键点**：
- 变量放 `.env`：`nyx/__init__.py` 已在 import 最前 `load_dotenv()`，早于任何 LLM/graph 调用。
- trace 覆盖：每次 `ChatOpenAI` 调用 + 每次 `StateGraph` 运行（节点步骤）。
- 脱敏：`LANGCHAIN_HIDE_INPUTS=true` / `LANGCHAIN_HIDE_OUTPUTS=true`（遮 prompt/回复原文）。
- **隐私**：完整 prompt/回复会上传 LangSmith 云；个人陪伴数据需掂量——可自托管 LangSmith，或不用时置 `LANGCHAIN_TRACING_V2=false`。
