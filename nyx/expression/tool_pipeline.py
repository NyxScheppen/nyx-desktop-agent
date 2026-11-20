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
from nyx.policy import policy
from nyx.tools.registry import is_read_only_call
from nyx.types import Plan, PlanStep

if TYPE_CHECKING:
    from nyx.expression.pipeline import ReplyDeps

_LOGGER = logging.getLogger(__name__)
_MAX_TOOL_ROUNDS = policy("expression", "tool_pipeline._MAX_TOOL_ROUNDS", int)
_MAX_REPLANS = policy("expression", "tool_pipeline._MAX_REPLANS", int)
_MAX_TOOL_ATTEMPTS = policy("expression", "tool_pipeline._MAX_TOOL_ATTEMPTS", int)


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
            step.get("text"),
            step.get("expected_tool"),
            step.get("done_condition"),
        )
        if (
            not isinstance(text, str)
            or not isinstance(condition, str)
            or (tool is not None and not isinstance(tool, str))
        ):
            return None
        result.append(PlanStep(text, tool, condition))
    return Plan(goal, result)


def execution_feedback(state: ToolState) -> str:
    """Keep actual call status separate from natural-language plan completion."""
    records = [
        {
            **{
                key: record[key]
                for key in (
                    "step",
                    "name",
                    "status",
                    "attempts",
                    "errors",
                    "args",
                )
            },
            policy(
                "expression", "tool_pipeline.copy.execution_feedback_extra_text", str
            ): format_prompt_time(record.get("started_at")),
            policy(
                "expression", "tool_pipeline.copy.execution_feedback_extra_text_2", str
            ): format_prompt_time(record.get("received_at")),
        }
        for record in reversed(list(state["records"].values()))
    ]
    text = "\n".join(
        state["notices"][
            -policy(
                "expression",
                "tool_pipeline.parameters.execution_feedback_text_chars_or_items",
                int,
            ) :
        ]
    )
    if state["replans"]:
        text += policy(
            "expression", "tool_pipeline.copy.execution_feedback_text", str
        ) + json.dumps(
            {
                "replans": state["replans"],
                "remaining_replans": _MAX_REPLANS - state["replans"],
            },
            ensure_ascii=False,
        )
        if state["replan_reason"] is not None:
            text += policy(
                "expression", "tool_pipeline.copy.execution_feedback_text_2", str
            )
        elif state["plan"] is not None:
            text += policy(
                "expression", "tool_pipeline.copy.execution_feedback_text_4", str
            )
        else:
            text += policy(
                "expression", "tool_pipeline.copy.execution_feedback_text_5", str
            )
    if records:
        text += policy(
            "expression", "tool_pipeline.copy.execution_feedback_text_3", str
        ) + json.dumps(records, ensure_ascii=False)
    return bound_material_block(text.strip(), _TOOL_BLOCK_MAX_CHARS // 2)


def _needs_plan_review(state: ToolState) -> bool:
    latest = next(reversed(state["records"].values()), None)
    return (
        state["plan"] is not None
        and latest is not None
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
                        text=policy(
                            "expression", "tool_pipeline.copy.execute_text_3", str
                        ),
                    )
                else:
                    pending = receive_result(
                        pending,
                        None,
                        (receipt["error"] if receipt else None)
                        or policy("expression", "tool_pipeline.copy.enter_text", str),
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
        user += policy("expression", "tool_pipeline.copy.plan_text", str) + json.dumps(
            state["schemas"], ensure_ascii=False
        )
        if state["replan_reason"] is not None or state["records"]:
            try:
                lessons = await deps.memory.search(
                    state["query"],
                    kind=MemoryKind.LESSON,
                )
                if lessons:
                    system += "\n\n" + _memory_block(lessons)
            except Exception:
                _LOGGER.exception(
                    policy("expression", "tool_pipeline.copy.plan_text_3", str)
                )
            user += (
                policy("expression", "tool_pipeline.templates.plan_text", str).format(
                    p0=format_prompt_time(state.get("plan_at"))
                )
                + json.dumps(state["plan"], ensure_ascii=False)
                + policy("expression", "tool_pipeline.copy.plan_text_4", str)
                + (
                    state["replan_reason"]
                    or policy("expression", "tool_pipeline.copy.plan_text_5", str)
                )
                + "\n"
                + _tool_outputs_block(
                    state["outputs"],
                    execution_feedback(state),
                )
            )
        parsed = None
        try:
            output = await deps.llm.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                module="expression",
                output_type="plan",
                json_mode=True,
                correlation_id=state["correlation_id"],
            )
            await deps.evaluator.evaluate(output)
            parsed = _parse_plan(output.content)
        except Exception:
            _LOGGER.exception(
                policy("expression", "tool_pipeline.copy.plan_text_2", str),
                state["correlation_id"],
            )
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
            user += policy(
                "expression", "tool_pipeline.templates.decide_text", str
            ).format(p0=format_prompt_time(state.get("plan_at")))
            user += policy(
                "expression", "tool_pipeline.copy.decide_text", str
            ) + json.dumps(state["plan"], ensure_ascii=False)
        user += "\n" + _tool_outputs_block(state["outputs"], execution_feedback(state))
        schemas = [
            *state["schemas"],
            {
                "type": "function",
                "function": {
                    "name": "replan",
                    "description": policy(
                        "expression", "tool_pipeline.copy.decide_text_3", str
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reason": {"type": "string"},
                        },
                        "required": ["reason"],
                    },
                },
            },
        ]
        try:
            output = await deps.llm.complete(
                [
                    {"role": "system", "content": state["system"]},
                    {"role": "user", "content": user},
                ],
                module="expression",
                output_type="tool",
                tools=schemas,
                correlation_id=state["correlation_id"],
            )
            await deps.evaluator.evaluate(output)
        except Exception:
            _LOGGER.exception(
                policy("expression", "tool_pipeline.copy.decide_text_4", str),
                state["correlation_id"],
            )
            return {
                "calls": [],
                "rounds": state["rounds"] + 1,
                "notices": [*state["notices"], TOOL_DECISION_FAILED],
            }
        notices = list(state["notices"])
        if len(output.tool_calls) > _TOOL_OUTPUT_MAX_ITEMS:
            notices.append(
                policy("expression", "tool_pipeline.copy.decide_text_2", str)
            )
        return {
            "calls": output.tool_calls[:_TOOL_OUTPUT_MAX_ITEMS],
            "cursor": 0,
            "call_id": output.call_id,
            "rounds": state["rounds"] + 1,
            "replan_reason": None,
            "notices": notices,
        }

    async def review_plan(state: ToolState) -> dict[str, Any]:
        current = cast(dict[str, Any], state["plan"])
        latest = next(reversed(state["records"].values()))
        user = (
            state["user"]
            + "\n"
            + PLAN_REVIEW_TASK
            + policy(
                "expression", "tool_pipeline.templates.review_plan_text_4", str
            ).format(p0=format_prompt_time(state.get("plan_at")))
            + policy("expression", "tool_pipeline.copy.decide_text", str)
            + json.dumps(current, ensure_ascii=False)
            + "\n"
            + _tool_outputs_block(state["outputs"], execution_feedback(state))
            + policy(
                "expression", "tool_pipeline.templates.review_plan_text_3", str
            ).format(p0=_MAX_TOOL_ROUNDS - state["rounds"])
            + policy(
                "expression", "tool_pipeline.templates.review_plan_text_2", str
            ).format(p0=_TOOL_OUTPUT_MAX_ITEMS - len(state["outputs"]))
            + policy(
                "expression", "tool_pipeline.templates.review_plan_text", str
            ).format(p0=_MAX_REPLANS - state["replans"])
        )
        review: dict[str, Any] = {
            "step": latest["step"],
            "action": "stop",
            "next_step": None,
            "reason": policy("expression", "tool_pipeline.copy.review_plan_text", str),
        }
        try:
            output = await deps.llm.complete(
                [
                    {"role": "system", "content": state["system"]},
                    {"role": "user", "content": user},
                ],
                module="expression",
                output_type="tool",
                json_mode=True,
                correlation_id=state["correlation_id"],
            )
            await deps.evaluator.evaluate(output)
            data: Any = json.loads(output.content)
            if not isinstance(data, dict):
                raise ValueError(
                    policy("expression", "tool_pipeline.copy.review_plan_text_2", str)
                )
            decision = cast(dict[str, Any], data)
            action, reason, step = (
                decision.get("action"),
                decision.get("reason"),
                decision.get("next_step"),
            )
            if (
                action not in ("continue", "replan", "stop")
                or not isinstance(reason, str)
                or not reason.strip()
                or (
                    action == "continue"
                    and (
                        type(step) is not int or not 1 <= step <= len(current["steps"])
                    )
                )
            ):
                raise ValueError(
                    policy("expression", "tool_pipeline.copy.review_plan_text_3", str)
                )
            review.update(
                action=action,
                reason=reason.strip()[
                    : policy(
                        "expression",
                        "tool_pipeline.parameters.review_plan_reason_chars_or_items",
                        int,
                    )
                ],
                next_step=step if action == "continue" else None,
            )
        except Exception:
            _LOGGER.exception(
                policy("expression", "tool_pipeline.copy.review_plan_text_4", str),
                state["correlation_id"],
            )
        return {
            "plan_review": review,
            "calls": [],
            "cursor": 0,
            "replan_reason": review["reason"] if review["action"] == "replan" else None,
            "notices": [
                *state["notices"],
                policy("expression", "tool_pipeline.copy.review_plan_text_5", str)
                + json.dumps(review, ensure_ascii=False),
            ],
        }

    def select_call(state: ToolState) -> dict[str, Any]:
        if _needs_plan_review(state):
            notices = list(state["notices"])
            remaining = state["calls"][state["cursor"] :]
            if remaining:
                notices.append(
                    policy("expression", "tool_pipeline.copy.select_call_text_3", str)
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
                reason = str(
                    args.get(
                        "reason",
                        policy(
                            "expression", "tool_pipeline.copy.select_call_text_4", str
                        ),
                    )
                )
                continue
            if name not in allowed:
                notices.append(
                    policy(
                        "expression", "tool_pipeline.templates.select_call_text_3", str
                    ).format(p0=name)
                )
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
                    policy("expression", "tool_pipeline.copy.select_call_text_2", str)
                    if prior["status"] == "uncertain"
                    else policy(
                        "expression", "tool_pipeline.copy.select_call_text_5", str
                    )
                    if prior["status"] == "exhausted"
                    else policy(
                        "expression", "tool_pipeline.copy.select_call_text_6", str
                    )
                )
                notices.append(
                    policy(
                        "expression", "tool_pipeline.templates.select_call_text", str
                    ).format(p0=name, p1=prior["step"], p2=why)
                )
                continue
            if len(state["outputs"]) >= _TOOL_OUTPUT_MAX_ITEMS:
                notices.append(
                    policy(
                        "expression", "tool_pipeline.templates.select_call_text_2", str
                    ).format(p0=name)
                )
                cursor = len(state["calls"])
                break
            pending = {
                "step": len(state["records"]) + 1,
                "key": key,
                "name": name,
                "args": args,
                "status": "pending",
                "attempts": 0,
                "errors": [],
                "retry_at": 0.0,
                "call_id": state["call_id"],
            }
            break
        if pending is None and reason is None and state["rounds"] >= _MAX_TOOL_ROUNDS:
            notices.append(
                policy("expression", "tool_pipeline.copy.select_call_text", str)
            )
        return {
            "cursor": cursor,
            "pending": pending,
            "notices": notices,
            "replan_reason": reason,
        }

    async def begin_attempt(state: ToolState) -> dict[str, Any]:
        pending = dict(state["pending"] or {})
        if (
            pending["attempts"] > 0
            and not pending.get("receipt_required")
            and not is_read_only_call(pending["name"], pending["args"])
        ):
            return {"pending": pending}
        if pending["status"] == "retry_wait":
            await asyncio.sleep(max(0.0, pending["retry_at"] - time.time()))
        if await input_pending(state):
            return {"exit_reason": "input_pending"}
        pending.update(
            attempts=pending["attempts"] + 1,
            status="running",
            log_id=str(uuid4()),
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
                    status="uncertain",
                    text=policy("expression", "tool_pipeline.copy.execute_text_3", str),
                )
                return {"pending": pending}
            return {
                "pending": receive_result(
                    pending,
                    None,
                    policy("expression", "tool_pipeline.copy.execute_text", str),
                ),
                "exit_reason": "input_pending",
            }
        if not pending.get("receipt_required") and not read_only:
            pending.update(
                status="uncertain",
                text=policy("expression", "tool_pipeline.copy.execute_text_2", str),
            )
            return {"pending": pending}
        result: Any = None
        error = None
        # Receipt I/O is outside capability error handling: never retry on a
        # persistence failure after the handler may already have produced effects.
        receipt = await deps.tools.start_execution(
            pending["log_id"],
            state["correlation_id"],
        )
        if receipt is not None and receipt["status"] == "success":
            result = json.loads(receipt["result_json"])
        elif receipt is not None and not read_only and name != "deep_research":
            pending.update(
                status="uncertain",
                errors=[*pending["errors"], receipt["error"]]
                if receipt["error"]
                else pending["errors"],
                text=policy("expression", "tool_pipeline.copy.execute_text_4", str)
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
                pending.update(
                    status="skipped",
                    text=policy("expression", "tool_pipeline.copy.execute_text_5", str),
                )
                return {"pending": pending}
            try:
                result = await deps.tools.call(
                    name,
                    args,
                    correlation_id=state["correlation_id"],
                    call_id=pending["call_id"],
                    log_id=pending["log_id"],
                )
            except Exception as exc:  # 取消继续传播，running 回执保留。
                error = f"{type(exc).__name__}: {exc}"[
                    : policy(
                        "expression",
                        "tool_pipeline.parameters.execute_error_chars_or_items",
                        int,
                    )
                ]
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
                error = str(cast(dict[str, Any], result)["error"])[
                    : policy(
                        "expression",
                        "tool_pipeline.parameters.receive_result_error_chars_or_items",
                        int,
                    )
                ]
        if error is not None:
            known_research_failure = (
                name == "deep_research"
                and isinstance(result, dict)
                and bool(cast(dict[str, Any], result).get("run_id"))
                and cast(dict[str, Any], result).get("status")
                in {"failed", "cancelled"}
            )
            pending.update(
                status="failed"
                if known_research_failure
                else "uncertain"
                if not read_only
                else "retry_wait"
                if pending["attempts"] < _MAX_TOOL_ATTEMPTS
                else "exhausted",
                errors=[*pending["errors"], error],
                retry_at=time.time() + pending["attempts"],
                text=(
                    policy(
                        "expression",
                        "tool_pipeline.templates.receive_result_text_2",
                        str,
                    ).format(p0=name, p1=error)
                    if read_only or known_research_failure
                    else policy(
                        "expression", "tool_pipeline.templates.receive_result_text", str
                    ).format(p0=name, p1=error)
                ),
            )
        else:
            empty = result is None or result == [] or result == {} or result == ""
            text = json.dumps(result, ensure_ascii=False)
            if len(text) > _MATERIAL_ITEM_MAX_CHARS:
                text = text[: _MATERIAL_ITEM_MAX_CHARS - 1] + "…"
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
                kind,
                f"{kind}:{name}",
                pending["text"],
                state["correlation_id"],
                evidence=[
                    {
                        "source_id": str(pending["log_id"]),
                        "summary": json.dumps(pending, ensure_ascii=False, default=str),
                    }
                ],
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
                policy("expression", "tool_pipeline.templates.accept_text", str).format(
                    p0=name
                )
            )
        calls, cursor = state["calls"], state["cursor"]
        if status in ("exhausted", "uncertain", "failed"):
            if cursor < len(calls):
                notices.append(
                    policy("expression", "tool_pipeline.copy.accept_text", str)
                    + json.dumps(calls[cursor:], ensure_ascii=False)
                )
            calls, cursor = [], 0
        return {
            "records": records,
            "outputs": outputs,
            "notices": notices,
            "recall_candidates": candidates,
            "pending": None,
            "calls": calls,
            "cursor": cursor,
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
                "notices": [
                    *state["notices"],
                    policy("expression", "tool_pipeline.copy.replan_text", str),
                ],
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
            limit = policy("expression", "tool_pipeline.copy.complete_text", str)
        elif state["rounds"] >= _MAX_TOOL_ROUNDS:
            limit = policy("expression", "tool_pipeline.copy.complete_text_2", str)
        elif len(state["outputs"]) >= _TOOL_OUTPUT_MAX_ITEMS:
            limit = policy("expression", "tool_pipeline.copy.complete_text_3", str)
        elif any(r["status"] == "uncertain" for r in state["records"].values()):
            limit = policy("expression", "tool_pipeline.copy.complete_text_4", str)
        return {
            "exit_reason": "complete",
            "notices": [*state["notices"], limit] if limit else state["notices"],
        }

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
