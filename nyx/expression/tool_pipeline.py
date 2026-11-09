# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false, reportUnknownMemberType=false
"""慢通道工具子图；每次调用及重试都有独立的持久恢复边界。"""

import asyncio
import json
import logging
import time
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict, cast
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from nyx.enums import MemoryKind
from nyx.expression.prompt import (
    _MATERIAL_ITEM_MAX_CHARS,
    _TOOL_BLOCK_MAX_CHARS,
    _TOOL_OUTPUT_MAX_ITEMS,
    PLAN_REVIEW_TASK,
    PLAN_TASK,
    TOOL_DECISION_FAILED,
    _memory_block,
    _tool_outputs_block,
    bound_material_block,
    format_prompt_time,
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
    input_event_ids: NotRequired[list[str]]
    input_revision: NotRequired[int]
    input_closed: NotRequired[bool]
    answered_attempt_id: NotRequired[str | None]
    allow_new_actions: NotRequired[bool]
    exit_reason: NotRequired[str]
    input_update: NotRequired[dict[str, Any] | None]
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
    plan_review: NotRequired[dict[str, Any]]
    plan_at: NotRequired[float | None]


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
        {**{key: record[key] for key in (
            "step", "name", "status", "attempts", "errors", "args",
        )}, "尝试开始时间": format_prompt_time(record.get("started_at")),
         "结果接收时间": format_prompt_time(record.get("received_at"))}
        for record in reversed(list(state["records"].values()))
    ]
    text = "\n".join(state["notices"][-5:])
    if state["replans"]:
        text += "\n[规划状态]\n" + json.dumps({
            "replans": state["replans"],
            "remaining_replans": _MAX_REPLANS - state["replans"],
        }, ensure_ascii=False)
        if state["replan_reason"] is not None:
            text += "\n已受理重规划，正在生成新计划。"
        elif state["plan"] is not None:
            text += "\n已完成重规划，当前计划已替代旧计划；之前的 replan 决策已处理。"
        else:
            text += "\n重规划未取得合法计划，已降级无计划执行。"
    if records:
        text += "\n[实际调用步骤状态]\n" + json.dumps(records, ensure_ascii=False)
    return bound_material_block(text.strip(), _TOOL_BLOCK_MAX_CHARS // 2)


def _needs_plan_review(state: ToolState) -> bool:
    latest = next(reversed(state["records"].values()), None)
    return (
        state["plan"] is not None and latest is not None
        and latest["status"] == "exhausted"
        and state.get("plan_review", {}).get("step") != latest["step"]
        and state["replan_reason"] is None
        and state["rounds"] < _MAX_TOOL_ROUNDS
        and len(state["outputs"]) < _TOOL_OUTPUT_MAX_ITEMS
    )


def build_tool_graph(deps: "ReplyDeps") -> CompiledStateGraph[ToolState]:
    """Build the existing plan/execute loop with one checkpoint per tool attempt."""

    async def input_pending(state: ToolState) -> bool:
        return not state.get(
            "input_closed", False
        ) and await deps.has_pending_reply_inputs(
            state.get("input_event_ids", []),
            state.get("answered_attempt_id"),
        )

    async def enter(state: ToolState) -> dict[str, Any]:
        update = state.get("input_update")
        if update is None:
            return {"exit_reason": "complete"}
        pending = dict(state["pending"] or {})
        changes: dict[str, Any] = {}
        if pending and pending["attempts"] > 0:
            status = pending["status"]
            if status == "running":
                receipt = await deps.tools.get_execution(pending["log_id"])
                if receipt is not None and receipt["status"] == "success":
                    pending = receive_result(
                        pending, json.loads(receipt["result_json"]), None
                    )
                elif not is_read_only_call(pending["name"], pending["args"]):
                    pending.update(
                        status="uncertain",
                        text="旧副作用结果未确认，禁止重放；请先查证。",
                    )
                else:
                    pending = receive_result(
                        pending,
                        None,
                        (receipt["error"] if receipt else None)
                        or "旧请求未取得可靠结果，等待新版决策。",
                    )
            if pending["status"] == "retry_wait":
                changes["records"] = {**state["records"], pending["key"]: pending}
            else:
                changes = await accept(cast(ToolState, {**state, "pending": pending}))
        return {
            **changes,
            **update,
            "input_update": None,
            "pending": None,
            "calls": [],
            "cursor": 0,
            "plan": None,
            "plan_review": {},
            "replan_reason": None,
            "exit_reason": "complete",
        }

    async def after_enter(state: ToolState) -> str:
        if await input_pending(state):
            return "yield_input"
        if (
            not state.get("allow_new_actions", True)
            or state["rounds"] >= _MAX_TOOL_ROUNDS
            or len(state["outputs"]) >= _TOOL_OUTPUT_MAX_ITEMS
            or any(r["status"] == "uncertain" for r in state["records"].values())
        ):
            return "complete"
        return "plan"

    async def plan(state: ToolState) -> dict[str, Any]:
        system = state["system"]
        user = state["user"] + "\n" + PLAN_TASK
        user += "\n授权工具：" + json.dumps(state["schemas"], ensure_ascii=False)
        if state["replan_reason"] is not None or state["records"]:
            try:
                lessons = await deps.memory.search(
                    state["query"], kind=MemoryKind.LESSON,
                )
                if lessons:
                    system += "\n\n" + _memory_block(lessons)
            except Exception:
                _LOGGER.exception("重规划经验召回失败")
            user += (
                f"\n[计划生成时间={format_prompt_time(state.get('plan_at'))}]\n上次计划："
                + json.dumps(state["plan"], ensure_ascii=False)
                + "\n重规划原因："
                + (state["replan_reason"] or "用户输入更新；保留已执行事实和剩余预算。")
                + "\n"
                + _tool_outputs_block(
                    state["outputs"],
                    execution_feedback(state),
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
            "system": system,
            "plan": asdict(parsed) if parsed else None,
            "plan_at": time.time() if parsed else None,
            "replan_reason": None,
            "rounds": 0 if state["replan_reason"] is not None else state["rounds"],
        }

    async def decide(state: ToolState) -> dict[str, Any]:
        # Older checkpoints may resume directly at this node after exhaustion.
        if _needs_plan_review(state):
            return {"calls": []}
        user = state["user"] + "\n" + state["task"]
        if state["plan"] is not None:
            user += f"\n[计划生成时间={format_prompt_time(state.get('plan_at'))}]"
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
            return {
                "calls": [],
                "rounds": state["rounds"] + 1,
                "notices": [*state["notices"], TOOL_DECISION_FAILED],
            }
        notices = list(state["notices"])
        if len(output.tool_calls) > _TOOL_OUTPUT_MAX_ITEMS:
            notices.append("跳过本轮第6项及后续调用：超过单轮调用数量上限。")
        return {
            "calls": output.tool_calls[:_TOOL_OUTPUT_MAX_ITEMS], "cursor": 0,
            "call_id": output.call_id, "rounds": state["rounds"] + 1,
            "replan_reason": None, "notices": notices,
        }

    async def review_plan(state: ToolState) -> dict[str, Any]:
        current = cast(dict[str, Any], state["plan"])
        latest = next(reversed(state["records"].values()))
        user = (
            state["user"] + "\n" + PLAN_REVIEW_TASK
            + f"\n[计划生成时间={format_prompt_time(state.get('plan_at'))}]"
            + "\n[当前计划]\n" + json.dumps(current, ensure_ascii=False)
            + "\n" + _tool_outputs_block(state["outputs"], execution_feedback(state))
            + f"\n剩余预算：工具判断{_MAX_TOOL_ROUNDS - state['rounds']}轮，"
            + f"结果{_TOOL_OUTPUT_MAX_ITEMS - len(state['outputs'])}项，"
            + f"重规划{_MAX_REPLANS - state['replans']}次。"
        )
        review: dict[str, Any] = {
            "step": latest["step"], "action": "stop", "next_step": None,
            "reason": "计划决策失败，停止新工具调用；已有结果仍可使用。",
        }
        try:
            output = await deps.llm.complete(
                [{"role": "system", "content": state["system"]},
                 {"role": "user", "content": user}],
                module="expression", output_type="tool", json_mode=True,
                correlation_id=state["correlation_id"],
            )
            await deps.evaluator.evaluate(output)
            data: Any = json.loads(output.content)
            if not isinstance(data, dict):
                raise ValueError("计划决策必须是 JSON 对象")
            decision = cast(dict[str, Any], data)
            action, reason, step = (
                decision.get("action"), decision.get("reason"),
                decision.get("next_step"),
            )
            if (
                action not in ("continue", "replan", "stop")
                or not isinstance(reason, str) or not reason.strip()
                or (action == "continue" and (
                    type(step) is not int or not 1 <= step <= len(current["steps"])
                ))
            ):
                raise ValueError("计划决策动作、原因或步骤无效")
            review.update(
                action=action, reason=reason.strip()[:1000],
                next_step=step if action == "continue" else None,
            )
        except Exception:
            _LOGGER.exception("计划决策失败 correlation_id=%s", state["correlation_id"])
        return {
            "plan_review": review, "calls": [], "cursor": 0,
            "replan_reason": review["reason"] if review["action"] == "replan" else None,
            "notices": [*state["notices"],
                        "[计划决策] " + json.dumps(review, ensure_ascii=False)],
        }

    def select_call(state: ToolState) -> dict[str, Any]:
        if _needs_plan_review(state):
            notices = list(state["notices"])
            remaining = state["calls"][state["cursor"]:]
            if remaining:
                notices.append(
                    "以下调用未执行：耗尽后必须先完成计划决策。"
                    + json.dumps(remaining, ensure_ascii=False)
                )
            return {"pending": None, "calls": [], "cursor": 0, "notices": notices}
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
                if (
                    prior["status"] == "retry_wait"
                    and prior["attempts"] < _MAX_TOOL_ATTEMPTS
                ):
                    pending = dict(prior)
                    break
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
        if await input_pending(state):
            return {"exit_reason": "input_pending"}
        pending.update(
            attempts=pending["attempts"] + 1, status="running", log_id=str(uuid4()),
            receipt_required=True,
            started_at=time.time(),
        )
        return {"pending": pending}

    async def execute(state: ToolState) -> dict[str, Any]:
        pending = dict(state["pending"] or {})
        name, args = pending["name"], pending["args"]
        read_only = is_read_only_call(name, args)
        if await input_pending(state):
            receipt = await deps.tools.get_execution(pending["log_id"])
            if receipt is not None and receipt["status"] == "success":
                return {
                    "pending": receive_result(
                        pending, json.loads(receipt["result_json"]), None
                    )
                }
            if not read_only:
                pending.update(
                    status="uncertain", text="旧副作用结果未确认，禁止重放；请先查证。"
                )
                return {"pending": pending}
            return {
                "pending": receive_result(
                    pending, None, "输入已更新，旧请求等待新版决策。"
                ),
                "exit_reason": "input_pending",
            }
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
        elif receipt is not None and not read_only and name != "deep_research":
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
            allowed &= {schema["function"]["name"] for schema in state["schemas"]}
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
        return {"pending": receive_result(pending, result, error)}

    def receive_result(
        pending: dict[str, Any],
        result: Any,
        error: str | None,
    ) -> dict[str, Any]:
        name = pending["name"]
        read_only = is_read_only_call(name, pending["args"])
        if error is None:
            if isinstance(result, dict) and result.get("error"):
                error = str(cast(dict[str, Any], result)["error"])[:1000]
        if error is not None:
            known_research_failure = (
                name == "deep_research" and isinstance(result, dict)
                and bool(cast(dict[str, Any], result).get("run_id"))
                and cast(dict[str, Any], result).get("status")
                in {"failed", "cancelled"}
            )
            pending.update(
                status="failed" if known_research_failure else
                "uncertain" if not read_only
                else "retry_wait" if pending["attempts"] < _MAX_TOOL_ATTEMPTS
                else "exhausted",
                errors=[*pending["errors"], error],
                retry_at=time.time() + pending["attempts"],
                text=(
                    f"工具 {name} 执行失败：{error}"
                    if read_only or known_research_failure else
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
        pending["received_at"] = time.time()
        return pending

    async def accept(state: ToolState) -> dict[str, Any]:
        pending = dict(state["pending"] or {})
        name, status = pending["name"], pending["status"]
        if status in ("exhausted", "empty"):
            kind = "tool_error" if status == "exhausted" else "tool_empty"
            await deps.memory.record_failure_signal(
                kind, f"{kind}:{name}", pending["text"], state["correlation_id"],
                evidence=[{"source_id": str(pending["log_id"]), "summary":
                    json.dumps(pending, ensure_ascii=False, default=str)}],
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
        if status in ("exhausted", "uncertain", "failed"):
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

    async def after_batch(state: ToolState) -> str:
        if await input_pending(state):
            return "yield_input"
        if state["pending"] is not None:
            return "begin_attempt"
        if state["replan_reason"] is not None:
            return "replan"
        if _needs_plan_review(state):
            return "review_plan"
        return "decide" if state["rounds"] < _MAX_TOOL_ROUNDS else "complete"

    def replan(state: ToolState) -> dict[str, Any]:
        if state["replans"] >= _MAX_REPLANS:
            return {
                "replan_reason": None,
                "notices": [*state["notices"], "跳过 replan：已达到一次重规划上限。"],
            }
        return {"replans": state["replans"] + 1}

    async def after_decide(state: ToolState) -> str:
        if await input_pending(state):
            return "yield_input"
        if _needs_plan_review(state):
            return "review_plan"
        return "select_call" if state["calls"] else "complete"

    async def after_review(state: ToolState) -> str:
        if await input_pending(state):
            return "yield_input"
        action = state.get("plan_review", {})["action"]
        if action == "replan":
            return "replan"
        return "decide" if action == "continue" else "complete"

    def after_execute(state: ToolState) -> str:
        if state.get("exit_reason") == "input_pending":
            return "yield_input"
        pending = cast(dict[str, Any], state["pending"])
        return "begin_attempt" if pending["status"] == "retry_wait" else "accept"

    def after_replan(state: ToolState) -> str:
        return "plan" if state["replan_reason"] is not None else "complete"

    async def after_plan(state: ToolState) -> str:
        return "yield_input" if await input_pending(state) else "decide"

    def yield_input(state: ToolState) -> dict[str, Any]:
        return {"exit_reason": "input_pending"}

    def complete(state: ToolState) -> dict[str, Any]:
        limit = ""
        if not state["schemas"]:
            limit = "当前没有获授权的可用工具。"
        elif state["rounds"] >= _MAX_TOOL_ROUNDS:
            limit = "工具判断预算已用尽，使用已有结果回答。"
        elif len(state["outputs"]) >= _TOOL_OUTPUT_MAX_ITEMS:
            limit = "工具结果数量预算已用尽。"
        elif any(r["status"] == "uncertain" for r in state["records"].values()):
            limit = "已有副作用结果未确认，不再启动新动作。"
        return {"exit_reason": "complete",
                "notices": [*state["notices"], limit] if limit else state["notices"]}

    def after_begin(state: ToolState) -> str:
        return (
            "yield_input" if state.get("exit_reason") == "input_pending" else "execute"
        )

    graph = StateGraph(ToolState)
    graph.add_node("enter", enter)
    graph.add_node("yield_input", yield_input)
    graph.add_node("complete", complete)
    graph.add_node("plan", plan)
    graph.add_node("decide", decide)
    graph.add_node("review_plan", review_plan)
    graph.add_node("select_call", select_call)
    graph.add_node("begin_attempt", begin_attempt)
    graph.add_node("execute", execute)
    graph.add_node("accept", accept)
    graph.add_node("replan", replan)
    graph.set_entry_point("enter")
    graph.add_conditional_edges("enter", after_enter)
    graph.add_conditional_edges("plan", after_plan)
    graph.add_conditional_edges("decide", after_decide)
    graph.add_conditional_edges("review_plan", after_review)
    graph.add_conditional_edges("select_call", after_batch)
    graph.add_conditional_edges("begin_attempt", after_begin)
    graph.add_conditional_edges("execute", after_execute)
    graph.add_edge("accept", "select_call")
    graph.add_conditional_edges("replan", after_replan)
    graph.add_edge("yield_input", END)
    graph.add_edge("complete", END)
    return graph.compile(checkpointer=deps.checkpointer or InMemorySaver())
