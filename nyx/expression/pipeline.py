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
from typing import Any, Literal, TypedDict, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from nyx.config import ExpressionConfig
from nyx.enums import ContextMode, MemoryKind, UserIntent
from nyx.eval.evaluator import Evaluator
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
    bound_material_block,
    build_backtrack_context,
    build_conversation_segments_prompt,
    build_reply_query,
    build_system_prompt,
    build_user_prompt,
    format_prompt_time,
    select_prompt_segments,
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
    ActivityContext,
    ConversationSegment,
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


class ReplyState(TypedDict):
    input_mode: Literal["durable", "direct"]
    input_events: list[Event]
    input_revision: int
    input_closed: bool
    request_start_index: int
    retrieval_query: str
    reasoning_steps: list[dict[str, Any]]
    reasoning_step_count: int
    next_action: str
    draft_revision: int
    draft_event_ids: list[str]
    needs_refresh: bool
    tool_exit_reason: str
    committed_reply: Event | None
    message: str
    message_at: float | None
    sampled_at: float
    annotation_at: float | None
    round_times: list[float]
    annotation: ReplyAnnotation | None
    skill: Skill | None
    mode: ContextMode
    context: list[Message]       # 回溯上下文（facade 入口填，快慢一致；不含当前消息）
    conversation_segments: list[ConversationSegment]  # 保留来源时间，不伪装成 Message
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
    canon: str
    ask_guidance: str
    config: ExpressionConfig
    history: deque[Message]              # facade 持有的会话历史（跨 reply）
    tools: ToolRegistry                  # use_tools 节点查资料（慢通道）
    knowledge_boundary: str | None
    collect_reply_inputs: Callable[[ReplyState], Awaitable[dict[str, Any]]]
    finalize_user_reply: Callable[[ReplyState], Awaitable[str]]
    archive_user_reply: Callable[[str, ReplyState | None], Awaitable[None]]
    has_pending_reply_inputs: Callable[[list[str], str | None], Awaitable[bool]]
    checkpointer: BaseCheckpointSaver[str] | None = None


_LOGGER = logging.getLogger(__name__)


def _is_question(text: str) -> bool:
    """Compatibility wrapper for the single classifier implementation."""
    return is_question(text)


def _rounds_block(
    think: list[str], speak: list[str], timestamps: list[float] | None = None
) -> str:
    """把前面几轮的 think/speak 拼成 prompt 段（多轮累积式）。空则返回 ""。

    调用点保证 think 与 speak 等长（respond 节点在每轮起始时二者同为已完成轮数）。
    """
    if not think:
        return ""
    lines = ["[本回合前面的思考]"]
    for i, (t, s) in enumerate(zip(think, speak), start=1):
        at = timestamps[i - 1] if timestamps and i <= len(timestamps) else None
        lines.append(f"[生成时间={format_prompt_time(at)}]")
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
        if state["input_events"]:
            current = "\n\n".join(
                f"[用户输入{i + 1}；原始时间={format_prompt_time(event.timestamp)}；"
                f"{'已被明确撤回' if i < state['request_start_index'] else '有效请求'}"
                "]\n"
                + event.content["message"]
                for i, event in enumerate(state["input_events"])
            )
            current += "\n以上保留原话。以较晚纠正为准；撤回不抹除已执行动作。"
        else:
            current = state["message"]
        summaries = "\n".join(
            f"[步骤{step['id']}；输入版本{step['input_revision']}；"
            f"生成时间={format_prompt_time(step['created_at'])}；"
            + (
                "当前判断]\n"
                if step["input_revision"] == state["input_revision"]
                else "旧输入、需复核]\n"
            )
            + step["summary"]
            for step in state["reasoning_steps"]
        )
        return "\n\n".join(
            part
            for part in (
                bound_material_block(
                    f"[阅读资料原始时间=未知；本轮取用时间={format_prompt_time(state['sampled_at'])}]\n"
                    + state["reading_context"],
                    _READING_BLOCK_MAX_CHARS,
                )
                if state["reading_context"]
                else "",
                build_conversation_segments_prompt(state["conversation_segments"]),
                build_user_prompt(
                    current,
                    state["context"],
                    state.get("annotation"),
                    message_at=state["message_at"],
                    annotation_at=state["annotation_at"],
                ),
                "[工作摘要，仅为判断，不是执行事实]\n" + summaries if summaries else "",
            )
            if part
        )

    async def search_facts(query: str) -> list[MemoryFact]:
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
        return {"mode": mode}

    async def assemble(state: ReplyState) -> dict[str, Any]:
        # 快慢通道共用有界对话召回；工具、经验和自我叙事仍只在慢通道。
        backtrack = build_backtrack_context(
            state["message"],
            list(deps.history),
            deps.config.max_context_len,
        )
        context = backtrack if state["mode"] is ContextMode.SLOW else state["context"]
        texts = [e.content["message"] for e in state["input_events"]]
        query = build_reply_query(
            texts[state["request_start_index"] :] if texts else [state["message"]],
            [m.content for m in backtrack[-3:]],
        )
        segment_search = getattr(deps.memory, "search_conversation_segments", None)
        recalled_segments: list[ConversationSegment] = []
        if state.get("skill") is None and callable(segment_search):
            segment_fn = cast(
                Callable[[str, int], Awaitable[list[Any]]], segment_search
            )
            try:
                segments = await segment_fn(query, 20)
                context_text = "\n".join(
                    f"{'用户' if item.role == 'user' else '尼克斯'}：{item.content}"
                    for item in context
                )
                recalled_segments = select_prompt_segments([
                    segment for segment in segments
                    if segment.raw_content and segment.raw_content not in context_text
                ])
            except Exception:
                _LOGGER.exception("对话段召回失败")
        # 普通聊天只召回连续对话段原文；阅读/网页知识不进入这里，
        # 旧的场景 Memory 也不再作为聊天记忆来源。
        memories = (
            await search_lessons(query) if state["mode"] is ContextMode.SLOW else []
        )
        facts = (
            await search_facts(query)
            if state.get("skill") is None and is_fact_query(state["message"])
            else []
        )
        narrative = (
            await deps.inner_life.get_narrative()
            if state["mode"] is ContextMode.SLOW else None
        )
        return {
            "context": context,
            "conversation_segments": recalled_segments,
            "memories": memories,
            "facts": facts,
            "narrative": narrative,
            "retrieval_query": query,
        }

    tool_graph = build_tool_graph(deps)

    async def pending(state: ReplyState) -> bool:
        answered = state["answered_attempt"]
        return not state["input_closed"] and await deps.has_pending_reply_inputs(
            [e.id for e in state["input_events"]],
            answered.id if answered else None,
        )

    async def reason_step(state: ReplyState) -> dict[str, Any]:
        if await pending(state):
            return {"needs_refresh": True}
        count = state["reasoning_step_count"] + 1
        steps = list(state["reasoning_steps"])
        action = "answer"
        try:
            output = await deps.llm.complete(
                [
                    {
                        "role": "system",
                        "content": build_system_prompt(
                            deps.canon,
                            state["state"],
                            state["narrative"],
                            state["memories"],
                            facts=state["facts"],
                            tool_outputs=state["tool_outputs"],
                            tool_feedback=state["tool_feedback"],
                            ask_guidance=_ask_guidance_for(
                                state["mode"], deps.ask_guidance
                            ),
                            knowledge_boundary=deps.knowledge_boundary,
                            intent=state["intent"],
                            temporal_context=state["temporal_context"],
                            activity_context=state["activity_context"],
                            discussion_fragment=(
                                state["skill"].system_fragment
                                if state["skill"]
                                else None
                            ),
                        ),
                    },
                    {
                        "role": "user",
                        "content": user_prompt(state)
                        + '\n只输出 JSON：{"summary":"当前理解、约束与下一步摘要",'
                        '"next_action":"continue/tools/answer"}。'
                        "summary 非空且不超过1000字符。"
                        "不要输出详尽推理链；工具只能由后续工具子图执行。",
                    },
                ],
                module="expression",
                output_type="reason",
                json_mode=True,
                correlation_id=state["correlation_id"],
            )
            await deps.evaluator.evaluate(output)
            parsed: Any = json.loads(output.content)
            if not isinstance(parsed, dict):
                raise ValueError("分析输出不是 JSON 对象")
            data = cast(dict[str, Any], parsed)
            if (
                not isinstance(data.get("summary"), str)
                or not 1 <= len(data["summary"].strip()) <= 1000
                or data.get("next_action") not in ("continue", "tools", "answer")
            ):
                raise ValueError("分析摘要或动作非法")
            steps.append(
                {
                    "id": count,
                    "input_revision": state["input_revision"],
                    "summary": data["summary"].strip(),
                    "created_at": time.time(),
                }
            )
            action = data["next_action"]
        except Exception:
            _LOGGER.exception("分步分析失败，使用原始输入和已确认结果回答")
        # Durable input/checkpoint failures must never become model fallback.
        refresh = await pending(state)
        if count >= 3 and action == "continue":
            action = "answer"
        return {
            "reasoning_step_count": count,
            "reasoning_steps": steps,
            "next_action": action,
            "needs_refresh": refresh,
        }

    async def use_tools(state: ReplyState) -> dict[str, Any]:
        skill = state.get("skill")
        config: RunnableConfig = {
            "configurable": {"thread_id": state["correlation_id"]},
            "recursion_limit": 200,
        }
        snapshot = await tool_graph.aget_state(config)
        schemas = (
            deps.tools.schema(skill.tool_names)
            if skill
            else [
                schema
                for schema in deps.tools.schema()
                if schema["function"]["name"] not in _DISCUSSION_TOOL_NAMES
            ]
        )
        answered = state["answered_attempt"]
        update: dict[str, Any] = {
            "input_event_ids": [e.id for e in state["input_events"]],
            "input_revision": state["input_revision"],
            "input_closed": state["input_closed"],
            "answered_attempt_id": answered.id if answered else None,
            "allow_new_actions": state["next_action"] == "tools" and bool(schemas),
            "query": state["retrieval_query"],
            "system": build_system_prompt(
                deps.canon,
                state["state"],
                state["narrative"],
                state["memories"],
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
            "schemas": schemas,
        }
        if snapshot.values:
            same = (
                all(
                    snapshot.values.get(key) == update[key]
                    for key in (
                        "input_event_ids",
                        "input_closed",
                        "schemas",
                        "allow_new_actions",
                    )
                )
                and snapshot.values.get("exit_reason") != "input_pending"
            )
            if same:
                result = (
                    await tool_graph.ainvoke(None, config, durability="sync")
                    if snapshot.next
                    else snapshot.values
                )
            else:
                result = await tool_graph.ainvoke(
                    cast(ToolState, {**snapshot.values, "input_update": update}),
                    config,
                    durability="sync",
                )
        else:
            initial = cast(
                ToolState,
                {
                    **update,
                    "correlation_id": state["correlation_id"],
                    "plan": None,
                    "rounds": 0,
                    "replans": 0,
                    "replan_reason": None,
                    "calls": [],
                    "call_id": None,
                    "cursor": 0,
                    "pending": None,
                    "records": {},
                    "notices": [],
                    "outputs": [],
                    "recall_candidates": {},
                    "exit_reason": "complete",
                },
            )
            result = await tool_graph.ainvoke(initial, config, durability="sync")
        saved = cast(ToolState, result)
        return {
            "tool_outputs": saved["outputs"],
            "tool_feedback": execution_feedback(saved),
            "tool_recall_candidates": list(saved["recall_candidates"].items()),
            "tool_exit_reason": saved.get("exit_reason", "complete"),
            "next_action": "after_tools" if update["allow_new_actions"] else "answer",
        }

    async def respond(state: ReplyState) -> dict[str, Any]:
        if await pending(state):
            return {"needs_refresh": True}
        skill = state.get("skill")
        memories = (
            await search_lessons(state["retrieval_query"])
            if state["mode"] is ContextMode.SLOW
            else state["memories"]
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
        prior = _rounds_block(state["think"], state["speak"], state["round_times"])
        task = RESPOND_TASK_CONTINUE if state["speak"] else RESPOND_TASK
        user = "\n".join(p for p in (prior, user, task) if p)
        if not state["speak"]:
            for segment in state["conversation_segments"]:
                try:
                    await deps.memory.record_conversation_segment_recall(
                        segment.id, state["correlation_id"]
                    )
                except Exception:
                    _LOGGER.exception("对话段召回计数失败")
        for segment_id, rendered in state["tool_recall_candidates"]:
            if rendered not in system:
                continue
            try:
                await deps.memory.record_conversation_segment_recall(
                    segment_id, state["correlation_id"]
                )
            except Exception:
                _LOGGER.exception("对话段召回计数失败")
        think, speak, needs_continuation = "", "我刚才有点卡住了，先让我缓一缓。", False
        fallback = True
        for _attempt in range(2):
            # Input I/O is outside the model failure boundary, including parse retries.
            if await pending(state):
                return {"needs_refresh": True}
            try:
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
            except Exception:
                break
            try:
                parsed_reply = _parse_reply(output.content)
            except ValueError:
                # Invalid model output still contributes usage records.
                await deps.evaluator.evaluate(output)
                user += (
                    "\n上一次输出无法解析。只输出合法 JSON 对象，"
                    "必须包含非空字符串 speak；不要输出 Markdown 或代码围栏。"
                )
                continue
            try:
                await deps.evaluator.evaluate(
                    _voice_output(output, "think", parsed_reply[0])
                )
                await deps.evaluator.evaluate(
                    _voice_output(output, "speak", parsed_reply[1])
                )
            except Exception:
                break
            think, speak, needs_continuation = parsed_reply
            fallback = False
            break
        return {
            "think": [] if fallback else [*state["think"], think],
            "speak": [speak] if fallback else [*state["speak"], speak],
            "round_times": [*state["round_times"], time.time()],
            "needs_continuation": needs_continuation,
            "fallback": fallback,
            "draft_revision": state["input_revision"],
            "draft_event_ids": [e.id for e in state["input_events"]],
            "needs_refresh": await pending(state),
        }

    def should_ask(state: ReplyState) -> dict[str, Any]:
        question = not state["fallback"] and _is_question(state["speak"][-1])
        return {
            "ask": "\n".join(state["speak"]) if question else None,
            "round": state["round"] + 1,
        }

    async def finalize_reply(state: ReplyState) -> dict[str, Any]:
        result = await deps.finalize_user_reply(state)
        return {
            "needs_refresh": result == "refresh",
            "committed_reply": state["committed_reply"],
        }

    async def record_message(state: ReplyState) -> dict[str, Any]:
        await deps.archive_user_reply(state["correlation_id"], state)
        return {}

    async def route_after_assemble(state: ReplyState) -> str:
        if await pending(state):
            return "collect_inputs"
        if state["fallback"]:
            return "finalize_reply"
        if state["mode"] is ContextMode.FAST:
            return "respond"
        return (
            "reason_step"
            if (
                state["reasoning_step_count"] < 3
                and state["reasoning_step_count"] == len(state["reasoning_steps"])
            )
            else "use_tools"
        )

    def route_after_reason(state: ReplyState) -> str:
        if state["needs_refresh"] or state["next_action"] == "continue":
            return "collect_inputs"
        return "use_tools"

    def route_after_tools(state: ReplyState) -> str:
        if state["tool_exit_reason"] == "input_pending":
            return "collect_inputs"
        return "collect_inputs" if state["next_action"] == "after_tools" else "respond"

    def route_after_respond(state: ReplyState) -> str:
        return "collect_inputs" if state["needs_refresh"] else "should_ask"

    def route_after_should_ask(state: ReplyState) -> str:
        if (
            state["mode"] is ContextMode.SLOW
            and not state["fallback"]
            and state["ask"] is None
            and state["needs_continuation"]
            and state["round"] < deps.config.slow_max_rounds
        ):
            return "respond"
        return "finalize_reply"

    graph = StateGraph(ReplyState)

    async def collect_inputs(state: ReplyState) -> dict[str, Any]:
        return await deps.collect_reply_inputs(state)

    def route_after_finalize(state: ReplyState) -> str:
        return "collect_inputs" if state["needs_refresh"] else "record_message"

    graph.add_node("collect_inputs", collect_inputs)
    graph.add_node("classify_channel", classify)
    graph.add_node("assemble_context", assemble)
    graph.add_node("reason_step", reason_step)
    graph.add_node("use_tools", use_tools)
    graph.add_node("respond", respond)
    graph.add_node("should_ask", should_ask)
    graph.add_node("finalize_reply", finalize_reply)
    graph.add_node("record_message", record_message)
    graph.set_entry_point("collect_inputs")
    graph.add_edge("collect_inputs", "classify_channel")
    graph.add_edge("classify_channel", "assemble_context")
    graph.add_conditional_edges("assemble_context", route_after_assemble)
    graph.add_conditional_edges("reason_step", route_after_reason)
    graph.add_conditional_edges("use_tools", route_after_tools)
    graph.add_conditional_edges("respond", route_after_respond)
    graph.add_conditional_edges("should_ask", route_after_should_ask)
    graph.add_conditional_edges("finalize_reply", route_after_finalize)
    graph.add_edge("record_message", END)
    return graph.compile()
