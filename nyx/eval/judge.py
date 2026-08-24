"""LLM-judge：语义质量 1-5 分，抽样触发（design §9.1 第三层 + §9.2 第 3 档）。"""
import json
import logging
import math
from typing import Any, cast

from nyx.llm.client import LlmClient
from nyx.types import LLMOutput

_JUDGE_SYSTEM = (
    "你是尼克斯（Nyx）的人格评审。给下面这段 Nyx 的输出打分："
    "语义质量 + 与语境的相关性，按 JSON 输出 {score}，"
    "score 为 1-5 的整数（5=优秀，1=差）。"
)

_logger = logging.getLogger(__name__)


def should_judge(output_type: str, sample_rate: float, roll: float) -> bool:
    """是否触发 LLM-judge（纯函数）：judge 输出不递归 judge + 抽样命中。

    tool 输出（use_tools 的工具决策）无文本可评，跳过 judge（避免空 content 打分）。
    """
    if output_type in ("judge", "tool"):
        return False
    return roll < sample_rate


async def judge_relevance(
    llm: LlmClient, output: LLMOutput
) -> tuple[float, LLMOutput | None]:
    """调 LLM 打分，返回 (score, judge 调用的 LLMOutput 供记账)。

    judge 环节失败不应崩整个 evaluate（eval 是纯记录性质）：
    - 传输失败（超时/5xx）→ 容错 0.0、无 judge_output（None，不记账）
    - JSON 解析失败 / 非 dict / score 非数字（含布尔）→ 容错 0.0，但仍返回
      judge_output（token 照记，原则 2）
    score 合法时 clamp 到 [1,5]（design §9.1 的 1-5 分），未评 = 0.0。
    """
    try:
        judge_output = await llm.complete(
            [
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": output.content},
            ],
            module="eval",
            output_type="judge",
            correlation_id=output.correlation_id,
            json_mode=True,
        )
    except Exception:
        _logger.exception(
            "judge LLM 调用失败 correlation_id=%s", output.correlation_id
        )
        return 0.0, None
    try:
        data = json.loads(judge_output.content)
        if isinstance(data, dict):
            raw = cast(dict[str, Any], data).get("score")
        else:
            raw = None
        # clamp [1,5]；未评 / 布尔 = 0.0
        if raw is None or isinstance(raw, bool):
            score = 0.0
        else:
            value = float(raw)
            if not math.isfinite(value):
                score = 0.0   # NaN/Infinity 不计分（float 对它们不抛异常，clamp 会漏）
            else:
                score = max(1.0, min(5.0, value))
    except (TypeError, ValueError, OverflowError):
        # JSONDecodeError 是 ValueError 子类；float(超大 int) 溢出，一并覆盖
        score = 0.0
    return score, judge_output
