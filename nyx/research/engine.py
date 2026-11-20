# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
"""可恢复、有预算的自由探索：按证据选择改写、搜索、阅读和追问。"""

import asyncio
import json
import logging
import math
import time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol, TypedDict, cast
from urllib.parse import urlparse

from langgraph.errors import NodeCancelledError
from langgraph.graph import END, StateGraph

from nyx.config import ExplorationConfig
from nyx.db import DATABASE_ERRORS
from nyx.enums import MemoryKind
from nyx.eval.evaluator import Evaluator
from nyx.events.event import SECONDS_PER_HOUR
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade, build_source_topic
from nyx.policy import policy
from nyx.tools.registry import ToolRegistry
from nyx.types import Activity

_MAX_STEPS = policy("research", "engine._MAX_STEPS", int)
_MAX_SEARCHES = policy("research", "engine._MAX_SEARCHES", int)
_MAX_SOURCES = policy("research", "engine._MAX_SOURCES", int)
_MAX_SECONDS = policy("research", "engine._MAX_SECONDS", float)
_MAX_TOKENS = policy("research", "engine._MAX_TOKENS", int)
_DECISION_RETRIES = policy("research", "engine._DECISION_RETRIES", int)
_SOURCE_TEXT_MAX_CHARS = policy("research", "engine._SOURCE_TEXT_MAX_CHARS", int)
_LOGGER = logging.getLogger(__name__)


def _excluded_source(result: Any) -> bool:
    """自发探索不把程序自身或创作文件当成外部知识来源。"""
    _name, url, path, _snippet = _result_parts(result)
    if url or not path:
        return False
    resolved = Path(path).resolve()
    return resolved.is_relative_to(Path(__file__).resolve().parents[1]) or (
        "creations" in Path(path).parts or "creations" in resolved.parts
    )


def should_explore(last_explored_at: float, rate_limit_hours: int, now: float) -> bool:
    """自由探索升级门槛（纯函数）：仅频率上限。

    「探索欲」条件由调用方结构保证：READING 活动仅由 DesireType.EXPLORATION 映射而来，
    故调用方在 activity.type is READING 时才调本函数。
    精力不再单独卡：探索消耗 -30，精力不足由 build_schedule 的 REST 穿插兜底。
    """
    return now - last_explored_at >= rate_limit_hours * SECONDS_PER_HOUR


class ResearchState(TypedDict):
    checkpoint: dict[str, Any]


class ResearchWaiting(Exception):
    """A durable user question pauses execution without settling the research."""


class _InvalidResearchOutput(ValueError):
    """Only malformed model output is eligible for regeneration."""


class ResearchNode(Protocol):
    async def __call__(self, state: ResearchState) -> ResearchState: ...


class CheckpointStore(Protocol):
    async def update(self, activity: Activity) -> None: ...


class ResearchEngine:
    """Evidence-driven exploration, with durable decisions and bounded work."""

    def __init__(
        self,
        llm: LlmClient,
        evaluator: Evaluator,
        tools: ToolRegistry,
        store: CheckpointStore,
        memory: MemoryFacade,
        exploration_config: ExplorationConfig,
        canon: str = "",
        *,
        fetch_source: Callable[..., Awaitable[dict[str, Any]]] | None = None,
        notify_progress: Callable[[Activity], Awaitable[None]] | None = None,
    ) -> None:
        self._llm = llm
        self._evaluator = evaluator
        self._tools = tools
        self._store = store
        self._memory = memory
        self._web_enabled = exploration_config.web_enabled
        self._canon = canon
        self._fetch_source = fetch_source
        self._notify_progress = notify_progress

    async def run(self, activity: Activity) -> dict[str, Any]:
        """Resume committed decisions; unreadable sources never become knowledge."""
        checkpoint = _exploration_checkpoint(activity)
        limits = checkpoint.get("limits", {})

        def limit(name: str, maximum: float) -> float:
            value = limits.get(name, maximum)
            return (
                max(0.0, min(float(value), maximum))
                if (isinstance(value, (int, float)) and math.isfinite(value))
                else maximum
            )

        self._max_steps = int(limit("max_steps", _MAX_STEPS))
        self._max_searches = int(limit("max_sub_queries", _MAX_SEARCHES))
        self._max_sources = int(limit("max_sources", _MAX_SOURCES))
        self._max_seconds = limit("seconds", _MAX_SECONDS)
        self._max_tokens = int(limit("tokens", _MAX_TOKENS))
        if checkpoint["state"] == "completed":
            return _result_from_checkpoint(checkpoint)
        if checkpoint["state"] == "failed":
            raise ValueError(checkpoint["failure_reason"])
        checkpoint["_clock"] = time.monotonic()
        try:
            remaining = self._max_seconds - float(checkpoint["elapsed_seconds"])
            if remaining <= 0:
                raise TimeoutError(policy("research", "engine.copy.run_text", str))
            async with asyncio.timeout(remaining):
                return await self._run(activity, checkpoint)
        except asyncio.CancelledError:
            await self._save_checkpoint(activity, checkpoint)
            raise
        except ResearchWaiting:
            await self._save_checkpoint(activity, checkpoint)
            raise
        except DATABASE_ERRORS:
            # A lost write acknowledgement is not a failed research outcome.
            # The service reloads the durable checkpoint and verifies receipts.
            raise
        except Exception as error:
            reason = str(error) or (
                policy("research", "engine.copy.run_text_2", str)
                if isinstance(error, TimeoutError)
                else type(error).__name__
            )
            checkpoint["failure_reason"] = reason[
                : policy(
                    "research",
                    "engine.parameters.run_checkpoint_failure_reason_chars_or_items",
                    int,
                )
            ]
            checkpoint["state"] = "failed"
            if not checkpoint["unresolved_questions"]:
                checkpoint["unresolved_questions"] = [checkpoint["question"]]
            await self._save_checkpoint(activity, checkpoint)
            if isinstance(error, TimeoutError) and not str(error):
                raise TimeoutError(reason) from error
            raise

    async def _run(
        self,
        activity: Activity,
        checkpoint: dict[str, Any],
    ) -> dict[str, Any]:
        """Actual graph nodes; receipts determine the resume node after a restart."""

        async def saved() -> ResearchState:
            await self._save_checkpoint(activity, checkpoint)
            return {"checkpoint": checkpoint}

        async def select_topic(_state: ResearchState) -> ResearchState:
            if not checkpoint["actions"]:
                checkpoint["actions"].append(
                    {
                        "name": "select_topic",
                        "text": checkpoint["initial_question"],
                        "started_at": time.time(),
                        "completed_at": time.time(),
                        "status": "success",
                        "ok": True,
                    }
                )
            return await saved()

        def route(_state: ResearchState) -> str:
            if checkpoint["state"] == "waiting_user":
                return "wait_user"
            if checkpoint["state"] == "sinking":
                return "settle"
            if checkpoint["state"] == "summarizing":
                return "summarize"
            if checkpoint["source_pending"] is not None:
                return "source_check"
            if checkpoint.get("next_action"):
                action = checkpoint["next_action"].get("action")
                return action if action in actions else "invalid_action"
            if (
                checkpoint["steps"] >= self._max_steps
                or checkpoint["tokens"] >= self._max_tokens
            ):
                checkpoint["stop_reason"] = policy(
                    "research", "engine.copy.route_text", str
                )
                checkpoint["unresolved_questions"] = [checkpoint["question"]]
                return "summarize"
            return "decide"

        async def decide(_state: ResearchState) -> ResearchState:
            decision = await self._ask(
                activity,
                checkpoint,
                "decide",
                _DECIDE_SYSTEM,
                {
                    "initial_question": checkpoint["initial_question"],
                    "question": checkpoint["question"],
                    "findings": checkpoint["findings"],
                    "previous_research": checkpoint.get("previous_research", []),
                    "strategy_lessons": checkpoint.get("strategy_lessons", []),
                    "actions": checkpoint["actions"],
                    "results": checkpoint["raw_results"],
                    "processed_sources": checkpoint["processed_sources"],
                    "available_actions": [
                        "rewrite",
                        "follow_up",
                        "knowledge_search",
                        "finish",
                        "ask_user",
                    ]
                    + (
                        ["search"]
                        if checkpoint["searches"] < self._max_searches
                        else []
                    )
                    + (
                        ["read"]
                        if checkpoint["sources"] < self._max_sources
                        and any(
                            (item.get("url") or item.get("path"))
                            not in checkpoint["processed_sources"]
                            for item in checkpoint["raw_results"]
                        )
                        else []
                    ),
                    "unresolved_questions": checkpoint["unresolved_questions"],
                    "remaining": {
                        "steps": self._max_steps - checkpoint["steps"],
                        "searches": self._max_searches - checkpoint["searches"],
                        "sources": self._max_sources - checkpoint["sources"],
                    },
                },
            )
            checkpoint["steps"] += 1
            checkpoint["next_action"] = decision
            return await saved()

        async def act(_state: ResearchState) -> ResearchState:
            decision = checkpoint["next_action"]
            await self._act(activity, checkpoint, decision)
            if checkpoint["source_pending"] is None:
                checkpoint["next_action"] = None
            return await saved()

        async def finish(_state: ResearchState) -> ResearchState:
            decision = checkpoint["next_action"]
            checkpoint["stop_reason"] = str(
                decision.get(
                    "reason", policy("research", "engine.copy.finish_text", str)
                )
            )
            checkpoint["unresolved_questions"] = _str_list(
                decision.get("unresolved_questions")
            )
            checkpoint["next_action"] = None
            checkpoint["actions"].append(
                {
                    "name": "finish",
                    "text": checkpoint["stop_reason"],
                    "status": "success",
                    "ok": True,
                }
            )
            checkpoint["state"] = "summarizing"
            return await saved()

        async def ask_user(_state: ResearchState) -> ResearchState:
            question = checkpoint["next_action"].get("question")
            if not isinstance(question, str) or not question.strip():
                raise ValueError(policy("research", "engine.copy.ask_user_text", str))
            question = question.strip()
            checkpoint["waiting_question"] = question
            checkpoint["state"] = "waiting_user"
            checkpoint["actions"].append(
                {
                    "name": "ask_user",
                    "text": question,
                    "status": "waiting",
                    "ok": False,
                }
            )
            checkpoint["next_action"] = None
            return await saved()

        async def wait_user(_state: ResearchState) -> ResearchState:
            raise ResearchWaiting(checkpoint["waiting_question"])

        async def source_check(_state: ResearchState) -> ResearchState:
            await self._assess_source(activity, checkpoint)
            return await saved()

        def after_check(_state: ResearchState) -> str:
            pending = checkpoint["source_pending"]
            assessment = pending["assessment"]
            return (
                "evidence"
                if (
                    assessment.get("relevant") is True
                    and assessment.get("usable") is True
                )
                else "reject"
            )

        async def evidence(_state: ResearchState) -> ResearchState:
            pending = checkpoint["source_pending"]
            if "source_chunk_id" not in pending:
                pending["source_chunk_id"] = await self._memory.record_reading_evidence(
                    str(pending["source_topic"]),
                    str(pending["source_name"]),
                    str(pending["source_text"]),
                    str(pending["location"]),
                    location=str(pending["location"]),
                )
            return await saved()

        async def digest(_state: ResearchState) -> ResearchState:
            pending = checkpoint["source_pending"]
            if "knowledge" not in pending:
                _profile, knowledge = await self._memory.digest_source_block(
                    str(pending["source_text"]),
                    str(pending["source_name"]),
                    _correlation_id(activity),
                    source_chunk_id=pending["source_chunk_id"],
                )
                for item in knowledge:
                    item["source_topic"] = str(pending["source_topic"])
                    item["source_name"] = str(pending["source_name"])
                pending["knowledge"] = knowledge
            return await saved()

        async def commit(_state: ResearchState) -> ResearchState:
            pending = checkpoint["source_pending"]
            knowledge = pending["knowledge"]
            if knowledge and not pending.get("knowledge_done"):
                await self._memory.remember_research_knowledge(
                    f"{activity.id}:{pending['location']}",
                    knowledge,
                    _correlation_id(activity),
                    pending["source_chunk_id"],
                )
                pending["knowledge_done"] = True
            checkpoint["knowledge"].extend(knowledge)
            checkpoint["findings"].append(
                f"{pending['source_name']} [{pending['location']}]："
                f"{pending['source_text']}"
            )
            checkpoint["actions"].append(
                {
                    "name": "digest_source",
                    "text": str(pending["source_name"]),
                    "location": pending["location"],
                    "ok": True,
                    "status": "success",
                    "result": policy(
                        "research", "engine.templates.commit_text", str
                    ).format(p0=len(knowledge)),
                }
            )
            return await release(_state)

        async def release(_state: ResearchState) -> ResearchState:
            checkpoint["processed_sources"].append(
                checkpoint["source_pending"]["location"]
            )
            checkpoint["source_pending"] = None
            checkpoint["next_action"] = None
            return await saved()

        async def summarize(_state: ResearchState) -> ResearchState:
            checkpoint["state"] = "summarizing"
            await saved()
            if not checkpoint["summary_done"]:
                if checkpoint["findings"]:
                    checkpoint["judged"] = await self._summarize(activity, checkpoint)
                else:
                    checkpoint["judged"] = {
                        "summary": policy(
                            "research", "engine.copy.summarize_text_6", str
                        ),
                        "failure_reason": checkpoint["stop_reason"]
                        or policy("research", "engine.copy.summarize_text_7", str),
                        "unresolved_questions": [checkpoint["question"]],
                    }
                checkpoint["summary_done"] = True
                judged = _checkpoint_judged(checkpoint)
                checkpoint["unresolved_questions"] = _str_list(
                    judged.get("unresolved_questions")
                )
                checkpoint["failure_reason"] = str(judged.get("failure_reason") or "")
                checkpoint["actions"].append(
                    {
                        "name": "finalize",
                        "text": str(judged.get("summary", "")),
                        "status": "success",
                        "ok": True,
                    }
                )
            checkpoint["state"] = "sinking"
            await self._save_checkpoint(activity, checkpoint)
            return await saved()

        async def settle(_state: ResearchState) -> ResearchState:
            checkpoint["sink_done"] = True
            checkpoint["state"] = "completed"
            return await saved()

        def observed(
            name: str,
            handler: Callable[[ResearchState], Awaitable[ResearchState]],
        ) -> ResearchNode:
            async def invoke(state: ResearchState) -> ResearchState:
                trace = checkpoint.setdefault("node_trace", [])
                entry = {
                    "node": name,
                    "started_at": time.time(),
                    "status": "running",
                    "attempt": 1 + sum(item["node"] == name for item in trace),
                    "input": {
                        "question": checkpoint["question"],
                        "action": deepcopy(checkpoint.get("next_action")),
                    },
                }
                trace.append(entry)
                await saved()
                _LOGGER.info(
                    policy("research", "engine.copy.invoke_text", str),
                    activity.id,
                    name,
                    entry["attempt"],
                )
                try:
                    result = await handler(state)
                    entry["status"] = "success"
                    return result
                except ResearchWaiting:
                    entry["status"] = "waiting"
                    raise
                except asyncio.CancelledError:
                    entry["status"] = "interrupted"
                    raise
                except Exception as error:
                    entry.update(status="failed", error=str(error))
                    raise
                finally:
                    entry["output"] = {
                        "state": checkpoint["state"],
                        "sources": checkpoint["sources"],
                        "knowledge_count": len(checkpoint["knowledge"]),
                    }
                    entry["completed_at"] = time.time()
                    await saved()
                    _LOGGER.info(
                        policy("research", "engine.copy.invoke_text_2", str),
                        activity.id,
                        name,
                        entry["status"],
                    )

            return invoke

        actions = {
            "rewrite",
            "follow_up",
            "knowledge_search",
            "search",
            "read",
            "finish",
            "ask_user",
        }
        graph = StateGraph(ResearchState)
        graph.add_node("select_topic", observed("select_topic", select_topic))
        graph.add_node("decide", observed("decide", decide))
        for name in actions - {"finish", "ask_user"}:
            graph.add_node(name, observed(name, act))
            graph.add_conditional_edges(name, route)
        graph.add_node("invalid_action", observed("invalid_action", act))
        graph.add_node("finish", observed("finish", finish))
        graph.add_node("ask_user", observed("ask_user", ask_user))
        graph.add_node("wait_user", observed("wait_user", wait_user))
        graph.add_node("source_check", observed("source_check", source_check))
        graph.add_node("evidence", observed("evidence", evidence))
        graph.add_node("digest", observed("digest", digest))
        graph.add_node("commit", observed("commit", commit))
        graph.add_node("reject", observed("reject", release))
        graph.add_node("summarize", observed("summarize", summarize))
        graph.add_node("settle", observed("settle", settle))
        graph.set_entry_point("select_topic")
        for name in (
            "select_topic",
            "decide",
            "invalid_action",
            "ask_user",
            "commit",
            "reject",
        ):
            graph.add_conditional_edges(name, route)
        graph.add_conditional_edges("source_check", after_check)
        graph.add_edge("evidence", "digest")
        graph.add_edge("digest", "commit")
        graph.add_edge("finish", "summarize")
        graph.add_edge("summarize", "settle")
        graph.add_edge("settle", END)
        try:
            await graph.compile().ainvoke(
                {"checkpoint": checkpoint},
                {
                    "recursion_limit": policy(
                        "research", "engine.graph_recursion_limit", int
                    )
                },
            )
        except NodeCancelledError as error:
            raise asyncio.CancelledError from error
        return _result_from_checkpoint(checkpoint)

    async def _act(
        self,
        activity: Activity,
        checkpoint: dict[str, Any],
        decision: dict[str, Any],
    ) -> None:
        action = decision.get("action")
        if action in {"rewrite", "follow_up"}:
            question = str(decision.get("question", "")).strip()[
                : policy(
                    "research", "engine.parameters.act_question_chars_or_items", int
                )
            ]
            if not question:
                raise ValueError(policy("research", "engine.copy.act_text", str))
            checkpoint["question"] = question
            checkpoint["unresolved_questions"] = [question]
            checkpoint["actions"].append(
                {
                    "name": action,
                    "text": question,
                    "status": "success",
                    "ok": True,
                    "reason": str(decision.get("reason", ""))[
                        : policy(
                            "research",
                            "engine.parameters.act_reason_chars_or_items",
                            int,
                        )
                    ],
                }
            )
            return
        if action == "knowledge_search":
            query = str(decision.get("query") or checkpoint["question"])
            result = await self._memory.search_knowledge(query)
            checkpoint["actions"].append(
                {
                    "name": action,
                    "text": query,
                    "status": "success",
                    "ok": True,
                    "result": result,
                }
            )
            if result.get("chunks"):
                checkpoint["findings"].append(json.dumps(result, ensure_ascii=False))
                checkpoint.setdefault("knowledge_sources", []).extend(result["chunks"])
            return
        if action == "search":
            query = str(decision.get("query", "")).strip()[
                : policy("research", "engine.parameters.act_query_chars_or_items", int)
            ]
            if not query:
                raise ValueError(policy("research", "engine.copy.act_text_2", str))
            if query in checkpoint["searched_queries"]:
                self._reject(
                    checkpoint,
                    "search",
                    policy("research", "engine.copy.act_text_3", str),
                )
                return
            if (
                not decision.get("budget_reserved")
                and checkpoint["searches"] >= self._max_searches
            ):
                self._reject(
                    checkpoint,
                    "search",
                    policy("research", "engine.copy.act_text_4", str),
                )
                return
            if not decision.get("budget_reserved"):
                checkpoint["searches"] += 1
                decision["budget_reserved"] = True
                await self._save_checkpoint(activity, checkpoint)
            result = await self._call_tool(
                activity,
                checkpoint,
                "web_search" if self._web_enabled else "local_search",
                {"query": query},
            )
            if not isinstance(result, list):
                raise ValueError(policy("research", "engine.copy.act_text_5", str))
            previous_count = len(checkpoint["raw_results"])
            for item in cast(list[Any], result):
                if not isinstance(item, dict) or _excluded_source(item):
                    continue
                name, url, path, snippet = _result_parts(item)
                if name and (url or path):
                    key = url or path
                    if not any(
                        (hit.get("url") or hit.get("path")) == key
                        for hit in checkpoint["raw_results"]
                    ):
                        checkpoint["raw_results"].append(
                            {
                                "title": name[
                                    : policy(
                                        "research",
                                        "engine.parameters.act_title_chars_or_items",
                                        int,
                                    )
                                ],
                                "url": url,
                                "path": path,
                                "snippet": snippet[
                                    : policy(
                                        "research",
                                        "engine.parameters.act_snippet_chars_or_items",
                                        int,
                                    )
                                ],
                                "query": query,
                            }
                        )
            added = len(checkpoint["raw_results"]) - previous_count
            checkpoint["tool_calls"][-1]["result"] = policy(
                "research", "engine.templates.act_text", str
            ).format(p0=len(cast(list[Any], result)), p1=added)
            checkpoint["searched_queries"].append(query)
            return
        if action == "read":
            index = decision.get("index")
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or not 0 <= index < len(checkpoint["raw_results"])
            ):
                self._reject(
                    checkpoint,
                    "read",
                    policy("research", "engine.copy.act_text_6", str),
                )
                return
            result = checkpoint["raw_results"][index]
            name, url, path, _snippet = _result_parts(result)
            key = url or path
            if key in checkpoint["processed_sources"]:
                self._reject(
                    checkpoint,
                    "read",
                    policy("research", "engine.copy.act_text_7", str),
                )
                return
            if (
                not decision.get("budget_reserved")
                and checkpoint["sources"] >= self._max_sources
            ):
                self._reject(
                    checkpoint,
                    "read",
                    policy("research", "engine.copy.act_text_8", str),
                )
                return
            if not decision.get("budget_reserved"):
                checkpoint["sources"] += 1
                decision["budget_reserved"] = True
                await self._save_checkpoint(activity, checkpoint)
            if url:
                fetched = await self._call_tool(
                    activity,
                    checkpoint,
                    "web_fetch",
                    {"url": url},
                )
                text = fetched["text"]
            else:
                fetched = await self._call_tool(
                    activity,
                    checkpoint,
                    "file_io",
                    {"action": "read", "path": path},
                )
                text = fetched["content"]
            checkpoint["source_pending"] = {
                "source_text": text[:_SOURCE_TEXT_MAX_CHARS],
                "truncated": len(text) > _SOURCE_TEXT_MAX_CHARS,
                "source_name": name,
                "location": key,
                "source_topic": build_source_topic("web" if url else "local", key),
                "question": checkpoint["question"],
            }
            await self._save_checkpoint(activity, checkpoint)
            return
        raise ValueError(
            policy("research", "engine.templates.act_text_2", str).format(p0=action)
        )

    @staticmethod
    def _reject(checkpoint: dict[str, Any], action: str, reason: str) -> None:
        checkpoint["actions"].append(
            {
                "name": action,
                "status": "rejected",
                "ok": False,
                "reason": reason,
            }
        )

    async def _assess_source(
        self,
        activity: Activity,
        checkpoint: dict[str, Any],
    ) -> None:
        pending = cast(dict[str, Any], checkpoint["source_pending"])
        text = str(pending.get("source_text", ""))
        location = str(pending.get("location", ""))
        if not text.strip():
            raise ValueError(policy("research", "engine.copy.assess_source_text", str))
        if "assessment" not in pending:
            pending["assessment"] = await self._ask(
                activity,
                checkpoint,
                "source_check",
                _SOURCE_CHECK_SYSTEM,
                {
                    "initial_question": checkpoint["initial_question"],
                    "question": pending.get("question", checkpoint["question"]),
                    "source_name": pending["source_name"],
                    "url_or_path": location,
                    "text": text,
                },
            )
            await self._save_checkpoint(activity, checkpoint)
        assessment = cast(dict[str, Any], pending["assessment"])
        accepted = (
            assessment.get("relevant") is True and assessment.get("usable") is True
        )
        if not pending.get("assessment_logged"):
            checkpoint["actions"].append(
                {
                    "name": "source_check",
                    "text": str(pending["source_name"]),
                    "location": location,
                    "ok": accepted,
                    "status": "success" if accepted else "rejected",
                    "reason": str(
                        assessment.get(
                            "reason",
                            policy("research", "engine.copy.assess_source_text_2", str),
                        )
                    )[
                        : policy(
                            "research",
                            "engine.parameters.assess_source_reason_chars_or_items",
                            int,
                        )
                    ],
                }
            )
            pending["assessment_logged"] = True
            checkpoint.setdefault("source_receipts", []).append(
                {
                    "source_name": pending["source_name"],
                    "location": location,
                    "source_text_raw": text,
                    "assessment_raw": assessment,
                    "captured_at": time.time(),
                    "truncated": bool(pending.get("truncated")),
                }
            )
            await self._save_checkpoint(activity, checkpoint)

    async def _call_tool(
        self,
        activity: Activity,
        checkpoint: dict[str, Any],
        name: str,
        args: dict[str, Any],
    ) -> Any:
        """Record execution and business failures before propagating them."""
        trace: dict[str, Any] = {
            "name": name,
            "args": args,
            "status": "running",
            "started_at": time.time(),
        }
        checkpoint["tool_calls"].append(trace)
        checkpoint["actions"].append(trace)
        await self._save_checkpoint(activity, checkpoint)
        try:
            result: Any = (
                await self._fetch_source(**args)
                if name == "web_fetch" and self._fetch_source is not None
                else await self._tools.call(name, args)
            )
            result_map = (
                cast(dict[str, Any], result) if isinstance(result, dict) else {}
            )
            if result_map.get("error"):
                raise ValueError(str(result_map["error"]))
            if name in {"web_fetch", "file_io"}:
                key = "text" if name == "web_fetch" else "content"
                text = result_map.get(key)
                if not isinstance(text, str) or not text.strip():
                    raise ValueError(
                        policy("research", "engine.copy.call_tool_text", str)
                    )
                trace["result"] = policy(
                    "research", "engine.templates.call_tool_text", str
                ).format(p0=len(text))
            elif name in {"web_search", "local_search"}:
                if not isinstance(result, list):
                    raise ValueError(policy("research", "engine.copy.act_text_5", str))
                trace["result"] = policy(
                    "research", "engine.templates.call_tool_text_2", str
                ).format(p0=len(cast(list[Any], result)))
            trace.update(ok=True, status="success")
            return cast(Any, result)
        except asyncio.CancelledError:
            trace.update(
                ok=False,
                status="interrupted",
                reason=policy("research", "engine.copy.call_tool_text_2", str),
            )
            raise
        except Exception as error:
            trace.update(
                ok=False,
                status="failed",
                reason=str(error)[
                    : policy(
                        "research",
                        "engine.parameters.call_tool_reason_chars_or_items",
                        int,
                    )
                ],
            )
            raise
        finally:
            trace["completed_at"] = time.time()
            await self._save_checkpoint(activity, checkpoint)

    async def _ask(
        self,
        activity: Activity,
        checkpoint: dict[str, Any],
        stage: str,
        instruction: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        if stage != "decide":
            return await self._ask_once(activity, checkpoint, stage, instruction, data)

        # Keep the correction budget across interruption/resume. Nothing is executed
        # or counted as a research step until a valid decision has been generated.
        errors = checkpoint.setdefault("decision_errors", [])
        while len(errors) <= _DECISION_RETRIES:
            if errors and checkpoint["tokens"] >= self._max_tokens:
                raise ValueError(
                    policy("research", "engine.copy.ask_text_2", str)
                    + errors[-1]["error"]
                )
            payload = data
            if errors:
                payload = {
                    **data,
                    "validation_feedback": errors,
                    "correction_instruction": (
                        policy("research", "engine.copy.ask_text", str)
                    ),
                }
            try:
                decision = await self._ask_once(
                    activity, checkpoint, stage, instruction, payload,
                )
                _validate_decision(decision, data)
            except _InvalidResearchOutput as error:
                call = checkpoint["model_calls"][-1]
                call["validation_error"] = str(error)
                errors.append({"output": call["output_raw"], "error": str(error)})
                await self._save_checkpoint(activity, checkpoint)
            else:
                checkpoint.pop("decision_errors", None)
                return decision
        raise ValueError(
            policy("research", "engine.templates.ask_text", str).format(
                p0=_DECISION_RETRIES
            )
            + errors[-1]["error"]
        )

    async def _ask_once(
        self,
        activity: Activity,
        checkpoint: dict[str, Any],
        stage: str,
        instruction: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        output = await self._llm.complete(
            [
                {
                    "role": "system",
                    "content": self._canon
                    + "\n\n"
                    + str(checkpoint.get("skill_fragment", ""))
                    + "\n\n"
                    + instruction
                    + policy("research", "engine.copy.ask_once_text_2", str),
                },
                {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
            ],
            module="activity",
            output_type=f"exploration_{stage}",
            correlation_id=_correlation_id(activity),
            json_mode=True,
        )
        checkpoint.setdefault("model_calls", []).append(
            {
                "stage": stage,
                "call_id": output.call_id,
                "output_raw": output.content,
                "completed_at": time.time(),
                "prompt_tokens": output.prompt_tokens,
                "completion_tokens": output.completion_tokens,
            }
        )
        checkpoint["tokens"] += output.prompt_tokens + output.completion_tokens
        await self._save_checkpoint(activity, checkpoint)
        try:
            await self._evaluator.evaluate(output)
        except Exception:
            _LOGGER.exception(policy("research", "engine.copy.ask_once_text", str))
        try:
            parsed = json.loads(output.content)
        except json.JSONDecodeError as error:
            raise _InvalidResearchOutput(
                policy("research", "engine.templates.ask_once_text_2", str).format(
                    p0=error.msg, p1=error.lineno, p2=error.colno
                )
            ) from error
        if not isinstance(parsed, dict):
            raise _InvalidResearchOutput(
                policy("research", "engine.templates.ask_once_text", str).format(
                    p0=stage
                )
            )
        return cast(dict[str, Any], parsed)

    async def _summarize(
        self,
        activity: Activity,
        checkpoint: dict[str, Any],
    ) -> dict[str, Any]:
        related = await self._memory.search(str(checkpoint["initial_question"]))
        result = await self._ask(
            activity,
            checkpoint,
            "finalize",
            _EXPLORATION_FINALIZE_SYSTEM,
            {
                "seed_topic": checkpoint["initial_question"],
                "question": checkpoint["question"],
                "findings": checkpoint["findings"],
                "sources": checkpoint.get("source_receipts", [])
                + checkpoint.get("knowledge_sources", []),
                "related_memories": [
                    m.summary for m in related if m.kind is MemoryKind.KNOWLEDGE
                ][
                    : policy(
                        "research",
                        "engine.parameters.summarize_related_memories_chars_or_items",
                        int,
                    )
                ],
                "unresolved_questions": checkpoint["unresolved_questions"],
                "stop_reason": checkpoint["stop_reason"],
            },
        )
        if not isinstance(result.get("summary"), str) or not result["summary"].strip():
            raise ValueError(policy("research", "engine.copy.summarize_text", str))
        if not isinstance(result.get("core_discovery", ""), str):
            raise ValueError(policy("research", "engine.copy.summarize_text_2", str))
        report = _validate_citations(result.get("report"), checkpoint)
        if checkpoint.get("require_citations") and result.get("core_discovery"):
            if not report:
                raise ValueError(
                    policy("research", "engine.copy.summarize_text_4", str)
                )
            checked = await self._ask(
                activity,
                checkpoint,
                "claim_support",
                policy("research", "engine.parameters.summarize_checked_text", str),
                {"question": checkpoint["question"], "report": report},
            )
            supported = checked.get("supported")
            if (
                not isinstance(supported, list)
                or len(cast(list[Any], supported)) != len(report)
                or any(value is not True for value in cast(list[Any], supported))
            ):
                raise ValueError(
                    policy("research", "engine.copy.summarize_text_5", str)
                )
        result["report"] = report
        # Knowledge already committed with source evidence is the only knowledge result.
        result["knowledge"] = checkpoint["knowledge"]
        if not result.get("core_discovery") and not result.get("failure_reason"):
            result["failure_reason"] = checkpoint["stop_reason"] or policy(
                "research", "engine.copy.summarize_text_3", str
            )
        if not isinstance(result.get("unresolved_questions"), list):
            result["unresolved_questions"] = checkpoint["unresolved_questions"]
        return result

    async def _save_checkpoint(
        self,
        activity: Activity,
        checkpoint: dict[str, Any],
    ) -> None:
        now = time.monotonic()
        previous = checkpoint.get("_clock", now)
        checkpoint["elapsed_seconds"] += max(0.0, now - previous)
        checkpoint["_clock"] = now
        progress = {
            **activity.progress,
            "intent": activity.progress.get(
                "intent", activity.progress.get("description", "")
            ),
            "exploration": deepcopy(
                {
                    key: value
                    for key, value in checkpoint.items()
                    if not key.startswith("_")
                }
            ),
        }
        # Only completed actions can replace an intended activity description.
        for action in reversed(checkpoint["actions"]):
            if action.get("ok") is True:
                name = action["name"]
                args = action.get("args", {})
                if name in {"web_search", "local_search"}:
                    progress["description"] = policy(
                        "research", "engine.templates.save_checkpoint_text", str
                    ).format(p0=args.get("query", ""))
                elif name == "web_fetch":
                    progress["description"] = policy(
                        "research", "engine.templates.save_checkpoint_text_2", str
                    ).format(p0=args.get("url", ""))
                elif name == "file_io":
                    progress["description"] = policy(
                        "research", "engine.templates.save_checkpoint_text_3", str
                    ).format(p0=args.get("path", ""))
                elif name in {"rewrite", "follow_up"}:
                    progress["description"] = policy(
                        "research", "engine.templates.save_checkpoint_text_4", str
                    ).format(p0=action.get("text", ""))
                elif name == "select_topic":
                    progress["description"] = policy(
                        "research", "engine.templates.save_checkpoint_text_5", str
                    ).format(p0=action.get("text", ""))
                elif name == "finalize":
                    progress["description"] = policy(
                        "research", "engine.templates.save_checkpoint_text_6", str
                    ).format(p0=action.get("text", ""))
                else:
                    continue
                break
        previous_raw = activity.progress.get("exploration", {})
        previous = (
            cast(dict[str, Any], previous_raw) if isinstance(previous_raw, dict) else {}
        )
        changed = (
            previous.get("actions") != progress["exploration"]["actions"]
            or previous.get("state") != progress["exploration"]["state"]
        )
        await self._store.update(replace(activity, progress=progress))
        activity.progress = progress
        if changed and self._notify_progress is not None:
            await self._notify_progress(activity)


def _result_parts(result: Any) -> tuple[str, str, str, str]:
    """一条检索结果 → (name, url, path, snippet)；无法解析返回空 tuple。

    兼容两种来源：web 结果 {title/name, url, snippet/content}、
    本地结果 {path, snippet}（local_search 只有 path+snippet，name 取文件名）。
    """
    if isinstance(result, dict):
        title = cast(str | None, result.get("title"))
        name_key = cast(str | None, result.get("name"))
        path_key = cast(str | None, result.get("path"))
        path = str(path_key or "")
        url = str(cast(str | None, result.get("url")) or "")
        snippet = str(
            cast(str | None, result.get("snippet"))
            or cast(str | None, result.get("content"))
            or ""
        )
        name = (
            title
            or name_key
            or (_basename(path) if path else "")
            or (_domain(url) if url else "")
        )
        return name, url, path, snippet
    if isinstance(result, str):
        return result, "", "", ""
    return "", "", "", ""


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
    saved: dict[str, Any] = {
        **checkpoint,
        "version": 2,
        "state": checkpoint.get("state", "deciding"),
        "topic": str(topic) if isinstance(topic, str) and topic else _topic(activity),
        "initial_question": checkpoint.get("initial_question") or _topic(activity),
        "question": checkpoint.get("question") or _topic(activity),
        "raw_results": _dict_list(checkpoint.get("raw_results")),
        "cursor": int(checkpoint.get("cursor", 0)),
        "findings": _str_list(checkpoint.get("findings")),
        "tool_calls": _dict_list(checkpoint.get("tool_calls")),
        "source_pending": (
            checkpoint.get("source_pending")
            if isinstance(checkpoint.get("source_pending"), dict)
            else None
        ),
        "summary_done": bool(checkpoint.get("summary_done")),
        "judged": judged if isinstance(judged, dict) else None,
        "sink_done": bool(checkpoint.get("sink_done")),
        "actions": _dict_list(checkpoint.get("actions")),
        "knowledge": _dict_list(checkpoint.get("knowledge")),
        "processed_sources": _str_list(checkpoint.get("processed_sources")),
        "searched_queries": _str_list(checkpoint.get("searched_queries")),
        "unresolved_questions": _str_list(checkpoint.get("unresolved_questions")),
        "next_action": checkpoint.get("next_action"),
        "steps": int(checkpoint.get("steps", 0)),
        "searches": int(checkpoint.get("searches", 0)),
        "sources": int(checkpoint.get("sources", 0)),
        "tokens": int(checkpoint.get("tokens", 0)),
        "elapsed_seconds": float(checkpoint.get("elapsed_seconds", 0)),
        "failure_reason": checkpoint.get("failure_reason", ""),
        "stop_reason": checkpoint.get("stop_reason", ""),
    }
    excluded = any(_excluded_source(item) for item in saved["raw_results"])
    legacy_uncommitted = (
        checkpoint.get("version") != 2
        and saved["state"] != "completed"
        and not saved["sink_done"]
    )
    if excluded or legacy_uncommitted:
        # Old snippets and summaries have no admission receipt. Recheck real pages
        # before producing any new knowledge; already committed history is retained.
        saved.update(
            state="deciding",
            findings=[],
            knowledge=[],
            source_pending=None,
            summary_done=False,
            judged=None,
            sink_done=False,
            next_action=None,
            raw_results=[
                item for item in saved["raw_results"] if not _excluded_source(item)
            ],
        )
    return saved


def _topic(activity: Activity) -> str:
    for key in ("intent", "description"):
        description = activity.progress.get(key)
        if isinstance(description, str) and description.strip():
            return description.strip()
    goal = activity.progress.get("goal")
    if isinstance(goal, dict):
        topic = cast(dict[str, Any], goal).get("topic")
        if isinstance(topic, str) and topic:
            return topic
    return activity.id


def _correlation_id(activity: Activity) -> str:
    value = activity.progress.get("correlation_id")
    if isinstance(value, str) and value:
        return value
    return activity.id


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


def _validate_citations(
    value: Any,
    checkpoint: dict[str, Any],
) -> list[dict[str, Any]]:
    sources = {
        item["location"]: item["source_text_raw"]
        for item in checkpoint.get("source_receipts", [])
        if item["assessment_raw"].get("relevant") is True
        and item["assessment_raw"].get("usable") is True
    }
    sources.update(
        {
            item["location"]: item["content"]
            for item in checkpoint.get("knowledge_sources", [])
            if item.get("location")
        }
    )
    report: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return report
    for item in cast(list[Any], value):
        if not isinstance(item, dict):
            continue
        item = cast(dict[str, Any], item)
        if not isinstance(item.get("claim"), str):
            continue
        citations = item.get("citations")
        if not isinstance(citations, list) or not citations:
            continue
        valid = all(
            isinstance(cite, dict)
            and isinstance(cite.get("quote"), str)
            and bool(cast(str, cite["quote"]).strip())
            and cite.get("source_url") in sources
            and cite["quote"] in sources[cite["source_url"]]
            for cite in cast(list[Any], citations)
        )
        if valid and len(json.dumps([*report, item], ensure_ascii=False)) <= policy(
            "research", "engine.parameters.validate_citations_value_limit", int
        ):
            report.append(item)
    return report


def _result_from_checkpoint(checkpoint: dict[str, Any]) -> dict[str, Any]:
    judged = _checkpoint_judged(checkpoint)
    topic = str(checkpoint["topic"])
    findings = cast(list[str], checkpoint["findings"])
    tool_calls = cast(list[dict[str, Any]], checkpoint["tool_calls"])
    core_discovery = str(judged.get("core_discovery") or "")
    return {
        "report": judged.get("report", []),
        "type": "free_exploration",
        "outcome": "won" if core_discovery else "exhausted",
        "summary": str(judged.get("summary") or topic),
        "core_discovery": core_discovery,
        "knowledge": (
            judged["knowledge"] if isinstance(judged.get("knowledge"), list) else []
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
        "actions": checkpoint["actions"],
        "question": checkpoint["initial_question"],
        "failure_reason": ""
        if core_discovery
        else (
            judged.get("failure_reason")
            or checkpoint["failure_reason"]
            or checkpoint["stop_reason"]
        ),
        "unresolved_questions": judged.get("unresolved_questions")
        or checkpoint["unresolved_questions"],
        "stop_reason": checkpoint["stop_reason"],
    }


def _validate_decision(decision: dict[str, Any], data: dict[str, Any]) -> None:
    action = decision.get("action")
    if not isinstance(action, str) or action not in data["available_actions"]:
        raise _InvalidResearchOutput(
            policy("research", "engine.copy.validate_decision_text", str)
            + ", ".join(data["available_actions"])
        )
    fields = {
        "rewrite": "question",
        "follow_up": "question",
        "ask_user": "question",
        "search": "query",
        "knowledge_search": "query",
        "read": "index",
        "finish": "unresolved_questions",
    }
    field = fields[action]
    unexpected = set(decision) - {"action", "reason", field}
    if unexpected:
        raise _InvalidResearchOutput(
            policy("research", "engine.templates.validate_decision_text_2", str).format(
                p0=action, p1=", ".join(sorted(unexpected)), p2=field
            )
        )
    if "reason" in decision and not isinstance(decision["reason"], str):
        raise _InvalidResearchOutput(
            policy("research", "engine.templates.validate_decision_text_3", str).format(
                p0=action
            )
        )
    value = decision.get(field)
    if field in {"question", "query"}:
        if not isinstance(value, str) or not value.strip():
            raise _InvalidResearchOutput(
                policy(
                    "research", "engine.templates.validate_decision_text_4", str
                ).format(p0=action, p1=field)
            )
        decision[field] = value.strip()
    elif action == "read":
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value < len(data["results"])
        ):
            raise _InvalidResearchOutput(
                policy(
                    "research", "engine.templates.validate_decision_text", str
                ).format(p0=len(data["results"]))
            )
    elif "unresolved_questions" in decision and (
        not isinstance(value, list)
        or any(not isinstance(item, str) for item in cast(list[Any], value))
    ):
        raise _InvalidResearchOutput(
            policy("research", "engine.copy.validate_decision_text_2", str)
        )


_DECIDE_SYSTEM = (
    policy("research", "engine._DECIDE_SYSTEM", str)
)

_SOURCE_CHECK_SYSTEM = (
    policy("research", "engine._SOURCE_CHECK_SYSTEM", str)
)

_EXPLORATION_FINALIZE_SYSTEM = (
    policy("research", "engine._EXPLORATION_FINALIZE_SYSTEM", str)
)
