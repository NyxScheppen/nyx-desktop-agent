"""Reading-only understand, investigate and answer loop; children can only read."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any, cast

from nyx.eval.evaluator import Evaluator
from nyx.expression.pipeline import ReplyState
from nyx.llm.client import LlmClient
from nyx.policy import policy
from nyx.reading.outputs import NeedEvidence, complete
from nyx.tools.execution import read_tool
from nyx.tools.registry import ToolRegistry

_PARAMS = policy("reading", "study.parameters", dict[str, int])
_PROMPTS = policy("reading", "study.prompts", dict[str, str])
_COPY = policy("reading", "study.copy", dict[str, str])


class _Refresh(Exception):
    pass


async def reply(
    state: ReplyState,
    llm: LlmClient,
    evaluator: Evaluator,
    system: str,
    tools: ToolRegistry,
    collect: Callable[[ReplyState], Awaitable[dict[str, Any]]],
    commit: Callable[[ReplyState], Awaitable[str]],
    has_pending: Callable[[list[str], str | None], Awaitable[bool]],
) -> None:
    async def current() -> None:
        if await has_pending([], None):
            raise _Refresh

    while True:
        cast(dict[str, Any], state).update(await collect(state))
        context = json.loads(state["reading_context"])
        original: dict[str, Any] = {
            "current_inputs": json.loads(state["message"]),
            "recent_dialogue": [asdict(m) for m in state["context"]],
            "active_requirements": context["active_requirements"],
            "presentation": context["presentation"],
        }
        specified = list(
            dict.fromkeys(
                int(index)
                for item in original["current_inputs"]
                for index in re.findall(
                    policy("reading", "study.explicit_paragraph_pattern", str),
                    item["message"],
                )
            )
        )
        original["specified_paragraphs"] = specified
        feedback: list[str] = []
        step = "understand"
        understanding: dict[str, Any] = {}
        evidence: dict[str, Any] = {}
        passages: dict[int, dict[str, Any]] = {}
        agents_used = 0
        tool_receipts: list[dict[str, Any]] = []
        evidence_remaining = _PARAMS["evidence_steps"]
        review_evidence_remaining = _PARAMS["review_evidence_steps"]
        targeted = False
        read_cache: dict[str, tuple[Any, dict[str, Any]]] = {}
        seen_sources: set[int] = set()
        repair_tasks: list[dict[str, Any]] = []
        repair_attempts: dict[str, int] = {}
        repair_last_read: dict[str, str] = {}
        interpretations: list[dict[str, Any]] = []
        interpretation_applied = False
        user_words = [t["message"] for t in original["current_inputs"]]
        user_words.extend(
            note for t in original["current_inputs"] for note in t["notes"]
        )
        user_words.extend(
            t["annotation"]["comment"]
            for t in original["current_inputs"]
            if t["annotation"] is not None
        )

        async def read(name: str, args: dict[str, Any]) -> Any:
            key = json.dumps([name, args], sort_keys=True, ensure_ascii=False)
            if key in read_cache:
                result, receipt = read_cache[key]
                tool_receipts.append(
                    {
                        **receipt,
                        "status": "already_provided",
                        "new_indices": [],
                        "message": _COPY["read_repeated"],
                    }
                )
                return result
            result, receipt = await read_tool(
                tools,
                name,
                args,
                state["correlation_id"],
                current,
            )
            tool_receipts.append(
                {
                    k: receipt[k]
                    for k in (
                        "name",
                        "args",
                        "status",
                        "attempts",
                        "errors",
                        "log_id",
                    )
                }
            )
            tool_receipts[-1]["returned_indices"] = (
                [
                    p["index"]
                    for p in cast(list[Any], result)
                    if isinstance(p, dict) and "index" in p
                ]
                if isinstance(result, list)
                else []
            )
            returned = set(cast(list[int], tool_receipts[-1]["returned_indices"]))
            tool_receipts[-1]["new_indices"] = sorted(returned - seen_sources)
            seen_sources.update(returned)
            if receipt["status"] not in ("success", "empty"):
                return []
            read_cache[key] = (result, dict(tool_receipts[-1]))
            return cast(Any, result)

        async def investigate(
            task: str,
            client: LlmClient,
            child: bool,
        ) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
            nonlocal agents_used, evidence_remaining
            reads: dict[int, dict[str, Any]] = {} if child else dict(passages)
            children: list[dict[str, Any]] = (
                [] if child else list(evidence.get("children", []))
            )
            read_log: list[dict[str, Any]] = (
                [] if child else list(evidence.get("read_log", []))
            )
            questions = [task] if child else understanding["subquestions"]
            previous = {c["question"]: c for c in evidence.get("checks", [])}
            checks = {
                question: previous.get(
                    question,
                    {
                        "question": question,
                        "status": "missing",
                        "sources": [],
                        "gap": _COPY["evidence_pending"],
                        "direct_sources": [],
                        "speaker": "",
                        "statement_type": "other",
                        "contradictions": [],
                        "context_gap": "",
                    },
                )
                for question in questions
            }
            remaining = _PARAMS["evidence_steps"] if child else evidence_remaining
            source_recoveries = policy("llm", "validation.attempts", int)
            candidates: list[dict[str, Any]] = []
            last_no_progress: str | None = None
            needs_repair_read = targeted
            if remaining and not targeted:
                for question in [t["task"] for t in repair_tasks] or questions:
                    candidates.extend(await read("book_locate", {"query": question}))
                for p in await read("book_search", {"query": task}):
                    reads[p["index"]] = p
                if not child:
                    for index in specified:
                        if 1 <= index <= context["book"]["total_paragraphs"]:
                            for p in await read(
                                "book_excerpt", {"start": index, "end": index}
                            ):
                                reads[p["index"]] = p

            async def validate(value: dict[str, Any]) -> None:
                if (
                    focus_task is not None
                    and value["action"] in ("search", "locate", "excerpt")
                    and value["repair_question"] != focus_task["question"]
                ):
                    raise ValueError(
                        "repair_question must match the assigned focus_task"
                    )
                if value["action"] == "delegate" and (
                    child
                    or targeted
                    or agents_used + len(value["tasks"]) > _PARAMS["agent_limit"]
                ):
                    raise ValueError("delegate: child depth or agent_limit exceeded")
                supplied = {c["question"] for c in value["checks"]}
                if not supplied <= set(questions) or (
                    value["action"] == "finish" and supplied != set(questions)
                ):
                    raise ValueError(
                        "checks must cover each task_subquestions verbatim"
                    )
                referenced = {
                    i
                    for c in value["checks"]
                    for i in [*c["sources"], *c["direct_sources"]]
                }
                referenced.update(
                    i for c in value.get("claims", []) for i in c["sources"]
                )
                if not referenced <= reads.keys():
                    raise NeedEvidence(sorted(referenced - reads.keys()), value)
                for check in value["checks"]:
                    if check["status"] == "supported" and not check["sources"]:
                        raise ValueError("supported check requires actual sources")
                if value["action"] == "excerpt":
                    if (
                        not 1
                        <= value["start"]
                        <= value["end"]
                        <= context["book"]["total_paragraphs"]
                    ):
                        raise ValueError("excerpt: invalid paragraph range")

            async def supplement(indices: list[int]) -> None:
                for index in indices:
                    if not 1 <= index <= context["book"]["total_paragraphs"]:
                        continue
                    found = await read("book_excerpt", {"start": index, "end": index})
                    reads.update({p["index"]: p for p in found})
                    read_log.append(
                        {
                            "tool": "book_excerpt",
                            "args": {"start": index, "end": index},
                            "returned_indices": [p["index"] for p in found],
                        }
                    )

            while True:
                if not child:
                    await current()
                pending_repairs = (
                    [
                        t
                        for t in repair_tasks
                        if checks[t["question"]]["status"] == "missing"
                    ]
                    if targeted
                    else []
                )
                focus_task = min(
                    pending_repairs,
                    key=lambda t: repair_attempts.get(t["question"], 0),
                    default=None,
                )
                try:
                    action = await complete(
                        client,
                        evaluator,
                        "evidence_repair_search"
                        if targeted
                        and remaining
                        and needs_repair_read
                        and focus_task is not None
                        and (
                            repair_attempts.get(focus_task["question"], 0) == 0
                            or repair_last_read.get(focus_task["question"]) == "excerpt"
                        )
                        else "evidence_repair"
                        if targeted and remaining and needs_repair_read
                        else "evidence"
                        if remaining
                        else "evidence_finish",
                        _PROMPTS["material_boundary"] + _PROMPTS["evidence"],
                        {
                            "task": task,
                            "task_subquestions": questions,
                            "checks": list(checks.values()),
                            "chapter_index": context["chapter_index"],
                            "chapter_candidates": candidates,
                            "tool_feedback": tool_receipts,
                            "original": original if not child else None,
                            "understanding": understanding,
                            "overview": context["materials"],
                            "coverage": context["coverage"],
                            "passages": list(reads.values()),
                            "children": children,
                            "read_log": read_log,
                            "steps_remaining": remaining,
                            "budget_exhausted": not remaining,
                            "excerpt_limit": _PARAMS["search_limit"],
                            "issues": feedback,
                            "repair_tasks": repair_tasks,
                            "focus_task": focus_task,
                            "repair_attempts": repair_attempts,
                            "targeted": targeted,
                            "child": child,
                            "agents_remaining": 0
                            if child or targeted
                            else _PARAMS["agent_limit"] - agents_used,
                        },
                        state["correlation_id"],
                        validate,
                    )
                except NeedEvidence as missing:
                    if source_recoveries:
                        source_recoveries -= 1
                        await supplement(missing.indices)
                        continue
                    if missing.value["action"] != "finish":
                        remaining = 0
                        if not child:
                            evidence_remaining = 0
                        continue
                    # No false read receipts: discard unsupported claims and
                    # expose their exact missing paragraphs in the partial report.
                    action = missing.value
                    gap = _COPY["evidence_missing"].format(indices=missing.indices)
                    action["summary"] = _COPY["evidence_partial"]
                    action["gaps"].append(gap)
                    action["claims"] = [
                        c for c in action["claims"] if set(c["sources"]) <= reads.keys()
                    ]
                    for check in action["checks"]:
                        if (
                            not set(check["sources"] + check["direct_sources"])
                            <= reads.keys()
                        ):
                            check.update(
                                status="missing",
                                gap=gap,
                                context_gap=gap,
                                sources=[i for i in check["sources"] if i in reads],
                                direct_sources=[
                                    i for i in check["direct_sources"] if i in reads
                                ],
                            )
                checks.update({c["question"]: c for c in action["checks"]})
                if action["action"] == "finish":
                    missing_questions = [
                        c["question"]
                        for c in checks.values()
                        if c["status"] == "missing"
                    ]
                    if missing_questions and remaining:
                        if targeted:
                            needs_repair_read = True
                        else:
                            remaining -= 1
                            if not child:
                                evidence_remaining = remaining
                        task = "\n".join(missing_questions)
                        continue
                    action.update(
                        checks=list(checks.values()),
                        children=children,
                        read_scope=list(reads),
                        read_log=read_log,
                        budget_exhausted=not remaining,
                    )
                    action["gaps"] = list(
                        dict.fromkeys(
                            [
                                *action["gaps"],
                                *(
                                    c["gap"]
                                    for c in checks.values()
                                    if c["status"] == "missing"
                                ),
                            ]
                        )
                    )
                    return action, reads
                if action["action"] == "understand":
                    return action, reads
                remaining -= 1
                if not child:
                    evidence_remaining = remaining
                if focus_task is not None:
                    question = focus_task["question"]
                    repair_attempts[question] = repair_attempts.get(question, 0) + 1
                    repair_last_read[question] = action["action"]
                if action["action"] == "delegate":
                    agents_used += len(action["tasks"])

                    async def run_child(
                        task: str,
                    ) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
                        async with asyncio.timeout(_PARAMS["agent_timeout_seconds"]):
                            return await investigate(task, llm.fork(), True)

                    results = await asyncio.gather(
                        *(run_child(task) for task in action["tasks"]),
                        return_exceptions=True,
                    )
                    for task, result in zip(action["tasks"], results, strict=True):
                        if isinstance(result, BaseException):
                            if isinstance(result, asyncio.CancelledError):
                                raise result
                            children.append({"task": task, "error": str(result)})
                        else:
                            report, source_reads = result
                            reads.update(source_reads)
                            children.append(
                                {
                                    "task": task,
                                    "result": report,
                                    "read_scope": list(source_reads),
                                }
                            )
                    continue
                name = {
                    "search": "book_search",
                    "excerpt": "book_excerpt",
                    "locate": "book_locate",
                }[action["action"]]
                args = {
                    k: v
                    for k, v in action.items()
                    if k not in ("action", "checks", "repair_question")
                }
                before_reads = set(reads)
                before_candidates = candidates
                requested_args = dict(args)
                if name == "book_excerpt":
                    args["end"] = min(
                        args["end"], args["start"] + _PARAMS["search_limit"] - 1
                    )
                found = await read(name, args)
                if args != requested_args:
                    tool_receipts[-1]["requested_args"] = requested_args
                needs_repair_read = False
                if name == "book_locate":
                    candidates = found
                else:
                    read_log.append(
                        {
                            "tool": name,
                            "args": args,
                            "returned_indices": [p["index"] for p in found],
                        }
                    )
                    reads.update({p["index"]: p for p in found})
                progress = set(reads) != before_reads or candidates != before_candidates
                if not progress:
                    key = json.dumps([name, args], sort_keys=True, ensure_ascii=False)
                    if key == last_no_progress:
                        remaining = 0
                        if not child:
                            evidence_remaining = 0
                    last_no_progress = key
                else:
                    last_no_progress = None

        try:
            for _ in range(
                _PARAMS["stage_attempts"] + _PARAMS["review_evidence_steps"]
            ):
                await current()
                if step == "understand":

                    async def validate_understanding(value: dict[str, Any]) -> None:
                        if value["promotion_request"] and not any(
                            value["promotion_request"] in w for w in user_words
                        ):
                            raise ValueError(
                                "promotion_request must quote current user words"
                            )
                        inputs = list(original["current_inputs"])
                        words = [t["message"] for t in inputs]
                        words.extend(note for t in inputs for note in t["notes"])
                        words.extend(
                            t["annotation"]["comment"]
                            for t in inputs
                            if t["annotation"] is not None
                        )
                        words.extend(original["active_requirements"])
                        for requirement in value["requirements"]:
                            if not requirement.strip() or not any(
                                requirement in text for text in words
                            ):
                                raise ValueError(
                                    _PROMPTS["requirements_invalid"].format(
                                        words=json.dumps(words, ensure_ascii=False)
                                    )
                                )
                        if not set(value["carry_requirements"]) <= set(
                            value["requirements"]
                        ):
                            raise ValueError(
                                "carry_requirements must be active requirements"
                            )

                    understanding = await complete(
                        llm,
                        evaluator,
                        "understand",
                        _PROMPTS["understand"],
                        {**original, "issues": feedback},
                        state["correlation_id"],
                        validate_understanding,
                    )
                    recalled = {}
                    for field, tool in (
                        ("history_query", "book_history"),
                        ("understanding_query", "book_understanding"),
                    ):
                        if understanding[field] is not None:
                            recalled[field] = await read(
                                tool, {"query": understanding[field]}
                            )
                    if recalled:
                        original["recalled"] = recalled
                        understanding = await complete(
                            llm,
                            evaluator,
                            "understand",
                            _PROMPTS["understand"],
                            {**original, "recalled": recalled, "issues": feedback},
                            state["correlation_id"],
                            validate_understanding,
                        )
                    step = "evidence"
                if step == "evidence":
                    evidence, passages = await investigate(
                        "\n".join(t["task"] for t in repair_tasks)
                        or understanding["question"],
                        llm,
                        False,
                    )
                    if evidence["action"] == "understand":
                        feedback = [evidence["reason"]]
                        step = "understand"
                        continue
                    step = "answer"
                await current()

                async def validate_answer(value: dict[str, Any]) -> None:
                    sources = {i for c in value["claims"] for i in c["sources"]}
                    addresses = re.findall(r"nyx-book:([^\s)]+)", value["speak"])
                    links = re.findall(
                        r"\[[^\]\n]+\]\(nyx-book:(\d+)\)",
                        value["speak"],
                    )
                    if len(addresses) != len(links):
                        raise ValueError(
                            "speak: nyx-book links require actual integer indices"
                        )
                    cited = {int(i) for i in links}
                    unread = (sources | cited) - passages.keys()
                    if unread:
                        raise NeedEvidence(sorted(unread), value)
                    selections = value["quote_selections"]
                    if len({s["index"] for s in selections}) != len(selections):
                        raise ValueError("quote_selections must have unique indices")
                    for selection in selections:
                        if (
                            selection["index"] not in cited
                            or selection["text"]
                            not in passages[selection["index"]]["text"]
                        ):
                            raise ValueError(
                                "quote_selections must be exact cited text"
                            )
                    selected_text = {s["index"]: s["text"] for s in selections}
                    for index in cited:
                        if (
                            not understanding["promotion_request"]
                            and passages[index].get("promotion")
                            and (
                                index not in selected_text
                                or re.search(
                                    policy("reading", "study.promotion_pattern", str),
                                    selected_text[index],
                                )
                            )
                        ):
                            raise ValueError(
                                "promotional source requires clean quote_selections: "
                                + str(index)
                            )
                    if not sources <= cited:
                        raise ValueError(
                            _PROMPTS["missing_links"].format(
                                links=" ".join(
                                    f"[{_COPY['source_label'].format(index=i)}]"
                                    f"(nyx-book:{i})"
                                    for i in sorted(sources - cited)
                                )
                            )
                        )

                try:
                    answer = await complete(
                        llm,
                        evaluator,
                        "answer",
                        system + "\n" + _PROMPTS["answer"],
                        {
                            "original": original,
                            "understanding": understanding,
                            "evidence": evidence,
                            "passages": list(passages.values()),
                            "coverage": context["coverage"],
                            "issues": feedback,
                            "budget_exhausted": evidence_remaining == 0,
                            "tool_feedback": tool_receipts,
                            "interpretations": interpretations,
                        },
                        state["correlation_id"],
                        validate_answer,
                    )
                except NeedEvidence as missing:
                    for index in missing.indices:
                        if not 1 <= index <= context["book"]["total_paragraphs"]:
                            continue
                        found = await read(
                            "book_excerpt", {"start": index, "end": index}
                        )
                        for passage in found:
                            passages[passage["index"]] = passage
                        evidence["read_log"].append(
                            {
                                "tool": "book_excerpt",
                                "args": {"start": index, "end": index},
                                "returned_indices": [p["index"] for p in found],
                            }
                        )
                    evidence["read_scope"] = list(passages)
                    feedback = [_PROMPTS["source_added"]]
                    continue
                await current()

                async def validate_review(value: dict[str, Any]) -> None:
                    if value["next_step"] == "evidence" and not value["tasks"]:
                        raise ValueError("evidence review requires concrete tasks")
                    if not {c["text"] for c in answer["claims"]} <= {
                        c["text"] for c in value["conclusions"]
                    }:
                        raise ValueError(
                            "conclusions must cover each answer claim text"
                        )
                    if {q["question"] for q in value["questions"]} != set(
                        understanding["subquestions"]
                    ):
                        raise ValueError("questions must cover each user subquestion")
                    statuses = {q["question"]: q["status"] for q in value["questions"]}
                    for task in value["tasks"]:
                        if statuses.get(task["question"]) != "missing" or not any(
                            task["user_requirement"] in w for w in user_words
                        ):
                            raise ValueError(
                                "repair task requires a missing user subquestion"
                                " and verbatim current user requirement"
                            )
                    if (
                        value["next_step"] == "accept"
                        and (evidence_remaining or review_evidence_remaining)
                        and "missing" in statuses.values()
                        and not value["tasks"]
                    ):
                        raise ValueError(
                            "missing user requirement needs a concrete repair task"
                        )
                    for item in value["interpretations"]:
                        if not any(item["user_text"] in w for w in user_words):
                            raise ValueError(
                                "interpretation must quote current user words"
                            )
                        if item["verdict"] == "correct":
                            if item["dimension"] is None or not item["difference"]:
                                raise ValueError(
                                    "correction requires a substantive difference"
                                )
                        elif item["dimension"] is not None or item["difference"]:
                            raise ValueError("acceptable paraphrase has no correction")

                review = await complete(
                    llm,
                    evaluator,
                    "review"
                    if evidence_remaining or review_evidence_remaining
                    else "review_partial",
                    _PROMPTS["review"]
                    if evidence_remaining or review_evidence_remaining
                    else _PROMPTS["review_partial"],
                    {
                        "original": original,
                        "understanding": understanding,
                        "evidence": evidence,
                        "passages": list(passages.values()),
                        "coverage": context["coverage"],
                        "answer": answer,
                        "review_evidence_remaining": review_evidence_remaining,
                        "budget_exhausted": evidence_remaining == 0,
                        "total_exhausted": evidence_remaining == 0
                        and review_evidence_remaining == 0,
                        "tool_feedback": tool_receipts,
                    },
                    state["correlation_id"],
                    validate_review,
                )
                review_questions = {
                    q["question"]: q["status"] for q in review["questions"]
                }
                interpretations = review["interpretations"] or interpretations
                unsupported = [c for c in review["conclusions"] if not c["supported"]]
                if review["next_step"] == "accept" and (
                    unsupported or "inaccurate" in review_questions.values()
                ):
                    review["next_step"] = "answer"
                    review["issues"].extend(
                        c["excess"] or c["contrary"] or c["text"] for c in unsupported
                    )
                    review["issues"].extend(
                        q
                        for q, status in review_questions.items()
                        if status == "inaccurate"
                    )
                if (
                    review["next_step"] == "accept"
                    and (evidence_remaining or review_evidence_remaining)
                    and "missing" in review_questions.values()
                ):
                    review["next_step"] = "evidence"
                for check in evidence["checks"]:
                    status = review_questions[check["question"]]
                    if status != "answered":
                        check.update(
                            status="missing",
                            gap="; ".join(
                                [t["gap"] for t in review["tasks"]] or review["issues"]
                            )
                            or check["question"],
                        )
                    elif check["sources"]:
                        check.update(status="supported", gap="")
                if review["next_step"] != "accept":
                    feedback = review["issues"]
                    step = review["next_step"]
                    repair_tasks = review["tasks"]
                    if step == "evidence" and not evidence_remaining:
                        evidence_remaining = review_evidence_remaining
                        review_evidence_remaining = 0
                        targeted = True
                    continue
                if interpretations and not interpretation_applied:
                    interpretation_applied = True
                    feedback = []
                    step = "answer"
                    continue
                state["speak"], state["think"] = [answer["speak"]], [answer["think"]]
                context["active_requirements"] = understanding["carry_requirements"]
                context["quote_selections"] = answer["quote_selections"]
                context["answer_status"] = (
                    "partial"
                    if any(c["status"] == "missing" for c in evidence["checks"])
                    else "complete"
                )
                state["reading_context"] = json.dumps(context, ensure_ascii=False)
                if await commit(state) == "committed":
                    return
                raise _Refresh
            if evidence.get("action") == "finish":
                # The review budget ends with visible, source-only partial
                # results. No rejected interpretation is published as fact.
                indices = list(
                    dict.fromkeys(
                        i
                        for c in evidence["checks"]
                        for i in [*c["direct_sources"], *c["sources"]]
                        if i in passages
                        and (
                            understanding["promotion_request"]
                            or not passages[i].get("promotion")
                        )
                    )
                )[: _PARAMS["search_limit"]]
                state["speak"] = [
                    _COPY["partial_reply"].format(
                        questions="\n".join(understanding["subquestions"]),
                        sources=" ".join(
                            f"[{_COPY['source_label'].format(index=i)}](nyx-book:{i})"
                            for i in indices
                        ),
                        gaps="\n".join(dict.fromkeys(evidence["gaps"])),
                    )
                ]
                state["think"] = []
                context.update(
                    answer_status="partial",
                    active_requirements=understanding["carry_requirements"],
                )
                state["reading_context"] = json.dumps(context, ensure_ascii=False)
                if await commit(state) == "committed":
                    return
                raise _Refresh
            raise ValueError(_COPY["error_stage_budget"])
        except _Refresh:
            continue
