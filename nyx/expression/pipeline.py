# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# langgraph 类型标注松散：add_node/compile/ainvoke 返回部分未知、graph.state 缺 stub
"""回复流程 LangGraph 图：快慢通道 + 多轮 think/speak + 场景化记忆。

节点为闭包，依赖经 ReplyDeps 注入。
"""
import json
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
from nyx.expression.prompt import (
    build_backtrack_context,
    build_system_prompt,
    build_user_prompt,
)
from nyx.inner_life.facade import InnerLifeFacade
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.tools.registry import ToolRegistry
from nyx.types import CurrentState, Memory, Message, SelfNarrative


def _ask_guidance_for(mode: ContextMode, ask_guidance: str | None) -> str | None:
    """仅慢通道注入 ask_guidance；快通道（speak 快速分支）不注入。"""
    return ask_guidance if mode is ContextMode.SLOW else None


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
    correlation_id: str          # 本次 reply 溯源（17 补，对齐 14-activity）
    last_slow_at: float          # 上次慢通道时间（facade 维护，每 reply 入 state）
    tool_outputs: list[str]      # use_tools 查到的工具结果（慢通道专属）


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


_THINK_TASK = (
    "（只写你此刻心里的念头：在想什么、在犹豫什么、什么感觉还没成形。"
    "这是你脑子里的内心活动，不是说给用户听的话——不要称呼用户，"
    "不要写成完整通顺的句子。）"
)
_THINK_TASK_CONTINUE = (
    "（接着你刚才的念头再往里想一层：它又让你想到什么、哪里还没想通、"
    "你在犹豫什么。不要复述之前想过的，也不要开始对用户说话。）"
)
_SPEAK_TASK = (
    "（把你最想对用户说的，先说出一句。不用一次把话说完，"
    "后面会接着往下说。）"
)
_SPEAK_TASK_CONTINUE = (
    "（接着你上面已经说出口的话，再往下说一句——补充、解释，"
    "或往深里说一层。不要重复已经说过的意思。）"
)
_USE_TOOLS_TASK = (
    "你可以调用工具查询信息（本地搜索、读写文件、联网搜索）。"
    "本次回复若需要查询资料，就调用相应工具；"
    "若不需要查询，就不要调用任何工具。"
)
_TOOL_OUTPUT_MAX_CHARS = 4000  # 单条工具结果注入 prompt 的字符上限（decision，可推翻）


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
        # 慢通道专属三件事——回溯上下文截断 + 检索记忆 + 取 self-narrative。
        # context 由 facade 入口朴素填（快通道用），慢通道在此按停条件重截断。
        context = build_backtrack_context(
            state["message"],
            list(deps.history),
            time.time(),
            deps.config.context_time_gap,
            deps.config.max_context_len,
        )
        memories = await deps.memory.search(state["message"])
        for m in memories:
            await deps.memory.record_recall(m.id)   # 慢通道检索命中即记「想起」
        narrative = await deps.inner_life.get_narrative()
        return {"context": context, "memories": memories, "narrative": narrative}

    async def use_tools(state: ReplyState) -> dict[str, Any]:
        # 慢通道专属：问 LLM 是否需查资料（文件/搜索），查到的结果拼进 tool_outputs，
        # think/speak 再据此回复。一轮，不做「查完再决定继续查」的循环。
        system = build_system_prompt(
            deps.canon, state["state"], state["narrative"], state["memories"],
            ask_guidance=_ask_guidance_for(state["mode"], deps.ask_guidance),
        )
        user = (
            build_user_prompt(state["message"], state["context"])
            + "\n"
            + _USE_TOOLS_TASK
        )
        output = await deps.llm.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            module="expression",
            output_type="tool",
            correlation_id=state["correlation_id"],
            tools=deps.tools.schema(),
        )
        await deps.evaluator.evaluate(output)
        outputs: list[str] = []
        for tc in output.tool_calls:
            name = tc.get("name", "")
            args = tc.get("args", {})
            try:
                result = await deps.tools.call(name, args)
                text = json.dumps(result, ensure_ascii=False)
                if len(text) > _TOOL_OUTPUT_MAX_CHARS:
                    text = text[:_TOOL_OUTPUT_MAX_CHARS] + "…"
            except Exception:  # 工具执行失败不崩回复（best-effort 豁免）
                text = f"工具 {name} 执行失败"
            outputs.append(f"{name}: {text}")
        return {"tool_outputs": outputs}

    async def think(state: ReplyState) -> dict[str, Any]:
        system = build_system_prompt(
            deps.canon, state["state"], state["narrative"], state["memories"],
            ask_guidance=_ask_guidance_for(state["mode"], deps.ask_guidance),
            tool_outputs=state["tool_outputs"],
        )
        user = build_user_prompt(state["message"], state["context"])
        prior = _rounds_block(state["think"], state["speak"])      # 前几轮 think/speak
        task = _THINK_TASK_CONTINUE if state["think"] else _THINK_TASK
        user = "\n".join(p for p in (prior, user, task) if p)
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
            deps.canon, state["state"], state["narrative"], state["memories"],
            ask_guidance=_ask_guidance_for(state["mode"], deps.ask_guidance),
            tool_outputs=state["tool_outputs"],
        )
        user = build_user_prompt(state["message"], state["context"])
        # 前几轮（不含本轮 think）
        prior = _rounds_block(state["think"][:-1], state["speak"])
        inner = f"[我刚刚的内心想法]\n{state['think'][-1]}"           # 本轮 think
        task = _SPEAK_TASK_CONTINUE if state["speak"] else _SPEAK_TASK
        user = "\n".join(p for p in (prior, inner, user, task) if p)
        output = await deps.llm.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            module="expression",
            output_type="speak",
            correlation_id=state["correlation_id"],
        )
        await deps.evaluator.evaluate(output)
        speak = output.content
        if state["mode"] is ContextMode.FAST:
            if _is_question(speak):
                # 快通道绕过 should_ask，问句结尾也落 ask（信号不丢）
                await deps.bus.publish(
                    internal_text_event(EventType.ASK, speak, state["correlation_id"])
                )
                return {"speak": state["speak"] + [speak], "ask": speak}
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
            deps.history.append(
                Message(
                    role="nyx",
                    content=nyx_text,
                    timestamp=now,
                    fast=(state["mode"] is ContextMode.FAST),
                )
            )
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
    graph.add_node("use_tools", use_tools)
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
    graph.add_edge("assemble_context", "use_tools")
    graph.add_edge("use_tools", "think")
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
