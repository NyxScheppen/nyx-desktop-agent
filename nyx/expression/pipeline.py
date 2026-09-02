# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# langgraph 类型标注松散：add_node/compile/ainvoke 返回部分未知、graph.state 缺 stub
"""回复流程 LangGraph 图：快慢通道 + 多轮 think/speak + 场景化记忆。

节点为闭包，依赖经 ReplyDeps 注入。
"""
import json
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, TypedDict, cast

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
from nyx.types import CurrentState, LLMOutput, Memory, Message, SelfNarrative


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


# 一轮 think+speak 一次生成：先写内心活动（think），再写说出口的话（speak），
# JSON 结构化输出（json_mode），解析后仍分开发 THINK/SPEAK/ASK 事件。
_RESPOND_TASK = (
    "（先写你此刻心里的念头——只写内心活动：在想什么、在犹豫什么、什么感觉还没成形，"
    "用第一人称「我」，不要称呼用户，不要写成完整通顺的句子，"
    "不要用「她」或「尼克斯」称呼自己；"
    "再写你最想对用户说出口的第一句话，不用一次把话说完。"
    '以 JSON 输出，形如 {"think": "内心活动", "speak": "说出口的话"}。）'
)
_RESPOND_TASK_CONTINUE = (
    "（接着上一轮：先再往深里想一层你的念头——不要复述之前想过的，继续第一人称「我」；"
    "再接着你上面已经说出口的话，往下说一句——补充、解释或往深里说，不要重复。"
    '以 JSON 输出，形如 {"think": "内心活动", "speak": "说出口的话"}。）'
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

    调用点保证 think 与 speak 等长（respond 节点在每轮起始时二者同为已完成轮数）。
    """
    if not think:
        return ""
    lines = ["[本回合前面的思考]"]
    for i, (t, s) in enumerate(zip(think, speak), start=1):
        lines.append(f"第{i}轮内心：{t}")
        lines.append(f"第{i}轮对外：{s}")
    return "\n".join(lines)


def _parse_reply(raw: str) -> tuple[str, str]:
    """解析 respond 节点 JSON 产出 → (think, speak)。结构非法抛 ValueError。

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
    return think.strip(), speak.strip()


def _voice_output(output: LLMOutput, type_: str, content: str) -> LLMOutput:
    """用解析出的 think/speak 文本重造 LLMOutput，供 evaluator 分别跑 OOC。"""
    return LLMOutput(
        module=output.module,
        type=type_,
        model=output.model,
        content=content,
        correlation_id=output.correlation_id,
        tool_calls=[],
    )


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

    async def respond(state: ReplyState) -> dict[str, Any]:
        system = build_system_prompt(
            deps.canon, state["state"], state["narrative"], state["memories"],
            ask_guidance=_ask_guidance_for(state["mode"], deps.ask_guidance),
            tool_outputs=state["tool_outputs"],
        )
        user = build_user_prompt(state["message"], state["context"])
        # 前几轮 think/speak（等长）
        prior = _rounds_block(state["think"], state["speak"])
        task = _RESPOND_TASK_CONTINUE if state["speak"] else _RESPOND_TASK
        user = "\n".join(p for p in (prior, user, task) if p)
        output = await deps.llm.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            module="expression",
            output_type="reply",
            correlation_id=state["correlation_id"],
            json_mode=True,
        )
        try:
            think, speak = _parse_reply(output.content)
        except ValueError:
            # best-effort：LLM 未吐合法 JSON → 整段当 speak（think 留空），不吞话。
            think, speak = "", output.content.strip()
        await deps.evaluator.evaluate(_voice_output(output, "think", think))
        await deps.evaluator.evaluate(_voice_output(output, "speak", speak))
        if think:
            await deps.bus.publish(
                internal_text_event(EventType.THINK, think, state["correlation_id"])
            )
        new_think = state["think"] + [think]
        new_speak = state["speak"] + [speak]
        if state["mode"] is ContextMode.FAST:
            if _is_question(speak):
                # 快通道绕过 should_ask，问句结尾也落 ask（信号不丢）
                await deps.bus.publish(
                    internal_text_event(EventType.ASK, speak, state["correlation_id"])
                )
                return {"think": new_think, "speak": new_speak, "ask": speak}
            await deps.bus.publish(
                internal_text_event(EventType.SPEAK, speak, state["correlation_id"])
            )
        return {"think": new_think, "speak": new_speak}

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
        return "assemble_context" if state["mode"] is ContextMode.SLOW else "respond"

    def route_after_respond(state: ReplyState) -> str:
        return "record_message" if state["mode"] is ContextMode.FAST else "should_ask"

    def route_after_should_ask(state: ReplyState) -> str:
        if state["ask"] is not None:
            # 问句：回合结束，也记场景化记忆
            return "generate_scene_memory"
        if state["round"] < deps.config.slow_max_rounds:
            return "respond"                               # 未到轮数上限，继续下一轮
        return "generate_scene_memory"                     # 轮满，回合结束

    graph = StateGraph(ReplyState)
    graph.add_node("classify_channel", classify)
    graph.add_node("assemble_context", assemble)
    graph.add_node("use_tools", use_tools)
    graph.add_node("respond", respond)
    graph.add_node("should_ask", should_ask)
    graph.add_node("record_message", record_message)
    graph.add_node("generate_scene_memory", generate_scene_memory)
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
        {"respond": "respond", "generate_scene_memory": "generate_scene_memory"},
    )
    graph.add_edge("generate_scene_memory", "record_message")
    graph.add_edge("record_message", END)
    return graph.compile()
