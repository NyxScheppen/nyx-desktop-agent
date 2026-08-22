# 测试需访问 _to_lc/_extract_usage 与 _model_name（spec 测试要点要求）
# pyright: reportPrivateUsage=false
import asyncio
from typing import Any, cast

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatResult

from nyx.config import ConfigError, LlmConfig
from nyx.llm.client import (
    LlmClient,
    LlmMessage,
    _extract_usage,
    _to_lc,
    resolve_base_url,
)
from nyx.types import LLMOutput


class FakeChatModel(BaseChatModel):
    """记录调用、返回预设响应的 fake model，测试不触网。"""

    @property
    def _llm_type(self) -> str:
        return "fake"

    def __init__(self, response: AIMessage) -> None:
        super().__init__()
        self._response = response
        self._recorded_messages: list[BaseMessage] = []
        self._recorded_kwargs: dict[str, Any] = {}

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
        self._recorded_kwargs = kwargs
        return self._response


def _client(
    response: AIMessage, model_name: str = "test-model"
) -> tuple[LlmClient, FakeChatModel]:
    fake = FakeChatModel(response)
    return LlmClient(fake, model_name), fake


def _complete(client: LlmClient, **kwargs: Any) -> LLMOutput:
    return asyncio.run(
        client.complete(
            [{"role": "user", "content": "q"}],
            module="test",
            output_type="reply",
            correlation_id="corr-1",
            **kwargs,
        )
    )


# ---- _to_lc ----


def test_to_lc_system() -> None:
    msg = _to_lc({"role": "system", "content": "hi"})
    assert isinstance(msg, SystemMessage)
    assert msg.content == "hi"


def test_to_lc_user() -> None:
    assert isinstance(_to_lc({"role": "user", "content": "x"}), HumanMessage)


def test_to_lc_assistant() -> None:
    assert isinstance(_to_lc({"role": "assistant", "content": "x"}), AIMessage)


def test_to_lc_invalid_role() -> None:
    with pytest.raises(ValueError):
        _to_lc(cast(LlmMessage, {"role": "invalid", "content": "x"}))


# ---- _extract_usage ----


def test_extract_usage_dict() -> None:
    response = AIMessage(content="x")
    setattr(response, "usage_metadata", {"input_tokens": 12, "output_tokens": 7})
    assert _extract_usage(response) == {"input": 12, "output": 7}


def test_extract_usage_pydantic() -> None:
    class _Usage:
        def model_dump(self) -> dict[str, int]:
            return {"input_tokens": 12, "output_tokens": 7}

    response = AIMessage(content="x")
    setattr(response, "usage_metadata", _Usage())
    assert _extract_usage(response) == {"input": 12, "output": 7}


def test_extract_usage_missing() -> None:
    assert _extract_usage(AIMessage(content="x")) == {"input": 0, "output": 0}


def test_extract_usage_none_value() -> None:
    # 键存在但值为 None：宽松兜底计 0，不 int(None) 裸崩
    response = AIMessage(content="x")
    setattr(response, "usage_metadata", {"input_tokens": None, "output_tokens": None})
    assert _extract_usage(response) == {"input": 0, "output": 0}


def test_extract_usage_unknown_shape() -> None:
    response = AIMessage(content="x")
    setattr(response, "usage_metadata", 42)
    assert _extract_usage(response) == {"input": 0, "output": 0}


def test_extract_usage_non_int_value() -> None:
    # 键值为非数字字符串：_safe_int 兜底计 0，不 int("abc") 裸崩
    response = AIMessage(content="x")
    setattr(response, "usage_metadata", {"input_tokens": "abc", "output_tokens": 7})
    assert _extract_usage(response) == {"input": 0, "output": 7}


# ---- complete ----


def test_complete_fields() -> None:
    client, _ = _client(AIMessage(content="hi"))
    out = _complete(client)
    assert out.id
    assert out.module == "test"
    assert out.type == "reply"
    assert out.correlation_id == "corr-1"
    assert out.content == "hi"
    assert out.model == "test-model"


def test_complete_token_usage() -> None:
    response = AIMessage(content="hi")
    setattr(response, "usage_metadata", {"input_tokens": 12, "output_tokens": 7})
    client, _ = _client(response)
    assert _complete(client).token_usage == {"input": 12, "output": 7}


def test_complete_token_usage_missing() -> None:
    client, _ = _client(AIMessage(content="hi"))
    assert _complete(client).token_usage == {"input": 0, "output": 0}


def test_complete_json_mode_on() -> None:
    client, fake = _client(AIMessage(content="hi"))
    _complete(client, json_mode=True)
    assert fake._recorded_kwargs["response_format"] == {"type": "json_object"}


def test_complete_json_mode_off() -> None:
    client, fake = _client(AIMessage(content="hi"))
    _complete(client)
    assert "response_format" not in fake._recorded_kwargs


def test_complete_tools_passthrough() -> None:
    client, fake = _client(AIMessage(content="hi"))
    tools = [{"type": "function", "function": {"name": "local_search"}}]
    _complete(client, tools=tools)
    assert fake._recorded_kwargs["tools"] == tools


def test_complete_tools_off() -> None:
    client, fake = _client(AIMessage(content="hi"))
    _complete(client)
    assert "tools" not in fake._recorded_kwargs


def test_complete_tool_calls_parsed() -> None:
    response = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "local_search",
                "args": {"q": "骑士"},
                "id": "call-1",
                "type": "tool_call",
            }
        ],
    )
    client, _ = _client(response)
    out = _complete(client)
    assert out.content == ""
    assert len(out.tool_calls) == 1
    assert out.tool_calls[0]["name"] == "local_search"
    assert out.tool_calls[0]["args"] == {"q": "骑士"}


def test_complete_no_tools_empty() -> None:
    client, _ = _client(AIMessage(content="hi"))
    assert _complete(client).tool_calls == []


def test_complete_messages_passthrough() -> None:
    client, fake = _client(AIMessage(content="hi"))
    messages: list[LlmMessage] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
    ]
    asyncio.run(
        client.complete(messages, module="t", output_type="o", correlation_id="c")
    )
    assert len(fake._recorded_messages) == 2
    assert isinstance(fake._recorded_messages[0], SystemMessage)
    assert isinstance(fake._recorded_messages[1], HumanMessage)
    assert fake._recorded_messages[0].content == "sys"
    assert fake._recorded_messages[1].content == "q"


def test_complete_non_text_content() -> None:
    response = AIMessage(content="x")
    setattr(response, "content", ["not", "text"])
    client, _ = _client(response)
    with pytest.raises(RuntimeError):
        _complete(client)


# ---- from_config ----


def test_resolve_base_url() -> None:
    # 显式覆盖优先 / 已知 provider 命中 / 未知 provider 返回 None
    assert resolve_base_url("deepseek", "http://localhost:1/v1") == "http://localhost:1/v1"
    assert resolve_base_url("openai", None) == "https://api.openai.com/v1"
    assert resolve_base_url("claude", None) is None


def test_from_config_unknown_provider_rejects() -> None:
    with pytest.raises(ConfigError):
        LlmClient.from_config(LlmConfig(provider="claude"))


def test_from_config_rejects_missing_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ConfigError):
        LlmClient.from_config(LlmConfig())


def test_from_config_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    client = LlmClient.from_config(LlmConfig())
    assert client._model_name == "deepseek-chat"


def test_from_config_known_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    client = LlmClient.from_config(
        LlmConfig(provider="openai", model="gpt-4o-mini", api_key_env="OPENAI_API_KEY")
    )
    assert client._model_name == "gpt-4o-mini"


def test_from_config_base_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    client = LlmClient.from_config(
        LlmConfig(base_url="http://localhost:11434/v1")
    )
    assert client._model_name == "deepseek-chat"
