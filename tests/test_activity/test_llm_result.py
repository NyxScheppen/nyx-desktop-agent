import json

import pytest

from nyx.activity.llm_result import parse_activity_result


def test_parse_activity_result_valid() -> None:
    """解析活动 LLM 结果：reading/creation 必需键齐全时返回原对象。"""
    assert parse_activity_result(
        json.dumps({"book": "b", "note": "n"}), "reading"
    ) == {"book": "b", "note": "n"}
    assert parse_activity_result(
        json.dumps({"title": "t", "content": "c"}), "creation"
    ) == {"title": "t", "content": "c"}


def test_parse_activity_result_missing_key_raises() -> None:
    with pytest.raises(ValueError):
        parse_activity_result(json.dumps({"book": "b"}), "reading")


def test_parse_activity_result_non_dict_raises() -> None:
    with pytest.raises(ValueError):
        parse_activity_result("[1, 2, 3]", "reading")


@pytest.mark.parametrize(
    ("output_type", "payload"),
    [
        ("reading", {"book": "", "note": "n"}),
        ("reading", {"book": "b", "note": 3}),
        ("creation", {"title": None, "content": "正文"}),
        ("creation", {"title": "标题", "content": "   "}),
    ],
)
def test_parse_activity_result_requires_non_empty_strings(
    output_type: str, payload: dict[str, object]
) -> None:
    with pytest.raises(ValueError):
        parse_activity_result(json.dumps(payload), output_type)


def test_parse_activity_result_unknown_type_raises() -> None:
    with pytest.raises(ValueError):
        parse_activity_result('{"title": "t", "content": "c"}', "unknown")
