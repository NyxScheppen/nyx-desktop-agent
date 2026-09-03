# pyright: reportPrivateUsage=false
import pytest

from nyx.expression.pipeline import (
    _is_question,
    _parse_reply,
    _rounds_block,
    _voice_output,
)
from nyx.types import LLMOutput


def test_is_question() -> None:
    assert _is_question("你今天好吗？") is True
    assert _is_question("你今天怎么样") is True
    assert _is_question("我很好。") is False


def test_rounds_block_empty() -> None:
    assert _rounds_block([], []) == ""


def test_rounds_block_single() -> None:
    out = _rounds_block(["t1"], ["s1"])
    assert "第1轮内心：t1" in out
    assert "第1轮对外：s1" in out


def test_rounds_block_two() -> None:
    out = _rounds_block(["t1", "t2"], ["s1", "s2"])
    assert out.index("第1轮内心：t1") < out.index("第1轮对外：s1")
    assert out.index("第1轮对外：s1") < out.index("第2轮内心：t2")
    assert out.index("第2轮内心：t2") < out.index("第2轮对外：s2")


def test_parse_reply_valid() -> None:
    assert _parse_reply('{"think": " 想法 ", "speak": " 你好 "}') == ("想法", "你好")


def test_parse_reply_missing_think_defaults_empty() -> None:
    assert _parse_reply('{"speak": "你好"}') == ("", "你好")


def test_parse_reply_non_string_think_defaults_empty() -> None:
    assert _parse_reply('{"think": 123, "speak": "你好"}') == ("", "你好")


def test_parse_reply_missing_speak_raises() -> None:
    with pytest.raises(ValueError):
        _parse_reply('{"think": "想法"}')


def test_parse_reply_empty_speak_raises() -> None:
    with pytest.raises(ValueError):
        _parse_reply('{"speak": "   "}')


def test_parse_reply_invalid_json_raises() -> None:
    with pytest.raises(ValueError):
        _parse_reply("not json")


def test_parse_reply_non_dict_raises() -> None:
    with pytest.raises(ValueError):
        _parse_reply('["a"]')


def test_voice_output_preserves_call_fields() -> None:
    src = LLMOutput(
        module="m", type="reply", model="x", content="full",
        correlation_id="c", prompt_tokens=9, completion_tokens=4, call_id="call-9",
    )
    out = _voice_output(src, "speak", "你好")
    assert out.type == "speak"
    assert out.content == "你好"
    assert out.tool_calls == []
    # think/speak 同源一次 complete()：token 与 call_id 沿袭（15-eval 去重锚点）
    assert out.prompt_tokens == 9
    assert out.completion_tokens == 4
    assert out.call_id == "call-9"
