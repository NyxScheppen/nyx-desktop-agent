# pyright: reportPrivateUsage=false
import asyncio
from typing import Any, cast

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatResult

from nyx.config import ConfigError, VisionConfig
from nyx.llm.vision import VisionClient


class FakeVisionModel(BaseChatModel):
    """记录调用、返回预设响应的 fake model，测试不触网。"""

    @property
    def _llm_type(self) -> str:
        return "fake"

    def __init__(self, response: AIMessage) -> None:
        super().__init__()
        self._response = response
        self._recorded_messages: list[BaseMessage] = []

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[])

    async def ainvoke(
        self,
        input: Any,
        config: Any = None,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> AIMessage:
        self._recorded_messages = input
        return self._response


def _client(response: AIMessage) -> tuple[VisionClient, FakeVisionModel]:
    fake = FakeVisionModel(response)
    return VisionClient(fake, "test-vision"), fake


def test_describe_returns_text() -> None:
    client, fake = _client(AIMessage(content="用户在写代码"))
    out = asyncio.run(client.describe(b"\x89PNG"))
    assert out == "用户在写代码"
    assert len(fake._recorded_messages) == 1
    msg = fake._recorded_messages[0]
    assert isinstance(msg, HumanMessage)
    content = msg.content
    assert isinstance(content, list)
    first = cast(dict[str, Any], content[0])
    second = cast(dict[str, Any], content[1])
    assert first["type"] == "text"
    assert second["type"] == "image_url"


def test_describe_non_text_raises() -> None:
    response = AIMessage(content="x")
    setattr(response, "content", ["not", "text"])
    client, _ = _client(response)
    with pytest.raises(RuntimeError):
        asyncio.run(client.describe(b"x"))


def test_from_config_unknown_provider_rejects() -> None:
    with pytest.raises(ConfigError):
        VisionClient.from_config(VisionConfig(provider="claude"))


def test_from_config_ok() -> None:
    client = VisionClient.from_config(VisionConfig())
    assert client._model_name == "llava"
