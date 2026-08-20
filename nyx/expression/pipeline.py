# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# langgraph 类型标注松散：add_node/compile/ainvoke 返回部分未知、graph.state 缺 stub
"""回复流程 LangGraph 图：快慢通道 + 多轮 think/speak + 场景化记忆。

节点为闭包，依赖经 ReplyDeps 注入。
"""
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from nyx.config import ExpressionConfig
from nyx.enums import ContextMode, EventType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import internal_text_event
from nyx.expression.classifier import QUESTION_MARKS, classify_channel
from nyx.expression.prompt import build_system_prompt, build_user_prompt
from nyx.inner_life.facade import InnerLifeFacade
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.types import CurrentState, Memory, Message, SelfNarrative


class ReplyState(TypedDict):
    message: str
    mode: ContextMode
    context: list[Message]       # 回溯上下文（facade 入口填，快慢一致；不含当前消息）
    memories: list[Memory]       # 检索到的记忆
    state: CurrentState          # 当前状态快照
    narrative: SelfNarrative | None   # 慢通道 assemble 填充，快通道恒 None
    think: list[str]             # 累积：每轮 think 追加（17 改，tech-ref §6.1 ripple）
    speak: list[str]             # 累积：每轮 speak 追加（17 改，tech-ref §6.1 ripple）
    ask: str | None
    round: int                   # 已完成 think/speak 的轮数（≤ slow_max_rounds）
    waiting_user: bool           # MVP 恒 False
    correlation_id: str          # 本次 reply 溯源（17 补，对齐 14-activity）
    last_slow_at: float          # 上次慢通道时间（facade 维护，每 reply 入 state）


@dataclass
class ReplyDeps:
    llm: LlmClient
    evaluator: Evaluator
    memory: MemoryFacade
    inner_life: InnerLifeFacade          # 慢通道 assemble 时取 narrative
    bus: EventBus
    canon: str
    config: ExpressionConfig
    history: deque[Message]              # facade 持有的会话历史（跨 reply）


_THINK_TASK = "（只输出你此刻的内心独白，不要输出给用户看。）"
_SPEAK_TASK = "（只输出你要对用户说的一句话。）"


def _is_question(text: str) -> bool:
    """speak 是否问句（纯函数）：含疑问词即视为问句。

    词表单一来源 = 16 classifier 的 QUESTION_MARKS。
    """
    return any(w in text for w in QUESTION_MARKS)


def _rounds_block(think: list[str], speak: list[str]) -> str:
    """把前面几轮的 think/speak 拼成 prompt 段（多轮累积式）。空则返回 ""。

    调用点保证 think 与 speak 等长（think 节点传完整累积，
    speak 节点传 think[:-1] 与 speak）。
    """
    if not think:
        return ""
    lines = ["[本回合前面的思考]"]
    for i, (t, s) in enumerate(zip(think, speak), start=1):
        lines.append(f"第{i}轮内心：{t}")
        lines.append(f"第{i}轮对外：{s}")
    return "\n".join(lines)


def build_reply_graph(deps: ReplyDeps) -> CompiledStateGraph[ReplyState]:
    """构建回复流程图。节点闭包捕获稳定依赖 deps；每 reply 变化的
    correlation_id/last_slow_at 走 state（图可复用，见 facade.__init__）。
    """

    async def classify(state: ReplyState) -> dict[str, Any]:
        mode = classify_channel(
            state["message"],
            state["state"],
            time.time(),
            state["last_slow_at"],
            deps.config.slow_threshold,
        )
        return {"mode": mode}

    async def assemble(state: ReplyState) -> dict[str, Any]:
        # 对话历史不再在此回溯：context 由 facade 入口统一填（快慢通道一致）。
        # 这里只做慢通道专属的两件事——检索记忆 + 取 self-narrative。
        memories = await deps.memory.search(state["message"])
        narrative = await deps.inner_life.get_narrative()
        return {"memories": memories, "narrative": narrative}

    async def think(state: ReplyState) -> dict[str, Any]:
        system = build_system_prompt(
            deps.canon, state["state"], state["narrative"], state["memories"]
        )
        user = build_user_prompt(state["message"], state["context"])
        prior = _rounds_block(state["think"], state["speak"])      # 前几轮 think/speak
        user = "\n".join(p for p in (prior, user, _THINK_TASK) if p)
        output = await deps.llm.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            module="expression",
            output_type="think",
            correlation_id=state["correlation_id"],
        )
        await deps.evaluator.evaluate(output)
        await deps.bus.publish(
            internal_text_event(
                EventType.THINK, output.content, state["correlation_id"]
            )
        )
        return {"think": state["think"] + [output.content]}

    async def speak(state: ReplyState) -> dict[str, Any]:
        system = build_system_prompt(
            deps.canon, state["state"], state["narrative"], state["memories"]
        )
        user = build_user_prompt(state["message"], state["context"])
        # 前几轮（不含本轮 think）
        prior = _rounds_block(state["think"][:-1], state["speak"])
        inner = f"[我刚刚的内心想法]\n{state['think'][-1]}"           # 本轮 think
        user = "\n".join(p for p in (prior, inner, user, _SPEAK_TASK) if p)
        output = await deps.llm.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            module="expression",
            output_type="speak",
            correlation_id=state["correlation_id"],
        )
        await deps.evaluator.evaluate(output)
        speak = output.content
        if state["mode"] is ContextMode.FAST:
            await deps.bus.publish(
                internal_text_event(EventType.SPEAK, speak, state["correlation_id"])
            )
        return {"speak": state["speak"] + [speak]}

    async def should_ask(state: ReplyState) -> dict[str, Any]:
        speak = state["speak"][-1]
        if _is_question(speak):
            await deps.bus.publish(
                internal_text_event(EventType.ASK, speak, state["correlation_id"])
            )
            return {"ask": speak}
        await deps.bus.publish(
            internal_text_event(EventType.SPEAK, speak, state["correlation_id"])
        )
        return {"ask": None, "round": state["round"] + 1}

    async def record_message(state: ReplyState) -> dict[str, Any]:
        # 回合末按序落历史：先用户消息、后 Nyx 消息（多轮拼接）。
        # 下一轮 reply 的 assemble 从这里回溯。
        now = time.time()
        deps.history.append(
            Message(role="user", content=state["message"], timestamp=now)
        )
        nyx_text = "\n".join(state["speak"])
        if nyx_text:
            deps.history.append(Message(role="nyx", content=nyx_text, timestamp=now))
        return {}

    async def generate_scene_memory(state: ReplyState) -> dict[str, Any]:
        await deps.memory.create_scene_memory({
            "correlation_id": state["correlation_id"],
            "user_message": state["message"],
            "nyx_think": "\n".join(state["think"]),
            "nyx_speak": "\n".join(state["speak"]),
        })
        return {}

    def route_after_classify(state: ReplyState) -> str:
        return "assemble_context" if state["mode"] is ContextMode.SLOW else "think"

    def route_after_speak(state: ReplyState) -> str:
        return "record_message" if state["mode"] is ContextMode.FAST else "should_ask"

    def route_after_should_ask(state: ReplyState) -> str:
        if state["ask"] is not None:
            # 问句：回合结束，也记场景化记忆
            return "generate_scene_memory"
        if state["round"] < deps.config.slow_max_rounds:
            return "think"                                 # 未到轮数上限，继续下一轮
        return "generate_scene_memory"                     # 轮满，回合结束

    graph = StateGraph(ReplyState)
    graph.add_node("classify_channel", classify)
    graph.add_node("assemble_context", assemble)
    graph.add_node("think", think)
    graph.add_node("speak", speak)
    graph.add_node("should_ask", should_ask)
    graph.add_node("record_message", record_message)
    graph.add_node("generate_scene_memory", generate_scene_memory)
    graph.set_entry_point("classify_channel")
    graph.add_conditional_edges(
        "classify_channel",
        route_after_classify,
        {"assemble_context": "assemble_context", "think": "think"},
    )
    graph.add_edge("assemble_context", "think")
    graph.add_edge("think", "speak")
    graph.add_conditional_edges(
        "speak",
        route_after_speak,
        {"record_message": "record_message", "should_ask": "should_ask"},
    )
    graph.add_conditional_edges(
        "should_ask",
        route_after_should_ask,
        {"think": "think", "generate_scene_memory": "generate_scene_memory"},
    )
    graph.add_edge("generate_scene_memory", "record_message")
    graph.add_edge("record_message", END)
    return graph.compile()
