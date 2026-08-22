# LLM 统一客户端

> 范围：`nyx/llm/client.py`（`LlmClient` + `LlmMessage`），LangChain 统一调用、默认 Deepseek、token 抽取、模型名随调用记录、可注入 mock。全项目唯一 LLM 出口。
> 纯客户端 spec：只做「调 LLM → 返回 `LLMOutput`」，不含 Facade、不含 DDL、不含 API。
> `LlmClient` / `LlmMessage` 定义内联在本文件；`LLMOutput` / `TokenUsageDict` / `LlmConfig` / `ConfigError` 取自 01-types / 02-config（见前置依赖）。

## 元信息

- **前置依赖**：01-types（`LLMOutput`、`TokenUsageDict`）、02-config（`LlmConfig`、`ConfigError`）

## 用户故事

> 作为 Nyx 系统的开发者，我想要全项目唯一的 LLM 调用出口——统一走 LangChain、默认 Deepseek、每次调用自动抽取 token 用量和模型名、测试可注入 mock，以便任何子系统调 LLM 都走同一条路、token 与模型可查可追溯。

## 验收标准

- [ ] `client.py` 含 `LlmClient` + `LlmMessage`，与「`nyx/llm/client.py`（完整）」段代码逐字一致
- [ ] 全项目只有这一处直接调 LLM（不直接用 httpx、不绕过 client 直接 `ChatOpenAI`）
- [ ] `complete()` 返回 `LLMOutput`：`id` 每次调用唯一（uuid4）、`token_usage` 从 `usage_metadata` 抽取（缺失计 0）、`model` 随每次调用回填
- [ ] `json_mode=True` 时向模型传 `response_format={"type": "json_object"}`
- [ ] `LlmClient(model=..., model_name=...)` 可注入 fake model，测试不触网
- [ ] `pyright` strict 零报错
- [ ] api key 不进代码：`from_config` 用 `os.environ.get(api_key_env)` 读，未设报 `ConfigError`

## 技术方案

- **新文件**：`nyx/llm/client.py`（无 Facade、无 API、无数据变更）
- **库**：`langchain_core`（`BaseChatModel` / 消息类）、`langchain_openai`（`ChatOpenAI`，deepseek / openai / ollama 等走 OpenAI 兼容接口）
- **公开面**：`from nyx.llm.client import LlmClient, LlmMessage`（不加 `__all__`）
- **内部类（非 Facade）**：Facade / LangGraph 节点都通过它调 LLM，是透明化+可追溯的落点
- **不落库**：`TokenUsage` 完整行的落库由 15-eval 的 `evaluate()` 统一做（每个 `LLMOutput` 必经 eval，不会漏记）；本 spec 只抽取并返回 `token_usage` + `model` + `id`（uuid4，供 `EvalReport.output_id`）
- **多 provider（OpenAI 兼容）**：`from_config` 用 `_resolve_base_url(provider, base_url)` 解析 endpoint——显式 `llm.base_url` 优先，否则查内置映射（deepseek / openai / ollama）；无命中报 `ConfigError`（列出内置 provider + 提示配 `llm.base_url`）。统一走 `ChatOpenAI`，token 抽取不变
- **不设超时/重试**：LangChain 默认，异常原样上抛由调用方处理
- **json_mode = 减少 parse 失败重试**：欲望生成/分类器/judge 要结构化输出，靠 `response_format` 保证合法 JSON，少一次重调
- **依赖 pin（实现时锁）**：`pyproject.toml` 里 `langchain-core`、`langchain-openai` 锁精确版本（非 `>=` 宽范围）；`pydantic` 用 `>=2.0` floor（`SecretStr` 自 v1 稳定，非 volatile API）。本 spec 的 `usage_metadata`（键 `input_tokens`/`output_tokens`）、`AIMessage.content`（文本为 `str`）、`response_format={"type":"json_object"}` 契约均以锁定版本为准，升级依赖须重跑本 spec 测试
- **类型收窄（质量门驱动）**：`_extract_usage` 里 `isinstance(usage, dict)` 把 `getattr` 返回的 `Any` 收窄成 `dict[Unknown, Unknown]`，赋给 `dict[str, Any]` 报 partially unknown，故 `cast(dict[str, Any], usage)`（与 02-config `_build` 同模式）；`from_config` 里 `api_key` 用 `SecretStr(api_key)` 包装——langchain-openai 的 `api_key` 别名类型是 `SecretStr | Callable | None`，plain `str` 不满足 pyright strict，`SecretStr` 顺带让密钥不进 repr/日志

### `nyx/llm/client.py`（完整）

```python
import os
import uuid
from typing import Any, Literal, TypedDict, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from nyx.config import ConfigError, LlmConfig
from nyx.types import LLMOutput, TokenUsageDict


# role 与 01-types 的 Message(user/nyx) 是两码事
class LlmMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


def _to_lc(m: LlmMessage) -> BaseMessage:
    """LlmMessage → LangChain 消息；纯函数，可单测。"""
    role = m["role"]
    if role == "system":
        return SystemMessage(content=m["content"])
    if role == "user":
        return HumanMessage(content=m["content"])
    if role == "assistant":
        return AIMessage(content=m["content"])
    raise ValueError(f"未知消息角色 {role!r}")   # 静态 Literal 已挡，此为运行期防御


def _safe_int(value: Any) -> int:
    """防御性转 int：非法值（None/非数字字符串/对象）计 0，不抛。纯函数。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _extract_usage(response: BaseMessage) -> TokenUsageDict:
    """从 LangChain 响应抽取 token 用量；兼容 dict 与 Pydantic 两种形状。"""
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        data: dict[str, Any] = {}
    elif hasattr(usage, "model_dump"):    # Pydantic v2（langchain-core 用 v2）
        data = usage.model_dump()
    elif isinstance(usage, dict):
        data = cast(dict[str, Any], usage)
    else:
        data = {}                          # 未知形状：不静默猜，计 0 待查
    return {
        "input": _safe_int(data.get("input_tokens") or 0),   # 键缺失/值 None/非法 计 0
        "output": _safe_int(data.get("output_tokens") or 0),
    }


# 内置 provider → OpenAI 兼容 base_url；不在表内者配 llm.base_url 覆盖
_PROVIDER_BASE_URLS = {
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
    "ollama": "http://localhost:11434/v1",
}


def _resolve_base_url(provider: str, base_url: str | None) -> str | None:
    """provider → base_url：显式 base_url 优先，否则查内置映射；无命中 None。纯函数。"""
    if base_url:
        return base_url
    return _PROVIDER_BASE_URLS.get(provider)


class LlmClient:
    """全项目唯一 LLM 出口。持有 LangChain model 与 model 名，负责调用与 token 抽取。"""

    def __init__(self, model: BaseChatModel, model_name: str) -> None:
        self._model = model
        self._model_name = model_name
        # 显式传，不依赖 LangChain 的 model_name 属性（fake 未必有）

    @classmethod
    def from_config(cls, config: LlmConfig) -> "LlmClient":
        base_url = _resolve_base_url(config.provider, config.base_url)
        if base_url is None:
            raise ConfigError(
                f"未知 provider={config.provider!r}：请设置 llm.base_url，"
                f"或使用内置 provider：{sorted(_PROVIDER_BASE_URLS)}"
            )
        api_key = os.environ.get(config.api_key_env)
        if not api_key:
            raise ConfigError(f"环境变量 {config.api_key_env} 未设置")
        return cls(
            ChatOpenAI(
                model=config.model,
                api_key=SecretStr(api_key),
                base_url=base_url,
            ),
            model_name=config.model,
        )

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMOutput:
        kwargs: dict[str, Any] = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if tools:
            kwargs["tools"] = tools  # bind_tools：function calling 工具定义
        response = await self._model.ainvoke([_to_lc(m) for m in messages], **kwargs)
        content = response.content
        if not isinstance(content, str):
            raise RuntimeError(f"期望文本 content，得到 {type(content).__name__}")
        tool_calls: list[dict[str, Any]] = []
        raw_calls = getattr(response, "tool_calls", None)
        if raw_calls:
            for tc in raw_calls:
                # LangChain 返回 pydantic ToolCall（有 model_dump）；兼容裸 dict。
                if hasattr(tc, "model_dump"):
                    tool_calls.append(cast(dict[str, Any], tc.model_dump()))
                else:
                    tool_calls.append(cast(dict[str, Any], tc))
        return LLMOutput(
            id=str(uuid.uuid4()),    # 每次调用唯一，供 EvalReport.output_id
            module=module,
            type=output_type,        # 15-eval 里对应 TokenUsage.purpose
            model=self._model_name,  # 模型名随每次调用记录，供 TokenUsage.model
            content=content,
            token_usage=_extract_usage(response),
            correlation_id=correlation_id,
            tool_calls=tool_calls,
        )
```

## 测试要点

- [ ] 单元测试 `tests/test_llm/`：
  - [ ] `_to_lc` 纯函数：`system`→`SystemMessage`、`user`→`HumanMessage`、`assistant`→`AIMessage`，`content` 透传；非法 role → `ValueError`
  - [ ] `_extract_usage` 纯函数：dict 形状 `{input_tokens: 12, output_tokens: 7}` → `{input: 12, output: 7}`；Pydantic v2 形状（`model_dump()` 返回同键 dict）→ 同上；`usage_metadata` 为 `None` → `{input: 0, output: 0}`；键存在但值为 `None` → `{input: 0, output: 0}`；键值为非数字字符串（如 `input_tokens="abc"`）→ `{input: 0, ...}` 不抛；未知形状（无 `model_dump` 非 dict）→ `{input: 0, output: 0}`
  - [ ] `complete`（注入 fake `BaseChatModel`：`ainvoke` 返回预设 `AIMessage`，记录消息与 kwargs）：
    - [ ] `id`（非空 uuid）/`module`/`type`/`correlation_id`/`content` 正确回填进 `LLMOutput`
    - [ ] `model` 回填：`LlmClient(fake, model_name="test-model")` → `LLMOutput.model == "test-model"`
    - [ ] `token_usage` 抽取：`usage_metadata={input_tokens: 12, output_tokens: 7}` → `{input: 12, output: 7}`
    - [ ] `usage_metadata` 缺失 → `{input: 0, output: 0}`
    - [ ] `json_mode=True` → 传给模型的 kwargs 含 `response_format={"type": "json_object"}`；`False` → 不含
    - [ ] `tools` 非空 → 传给模型的 kwargs 含 `tools`；空/None → 不含
    - [ ] fake 返回带 `tool_calls` 的 `AIMessage`（pydantic ToolCall 有 `model_dump`）→ `LLMOutput.tool_calls` 正确解析出 name/args；无 `tool_calls` → `[]`
    - [ ] `messages` 顺序与内容按原序透传（fake 记录收到的 LangChain 消息）
    - [ ] 非文本 content（fake 返回 `content=list`）→ `RuntimeError`（不是 `str(list)` 的 repr 垃圾）
  - [ ] `_resolve_base_url` 纯函数：显式 `base_url` 优先 / 已知 provider 命中 / 未知 provider 返回 `None`
  - [ ] `from_config`（`monkeypatch` 环境变量）：`provider="claude"`（无 base_url）→ `ConfigError`；`api_key_env` 未设（`delenv`）→ `ConfigError`；正常 → 返回 `LlmClient` 且 `_model_name == config.model`（`setenv` 设 key）；`provider="openai"` → 正常返回；自定义 `base_url` → 正常返回
- [ ] 集成测试：无（`LlmClient` 是内部类，无 Facade 管道；不测真实 LLM）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] 后续 Facade 调 LLM 都经 `LlmClient`（无绕过）；`token_usage` + `model` 产出为 15-eval 落库提供完整数据源，落库后 `/api/tokens` 可查每一次调用的用量与模型
