# pyright: reportPrivateUsage=false
"""Facade declarations and the two phases of application assembly."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from nyx.activity.facade import ActivityFacade
from nyx.activity.store import ActivityStore
from nyx.bootstrap import initialize_card, load_skills
from nyx.character_cards import CharacterCard
from nyx.config import Config
from nyx.db import Database
from nyx.desire.facade import DesireFacade
from nyx.desire.store import DesireStore
from nyx.enums import EventType, TickType
from nyx.eval.evaluator import Evaluator
from nyx.eval.store import EvalStore
from nyx.events.bus import EventBus
from nyx.events.routing import ROUTE_SPECS, RouteSpec
from nyx.expression.facade import ExpressionFacade
from nyx.expression.store import ExpressionInteractionStore
from nyx.harness import PluginContext
from nyx.harness import build_plugins as _build_plugins
from nyx.harness import start_plugins as _start_plugins
from nyx.harness import validate_optional as _validate_optional
from nyx.inner_life.facade import InnerLifeFacade
from nyx.inner_life.store import InnerLifeStore
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade, build_source_topic
from nyx.memory.facts import MemoryFactStore
from nyx.memory.retrieval import EmbedFn, MemoryRetrieval
from nyx.memory.store import MemoryStore
from nyx.reading.facade import ReadingFacade
from nyx.reading.store import ReadingStore
from nyx.research import build_deep_research_tool
from nyx.runtime import (
    on_desire_eval,
    on_initiate_chat_check,
    on_mutter_check,
    on_reflection_check,
    on_schedule_block_start,
    on_user_message,
)
from nyx.tools.activity_tasks import build_activity_task_tools
from nyx.tools.registry import ToolRegistry
from nyx.tools.web_fetch import build_web_fetch_tool
from nyx.tools.web_search import build_web_search_tool
from nyx.types import Book, CurrentState, Event, ReadingProgress, Tool

if TYPE_CHECKING:
    from nyx.app_context import _App

Handler = Callable[[Event], Awaitable[None]]
RuntimeHandler = Callable[["_App", Event], Awaitable[None]]
Factory = Callable[["PluginContext"], Awaitable[Mapping[str, object]]]
Lifecycle = Callable[["PluginContext"], Awaitable[None]]


class FacadePlugin(Protocol):
    """Declare the capabilities, tool surface and lifecycle of one component."""

    name: str
    provides: tuple[str, ...]
    requires: tuple[str, ...]
    optional: tuple[str, ...]

    def tools(self) -> list[Tool]: ...
    def routes(self) -> list[RouteSpec]: ...
    async def start(self, ctx: PluginContext) -> None: ...
    async def quiesce(self) -> None: ...


async def _get_state(ctx: PluginContext) -> CurrentState:
    reader = cast(Callable[[], Awaitable[CurrentState]], ctx.get("state_reader"))
    return await reader()


async def _get_observation(ctx: PluginContext) -> dict[str, str]:
    reader = cast(
        Callable[[], Awaitable[dict[str, str]]], ctx.get("observation_reader")
    )
    return await reader()


def _claim_return(ctx: PluginContext) -> dict[str, float] | None:
    return cast(Callable[[], dict[str, float] | None], ctx.get("claim_return"))()


def _finish_return(ctx: PluginContext, claim: dict[str, float] | None) -> None:
    cast(Callable[[dict[str, float] | None], None], ctx.get("finish_return"))(claim)


def _release_return(ctx: PluginContext, claim: dict[str, float] | None) -> None:
    cast(Callable[[dict[str, float] | None], None], ctx.get("release_return"))(claim)


async def _read_reading_context(
    ctx: PluginContext, source_id: str, query: str
) -> str:
    reader = cast(
        Callable[[str, str], Awaitable[str]] | None, ctx.get("reading_context")
    )
    return "" if reader is None else await reader(source_id, query)


async def _list_reader_books(ctx: PluginContext) -> list[Book]:
    return await cast(ReadingFacade, ctx.get("reading")).list_readable_books()


async def _read_reader_book(
    ctx: PluginContext,
    book_id: str,
    target_paragraph: int | None,
    correlation_id: str,
) -> dict[str, Any]:
    return await cast(ReadingFacade, ctx.get("reading")).read_for_activity(
        book_id, target_paragraph, correlation_id
    )


async def _get_reader_book(ctx: PluginContext, book_id: str) -> Book:
    return await cast(ReadingFacade, ctx.get("reading")).get_book(book_id)


async def _get_reader_progress(ctx: PluginContext, book_id: str) -> ReadingProgress:
    return await cast(ReadingFacade, ctx.get("reading")).get_progress(book_id)


async def _on_failure(
    ctx: PluginContext, kind: str, topic: str, summary: str, correlation_id: str
) -> None:
    callback = cast(
        Callable[[str, str, str, str], Awaitable[None]] | None,
        ctx.get("on_failure"),
    )
    if callback is not None:
        await callback(kind, topic, summary, correlation_id)


@dataclass
class _Plugin:
    name: str
    factory: Factory
    provides: tuple[str, ...]
    requires: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    route_bindings: tuple[tuple[RouteSpec, RuntimeHandler], ...] = ()
    tool_factory: Callable[[PluginContext], list[Tool]] | None = None
    starter: Lifecycle | None = None
    stopper: Lifecycle | None = None
    context: PluginContext = field(init=False, repr=False, compare=False)

    def tools(self) -> list[Tool]:
        return [] if self.tool_factory is None else self.tool_factory(self.context)

    def routes(self) -> list[RouteSpec]:
        return [spec for spec, _ in self.route_bindings]

    async def start(self, ctx: PluginContext) -> None:
        if self.starter is not None:
            await self.starter(ctx)

    async def quiesce(self) -> None:
        if self.stopper is not None:
            await self.stopper(self.context)


def validate_routes(plugins: Sequence[FacadePlugin]) -> None:
    """Keep both the full route specification and consumer identities equal."""
    routes = [spec for plugin in plugins for spec in plugin.routes()]
    if len(routes) != len(ROUTE_SPECS) or set(routes) != set(ROUTE_SPECS):
        raise RuntimeError("plugin routes differ from ROUTE_SPECS")


def validate_optional(
    plugins: Sequence[_Plugin], capabilities: Mapping[str, object]
) -> None:
    _validate_optional(
        plugins, capabilities, degradable=("reading_context", "on_failure")
    )


async def build_plugins(
    plugins: Sequence[_Plugin],
    capabilities: dict[str, object],
) -> None:
    """Construct only after validating the complete strong dependency graph."""
    validate_routes(plugins)
    await _build_plugins(plugins, capabilities)


async def start_plugins(
    plugins: Sequence[_Plugin],
    capabilities: Mapping[str, object],
) -> None:
    """Finish optional injection before restoration and runtime admission."""
    await _start_plugins(
        plugins, capabilities, degradable=("reading_context", "on_failure")
    )


def route_handlers(app: _App) -> dict[str, Handler]:
    """Bind the same declarations used by assembly, including test contexts."""
    plugins = default_plugins()
    validate_routes(plugins)
    return {
        spec.consumer_id: partial(handler, app)
        for plugin in plugins
        for spec, handler in plugin.route_bindings
    }


async def _build_eval(ctx: PluginContext) -> Mapping[str, object]:
    return {
        "evaluator": Evaluator(
            cast(EmbedFn, ctx.get("embed")),
            cast(EvalStore, ctx.get("eval_store")),
            partial(_on_failure, ctx),
        )
    }


async def _build_memory(ctx: PluginContext) -> Mapping[str, object]:
    db = cast(Database, ctx.get("db"))
    store = MemoryStore(db)
    embed = cast(EmbedFn, ctx.get("embed"))
    memory = MemoryFacade(
        store,
        MemoryRetrieval(store, embed),
        cast(EventBus, ctx.get("bus")),
        cast(LlmClient, ctx.get("llm")),
        cast(Evaluator, ctx.get("evaluator")),
        cast(Config, ctx.get("config")).memory,
        embed,
        MemoryFactStore(db),
    )
    return {"memory": memory, "on_failure": memory.record_failure_signal}


async def _build_tools(ctx: PluginContext) -> Mapping[str, object]:
    memory = cast(MemoryFacade, ctx.get("memory"))

    async def ingest_web(text: str, url: str) -> dict[str, Any]:
        return await memory.ingest_source(
            text,
            build_source_topic("web", url),
            url,
            url,
            location=url,
        )

    builder = cast(Callable[..., ToolRegistry], ctx.get("build_tools"))
    return {"tools": builder(ingest=ingest_web)}


async def _build_desire(ctx: PluginContext) -> Mapping[str, object]:
    memory = cast(MemoryFacade, ctx.get("memory"))
    return {
        "desire": DesireFacade(
            DesireStore(cast(Database, ctx.get("db"))),
            cast(EventBus, ctx.get("bus")),
            cast(LlmClient, ctx.get("llm")),
            cast(Evaluator, ctx.get("evaluator")),
            cast(Config, ctx.get("config")).desire,
            memory.list_memories,
            cast(EmbedFn, ctx.get("embed")),
        )
    }


async def _build_activity(ctx: PluginContext) -> Mapping[str, object]:
    config = cast(Config, ctx.get("config"))
    return {
        "activity": ActivityFacade(
            ActivityStore(cast(Database, ctx.get("db"))),
            cast(EventBus, ctx.get("bus")),
            cast(LlmClient, ctx.get("llm")),
            cast(Evaluator, ctx.get("evaluator")),
            cast(ToolRegistry, ctx.get("tools")),
            cast(DesireFacade, ctx.get("desire")),
            cast(MemoryFacade, ctx.get("memory")),
            partial(_get_state, ctx),
            partial(_get_observation, ctx),
            config.activity,
            config.exploration,
            cast(str, ctx.get("canon")),
            partial(_list_reader_books, ctx),
            partial(_read_reader_book, ctx),
            partial(_get_reader_book, ctx),
            partial(_get_reader_progress, ctx),
            fetch_source=(
                build_web_fetch_tool().handler
                if config.exploration.web_enabled
                else None
            ),
        )
    }


def _activity_tools(ctx: PluginContext) -> list[Tool]:
    activity = cast(ActivityFacade, ctx.get("activity"))
    tools = build_activity_task_tools(
        activity.assign_web_task,
        activity.assign_book_task_by_title,
        activity.assign_creation_task,
        activity.assign_browser_survey_task,
    )
    return (
        [tools[0], tools[3], tools[1], tools[2]]
        if cast(Config, ctx.get("config")).exploration.web_enabled
        else [tools[1], tools[2]]
    )


def _memory_tools(ctx: PluginContext) -> list[Tool]:
    memory = cast(MemoryFacade, ctx.get("memory"))
    config = cast(Config, ctx.get("config"))

    async def memory_recall(query: str, top_k: int = 8) -> dict[str, Any]:
        segments = await memory.search_conversation_segments(
            query, max(1, min(top_k, 8))
        )
        facts = await memory.search_facts(query, scope="profile")
        facts += await memory.search_facts(query, scope="conversation")
        return {
            "segments": [
                {
                    "segment_id": segment.id,
                    "content": segment.raw_content,
                    "importance": segment.importance,
                    "freshness": segment.freshness,
                }
                for segment in segments
            ],
            "facts": [
                {
                    "subject": fact.subject,
                    "predicate": fact.predicate,
                    "object_value": fact.object_value,
                    "scope": fact.scope,
                    "source_segment_id": fact.source_segment_id,
                }
                for fact in facts
            ],
        }

    async def knowledge_search(query: str, top_k: int = 5) -> dict[str, Any]:
        return await memory.search_knowledge(query, top_k)

    return [
        Tool(
            "memory_recall",
            "召回相关对话段原文和用户/关系事实；"
            "结果带 segment_id 或 source_segment_id 供引用。",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要回忆的主题或问题"},
                    "top_k": {
                        "type": "integer",
                        "description": "返回对话段条数上限",
                        "default": 8,
                    },
                },
                "required": ["query"],
            },
            memory_recall,
        ),
        Tool(
            "knowledge_search",
            "检索知识事实与证据块原文，返回 source_chunk_id 溯源供逐条引用。",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要检索的知识主题"},
                    "top_k": {
                        "type": "integer",
                        "description": "返回事实条数上限",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
            knowledge_search,
        ),
        build_deep_research_tool(
            search=(
                build_web_search_tool().handler
                if config.exploration.web_enabled
                else None
            ),
            fetch=(
                build_web_fetch_tool().handler
                if config.exploration.web_enabled
                else None
            ),
            llm=cast(LlmClient, ctx.get("llm")),
            ingest=memory.ingest_source,
            evaluator=cast(Evaluator, ctx.get("evaluator")),
        ),
    ]


async def _build_inner_life(ctx: PluginContext) -> Mapping[str, object]:
    inner = InnerLifeFacade(
        InnerLifeStore(cast(Database, ctx.get("db"))),
        cast(ActivityFacade, ctx.get("activity")),
        cast(DesireFacade, ctx.get("desire")),
        cast(MemoryFacade, ctx.get("memory")),
        cast(EventBus, ctx.get("bus")),
        cast(LlmClient, ctx.get("llm")),
        cast(Evaluator, ctx.get("evaluator")),
        cast(Config, ctx.get("config")),
    )
    return {"inner_life": inner, "state_reader": inner.get_state}


async def _build_expression(ctx: PluginContext) -> Mapping[str, object]:
    db = cast(Database, ctx.get("db"))
    tools = cast(ToolRegistry, ctx.get("tools"))
    for tool in (
        *cast(list[Tool], ctx.get("activity_tools")),
        *cast(list[Tool], ctx.get("memory_tools")),
    ):
        tools.register(tool)
    skills = load_skills(cast(Path, ctx.get("prompt_dir")) / "skills", tools)
    await initialize_card(db, cast(CharacterCard, ctx.get("card")).manifest)
    return {
        "expression": ExpressionFacade(
            cast(EventBus, ctx.get("bus")),
            cast(LlmClient, ctx.get("llm")),
            cast(Evaluator, ctx.get("evaluator")),
            cast(MemoryFacade, ctx.get("memory")),
            cast(ActivityFacade, ctx.get("activity")),
            cast(DesireFacade, ctx.get("desire")),
            cast(InnerLifeFacade, ctx.get("inner_life")),
            cast(str, ctx.get("canon")),
            cast(str, ctx.get("ask")),
            cast(Config, ctx.get("config")).expression,
            tools,
            skills=skills,
            interaction_store=ExpressionInteractionStore(db),
            knowledge_boundary=cast(str, ctx.get("knowledge_boundary")),
            observation_reader=partial(_get_observation, ctx),
            claim_return=partial(_claim_return, ctx),
            finish_return=partial(_finish_return, ctx),
            release_return=partial(_release_return, ctx),
            reading_context_reader=partial(_read_reading_context, ctx),
            checkpointer=db.checkpointer,
        )
    }


async def _build_reading(ctx: PluginContext) -> Mapping[str, object]:
    reading = ReadingFacade(
        ReadingStore(cast(Database, ctx.get("db"))),
        cast(InnerLifeFacade, ctx.get("inner_life")),
        cast(DesireFacade, ctx.get("desire")),
        cast(MemoryFacade, ctx.get("memory")),
        cast(LlmClient, ctx.get("llm")),
        cast(Evaluator, ctx.get("evaluator")),
        cast(EventBus, ctx.get("bus")),
        cast(str, ctx.get("canon")),
        cast(ExpressionFacade, ctx.get("expression")),
    )
    return {"reading": reading, "reading_context": reading.build_reply_context}


async def _start_activity(ctx: PluginContext) -> None:
    await cast(ActivityFacade, ctx.get("activity")).recover_stale_running()


async def _start_expression(ctx: PluginContext) -> None:
    await cast(ExpressionFacade, ctx.get("expression")).restore_history()


async def _quiesce_activity(ctx: PluginContext) -> None:
    await cast(ActivityFacade, ctx.get("activity")).quiesce()


async def _apply_inner_event(app: _App, event: Event, *, consumer_id: str) -> None:
    await app.inner_life.apply_event(event, consumer_id)


def default_plugins() -> tuple[_Plugin, ...]:
    """Declare the seven domain components and their existing tool registry."""
    return (
        _Plugin("eval", _build_eval, ("evaluator",), optional=("on_failure",)),
        _Plugin(
            "memory",
            _build_memory,
            ("memory", "on_failure", "memory_tools"),
            ("evaluator",),
            tool_factory=_memory_tools,
            route_bindings=(
                (
                    RouteSpec(
                        EventType.ANNOTATION_FEEDBACK,
                        "memory.annotation_feedback",
                        "memory",
                        "apply_annotation_feedback",
                    ),
                    lambda app, event: app.memory.apply_annotation_feedback(
                        event, "memory.annotation_feedback"
                    ),
                ),
                (
                    RouteSpec(
                        EventType.USER_MESSAGE,
                        "memory.annotation_recording",
                        "memory",
                        "record_annotation_feedback",
                    ),
                    lambda app, event: app.memory.record_annotation_feedback(
                        event, "memory.annotation_recording"
                    ),
                ),
                (
                    RouteSpec(
                        EventType.ACTIVITY_END,
                        "memory.activity_end",
                        "memory",
                        "remember_activity",
                    ),
                    lambda app, event: app.memory.remember_activity(
                        event, "memory.activity_end"
                    ),
                ),
                (
                    RouteSpec(
                        EventType.SCENE_MEMORY_REQUESTED,
                        "memory.scene_reply",
                        "memory",
                        "remember_scene",
                    ),
                    lambda app, event: app.memory.remember_scene(
                        event, "memory.scene_reply"
                    ),
                ),
            ),
        ),
        _Plugin("tools", _build_tools, ("tools",), ("memory",)),
        _Plugin(
            "desire",
            _build_desire,
            ("desire",),
            ("memory", "evaluator"),
            route_bindings=(
                (
                    RouteSpec(
                        EventType.OBSERVATION_STATE,
                        "desire.observation_state",
                        "desire",
                        "apply_observation_state",
                    ),
                    lambda app, event: app.desire.add_value(
                        event, "desire.observation_state"
                    ),
                ),
                (
                    RouteSpec(
                        EventType.ACTIVITY_END,
                        "desire.activity_end",
                        "desire",
                        "apply_activity_end",
                    ),
                    lambda app, event: app.desire.add_value(
                        event, "desire.activity_end"
                    ),
                ),
                (
                    RouteSpec(
                        EventType.CLOCK_TICK,
                        "desire.desire_eval",
                        "desire",
                        "on_desire_eval",
                        TickType.DESIRE_EVAL,
                    ),
                    on_desire_eval,
                ),
            ),
        ),
        _Plugin(
            "activity",
            _build_activity,
            ("activity", "activity_tools"),
            ("tools", "desire", "memory", "evaluator"),
            ("state_reader", "observation_reader", "reading"),
            tool_factory=_activity_tools,
            starter=_start_activity,
            stopper=_quiesce_activity,
            route_bindings=(
                (
                    RouteSpec(
                        EventType.DESIRE_GENERATED,
                        "activity.desire_generated",
                        "activity",
                        "on_desire_generated",
                    ),
                    lambda app, event: app.activity.on_desire_generated(event),
                ),
                (
                    RouteSpec(
                        EventType.CLOCK_TICK,
                        "activity.schedule_block_start",
                        "activity",
                        "on_schedule_block_start",
                        TickType.SCHEDULE_BLOCK_START,
                    ),
                    on_schedule_block_start,
                ),
            ),
        ),
        _Plugin(
            "inner_life",
            _build_inner_life,
            ("inner_life", "state_reader"),
            ("activity", "desire", "memory", "evaluator"),
            route_bindings=tuple(
                (
                    RouteSpec(event_type, consumer_id, "inner_life", handler_key),
                    partial(_apply_inner_event, consumer_id=consumer_id),
                )
                for event_type, consumer_id, handler_key in (
                    (
                        EventType.OBSERVATION_STATE,
                        "inner_life.observation_state",
                        "apply_observation_state",
                    ),
                    (
                        EventType.DESIRE_SATISFIED,
                        "inner_life.desire_satisfied",
                        "apply_desire_satisfied",
                    ),
                    (
                        EventType.ACTIVITY_END,
                        "inner_life.activity_end",
                        "apply_activity_end",
                    ),
                    (EventType.REFLECTION, "inner_life.reflection", "apply_reflection"),
                )
            )
            + (
                (
                    RouteSpec(
                        EventType.CLOCK_TICK,
                        "inner_life.reflection_check",
                        "inner_life",
                        "on_reflection_check",
                        TickType.REFLECTION_CHECK,
                    ),
                    on_reflection_check,
                ),
            ),
        ),
        _Plugin(
            "expression",
            _build_expression,
            ("expression",),
            (
                "memory",
                "activity",
                "desire",
                "inner_life",
                "tools",
                "evaluator",
                "activity_tools",
                "memory_tools",
            ),
            (
                "observation_reader",
                "claim_return",
                "finish_return",
                "release_return",
                "reading_context",
            ),
            starter=_start_expression,
            route_bindings=(
                (
                    RouteSpec(
                        EventType.USER_MESSAGE,
                        "expression.user_message",
                        "expression",
                        "on_user_message",
                    ),
                    on_user_message,
                ),
                (
                    RouteSpec(
                        EventType.CLOCK_TICK,
                        "expression.mutter_check",
                        "expression",
                        "on_mutter_check",
                        TickType.MUTTER_CHECK,
                    ),
                    on_mutter_check,
                ),
                (
                    RouteSpec(
                        EventType.CLOCK_TICK,
                        "expression.initiate_chat_check",
                        "expression",
                        "on_initiate_chat_check",
                        TickType.INITIATE_CHAT_CHECK,
                    ),
                    on_initiate_chat_check,
                ),
            ),
        ),
        _Plugin(
            "reading",
            _build_reading,
            ("reading", "reading_context"),
            ("inner_life", "desire", "memory", "expression", "evaluator"),
        ),
    )
