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


class LlmClient:
    """全项目唯一 LLM 出口。持有 LangChain model 与 model 名，负责调用与 token 抽取。"""

    def __init__(self, model: BaseChatModel, model_name: str) -> None:
        self._model = model
        self._model_name = model_name
        # 显式传，不依赖 LangChain 的 model_name 属性（fake 未必有）

    @classmethod
    def from_config(cls, config: LlmConfig) -> "LlmClient":
        if config.provider != "deepseek":
            raise ConfigError(
                f"暂不支持 provider={config.provider!r}，当前只支持 deepseek"
            )
        api_key = os.environ.get(config.api_key_env)
        if not api_key:
            raise ConfigError(f"环境变量 {config.api_key_env} 未设置")
        return cls(
            ChatOpenAI(
                model=config.model,
                api_key=SecretStr(api_key),
                base_url="https://api.deepseek.com",
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
        if module == "expression":
            # 调试：终端打印每次组装好的完整 prompt（system + user 全文）
            print(f"\n{'=' * 72}", flush=True)
            print(f"[prompt] {output_type} corr={correlation_id}", flush=True)
            for m in messages:
                print(f"\n--- {m['role']} ---", flush=True)
                print(m["content"], flush=True)
            print(f"{'=' * 72}\n", flush=True)
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
