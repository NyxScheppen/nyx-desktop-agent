import os
from typing import Any, Literal, TypedDict, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from nyx.config import ConfigError, LlmConfig
from nyx.types import LLMOutput


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


# 内置 provider → OpenAI 兼容 base_url；不在表内者配 llm.base_url 覆盖
_PROVIDER_BASE_URLS = {
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
    "ollama": "http://localhost:11434/v1",
}


def resolve_base_url(provider: str, base_url: str | None) -> str | None:
    """provider → base_url：显式 base_url 优先，否则查内置映射；无命中 None。纯函数。"""
    if base_url:
        return base_url
    return _PROVIDER_BASE_URLS.get(provider)


class LlmClient:
    """全项目唯一 LLM 出口。持有 LangChain model 与 model 名，负责调用。"""

    def __init__(self, model: BaseChatModel, model_name: str) -> None:
        self._model = model
        self._model_name = model_name
        # 显式传，不依赖 LangChain 的 model_name 属性（fake 未必有）

    @classmethod
    def from_config(cls, config: LlmConfig) -> "LlmClient":
        base_url = resolve_base_url(config.provider, config.base_url)
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
                timeout=config.timeout,
                max_retries=config.max_retries,
                temperature=config.temperature,
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
            kwargs["tools"] = tools  # OpenAI 兼容 function calling 工具定义
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
            module=module,
            type=output_type,
            model=self._model_name,
            content=content,
            correlation_id=correlation_id,
            tool_calls=tool_calls,
        )
