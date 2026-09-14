# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
"""线性自由探索：联网搜索 → 抓正文 → 一次 LLM 总结 → 落记忆/欲望。

砍掉逐层地牢游戏层（FloorNode/决策支/托管/下楼），只保留「搜 → 读 → 总结」这条线。
"""
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from nyx.activity.store import ActivityStore
from nyx.config import ExplorationConfig
from nyx.desire.facade import DesireFacade
from nyx.enums import DesireType
from nyx.eval.evaluator import Evaluator
from nyx.events.event import SECONDS_PER_HOUR
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.tools.registry import ToolRegistry
from nyx.types import Activity, LongTermDesire

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
        store: ActivityStore,
        desire: DesireFacade,
        memory: MemoryFacade,
        exploration_config: ExplorationConfig,
    ) -> None:
        self._llm = llm
        self._evaluator = evaluator
        self._tools = tools
        self._store = store
        self._desire = desire
        self._memory = memory
        self._web_enabled = exploration_config.web_enabled

    async def run(self, activity: Activity) -> dict[str, Any]:
        """跑完一次探索状态机，并把 checkpoint 写入 activity.progress。

        返回 {type, outcome, summary, core_discovery, knowledge,
        new_topics, strong_new_topics, findings, tools}；tools 记录本次
        探索实际调用的工具（name/args/ok），供前端展示「做了什么」。
        """
        checkpoint = _exploration_checkpoint(activity)
        topic = str(checkpoint["topic"])
        correlation_id = _correlation_id(activity)
        if checkpoint["state"] == "completed":
            return _result_from_checkpoint(checkpoint)
        if checkpoint["state"] == "searching":
            tool_calls = cast(list[dict[str, Any]], checkpoint["tool_calls"])
            checkpoint["raw_results"] = await self._search(topic, tool_calls)
            checkpoint["state"] = "reading_results"
            await self._save_checkpoint(activity, checkpoint)
        if checkpoint["state"] == "reading_results":
            await self._read_results(activity, checkpoint)
            checkpoint["state"] = "summarizing"
            await self._save_checkpoint(activity, checkpoint)
        if checkpoint["state"] == "summarizing":
            if not bool(checkpoint["summary_done"]):
                findings = cast(list[str], checkpoint["findings"])
                checkpoint["judged"] = await self._summarize(
                    topic, findings, correlation_id
                )
                checkpoint["summary_done"] = True
            checkpoint["state"] = "sinking"
            await self._save_checkpoint(activity, checkpoint)
        if checkpoint["state"] == "sinking":
            judged = _checkpoint_judged(checkpoint)
            if not bool(checkpoint["sink_done"]):
                await self._sink(judged, correlation_id)
                checkpoint["sink_done"] = True
            checkpoint["state"] = "completed"
            await self._save_checkpoint(activity, checkpoint)
        return _result_from_checkpoint(checkpoint)

    async def _read_results(
        self, activity: Activity, checkpoint: dict[str, Any]
    ) -> None:
        raw_results = cast(list[Any], checkpoint["raw_results"])
        findings = cast(list[str], checkpoint["findings"])
        tool_calls = cast(list[dict[str, Any]], checkpoint["tool_calls"])
        cursor = int(checkpoint["cursor"])
        while cursor < min(len(raw_results), _FETCH_COUNT):
            result = raw_results[cursor]
            name, url, snippet = _result_parts(result)
            if name:
                content = snippet
                if url:
                    try:
                        fetched = await self._call_tool(
                            "web_fetch", {"url": url}, tool_calls
                        )
                        if isinstance(fetched, dict):
                            text = cast(dict[str, Any], fetched).get("text")
                            if isinstance(text, str) and text.strip():
                                content = text
                    except Exception:
                        pass  # best-effort：抓正文失败不崩 run，snippet 兜底
                findings.append(f"{name}：{content}")
            cursor += 1
            checkpoint["cursor"] = cursor
            await self._save_checkpoint(activity, checkpoint)
        if not findings:
            _logger.info(
                "自由探索无 findings topic=%s（联网/本地均空）",
                checkpoint["topic"],
            )

    async def _call_tool(
        self, name: str, args: dict[str, Any], tool_calls: list[dict[str, Any]]
    ) -> Any:
        """调用工具并就地记录 {name, args, ok}；失败记 ok=False 后上抛。"""
        try:
            result = await self._tools.call(name, args)
            tool_calls.append({"name": name, "args": args, "ok": True})
            return result
        except Exception:
            tool_calls.append({"name": name, "args": args, "ok": False})
            raise

    async def _search(
        self, topic: str, tool_calls: list[dict[str, Any]]
    ) -> list[Any]:
        """联网搜索 → 本地搜索兜底；禁用联网时直落本地。"""
        if self._web_enabled:
            res = await self._call_tool("web_search", {"query": topic}, tool_calls)
            if not res:
                res = await self._call_tool(
                    "local_search", {"query": topic}, tool_calls
                )
        else:
            res = await self._call_tool("local_search", {"query": topic}, tool_calls)
        if not isinstance(res, list):
            return []
        return cast(list[Any], res)

    async def _summarize(
        self, topic: str, findings: list[str], correlation_id: str
    ) -> dict[str, Any]:
        """一次 LLM 总结产出 summary/core_discovery/knowledge/new_topics。

        best-effort：LLM/parse 失败记日志、按空结果走兜底，不重抛。
        """
        related: list[str] = []
        try:
            memories = await self._memory.search(topic)
            related = [m.summary for m in memories[:5]]
        except Exception:
            _logger.exception("探索相关记忆检索失败 topic=%s", topic)
        try:
            output = await self._llm.complete(
                [
                    {"role": "system", "content": _EXPLORATION_FINALIZE_SYSTEM},
                    {"role": "user", "content": json.dumps({
                        "seed_topic": topic,
                        "findings": findings,
                        "related_memories": related,
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

    async def _sink(self, judged: dict[str, Any], correlation_id: str) -> None:
        """探索终局回写：强烈新兴趣进长期欲望，知识进长期记忆。"""
        strong = judged.get("strong_new_topics")
        if isinstance(strong, list):
            for topic in cast(list[Any], strong):
                if not isinstance(topic, str) or not topic.strip():
                    continue
                await self._desire.add_long_term(
                    LongTermDesire(
                        id=str(uuid.uuid4()),
                        created_at=time.time(),
                        type=DesireType.EXPLORATION,
                        name=topic,
                        description=f"想弄懂「{topic}」",
                        strength=0.5,
                        progress=0.0,
                        subtopics=[],
                    )
                )
        knowledge = judged.get("knowledge")
        if isinstance(knowledge, list):
            items: list[dict[str, str]] = []
            for k in cast(list[Any], knowledge):
                if not isinstance(k, dict):
                    continue
                item_map = cast(dict[str, Any], k)
                if not str(item_map.get("content", "")).strip():
                    continue
                items.append(
                    {
                        "topic": str(item_map.get("topic", "")),
                        "content": str(item_map.get("content", "")),
                    }
                )
            if items:
                await self._memory.remember_knowledge(items, correlation_id)

    async def _save_checkpoint(
        self, activity: Activity, checkpoint: dict[str, Any]
    ) -> None:
        activity.progress["exploration"] = checkpoint
        await self._store.update(activity)


def _result_parts(result: Any) -> tuple[str, str, str]:
    """一条检索结果 → (name, url, snippet)；无法解析返回 ('', '', '')。

    兼容两种来源：web 结果 {title/name, url, snippet/content}、
    本地结果 {path, snippet}（local_search 只有 path+snippet，name 取文件名）。
    """
    if isinstance(result, dict):
        title = cast(str | None, result.get("title"))
        name_key = cast(str | None, result.get("name"))
        path_key = cast(str | None, result.get("path"))
        url = str(cast(str | None, result.get("url")) or "")
        snippet = str(
            cast(str | None, result.get("snippet"))
            or cast(str | None, result.get("content"))
            or ""
        )
        name = (
            title
            or name_key
            or (_basename(path_key) if path_key else "")
            or (_domain(url) if url else "")
        )
        return name, url, snippet
    if isinstance(result, str):
        return result, "", ""
    return "", "", ""


def _basename(path: str) -> str:
    """本地结果名兜底：取文件 basename（local_search 返回 {path, snippet}）。"""
    return Path(path).name


def _domain(url: str) -> str:
    """结果名兜底：title 缺失时用域名。"""
    host = urlparse(url).hostname
    return host or url


def _exploration_checkpoint(activity: Activity) -> dict[str, Any]:
    raw = activity.progress.get("exploration")
    checkpoint = cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
    topic = checkpoint.get("topic")
    judged = checkpoint.get("judged")
    return {
        "state": _state_or_default(checkpoint.get("state")),
        "topic": str(topic) if isinstance(topic, str) and topic else _topic(activity),
        "raw_results": _list_or_empty(checkpoint.get("raw_results")),
        "cursor": int(checkpoint.get("cursor", 0)),
        "findings": _str_list(checkpoint.get("findings")),
        "tool_calls": _dict_list(checkpoint.get("tool_calls")),
        "summary_done": bool(checkpoint.get("summary_done")),
        "judged": judged if isinstance(judged, dict) else None,
        "sink_done": bool(checkpoint.get("sink_done")),
    }


def _state_or_default(value: Any) -> str:
    if value in {"searching", "reading_results", "summarizing", "sinking", "completed"}:
        return str(value)
    return "searching"


def _topic(activity: Activity) -> str:
    goal = activity.progress.get("goal")
    if isinstance(goal, dict):
        topic = cast(dict[str, Any], goal).get("topic")
        if isinstance(topic, str) and topic:
            return topic
    desc = activity.progress.get("description")
    if isinstance(desc, str) and desc:
        return desc
    return activity.id


def _correlation_id(activity: Activity) -> str:
    value = activity.progress.get("correlation_id")
    if isinstance(value, str) and value:
        return value
    return activity.id


def _list_or_empty(value: Any) -> list[Any]:
    if isinstance(value, list):
        return cast(list[Any], value)
    return []


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items = cast(list[Any], value)
    return [item for item in items if isinstance(item, str)]


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items = cast(list[Any], value)
    return [cast(dict[str, Any], item) for item in items if isinstance(item, dict)]


def _checkpoint_judged(checkpoint: dict[str, Any]) -> dict[str, Any]:
    judged = checkpoint.get("judged")
    if isinstance(judged, dict):
        return cast(dict[str, Any], judged)
    return {}


def _result_from_checkpoint(checkpoint: dict[str, Any]) -> dict[str, Any]:
    judged = _checkpoint_judged(checkpoint)
    topic = str(checkpoint["topic"])
    findings = cast(list[str], checkpoint["findings"])
    tool_calls = cast(list[dict[str, Any]], checkpoint["tool_calls"])
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
        "tools": tool_calls,
    }


_EXPLORATION_FINALIZE_SYSTEM = (
    "你是尼克斯，一个住在用户电脑里的 AI 同伴，"
    "温柔克制、思虑很深，想真正弄懂一件事的来龙去脉。"
    "你刚针对一个种子话题做了一场探索：搜索资料、读了几条发现（标题+正文），"
    "现在要结算这场探索，判断有没有挖到核心发现、哪些新话题值得继续追。"
    "给你的「相关记忆」是你之前对类似话题已经知道/想过的东西，"
    "用来判断这次的发现是不是新的、要不要和旧认知衔接。"
    "只输出 JSON，键：\n"
    "- summary：一句话总结这场探索得到了什么（非空字符串）。\n"
    "- core_discovery：若真相已明，填一句非空的核心发现；否则填空串。\n"
    "- knowledge：客观知识点数组，每项 {topic, content}，"
    "topic 是主题/概念名、content 是一句完整自洽的知识陈述。\n"
    "- strong_new_topics：字符串数组，抽象归并后的长期源话题（如"
    "「我想要了解人的痛苦」），把细碎发现归并到宽泛源话题下、少而精，"
    "不输出细碎子话题。\n"
    "- casual_new_topics：字符串数组，一般好奇，不值得立长期欲望。"
)
