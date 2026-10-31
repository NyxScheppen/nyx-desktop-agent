# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false, reportUnknownMemberType=false
# langgraph 类型标注松散：add_node/compile/ainvoke 返回部分未知、graph.state 缺 stub
"""回复流程 LangGraph 图：快慢通道 + 多轮 think/speak + 场景化记忆。

节点为闭包，依赖经 ReplyDeps 注入。
"""
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from nyx.config import ExpressionConfig
from nyx.enums import ContextMode, EventType, InteractionKind, MemoryKind, UserIntent
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import internal_text_event
from nyx.expression.classifier import (
    classify_channel,
    is_question,
)
from nyx.expression.prompt import (
    _READING_BLOCK_MAX_CHARS,
    DISCUSSION_USE_TOOLS_TASK,
    RESPOND_TASK,
    RESPOND_TASK_CONTINUE,
    TOOL_REPLY_GUIDANCE,
    USE_TOOLS_TASK,
    activity_summary,
    bound_material_block,
    build_backtrack_context,
    build_conversation_segments_prompt,
    build_system_prompt,
    build_user_prompt,
)
from nyx.expression.tool_pipeline import (
    ToolState,
    build_tool_graph,
    execution_feedback,
)
from nyx.expression.tool_pipeline import _parse_plan as _parse_plan
from nyx.inner_life.facade import InnerLifeFacade
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.memory.facts import is_fact_query
from nyx.tools.registry import ToolRegistry
from nyx.types import (
    Activity,
    ActivityContext,
    CurrentState,
    Event,
    InteractionAttempt,
    LlmMessage,
    LLMOutput,
    Memory,
    MemoryFact,
    Message,
    ReplyAnnotation,
    SelfNarrative,
    Skill,
)

_DISCUSSION_TOOL_NAMES = frozenset(
    ("memory_recall", "knowledge_search", "deep_research")
)


def _ask_guidance_for(mode: ContextMode, ask_guidance: str | None) -> str | None:
    """仅慢通道注入 ask_guidance；快通道（speak 快速分支）不注入。"""
    return ask_guidance if mode is ContextMode.SLOW else None


def _message_activity(context: ActivityContext) -> Activity | None:
    """Choose the most recent observed activity for history attribution."""
    return context.current if context.current is not None else context.interrupted


class ReplyState(TypedDict):
    message: str
    annotation: ReplyAnnotation | None
    skill: Skill | None
    mode: ContextMode
    context: list[Message]       # 回溯上下文（facade 入口填，快慢一致；不含当前消息）
    conversation_segments: list[str]  # 独立历史段，不伪装成 Message
    memories: list[Memory]       # 预算筛选后实际进入 prompt 的记忆
    facts: list[MemoryFact]       # 当前有效事实（与原始记忆隔离）
    state: CurrentState          # 当前状态快照
    narrative: SelfNarrative | None   # 慢通道 assemble 填充，快通道恒 None
    # 累积：每轮 think/speak 追加（11-expression，tech-ref §6.1 ripple）
    think: list[str]
    speak: list[str]
    needs_continuation: bool
    ask: str | None
    round: int                   # 已完成 think/speak 的轮数（≤ slow_max_rounds）
    correlation_id: str          # 本次 reply 溯源（11-expression，按 09-activity 对齐）
    last_slow_at: float          # 上次慢通道时间（facade 维护，每 reply 入 state）
    tool_feedback: str
    tool_outputs: list[str]      # use_tools 查到的工具结果（慢通道专属）
    tool_recall_candidates: list[tuple[str, str]]
    intent: UserIntent
    temporal_context: str
    reading_context: str
    activity_context: ActivityContext
    answered_attempt: InteractionAttempt | None
    claimed_return: dict[str, float] | None
    fallback: bool


@dataclass
class ReplyDeps:
    llm: LlmClient
    evaluator: Evaluator
    memory: MemoryFacade
    inner_life: InnerLifeFacade          # 慢通道 assemble 时取 narrative
    bus: EventBus
    canon: str
    ask_guidance: str
    config: ExpressionConfig
    history: deque[Message]              # facade 持有的会话历史（跨 reply）
    tools: ToolRegistry                  # use_tools 节点查资料（慢通道）
    knowledge_boundary: str | None
    register_question: Any
    commit_reply_events: Any
    checkpointer: BaseCheckpointSaver[str] | None = None


_LOGGER = logging.getLogger(__name__)


def _is_question(text: str) -> bool:
    """Compatibility wrapper for the single classifier implementation."""
    return is_question(text)


def _rounds_block(think: list[str], speak: list[str]) -> str:
    """把前面几轮的 think/speak 拼成 prompt 段（多轮累积式）。空则返回 ""。

    调用点保证 think 与 speak 等长（respond 节点在每轮起始时二者同为已完成轮数）。
    """
    if not think:
        return ""
    lines = ["[本回合前面的思考]"]
    for i, (t, s) in enumerate(zip(think, speak), start=1):
        lines.append(f"第{i}轮内心：{t}")
        lines.append(f"第{i}轮对外：{s}")
    return "\n".join(lines)


def _parse_reply(raw: str) -> tuple[str, str, bool]:
    """解析 respond 节点 JSON 产出 → (think, speak, needs_continuation)。

    think 缺/非字符串归空；speak 缺/空/非字符串抛 ValueError（回复必须有话说）。
    """
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"respond JSON 应是对象，得到 {type(data).__name__}")
    parsed = cast(dict[str, Any], data)
    think = parsed.get("think")
    speak = parsed.get("speak")
    if not isinstance(speak, str) or not speak.strip():
        raise ValueError("respond JSON 缺 speak 或非空字符串")
    if not isinstance(think, str):
        think = ""
    continuation = parsed.get("needs_continuation", False)
    if not isinstance(continuation, bool):
        continuation = False
    return think.strip(), speak.strip(), continuation


def _voice_output(output: LLMOutput, type_: str, content: str) -> LLMOutput:
    """用解析出的 think/speak 文本重造 LLMOutput，供 evaluator 分别跑 OOC。

    保留 prompt_tokens/completion_tokens/call_id：think/speak 同源一次
    complete()，eval 记账需沿袭同一调用（token 去重锚点，10-eval）。
    """
    return LLMOutput(
        module=output.module,
        type=type_,
        model=output.model,
        content=content,
        correlation_id=output.correlation_id,
        tool_calls=[],
        prompt_tokens=output.prompt_tokens,
        completion_tokens=output.completion_tokens,
        call_id=output.call_id,
        prompt_messages=(
            [
                LlmMessage(role=message["role"], content=message["content"])
                for message in output.prompt_messages
            ]
            if output.prompt_messages is not None
            else None
        ),
    )


def build_reply_graph(deps: ReplyDeps) -> CompiledStateGraph[ReplyState]:
    """构建回复流程图。节点闭包捕获稳定依赖 deps；每 reply 变化的
    correlation_id/last_slow_at 走 state（图可复用，见 facade.__init__）。
    """

    def user_prompt(state: ReplyState) -> str:
        return "\n\n".join(
            part
            for part in (
                bound_material_block(
                    state["reading_context"], _READING_BLOCK_MAX_CHARS
                ),
                build_conversation_segments_prompt(state["conversation_segments"]),
                build_user_prompt(
                    state["message"], state["context"], state.get("annotation")
                ),
            )
            if part
        )

    async def search_facts(query: str) -> list[MemoryFact]:
        if not is_fact_query(query):
            return []
        method = getattr(deps.memory, "search_facts", None)
        if not callable(method):
            return []
        typed = cast(Callable[[str], Awaitable[list[MemoryFact]]], method)
        try:
            return await typed(query)
        except Exception:
            return []

    async def search_lessons(query: str) -> list[Memory]:
        try:
            return await deps.memory.search(query, kind=MemoryKind.LESSON)
        except Exception:
            _LOGGER.exception("经验召回失败")
            return []

    async def classify(state: ReplyState) -> dict[str, Any]:
        saved = (
            await deps.checkpointer.aget_tuple({
                "configurable": {"thread_id": state["correlation_id"]},
            }) if deps.checkpointer is not None else None
        )
        if saved is not None or state.get("skill") is not None:
            mode = ContextMode.SLOW
        else:
            mode = classify_channel(
                state["message"],
                state["state"],
                time.time(),
                state["last_slow_at"],
                deps.config.slow_threshold,
            )
        facts = (
            await search_facts(state["message"])
            if state.get("skill") is None else []
        )
        return {"mode": mode, "facts": facts}

    async def assemble(state: ReplyState) -> dict[str, Any]:
        # 慢通道专属四件事——回溯上下文截断 + 检索记忆/事实 + 取 self-narrative。
        # context 由 facade 入口朴素填（快通道用），慢通道在此按停条件重截断。
        context = build_backtrack_context(
            state["message"],
            list(deps.history),
            deps.config.max_context_len,
        )
        prior_turns = [m.content[:120] for m in context[-3:]]
        query = "\n".join([*prior_turns, state["message"]])[-300:]
        segment_search = getattr(deps.memory, "search_conversation_segments", None)
        recalled_segments: list[str] = []
        if state.get("skill") is None and callable(segment_search):
            segment_fn = cast(
                Callable[[str, int], Awaitable[list[Any]]], segment_search
            )
            segments = await segment_fn(query, 20)
            recall_segment = getattr(
                deps.memory, "record_conversation_segment_recall", None
            )
            rendered = ""
            context_contents = {item.content for item in context}
            for segment in segments:
                if segment.raw_content in context_contents:
                    continue
                candidate = [*recalled_segments, segment.raw_content]
                next_rendered = build_conversation_segments_prompt(candidate)
                if next_rendered == rendered:
                    break
                recalled_segments.append(segment.raw_content)
                rendered = next_rendered
                if callable(recall_segment):
                    recall_fn = cast(
                        Callable[[str, str], Awaitable[None]], recall_segment
                    )
                    await recall_fn(segment.id, state["correlation_id"])
        # 普通聊天只召回连续对话段原文；阅读/网页知识不进入这里，
        # 旧的场景 Memory 也不再作为聊天记忆来源。
        memories = await search_lessons(query)
        facts = state["facts"]
        narrative = await deps.inner_life.get_narrative()
        return {
            "context": context, "conversation_segments": recalled_segments,
            "memories": memories, "facts": facts,
            "narrative": narrative,
        }

    tool_graph = build_tool_graph(deps)

    async def use_tools(state: ReplyState) -> dict[str, Any]:
        skill = state.get("skill")
        config: RunnableConfig = {
            "configurable": {"thread_id": state["correlation_id"]},
            "recursion_limit": 200,
        }
        snapshot = await tool_graph.aget_state(config)
        if snapshot.values:
            result = (
                await tool_graph.ainvoke(None, config, durability="sync")
                if snapshot.next else snapshot.values
            )
        else:
            schemas = (
                deps.tools.schema(skill.tool_names) if skill else [
                    schema for schema in deps.tools.schema()
                    if schema["function"]["name"] not in _DISCUSSION_TOOL_NAMES
                ]
            )
            initial: ToolState = {
                "correlation_id": state["correlation_id"], "query": state["message"],
                "system": build_system_prompt(
                    deps.canon, state["state"], state["narrative"], state["memories"],
                    facts=state["facts"],
                    ask_guidance=_ask_guidance_for(state["mode"], deps.ask_guidance),
                    knowledge_boundary=deps.knowledge_boundary,
                    intent=state["intent"],
                    discussion_fragment=skill.system_fragment if skill else None,
                    temporal_context=state["temporal_context"],
                    activity_context=state["activity_context"],
                ),
                "user": user_prompt(state),
                "task": DISCUSSION_USE_TOOLS_TASK if skill else USE_TOOLS_TASK,
                "schemas": schemas, "plan": None, "rounds": 0, "replans": 0,
                "replan_reason": None, "calls": [], "call_id": None, "cursor": 0,
                "pending": None, "records": {}, "notices": [], "outputs": [],
                "recall_candidates": {},
            }
            result = await tool_graph.ainvoke(initial, config, durability="sync")
        saved_state = cast(ToolState, result)
        return {
            "tool_outputs": saved_state["outputs"],
            "tool_feedback": execution_feedback(saved_state),
            "tool_recall_candidates": list(saved_state["recall_candidates"].items()),
        }

    def text_events(
        state: ReplyState,
        think: list[str],
        speak: list[str],
        *,
        omit_final_speak: bool = False,
    ) -> list[Event]:
        events: list[Event] = []
        final_index = len(speak) - 1
        for index, (thought, spoken) in enumerate(zip(think, speak)):
            if thought:
                events.append(
                    internal_text_event(
                        EventType.THINK, thought, state["correlation_id"]
                    )
                )
            if not (omit_final_speak and index == final_index):
                events.append(
                    internal_text_event(
                        EventType.SPEAK, spoken, state["correlation_id"]
                    )
                )
        return events

    async def respond(state: ReplyState) -> dict[str, Any]:
        skill = state.get("skill")
        memories = (
            await search_lessons(state["message"])
            if state["mode"] is ContextMode.SLOW else state["memories"]
        )
        system = build_system_prompt(
            deps.canon, state["state"], state["narrative"], memories,
            facts=state["facts"],
            ask_guidance=_ask_guidance_for(state["mode"], deps.ask_guidance),
            tool_outputs=state["tool_outputs"],
            tool_feedback=state.get("tool_feedback", ""),
            knowledge_boundary=deps.knowledge_boundary,
            intent=state["intent"],
            discussion_fragment=skill.system_fragment if skill else None,
            temporal_context=state["temporal_context"],
            activity_context=state["activity_context"],
        )
        if state["tool_outputs"] or state.get("tool_feedback"):
            system += "\n\n" + TOOL_REPLY_GUIDANCE
        user = user_prompt(state)
        # 前几轮 think/speak（等长）
        prior = _rounds_block(state["think"], state["speak"])
        task = RESPOND_TASK_CONTINUE if state["speak"] else RESPOND_TASK
        user = "\n".join(p for p in (prior, user, task) if p)
        try:
            for segment_id, rendered in state["tool_recall_candidates"]:
                if rendered not in system:
                    continue
                try:
                    await deps.memory.record_conversation_segment_recall(
                        segment_id, state["correlation_id"]
                    )
                except Exception:
                    _LOGGER.exception("对话段召回计数失败")
            output = await deps.llm.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                module="expression",
                output_type="reply",
                correlation_id=state["correlation_id"],
                json_mode=True,
            )
            try:
                think, speak, needs_continuation = _parse_reply(output.content)
            except ValueError:
                retry_user = (
                    user
                    + "\n上一次输出无法解析。只输出合法 JSON 对象，"
                    "必须包含非空字符串 speak；"
                    "不要输出 Markdown、解释或代码围栏。"
                )
                output = await deps.llm.complete(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": retry_user},
                    ],
                    module="expression",
                    output_type="reply",
                    correlation_id=state["correlation_id"],
                    json_mode=True,
                )
                think, speak, needs_continuation = _parse_reply(output.content)
            await deps.evaluator.evaluate(_voice_output(output, "think", think))
            await deps.evaluator.evaluate(_voice_output(output, "speak", speak))
        except Exception:
            fallback = "我刚才有点卡住了，先让我缓一缓。"
            event = internal_text_event(
                EventType.SPEAK, fallback, state["correlation_id"]
            )
            event.content["response_kind"] = "fallback"
            event.content["attempt_id"] = None
            await deps.commit_reply_events(
                [event], state["answered_attempt"], None
            )
            return {
                "think": [],
                "speak": [fallback],
                "needs_continuation": False,
                "fallback": True,
            }
        new_think = state["think"] + [think]
        new_speak = state["speak"] + [speak]
        if state["mode"] is ContextMode.FAST:
            if _is_question(speak):
                # 快通道绕过 should_ask，问句结尾也落 ask（信号不丢）
                await deps.register_question(
                    speak,
                    InteractionKind.CHAT_ASK,
                    state["correlation_id"],
                    state["correlation_id"],
                    claimed_return=state["claimed_return"],
                    preceding_events=text_events(
                        state, new_think, new_speak, omit_final_speak=True
                    ),
                    answered_attempt=state["answered_attempt"],
                )
                return {
                    "think": new_think,
                    "speak": new_speak,
                    "needs_continuation": False,
                    "ask": speak,
                }
            await deps.commit_reply_events(
                text_events(state, new_think, new_speak),
                state["answered_attempt"],
                state["claimed_return"],
            )
        return {
            "think": new_think,
            "speak": new_speak,
            "needs_continuation": needs_continuation,
        }

    async def should_ask(state: ReplyState) -> dict[str, Any]:
        speak = state["speak"][-1]
        if _is_question(speak):
            await deps.register_question(
                speak,
                InteractionKind.CHAT_ASK,
                state["correlation_id"],
                state["correlation_id"],
                claimed_return=state["claimed_return"],
                preceding_events=text_events(
                    state,
                    state["think"],
                    state["speak"],
                    omit_final_speak=True,
                ),
                followup_events=[],
                answered_attempt=state["answered_attempt"],
            )
            return {"ask": speak}
        final_round = state["round"] + 1 >= deps.config.slow_max_rounds
        if not state["needs_continuation"] or final_round:
            await deps.commit_reply_events(
                [
                    *text_events(state, state["think"], state["speak"]),
                ],
                state["answered_attempt"],
                state["claimed_return"],
            )
        return {"ask": None, "round": state["round"] + 1}

    async def record_message(state: ReplyState) -> dict[str, Any]:
        # 回合末按序落历史：先用户消息、后 Nyx 消息（多轮拼接）。
        # 下一轮 reply 的 assemble 从这里回溯。
        now = time.time()
        observed_activity = _message_activity(state["activity_context"])
        append_segment = getattr(deps.memory, "append_conversation_turn", None)
        append_fn: Callable[[str, str, float], Awaitable[None]] | None = None
        if callable(append_segment):
            append_fn = cast(
                Callable[[str, str, float], Awaitable[None]], append_segment
            )
            await append_fn(
                f"{state['correlation_id']}:user", state["message"], now
            )
        deps.history.append(
            Message(
                role="user",
                content=state["message"],
                timestamp=now,
                activity_id=(observed_activity.id if observed_activity else None),
                activity_type=(observed_activity.type if observed_activity else None),
                activity_summary=(
                    activity_summary(observed_activity)
                    if observed_activity is not None
                    else None
                ),
            )
        )
        nyx_text = "\n".join(state["speak"])
        if nyx_text:
            if append_fn is not None:
                await append_fn(
                    f"{state['correlation_id']}:nyx", nyx_text, now
                )
            deps.history.append(
                Message(
                    role="nyx",
                    content=nyx_text,
                    timestamp=now,
                    fast=(state["mode"] is ContextMode.FAST),
                    activity_id=(observed_activity.id if observed_activity else None),
                    activity_type=(
                        observed_activity.type if observed_activity else None
                    ),
                    activity_summary=(
                        activity_summary(observed_activity)
                        if observed_activity is not None
                        else None
                    ),
                )
            )
        return {}

    def route_after_classify(state: ReplyState) -> str:
        return "assemble_context" if state["mode"] is ContextMode.SLOW else "respond"

    def route_after_respond(state: ReplyState) -> str:
        if state["fallback"]:
            return "record_message"
        return "record_message" if state["mode"] is ContextMode.FAST else "should_ask"

    def route_after_should_ask(state: ReplyState) -> str:
        if state["ask"] is not None:
            return "record_message"
        if state["needs_continuation"] and state["round"] < deps.config.slow_max_rounds:
            return "respond"                               # 未到轮数上限，继续下一轮
        return "record_message"                            # 轮满，回合结束

    graph = StateGraph(ReplyState)
    graph.add_node("classify_channel", classify)
    graph.add_node("assemble_context", assemble)
    graph.add_node("use_tools", use_tools)
    graph.add_node("respond", respond)
    graph.add_node("should_ask", should_ask)
    graph.add_node("record_message", record_message)
    graph.set_entry_point("classify_channel")
    graph.add_conditional_edges(
        "classify_channel",
        route_after_classify,
        {"assemble_context": "assemble_context", "respond": "respond"},
    )
    graph.add_edge("assemble_context", "use_tools")
    graph.add_edge("use_tools", "respond")
    graph.add_conditional_edges(
        "respond",
        route_after_respond,
        {"record_message": "record_message", "should_ask": "should_ask"},
    )
    graph.add_conditional_edges(
        "should_ask",
        route_after_should_ask,
        {"respond": "respond", "record_message": "record_message"},
    )
    graph.add_edge("record_message", END)
    return graph.compile()
