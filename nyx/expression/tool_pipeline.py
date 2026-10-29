# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false, reportUnknownMemberType=false
"""慢通道工具子图；每次调用及重试都有独立的持久恢复边界。"""

import asyncio
import json
import logging
import time
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, TypedDict, cast
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from nyx.enums import MemoryKind
from nyx.expression.prompt import (
    _MATERIAL_ITEM_MAX_CHARS,
    _TOOL_BLOCK_MAX_CHARS,
    _TOOL_OUTPUT_MAX_ITEMS,
    PLAN_TASK,
    TOOL_DECISION_FAILED,
    _memory_block,
    _tool_outputs_block,
    bound_material_block,
)
from nyx.tools.registry import is_read_only_call
from nyx.types import Plan, PlanStep

if TYPE_CHECKING:
    from nyx.expression.pipeline import ReplyDeps

_LOGGER = logging.getLogger(__name__)
_MAX_TOOL_ROUNDS = 4
_MAX_REPLANS = 1
_MAX_TOOL_ATTEMPTS = 3


class ToolState(TypedDict):
    correlation_id: str
    query: str
    system: str
    user: str
    task: str
    schemas: list[dict[str, Any]]
    plan: dict[str, Any] | None
    rounds: int
    replans: int
    replan_reason: str | None
    calls: list[dict[str, Any]]
    call_id: str | None
    cursor: int
    pending: dict[str, Any] | None
    records: dict[str, dict[str, Any]]
    notices: list[str]
    outputs: list[str]
    recall_candidates: dict[str, str]


def _parse_plan(raw: str) -> Plan | None:
    """Malformed model plans fall back to the existing ReAct path."""
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    parsed = cast(dict[str, Any], data)
    goal = parsed.get("goal")
    steps = parsed.get("steps")
    if not isinstance(goal, str) or not isinstance(steps, list) or not steps:
        return None
    result: list[PlanStep] = []
    for item in cast(list[Any], steps):
        if not isinstance(item, dict):
            return None
        step = cast(dict[str, Any], item)
        text, tool, condition = (
            step.get("text"), step.get("expected_tool"), step.get("done_condition")
        )
        if (
            not isinstance(text, str) or not isinstance(condition, str)
            or (tool is not None and not isinstance(tool, str))
        ):
            return None
        result.append(PlanStep(text, tool, condition))
    return Plan(goal, result)


def execution_feedback(state: ToolState) -> str:
    """Keep actual call status separate from natural-language plan completion."""
    records = [
        {key: record[key] for key in (
            "step", "name", "status", "attempts", "errors", "args",
        )}
        for record in reversed(list(state["records"].values()))
    ]
    text = "\n".join(state["notices"][-5:])
    if records:
        text += "\n[实际调用步骤状态]\n" + json.dumps(records, ensure_ascii=False)
    return bound_material_block(text.strip(), _TOOL_BLOCK_MAX_CHARS // 2)


def build_tool_graph(deps: "ReplyDeps") -> CompiledStateGraph[ToolState]:
    """Build the existing plan/execute loop with one checkpoint per tool attempt."""

    async def plan(state: ToolState) -> dict[str, Any]:
        system = state["system"]
        user = state["user"] + "\n" + PLAN_TASK
        user += "\n授权工具：" + json.dumps(state["schemas"], ensure_ascii=False)
        if state["replan_reason"] is not None:
            try:
                lessons = await deps.memory.search(
                    state["query"], kind=MemoryKind.LESSON,
                )
                if lessons:
                    system += "\n\n" + _memory_block(lessons)
            except Exception:
                _LOGGER.exception("重规划经验召回失败")
            user += (
                "\n上次计划：" + json.dumps(state["plan"], ensure_ascii=False)
                + "\n重规划原因：" + state["replan_reason"]
                + "\n" + _tool_outputs_block(
                    state["outputs"], execution_feedback(state),
                )
            )
        parsed = None
        try:
            output = await deps.llm.complete(
                [{"role": "system", "content": system},
                 {"role": "user", "content": user}],
                module="expression", output_type="plan", json_mode=True,
                correlation_id=state["correlation_id"],
            )
            await deps.evaluator.evaluate(output)
            parsed = _parse_plan(output.content)
        except Exception:
            _LOGGER.exception("规划失败 correlation_id=%s", state["correlation_id"])
        return {
            "system": system, "plan": asdict(parsed) if parsed else None,
            "replan_reason": None, "rounds": 0,
        }

    async def decide(state: ToolState) -> dict[str, Any]:
        user = state["user"] + "\n" + state["task"]
        if state["plan"] is not None:
            user += "\n[当前计划]\n" + json.dumps(state["plan"], ensure_ascii=False)
        user += "\n" + _tool_outputs_block(state["outputs"], execution_feedback(state))
        schemas = [*state["schemas"], {
            "type": "function", "function": {
                "name": "replan",
                "description": "原计划整体失效且没有备用步骤时重规划（最多一次）",
                "parameters": {"type": "object", "properties": {
                    "reason": {"type": "string"},
                }, "required": ["reason"]},
            },
        }]
        try:
            output = await deps.llm.complete(
                [{"role": "system", "content": state["system"]},
                 {"role": "user", "content": user}],
                module="expression", output_type="tool", tools=schemas,
                correlation_id=state["correlation_id"],
            )
            await deps.evaluator.evaluate(output)
        except Exception:
            _LOGGER.exception("工具判断失败 correlation_id=%s", state["correlation_id"])
            return {"calls": [], "notices": [*state["notices"], TOOL_DECISION_FAILED]}
        notices = list(state["notices"])
        if len(output.tool_calls) > _TOOL_OUTPUT_MAX_ITEMS:
            notices.append("跳过本轮第6项及后续调用：超过单轮调用数量上限。")
        return {
            "calls": output.tool_calls[:_TOOL_OUTPUT_MAX_ITEMS], "cursor": 0,
            "call_id": output.call_id, "rounds": state["rounds"] + 1,
            "replan_reason": None, "notices": notices,
        }

    def select_call(state: ToolState) -> dict[str, Any]:
        cursor = state["cursor"]
        notices = list(state["notices"])
        reason = state["replan_reason"]
        allowed = {schema["function"]["name"] for schema in state["schemas"]}
        # A saved request must also respect tools disabled since the process restarted.
        allowed &= {schema["function"]["name"] for schema in deps.tools.schema()}
        pending: dict[str, Any] | None = None
        while cursor < len(state["calls"]):
            tc = state["calls"][cursor]
            cursor += 1
            name, args = tc.get("name", ""), tc.get("args", {})
            if name == "replan":
                reason = str(args.get("reason", "计划走不通"))
                continue
            if name not in allowed:
                notices.append(f"跳过 {name}：当前未授权该工具。")
                continue
            key = name + ":" + json.dumps(args, ensure_ascii=False, sort_keys=True)
            if key in state["records"]:
                prior = state["records"][key]
                why = (
                    "结果未确认，禁止重复副作用" if prior["status"] == "uncertain"
                    else "重试次数已耗尽" if prior["status"] == "exhausted"
                    else "已经执行"
                )
                notices.append(
                    f"跳过 {name}（步骤{prior['step']}）：同名同参数{why}，"
                    "请换参数、执行备用步骤或 replan。"
                )
                continue
            if len(state["outputs"]) >= _TOOL_OUTPUT_MAX_ITEMS:
                notices.append(f"跳过 {name} 及后续调用：工具结果数量预算已用尽。")
                cursor = len(state["calls"])
                break
            pending = {
                "step": len(state["records"]) + 1, "key": key,
                "name": name, "args": args, "status": "pending",
                "attempts": 0, "errors": [], "retry_at": 0.0,
                "call_id": state["call_id"],
            }
            break
        if pending is None and reason is None and state["rounds"] >= _MAX_TOOL_ROUNDS:
            notices.append("停止工具调用：当前计划的4轮工具判断预算已用尽。")
        return {
            "cursor": cursor, "pending": pending, "notices": notices,
            "replan_reason": reason,
        }

    async def begin_attempt(state: ToolState) -> dict[str, Any]:
        pending = dict(state["pending"] or {})
        if (
            pending["attempts"] > 0 and not pending.get("receipt_required")
            and not is_read_only_call(pending["name"], pending["args"])
        ):
            return {"pending": pending}
        if pending["status"] == "retry_wait":
            await asyncio.sleep(max(0.0, pending["retry_at"] - time.time()))
        pending.update(
            attempts=pending["attempts"] + 1, status="running", log_id=str(uuid4()),
            receipt_required=True,
        )
        return {"pending": pending}

    async def execute(state: ToolState) -> dict[str, Any]:
        pending = dict(state["pending"] or {})
        name, args = pending["name"], pending["args"]
        read_only = is_read_only_call(name, args)
        if not pending.get("receipt_required") and not read_only:
            pending.update(
                status="uncertain",
                text="旧 checkpoint 的副作用结果未确认，禁止重放；请先查证。",
            )
            return {"pending": pending}
        result: Any = None
        error = None
        # Receipt I/O is outside capability error handling: never retry on a
        # persistence failure after the handler may already have produced effects.
        receipt = await deps.tools.start_execution(
            pending["log_id"], state["correlation_id"],
        )
        if receipt is not None and receipt["status"] == "success":
            result = json.loads(receipt["result_json"])
        elif receipt is not None and not read_only:
            pending.update(
                status="uncertain",
                errors=[*pending["errors"], receipt["error"]] if receipt["error"]
                else pending["errors"],
                text="副作用执行结果未确认，禁止重放；请先查证实际结果。"
                + (receipt["error"] or ""),
            )
            return {"pending": pending}
        elif receipt is not None and receipt["status"] == "error":
            error = receipt["error"]
        else:
            # Disabling a tool forbids new execution, not reuse of an outcome
            # already committed before the restart.
            allowed = {schema["function"]["name"] for schema in deps.tools.schema()}
            if name not in allowed:
                pending.update(status="skipped", text="跳过：恢复后该工具已不再授权。")
                return {"pending": pending}
            try:
                result = await deps.tools.call(
                    name, args, correlation_id=state["correlation_id"],
                    call_id=pending["call_id"], log_id=pending["log_id"],
                )
            except Exception as exc:  # 取消继续传播，running 回执保留。
                error = f"{type(exc).__name__}: {exc}"[:1000]
            await deps.tools.finish_execution(pending["log_id"], result, error)
        if error is None:
            if isinstance(result, dict) and result.get("error"):
                error = str(cast(dict[str, Any], result)["error"])[:1000]
        if error is not None:
            pending.update(
                status="uncertain" if not read_only
                else "retry_wait" if pending["attempts"] < _MAX_TOOL_ATTEMPTS
                else "exhausted",
                errors=[*pending["errors"], error],
                retry_at=time.time() + pending["attempts"],
                text=(
                    f"工具 {name} 执行失败：{error}" if read_only else
                    f"工具 {name} 副作用结果未确认，禁止重试，请先查证：{error}"
                ),
            )
        else:
            empty = result is None or result == [] or result == {} or result == ""
            text = json.dumps(result, ensure_ascii=False)
            if len(text) > _MATERIAL_ITEM_MAX_CHARS:
                text = text[:_MATERIAL_ITEM_MAX_CHARS - 1] + "…"
            pending.update(status="empty" if empty else "success", text=text)
            if name == "memory_recall" and isinstance(result, dict):
                segments = cast(dict[str, Any], result).get("segments", [])
                pending["recall_candidates"] = {
                    entry["segment_id"]: json.dumps(entry, ensure_ascii=False)
                    for entry in cast(list[Any], segments)
                    if isinstance(entry, dict)
                    and isinstance(entry.get("segment_id"), str)
                }
        return {"pending": pending}

    async def accept(state: ToolState) -> dict[str, Any]:
        pending = dict(state["pending"] or {})
        name, status = pending["name"], pending["status"]
        if status in ("exhausted", "empty"):
            kind = "tool_error" if status == "exhausted" else "tool_empty"
            await deps.memory.record_failure_signal(
                kind, f"{kind}:{name}", pending["text"], state["correlation_id"],
            )
        records = {**state["records"], pending["key"]: pending}
        outputs = list(state["outputs"])
        notices = list(state["notices"])
        candidates = dict(state["recall_candidates"])
        rendered = f"{name}: {pending['text']}"
        feedback = execution_feedback({**state, "records": records})
        before = _tool_outputs_block(outputs, feedback)
        after = _tool_outputs_block([*outputs, rendered], feedback)
        if after != before:
            outputs.append(rendered)
            if status != "skipped":
                await deps.tools.mark_adopted(pending["log_id"])
            candidates.update(pending.get("recall_candidates", {}))
        else:
            notices.append(
                f"跳过 {name} 的结果回注：工具文本预算不足；不能声称已使用该结果。"
            )
        calls, cursor = state["calls"], state["cursor"]
        if status in ("exhausted", "uncertain"):
            if cursor < len(calls):
                notices.append(
                    "以下调用未执行：前一步重试耗尽或副作用结果未确认，先交模型重新决策。"
                    + json.dumps(calls[cursor:], ensure_ascii=False)
                )
            calls, cursor = [], 0
        return {
            "records": records, "outputs": outputs, "notices": notices,
            "recall_candidates": candidates, "pending": None,
            "calls": calls, "cursor": cursor,
        }

    def after_batch(state: ToolState) -> str:
        if state["pending"] is not None:
            return "begin_attempt"
        if state["replan_reason"] is not None:
            return "replan"
        return "decide" if state["rounds"] < _MAX_TOOL_ROUNDS else END

    def replan(state: ToolState) -> dict[str, Any]:
        if state["replans"] >= _MAX_REPLANS:
            return {
                "replan_reason": None,
                "notices": [*state["notices"], "跳过 replan：已达到一次重规划上限。"],
            }
        return {"replans": state["replans"] + 1}

    def after_decide(state: ToolState) -> str:
        return "select_call" if state["calls"] else END

    def after_execute(state: ToolState) -> str:
        pending = cast(dict[str, Any], state["pending"])
        return "begin_attempt" if pending["status"] == "retry_wait" else "accept"

    def after_replan(state: ToolState) -> str:
        return "plan" if state["replan_reason"] is not None else END

    graph = StateGraph(ToolState)
    graph.add_node("plan", plan)
    graph.add_node("decide", decide)
    graph.add_node("select_call", select_call)
    graph.add_node("begin_attempt", begin_attempt)
    graph.add_node("execute", execute)
    graph.add_node("accept", accept)
    graph.add_node("replan", replan)
    graph.set_entry_point("plan")
    graph.add_edge("plan", "decide")
    graph.add_conditional_edges("decide", after_decide)
    graph.add_conditional_edges("select_call", after_batch)
    graph.add_edge("begin_attempt", "execute")
    graph.add_conditional_edges("execute", after_execute)
    graph.add_edge("accept", "select_call")
    graph.add_conditional_edges("replan", after_replan)
    return graph.compile(checkpointer=deps.checkpointer or InMemorySaver())
