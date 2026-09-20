import base64
import os
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from nyx.config import ConfigError, VisionConfig
from nyx.llm.client import resolve_base_url

_DESCRIBE_PROMPT = (
    "你是屏幕观察助手。用一句简短的中文描述截图里用户正在做什么"
    "（应用/网页/内容主题），不超过 30 字；"
    "无法判断就只回「无法判断」这四个字，不要解释。"
)


class VisionClient:
    """屏幕视觉调用：OpenAI 兼容多模态（Ollama 视觉模型同协议），描述截图内容。

    与 LlmClient 分开：视觉用独立 model，且消息带 image_url 多模态块，
    不混入纯文本 complete 路径。
    """

    def __init__(self, model: BaseChatModel, model_name: str) -> None:
        self._model = model
        self._model_name = model_name

    @classmethod
    def from_config(cls, config: VisionConfig) -> "VisionClient":
        base_url = resolve_base_url(config.provider, config.base_url)
        if base_url is None:
            raise ConfigError(
                f"未知 provider={config.provider!r}：请设置 vision.base_url"
            )
        api_key = os.environ.get(config.api_key_env)
        if not api_key:
            if config.provider != "ollama":
                raise ConfigError(f"环境变量 {config.api_key_env} 未设置")
            api_key = "ollama"  # Ollama 免 key，ChatOpenAI 要求非空，占位（服务端忽略）
        return cls(
            ChatOpenAI(
                model=config.model,
                api_key=SecretStr(api_key),
                base_url=base_url,
            ),
            model_name=config.model,
        )

    async def describe(self, image_bytes: bytes) -> str:
        """截图 → 一句话中文描述。非文本响应 raise（对齐 LlmClient.complete）。"""
        b64 = base64.b64encode(image_bytes).decode("ascii")
        content: list[str | dict[Any, Any]] = [
            {"type": "text", "text": _DESCRIBE_PROMPT},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            },
        ]
        response = await self._model.ainvoke([HumanMessage(content=content)])
        if not isinstance(response.content, str):
            raise RuntimeError(
                f"期望文本 content，得到 {type(response.content).__name__}"
            )
        return response.content
