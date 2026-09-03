"""eval Evaluator：OOC 告警 + 记账（15-eval）。基础设施（非 Facade）。

算 OOC（关键词 + embedding 两档）、命中时 `logger.warning`，并把每次评估
写进 `eval_log`（含 token 消耗），供前端查询。落库失败 best-effort 不重抛。
"""
import logging
import time
import uuid

from nyx.eval.ooc_embed import build_baseline, is_voice_type, ooc_embed_score
from nyx.eval.rules import ooc_score
from nyx.eval.store import EvalStore
from nyx.memory.retrieval import EmbedFn
from nyx.types import EvalRecord, LLMOutput

_logger = logging.getLogger(__name__)


class Evaluator:
    """对所有 LLM 产出做 OOC 告警 + 写 `eval_log` 记账。

    embed 实例由组合根注入（与记忆检索共享同一份）；store 同样注入
    （None = 测试/无库时关闭记账）。落库失败只记日志、不重抛（best-effort 旁路）。
    """

    # embedding 告警阈值：低于此值视为疑似 OOC（与 ooc_embed 阈值同源）。
    _EMBED_WARN_THRESHOLD = 0.7

    def __init__(
        self,
        embed: EmbedFn | None = None,
        store: EvalStore | None = None,
    ) -> None:
        self._embed = embed          # None = embedding 档关闭（仅关键词）
        self._baseline: list[list[float]] | None = None   # 语料向量惰性缓存
        self._store = store          # None = 不落库（测试/无库）

    async def evaluate(self, output: LLMOutput) -> None:
        """算 OOC、告警，并写一条 `eval_log`（best-effort）。"""
        keyword = ooc_score(output.content)
        if keyword < 1.0:
            _logger.warning(
                "OOC 关键词命中 module=%s type=%s score=%.2f",
                output.module, output.type, keyword,
            )
        embed_s: float | None = None
        if self._embed is not None and is_voice_type(output.type):
            try:
                if self._baseline is None:
                    self._baseline = await build_baseline(self._embed)
                embed_s = await ooc_embed_score(
                    self._embed, output.content, self._baseline,
                )
            except Exception:
                _logger.exception("embedding OOC 失败 module=%s", output.module)
            if embed_s is not None and embed_s < self._EMBED_WARN_THRESHOLD:
                _logger.warning(
                    "OOC embedding 低分 module=%s type=%s score=%.2f",
                    output.module, output.type, embed_s,
                )
        if self._store is not None:
            await self._record(self._store, output, keyword, embed_s)

    async def _record(
        self,
        store: EvalStore,
        output: LLMOutput,
        keyword: float,
        embed_s: float | None,
    ) -> None:
        """写一条 eval_log；失败只记日志不重抛（best-effort 旁路）。"""
        try:
            await store.insert(
                EvalRecord(
                    id=str(uuid.uuid4()),
                    created_at=time.time(),
                    call_id=output.call_id,
                    module=output.module,
                    output_type=output.type,
                    model=output.model,
                    correlation_id=output.correlation_id,
                    ooc_keyword=keyword,
                    ooc_embed=embed_s,
                    prompt_tokens=output.prompt_tokens,
                    completion_tokens=output.completion_tokens,
                )
            )
        except Exception:
            _logger.exception(
                "eval 落库失败 module=%s type=%s",
                output.module, output.type,
            )
