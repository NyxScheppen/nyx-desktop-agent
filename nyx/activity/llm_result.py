import json
from typing import Any, cast

from nyx.policy import policy


def parse_activity_result(raw: str, output_type: str) -> dict[str, Any]:
    """解析 LLM 活动结果并做最小结构校验。

    reading 需 {book, note}；creation 需 {title, content}。
    缺键或顶层非对象时 fail-fast，避免活动完成后沉淀错误结构。
    """
    data: Any = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(
            policy(
                "activity", "llm_result.templates.parse_activity_result_text_2", str
            ).format(p0=type(data).__name__)
        )
    parsed = cast(dict[str, Any], data)
    if output_type == "reading":
        required = ("book", "note")
    elif output_type == "creation":
        required = ("title", "content")
    else:
        raise ValueError(
            policy(
                "activity", "llm_result.templates.parse_activity_result_text_4", str
            ).format(p0=output_type)
        )
    if not all(k in parsed for k in required):
        raise ValueError(
            policy(
                "activity", "llm_result.templates.parse_activity_result_text_3", str
            ).format(p0=required)
        )
    for key in required:
        value = parsed[key]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                policy(
                    "activity", "llm_result.templates.parse_activity_result_text", str
                ).format(p0=key)
            )
    return parsed
