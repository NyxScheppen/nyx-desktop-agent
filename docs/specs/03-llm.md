# LLM 统一客户端

> 范围：`nyx/llm/client.py`（`LlmClient` + `LlmMessage`）与 `nyx/llm/vision.py`（`VisionClient`），统一处理文本与多模态 LLM 调用、模型记录、provider endpoint 和可注入 mock。
> 纯客户端 spec：只做「调 LLM → 返回 `LLMOutput`」，不含 Facade、不含 DDL、不含 API。
> spec 只定义客户端契约（`LlmClient` / `LlmMessage` / `VisionClient` 签名 + 调用语义）；实现以 `nyx/llm/client.py` 与 `nyx/llm/vision.py` 源文件为准。`LLMOutput` 由 `01-types` 定义，`LlmConfig` / `VisionConfig` / `ConfigError` 由 `02-config` 定义。

## 元信息

- **前置依赖**：01-types（`LLMOutput`）、02-config（`LlmConfig`、`VisionConfig`、`ConfigError`）

## 用户故事

> 作为 Nyx 系统的开发者，我想要全项目唯一的 LLM 调用出口——统一走 LangChain、默认 Deepseek、模型名随每次调用记录、测试可注入 mock，以便任何子系统调 LLM 都走同一条路。

## 验收标准

- [ ] `client.py` 含 `LlmClient` + `LlmMessage`，`vision.py` 含 `VisionClient`（实现见对应源文件）
- [ ] 全项目只有这一处直接调 LLM（不直接用 httpx、不绕过 client 直接 `ChatOpenAI`）
- [ ] `complete()` 返回 `LLMOutput`：`module`/`type`/`content`/`model`/`correlation_id` 及独立的 `prompt_messages` 快照随每次调用正确回填
- [ ] `json_mode=True` 时向模型传 `response_format={"type": "json_object"}`
- [ ] `LlmClient(model=..., model_name=...)` 可注入 fake model，测试不触网
- [ ] `pyright` strict 零报错
- [ ] api key 不进代码：`from_config` 用 `os.environ.get(api_key_env)` 读，未设报 `ConfigError`

## 技术方案

- **实现文件**：`nyx/llm/client.py`、`nyx/llm/vision.py`（无 Facade、无 API、无数据变更）
- **库**：`langchain_core`（`BaseChatModel` / 消息类）、`langchain_openai`（`ChatOpenAI`，deepseek / openai / ollama 等走 OpenAI 兼容接口）
- **公开面**：`LlmMessage` 的唯一类型定义在 `nyx.types`，`nyx.llm.client` 导入后仍支持 `from nyx.llm.client import LlmClient, LlmMessage`；视觉入口为 `from nyx.llm.vision import VisionClient`（不加 `__all__`）
- **最终 prompt 快照**：`complete()` 在第一次 await 前复制 `role/content`，并用该快照构造传给 `ainvoke` 的消息；返回的 `LLMOutput.prompt_messages` 因而不受调用方后续修改影响。这里的“最终 prompt”仅指应用层有序消息，不包含独立 kwargs `tools` / `response_format`、provider 隐式字段或视觉调用。
- **内部类（非 Facade）**：Facade / LangGraph 节点都通过它调 LLM，是透明化+可追溯的落点
- **多 provider（OpenAI 兼容）**：`from_config` 用 `resolve_base_url(provider, base_url)` 解析 endpoint——显式 `llm.base_url` 优先，否则查内置映射（deepseek / openai / ollama）；无命中报 `ConfigError`（列出内置 provider + 提示配 `llm.base_url`）。统一走 `ChatOpenAI`，token 抽取不变
- **屏幕视觉（`vision.py`，V2）**：`VisionClient` 是独立多模态客户端（Ollama 视觉模型同走 OpenAI 兼容 `ChatOpenAI`），消息带 `image_url` 块、不混入纯文本 `complete`；复用 `resolve_base_url`（故该函数公开，供 `vision.py` 跨模块导入）。`from_config` 与 `LlmClient` 同规则读 key：`os.environ.get(config.api_key_env)`，未设且非 Ollama 报 `ConfigError`、Ollama 免 key 占位。
- **超时/重试**：`from_config` 把 `config.timeout` / `config.max_retries`（02-config 的 `LlmConfig`）透传给 `ChatOpenAI`；重试仍由 LangChain 兜底，异常原样上抛由调用方处理
- **采样温度**：`from_config` 把 `config.temperature`（02-config 的 `LlmConfig`，0-2，默认 0.8）透传给 `ChatOpenAI`；`complete()` 不加 per-call 温度——单一全局旋钮，让人格声音更一致（比 DeepSeek 默认 1.0 略收紧）
- **json_mode = 减少 parse 失败重试**：欲望生成/分类器要结构化输出，靠 `response_format` 保证合法 JSON，少一次重调
- **依赖 pin（实现时锁）**：`pyproject.toml` 里 `langchain-core`、`langchain-openai` 锁精确版本（非 `>=` 宽范围）；`pydantic` 用 `>=2.0` floor（`SecretStr` 自 v1 稳定，非 volatile API）。本 spec 的 `AIMessage.content`（文本为 `str`）、`response_format={"type":"json_object"}` 契约均以锁定版本为准，升级依赖须重跑本 spec 测试
- **类型收窄（质量门驱动）**：`from_config` 里 `api_key` 用 `SecretStr(api_key)` 包装——langchain-openai 的 `api_key` 别名类型是 `SecretStr | Callable | None`，plain `str` 不满足 pyright strict，`SecretStr` 顺带让密钥不进 repr/日志
- **配置边界**：`provider`、`model`、endpoint、超时、重试、温度和密钥环境变量名的字段、默认值、类型校验由 `02-config` 定义；本 spec 只定义这些值如何构造客户端以及如何影响调用。

## 测试要点

- [ ] 单元测试 `tests/test_llm/`：
  - [ ] `_to_lc` 纯函数：`system`→`SystemMessage`、`user`→`HumanMessage`、`assistant`→`AIMessage`，`content` 透传；非法 role → `ValueError`
  - [ ] `complete`（注入 fake `BaseChatModel`：`ainvoke` 返回预设 `AIMessage`，记录消息与 kwargs）：
    - [ ] `module`/`type`/`correlation_id`/`content` 正确回填进 `LLMOutput`
    - [ ] `model` 回填：`LlmClient(fake, model_name="test-model")` → `LLMOutput.model == "test-model"`
    - [ ] `json_mode=True` → 传给模型的 kwargs 含 `response_format={"type": "json_object"}`；`False` → 不含
    - [ ] `tools` 非空 → 传给模型的 kwargs 含 `tools`；空/None → 不含
    - [ ] fake 返回带 `tool_calls` 的 `AIMessage`（pydantic ToolCall 有 `model_dump`）→ `LLMOutput.tool_calls` 正确解析出 name/args；无 `tool_calls` → `[]`
    - [ ] `messages` 顺序与内容按原序透传（fake 记录收到的 LangChain 消息）
    - [ ] `prompt_messages` 保留 Unicode/换行且与输入解除可变别名，`LLMOutput.__repr__` 不暴露 prompt
    - [ ] 非文本 content（fake 返回 `content=list`）→ `RuntimeError`（不是 `str(list)` 的 repr 垃圾）
  - [ ] `resolve_base_url` 纯函数：显式 `base_url` 优先 / 已知 provider 命中 / 未知 provider 返回 `None`
  - [ ] `from_config`（`monkeypatch` 环境变量）：`provider="claude"`（无 base_url）→ `ConfigError`；`api_key_env` 未设（`delenv`）→ `ConfigError`；正常 → 返回 `LlmClient` 且 `_model_name == config.model`（`setenv` 设 key）；`provider="openai"` → 正常返回；自定义 `base_url` → 正常返回；`temperature` 透传（monkeypatch `ChatOpenAI` 捕获 kwargs，断言 `temperature` 值）
- [ ] 集成测试：无（`LlmClient` 是内部类，无 Facade 管道；不测真实 LLM）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] 后续 Facade 调 LLM 都经 `LlmClient`（无绕过）；`model` 随每次调用记录
