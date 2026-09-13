import json
from typing import Any, cast


def parse_activity_result(raw: str, output_type: str) -> dict[str, Any]:
    """解析 LLM 活动结果并做最小结构校验。

    reading 需 {book, note}；creation 需 {title, content}。
    缺键或顶层非对象时 fail-fast，避免活动完成后沉淀错误结构。
    """
    data: Any = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"活动结果 JSON 应是对象，得到 {type(data).__name__}")
    parsed = cast(dict[str, Any], data)
    required = ("book", "note") if output_type == "reading" else ("title", "content")
    if not all(k in parsed for k in required):
        raise ValueError(f"活动结果 JSON 缺键：{required}")
    return parsed
