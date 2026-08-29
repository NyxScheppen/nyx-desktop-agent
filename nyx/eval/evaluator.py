"""eval Evaluator：OOC 轻量告警。基础设施（非 Facade），只算 OOC、
命中黑名单或 embedding 低于阈值时 `logger.warning`，不落库不落分。
"""
import logging

from nyx.eval.ooc_embed import build_baseline, is_voice_type, ooc_embed_score
from nyx.eval.rules import ooc_score
from nyx.memory.retrieval import EmbedFn
from nyx.types import LLMOutput

_logger = logging.getLogger(__name__)


class Evaluator:
    """对所有 LLM 产出做 OOC 轻量告警（保留关键词 + embedding 纯函数）。

    不再落库、不再计 token、不再返回报告——只在对白类输出偏离人设时
    `logger.warning`。embed 实例由组合根注入（与记忆检索共享同一份）。
    """

    # embedding 告警阈值：低于此值视为疑似 OOC（与 ooc_embed 阈值同源）。
    _EMBED_WARN_THRESHOLD = 0.7

    def __init__(self, embed: EmbedFn | None = None) -> None:
        self._embed = embed          # None = embedding 档关闭（仅关键词）
        self._baseline: list[list[float]] | None = None   # 语料向量惰性缓存

    async def evaluate(self, output: LLMOutput) -> None:
        """算 OOC，疑似偏离人设时告警。不落库、不落分、不返回报告。"""
        keyword = ooc_score(output.content)
        if keyword < 1.0:
            _logger.warning(
                "OOC 关键词命中 module=%s type=%s score=%.2f",
                output.module, output.type, keyword,
            )
        if self._embed is None or not is_voice_type(output.type):
            return
        try:
            if self._baseline is None:
                self._baseline = await build_baseline(self._embed)
            embed_s = await ooc_embed_score(self._embed, output.content, self._baseline)
        except Exception:
            _logger.exception("embedding OOC 失败 module=%s", output.module)
            return
        if embed_s < self._EMBED_WARN_THRESHOLD:
            _logger.warning(
                "OOC embedding 低分 module=%s type=%s score=%.2f",
                output.module, output.type, embed_s,
            )
