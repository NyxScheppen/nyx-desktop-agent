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
