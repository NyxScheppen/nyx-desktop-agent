"""eval Evaluator：OOC 告警 + 记账。基础设施（非 Facade）。

对表达计算 OOC 关键词信号、命中时 `logger.warning`，并把每次评估
写进 `eval_log`（含 token 消耗），供前端查询。落库失败 best-effort 不重抛。
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from nyx.behavior.context import behavior_context
from nyx.eval.rules import ooc_score
from nyx.eval.store import EvalStore
from nyx.types import EvalRecord, LLMOutput

_logger = logging.getLogger(__name__)


class Evaluator:
    """对角色表达做 OOC 告警 + 写 `eval_log` 记账。

    关键词只提供待行为反思核实的信号，不用固定角色语料判定所有角色。
    store 由组合根注入
    （None = 测试/无库时关闭记账）。落库失败只记日志、不重抛（best-effort 旁路）。
    """

    def __init__(
        self,
        store: EvalStore | None = None,
        on_failure: Callable[[str, str, str, str], Awaitable[None]] | None = None,
    ) -> None:
        self._store = store  # None = 不落库（测试/无库）
        self._on_failure = on_failure

    async def evaluate(self, output: LLMOutput) -> None:
        """算 OOC、告警，并写一条 `eval_log`（best-effort）。"""
        voice = output.module == "expression" and output.type in {
            "speak",
            "think",
            "initiate_chat",
        }
        keyword = ooc_score(output.content) if voice else 1.0
        if keyword < 1.0:
            _logger.warning(
                "OOC 关键词命中 module=%s type=%s score=%.2f",
                output.module,
                output.type,
                keyword,
            )
        embed_s: float | None = None
        if self._store is not None:
            await self._record(self._store, output, keyword, embed_s)
        if self._on_failure is not None and keyword < 1.0:
            context = behavior_context.get()
            token = behavior_context.set(
                {
                    **(context or {}),
                    "output_module": output.module,
                    "output_type": output.type,
                    "assistant_output_raw": output.content,
                    "call_id": output.call_id,
                    "scores": {"keyword": keyword, "embed": embed_s},
                }
            )
            try:
                await self._on_failure(
                    "ooc",
                    f"ooc:{output.module}",
                    f"表达偏离人设\n调用：{output.call_id} · {output.type}\n"
                    f"评分：keyword={keyword}, embed={embed_s}\n"
                    f"具体原文：\n{output.content}",
                    output.correlation_id,
                )
            except Exception:
                _logger.exception("OOC 失败信号记录失败 module=%s", output.module)
            finally:
                behavior_context.reset(token)

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
                ),
                output.prompt_messages,
            )
        except Exception:
            _logger.exception(
                "eval 落库失败 module=%s type=%s",
                output.module,
                output.type,
            )
