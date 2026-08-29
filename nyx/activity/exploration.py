# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
"""线性自由探索：联网搜索 → 抓正文 → 一次 LLM 总结 → 落记忆/欲望。

砍掉逐层地牢游戏层（FloorNode/决策支/托管/下楼），只保留「搜 → 读 → 总结」这条线。
"""
import json
import logging
from typing import Any, cast
from urllib.parse import urlparse

from nyx.config import ExplorationConfig
from nyx.eval.evaluator import Evaluator
from nyx.events.event import SECONDS_PER_HOUR
from nyx.llm.client import LlmClient
from nyx.tools.registry import ToolRegistry

_logger = logging.getLogger(__name__)

_FETCH_COUNT = 3  # 抓正文的检索结果条数（decision，可推翻）


def should_explore(last_explored_at: float, rate_limit_hours: int, now: float) -> bool:
    """自由探索升级门槛（纯函数）：仅频率上限。

    「探索欲」条件由调用方结构保证：READING 活动仅由 DesireType.EXPLORATION 映射而来，
    故调用方在 activity.type is READING 时才调本函数。
    精力不再单独卡：探索消耗 -30，精力不足由 build_schedule 的 REST 穿插兜底。
    """
    return now - last_explored_at >= rate_limit_hours * SECONDS_PER_HOUR


class Exploration:
    """自由探索：线性「搜 → 抓正文 → 总结」，无决策点、无中途广播。"""

    def __init__(
        self,
        llm: LlmClient,
        evaluator: Evaluator,
        tools: ToolRegistry,
        exploration_config: ExplorationConfig,
    ) -> None:
        self._llm = llm
        self._evaluator = evaluator
        self._tools = tools
        self._web_enabled = exploration_config.web_enabled

    async def run(
        self,
        topic: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        """跑完一次探索：搜 → 抓前 _FETCH_COUNT 条正文 → 一次 LLM 总结。

        返回 {type, outcome, summary, core_discovery, knowledge,
        new_topics, strong_new_topics, findings}。
        """
        results = await self._search(topic)
        findings: list[str] = []
        for result in results[:_FETCH_COUNT]:
            name, url, snippet = _result_parts(result)
            if not name:
                continue
            content = snippet
            if url:
                try:
                    content = str(await self._tools.call("web_fetch", {"url": url}))
                except Exception:
                    pass  # best-effort：抓正文失败不崩 run，snippet 兜底
            findings.append(f"{name}：{content}")
        judged = await self._summarize(topic, findings, correlation_id)
        core_discovery = str(judged.get("core_discovery") or "")
        return {
            "type": "free_exploration",
            "outcome": "won" if core_discovery else "exhausted",
            "summary": str(judged.get("summary") or topic),
            "core_discovery": core_discovery,
            "knowledge": (
                judged["knowledge"]
                if isinstance(judged.get("knowledge"), list)
                else []
            ),
            "new_topics": (
                judged["casual_new_topics"]
                if isinstance(judged.get("casual_new_topics"), list)
                else []
            ),
            "strong_new_topics": (
                judged["strong_new_topics"]
                if isinstance(judged.get("strong_new_topics"), list)
                else []
            ),
            "findings": findings,
        }

    async def _search(self, topic: str) -> list[Any]:
        """联网搜索 → 本地搜索兜底；禁用联网时直落本地。"""
        if self._web_enabled:
            res = await self._tools.call("web_search", {"query": topic})
            if not res:
                res = await self._tools.call("local_search", {"query": topic})
        else:
            res = await self._tools.call("local_search", {"query": topic})
        if not isinstance(res, list):
            return []
        return cast(list[Any], res)

    async def _summarize(
        self, topic: str, findings: list[str], correlation_id: str
    ) -> dict[str, Any]:
        """一次 LLM 总结产出 summary/core_discovery/knowledge/new_topics。

        best-effort：LLM/parse 失败记日志、按空结果走兜底，不重抛。
        """
        try:
            output = await self._llm.complete(
                [
                    {"role": "system", "content": _EXPLORATION_FINALIZE_SYSTEM},
                    {"role": "user", "content": json.dumps({
                        "seed_topic": topic,
                        "findings": findings,
                    }, ensure_ascii=False)},
                ],
                module="activity",
                output_type="exploration_finalize",
                correlation_id=correlation_id,
                json_mode=True,
            )
            await self._evaluator.evaluate(output)
            data = json.loads(output.content)
            if not isinstance(data, dict):
                return {}
            return cast(dict[str, Any], data)
        except Exception:
            _logger.exception("探索总结失败 topic=%s", topic)
            return {}


def _result_parts(result: Any) -> tuple[str, str, str]:
    """一条检索结果 → (name, url, snippet)；无法解析返回 ('', '', '')。"""
    if isinstance(result, dict):
        title = cast(str | None, result.get("title"))
        name_key = cast(str | None, result.get("name"))
        url = str(cast(str | None, result.get("url")) or "")
        snippet = str(
            cast(str | None, result.get("snippet"))
            or cast(str | None, result.get("content"))
            or ""
        )
        name = title or name_key or (_domain(url) if url else "")
        return name, url, snippet
    if isinstance(result, str):
        return result, "", ""
    return "", "", ""


def _domain(url: str) -> str:
    """结果名兜底：title 缺失时用域名。"""
    host = urlparse(url).hostname
    return host or url


_EXPLORATION_FINALIZE_SYSTEM = (
    "你是尼克斯的探索结算器。基于这场探索的种子话题与发现，判断是否挖到了核心发现。"
    "按 JSON 输出：summary（一句话总结）、"
    "core_discovery（若真相已明则非空字符串，否则空串）、"
    "knowledge（数组，每项 {topic, content}，客观知识点）、"
    "strong_new_topics（数组，值得长期追的强烈新兴趣）、"
    "casual_new_topics（数组，一般好奇，不值得立长期欲望）。"
)
