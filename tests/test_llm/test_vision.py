# pyright: reportPrivateUsage=false
import asyncio
from typing import Any, cast

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatResult

from nyx.config import ConfigError, VisionConfig
from nyx.enums import GamePhase, GameProfile, VisionResultStatus
from nyx.llm.vision import VisionClient
from nyx.types import GameVisionRequest


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


def test_from_config_requires_key_for_non_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 非 Ollama 后端缺 key → ConfigError（不再硬编码 "ollama" 静默 401）
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ConfigError):
        VisionClient.from_config(VisionConfig(provider="openai"))


def test_from_config_reads_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    client = VisionClient.from_config(VisionConfig(provider="openai"))
    assert client._model_name == "llava"


def test_observe_parses_structured_result() -> None:
    response = AIMessage(content='{"phase":"dialogue","speaker":"Kim",'
        '"speaker_evidence_ids":["ocr:1"],"dialogue_evidence_ids":["ocr:1"],'
        '"choice_evidence_ids":[],"entity_evidence_ids":[],"scene_evidence_ids":[],'
        '"visible_entities":[],"scene_summary":null,"uncertainties":[]}')
    fake = FakeVisionModel(response)
    client = VisionClient(fake, "test-vision", VisionConfig(enabled=True, timeout=1.0))
    result = asyncio.run(client.observe(
        GameVisionRequest("s", GameProfile.DISCO_ELYSIUM, 1, True, [], [], None)
    ))
    assert result.status is VisionResultStatus.OK
    assert result.phase is GamePhase.DIALOGUE
    assert result.speaker_evidence_ids == ["ocr:1"]


def test_observe_disabled_does_not_call_model() -> None:
    client, fake = _client(AIMessage(content="{}"))
    result = asyncio.run(client.observe(
        GameVisionRequest("s", GameProfile.GENERIC_TEXT, 1, False, [], [], None)
    ))
    assert result.status is VisionResultStatus.DISABLED
    assert fake._recorded_messages == []


def test_observe_malformed_is_rejected_without_retry() -> None:
    client, _ = _client(AIMessage(content="not json"))
    result = asyncio.run(client.observe(
        GameVisionRequest("s", GameProfile.GENERIC_TEXT, 1, True, [], [], None)
    ))
    assert result.status is VisionResultStatus.MALFORMED
