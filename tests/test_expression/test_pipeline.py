# pyright: reportPrivateUsage=false
import pytest

from nyx.expression.pipeline import (
    _is_question,
    _parse_plan,
    _parse_reply,
    _rounds_block,
    _voice_output,
)
from nyx.types import LLMOutput


def test_parse_plan_preserves_steps_and_optional_tool() -> None:
    plan = _parse_plan(
        '{"goal":"查资料","steps":[{"text":"搜索","expected_tool":"search",'
        '"done_condition":"获得来源"},{"text":"对比","done_condition":"结论明确"}]}'
    )
    assert plan is not None and plan.goal == "查资料"
    assert [(s.text, s.expected_tool, s.done_condition) for s in plan.steps] == [
        ("搜索", "search", "获得来源"), ("对比", None, "结论明确"),
    ]


@pytest.mark.parametrize("raw", [
    "not json", "[]", '{"goal":"空","steps":[]}',
    '{"goal":"坏步骤","steps":[null]}',
    '{"goal":"缺字段","steps":[{"text":"搜索"}]}',
    '{"goal":"坏类型","steps":[{"text":3,"done_condition":"完成"}]}',
])
def test_parse_plan_invalid_returns_none(raw: str) -> None:
    assert _parse_plan(raw) is None


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
    assert _parse_reply(
        '{"think": " 想法 ", "speak": " 你好 "}'
    ) == ("想法", "你好", False)


def test_parse_reply_reads_continuation_flag() -> None:
    assert _parse_reply(
        '{"think": "想法", "speak": "你好", "needs_continuation": true}'
    ) == ("想法", "你好", True)


@pytest.mark.parametrize("flag", ['"true"', "1", "null", "{}"])
def test_parse_reply_invalid_continuation_flag_finishes(flag: str) -> None:
    assert _parse_reply(
        '{"speak": "你好", "needs_continuation": ' + flag + "}"
    ) == ("", "你好", False)


def test_parse_reply_missing_think_defaults_empty() -> None:
    assert _parse_reply('{"speak": "你好"}') == ("", "你好", False)


def test_parse_reply_non_string_think_defaults_empty() -> None:
    assert _parse_reply('{"think": 123, "speak": "你好"}') == ("", "你好", False)


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
        prompt_messages=[{"role": "user", "content": "你好"}],
    )
    out = _voice_output(src, "speak", "你好")
    assert out.type == "speak"
    assert out.content == "你好"
    assert out.tool_calls == []
    # think/speak 同源一次 complete()：token 与 call_id 沿袭（token 去重锚点）
    assert out.prompt_tokens == 9
    assert out.completion_tokens == 4
    assert out.call_id == "call-9"
    assert out.prompt_messages == [{"role": "user", "content": "你好"}]
@pytest.mark.parametrize(
    ("messages", "expected"),
    [
        (
            ["周末去哪玩？", "算了一下，预算三百"],
            (0, "周末去哪玩？\n\n算了一下，预算三百"),
        ),
        (["周六去杭州，预算三百", "改成周日"], (0, "周六去杭州，预算三百\n\n改成周日")),
        (["旧任务", "取消整个任务，只陪我聊聊"], (1, "取消整个任务，只陪我聊聊")),
        (["旧任务", "不要取消整个任务"], (0, "旧任务\n\n不要取消整个任务")),
        (["旧任务", "“取消整个任务”，是引用"], (0, "旧任务\n\n“取消整个任务”，是引用")),
        (
            ["旧任务", "取消刚才的任务里的杭州行程"],
            (0, "旧任务\n\n取消刚才的任务里的杭州行程"),
        ),
    ],
)
def test_derive_request_preserves_local_corrections(
    messages: list[str],
    expected: tuple[int, str],
) -> None:
    from nyx.expression.classifier import derive_request_text

    assert derive_request_text(messages) == expected


def test_reply_query_keeps_latest_correction_and_both_ends() -> None:
    from nyx.expression.prompt import build_reply_query

    assert (
        build_reply_query(["杭州，预算三百", "改成周日"], [])
        == "改成周日\n杭州，预算三百"
    )
    query = build_reply_query(["不要" + "甲" * 400 + "预算二百"], [])
    assert len(query) == 300 and query.startswith("不要") and query.endswith("预算二百")
    assert build_reply_query(["你好"], ["旧任务"]) == ""
