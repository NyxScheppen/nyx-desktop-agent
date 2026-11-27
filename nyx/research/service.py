"""Two origins, one durable research execution and settlement path."""

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any, cast

from nyx.behavior.context import behavior_context, tool_execution_id
from nyx.behavior.service import BehaviorService
from nyx.behavior.store import BehaviorStore, SkillStore, skill_snapshot
from nyx.config import ExplorationConfig
from nyx.db import DATABASE_ERRORS
from nyx.enums import ActivityStatus, ActivityType, EventType
from nyx.eval.evaluator import Evaluator
from nyx.events.event import internal_event
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.policy import policy
from nyx.research.engine import ResearchEngine, ResearchWaiting
from nyx.tools.registry import ToolRegistry
from nyx.types import Activity

_LOGGER = logging.getLogger(__name__)


class _RunCheckpointStore:
    """The shared engine's progress sink, independent of activity/chat cleanup."""

    def __init__(self, service: "ResearchService", record: dict[str, Any]) -> None:
        self.service, self.record = service, record

    async def update(self, activity: Activity) -> None:
        if self.service.cancellation_requested(self.record["id"]):
            self.record["cancel_requested"] = True
        self.record["activity"] = asdict(activity)
        checkpoint = activity.progress["exploration"]
        self.record["status"] = checkpoint["state"]
        await self.service.store.save("research_run", self.record)


class ResearchService:
    def __init__(
        self,
        store: BehaviorStore,
        skills: SkillStore,
        behavior: BehaviorService,
        memory: MemoryFacade,
        tools: ToolRegistry,
        llm: LlmClient,
        evaluator: Evaluator,
        config: ExplorationConfig,
        canon: str,
        fetch_source: Callable[..., Awaitable[dict[str, Any]]] | None,
    ) -> None:
        self.store, self.skills, self.behavior = store, skills, behavior
        self.memory, self.tools, self.llm = memory, tools, llm
        self.evaluator, self.config, self.canon = evaluator, config, canon
        self.fetch_source = fetch_source
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._cancel_requested: set[str] = set()
        self.resume_activity: (
            Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None
        ) = None

    def cancellation_requested(self, run_id: str) -> bool:
        return run_id in self._cancel_requested

    async def recover(self) -> None:
        """Expose abandoned workers as resumable; never silently restart research."""
        async with self.store.db.transaction():
            cursor = await self.store.db.conn.execute(
                "SELECT id FROM research_run WHERE status NOT IN "
                "('failed','cancelled','waiting_user','paused','uncertain')"
            )
            for row in await cursor.fetchall():
                record = await self.store.get("research_run", row["id"])
                if record is not None and not record.get("settled"):
                    record.update(status="paused", interrupted_at=time.time())
                    await self.store.save("research_run", record)

    async def deep_research(
        self,
        query: str,
        run_id: str | None = None,
        answer: str | None = None,
    ) -> dict[str, Any]:
        context = behavior_context.get() or {}
        correlation_id = str(
            context.get("correlation_id")
            or tool_execution_id.get()
            or hashlib.sha256(query.encode()).hexdigest()
        )
        key = tool_execution_id.get() or f"{correlation_id}:{query}"
        if run_id is None:
            run_id = "research:" + hashlib.sha256(key.encode()).hexdigest()
            result = await self.run(run_id, query, "dialogue", correlation_id)
        else:
            result = await self.resume(run_id, answer)
        compact: dict[str, Any] = {
            "run_id": run_id,
            "status": result.get("status", result.get("outcome")),
            "summary": str(result.get("summary", ""))[
                : policy(
                    "research",
                    "service.parameters.deep_research_summary_chars_or_items",
                    int,
                )
            ],
        }
        if result.get("error"):
            compact["error"] = str(result["error"])[
                : policy(
                    "research",
                    "service.parameters.deep_research_compact_error_chars_or_items",
                    int,
                )
            ]
        if result.get("question"):
            compact["question"] = result["question"]
        compact["report"] = result.get("report", [])
        return compact

    async def run_activity(
        self,
        activity: Activity,
        notify: Callable[[Activity], Awaitable[None]],
    ) -> dict[str, Any]:
        question = str(
            activity.progress.get("intent")
            or activity.progress.get("description")
            or activity.id
        )
        cid = str(activity.progress.get("correlation_id") or activity.id)
        return await self.run(
            f"research:activity:{activity.id}",
            question,
            "desire",
            cid,
            activity=activity,
            notify=notify,
        )

    async def run(
        self,
        run_id: str,
        question: str,
        origin: str,
        correlation_id: str,
        *,
        activity: Activity | None = None,
        notify: Callable[[Activity], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            task = asyncio.current_task()
            if task is not None:
                self._tasks[run_id] = task
            try:
                return await self._run(
                    run_id, question, origin, correlation_id, activity, notify
                )
            except DATABASE_ERRORS as error:
                durable = await self.store.get("research_run", run_id)
                if durable is None:
                    raise
                if durable.get("settled"):
                    return cast(dict[str, Any], durable["result"])
                durable.update(
                    status="uncertain", last_error=f"{type(error).__name__}: {error}"
                )
                await self.store.save("research_run", durable)
                return {
                    "run_id": run_id,
                    "status": "uncertain",
                    "error": str(error),
                    "summary": policy("research", "service.copy.run_text_6", str),
                }
            finally:
                self._tasks.pop(run_id, None)

    async def _run(
        self,
        run_id: str,
        question: str,
        origin: str,
        correlation_id: str,
        activity: Activity | None,
        notify: Callable[[Activity], Awaitable[None]] | None,
    ) -> dict[str, Any]:
        record = await self.store.get("research_run", run_id)
        if record is None:
            inherited = (behavior_context.get() or {}).get("skill_snapshot_raw")
            snapshot = (
                inherited
                if inherited and inherited["name"] == "discussion"
                else skill_snapshot(await self.skills.load("discussion"))
            )
            lessons = await self.memory.search_behavior_experiences(
                question,
                "discussion",
            )
            strategies = [
                item.content.split(
                    policy("research", "service.copy.run_text_9", str), 1
                )[0][
                    : policy(
                        "research",
                        "service.parameters.run_strategies_chars_or_items",
                        int,
                    )
                ]
                for item in lessons[
                    : policy(
                        "research",
                        "service.parameters.run_strategies_chars_or_items_2",
                        int,
                    )
                ]
            ]
            history = await self.store.list_records(
                "research_run", policy("research", "service.history_limit", int)
            )
            scope = activity.progress.get("research_scope") if activity else None
            seen_questions = {question}
            previous_research: list[dict[str, str]] = []
            history_chars = policy("research", "engine.context.history_chars", int)
            history_limit = policy(
                "research", "service.parameters.run_previous_research_chars_or_items",
                int,
            )
            for item in history:
                prior_result = cast(dict[str, Any], item.get("result") or {})
                prior_question = item["question"]
                prior_scope = item["activity"]["progress"].get("research_scope")
                if (
                    prior_question in seen_questions
                    or prior_scope != scope
                    or not prior_result.get("core_discovery")
                ):
                    continue
                seen_questions.add(prior_question)
                previous_research.append({
                    "question": prior_question[:history_chars],
                    "conclusion": prior_result["core_discovery"][:history_chars],
                })
                if len(previous_research) >= history_limit:
                    break
            initial_progress: dict[str, Any] = (
                dict(activity.progress) if activity else {"intent": question}
            )
            checkpoint: dict[str, Any] = dict(initial_progress.get("exploration") or {})
            checkpoint.update(
                skill_fragment=snapshot["system_fragment"],
                require_citations=True,
                strategy_lessons=list(dict.fromkeys(strategies))[
                    : policy(
                        "research",
                        "service.parameters.run_strategy_lessons_chars_or_items",
                        int,
                    )
                ],
                previous_research=previous_research,
            )
            initial_progress.update(
                exploration=checkpoint, correlation_id=correlation_id
            )
            if activity is None:
                activity = Activity(
                    run_id,
                    ActivityType.FREE_EXPLORATION,
                    "research",
                    ActivityStatus.RUNNING,
                    initial_progress,
                    time.time(),
                )
            else:
                activity.progress = initial_progress
            record = {
                "id": run_id,
                "origin": origin,
                "correlation_id": correlation_id,
                "status": "deciding",
                "created_at": time.time(),
                "question": question,
                "skill_snapshot_raw": snapshot,
                "activity": asdict(activity),
            }
            await self.store.save("research_run", record, insert_only=True)
            _LOGGER.info(
                policy("research", "service.copy.run_text", str), run_id, origin
            )
        saved_activity = cast(dict[str, Any], record["activity"])
        if activity is None:
            activity = Activity(
                saved_activity["id"],
                ActivityType(saved_activity["type"]),
                saved_activity["schedule_block_id"],
                ActivityStatus(saved_activity["status"]),
                saved_activity["progress"],
                saved_activity["started_at"],
                saved_activity.get("ended_at"),
            )
        else:
            activity.progress = saved_activity["progress"]
        if record.get("settled"):
            if notify:
                await notify(activity)
            return cast(dict[str, Any], record["result"])

        async def progress(current: Activity) -> None:
            # Persistence precedes notification. SSE failures cannot rewrite results.
            try:
                await self.behavior.bus.publish(
                    internal_event(
                        EventType.RESEARCH_PROGRESS,
                        {
                            "run_id": run_id,
                            "status": record["status"],
                            "correlation_id": correlation_id,
                        },
                        correlation_id,
                    )
                )
                if notify:
                    await notify(current)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception(
                    policy("research", "service.copy.progress_text", str), run_id
                )

        if record.get("cancel_requested"):
            record["status"] = "cancelled"
            return await self._settle(
                record,
                activity,
                {
                    "error": policy("research", "service.copy.run_text_3", str),
                    "outcome": "cancelled",
                    "summary": policy("research", "service.copy.run_text_4", str),
                },
            )

        if origin == "dialogue" and not self.config.web_enabled:
            record["status"] = "failed"
            return await self._settle(
                record,
                activity,
                {
                    "outcome": "failed",
                    "error": policy("research", "service.copy.run_text_7", str),
                    "summary": policy("research", "service.copy.run_text_8", str),
                    "report": [],
                },
            )

        engine = ResearchEngine(
            self.llm,
            self.evaluator,
            self.tools,
            _RunCheckpointStore(self, record),
            self.memory,
            self.config,
            self.canon,
            fetch_source=self.fetch_source,
            notify_progress=progress,
        )
        paused_checkpoint = activity.progress.get("exploration", {})
        if paused_checkpoint.pop("pause_reason", None) == "provider":
            record.pop("last_error", None)
        try:
            result = await engine.run(activity)
            record["status"] = "completed"
        except ResearchWaiting as waiting:
            record["status"] = "waiting_user"
            await self.store.save("research_run", record)
            await progress(activity)
            return {
                "run_id": run_id,
                "status": "waiting_user",
                "question": str(waiting),
                "notice": policy("research", "service.copy.run_text_2", str),
            }
        except asyncio.CancelledError:
            if run_id not in self._cancel_requested:
                record["status"] = "paused"
                await self.store.save("research_run", record)
                raise
            record["status"] = "cancelled"
            result = {
                "error": policy("research", "service.copy.run_text_3", str),
                "outcome": "cancelled",
                "summary": policy("research", "service.copy.run_text_4", str),
            }
        except DATABASE_ERRORS:
            raise
        except Exception as error:
            checkpoint = activity.progress["exploration"]
            if checkpoint.get("pause_reason") == "provider":
                record.update(
                    status="paused",
                    last_error=policy("research", "engine.copy.provider_paused", str),
                )
                await self.store.save("research_run", record)
                await progress(activity)
                return {"run_id": run_id, "status": "paused"}
            record["status"] = "failed"
            result = {
                "error": str(error) or type(error).__name__,
                "outcome": "failed",
                "summary": policy("research", "service.copy.run_text_5", str),
                "actions": activity.progress.get("exploration", {}).get("actions", []),
            }
        result = await self._settle(record, activity, result)
        await progress(activity)
        return result

    async def _settle(
        self,
        record: dict[str, Any],
        activity: Activity,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        checkpoint = activity.progress.get("exploration", {})
        record.update(
            result={**result, "run_id": record["id"], "status": record["status"]},
            finished_at=time.time(),
        )
        originals = {
            "run_id": record["id"],
            "occurred_at": record["created_at"],
            "signal_ids": [record["id"]],
            "trigger_type": "research_outcome",
            "topic_raw": record["question"],
            "execution_status": record["status"],
            "actions_raw": checkpoint.get("actions", []),
            "result_raw": result,
            "source_excerpts_raw": checkpoint.get("source_receipts", []),
            "node_trace_raw": checkpoint.get("node_trace", []),
            "unresolved_questions": checkpoint.get("unresolved_questions", []),
            "skill_snapshot_raw": record["skill_snapshot_raw"],
            "evidence_ids": ["run", "actions", "result", "skill"],
        }
        async with self.store.db.transaction():
            event = await self.behavior.submit(
                record["id"],
                "discussion",
                originals,
                record["correlation_id"],
                in_transaction=True,
            )
            record["settled"] = True
            await self.store.save("research_run", record)
        await self.behavior.bus.announce_committed(event)
        _LOGGER.info(
            policy("research", "service.copy.settle_text", str),
            record["id"],
            record["status"],
        )
        return cast(dict[str, Any], record["result"])

    async def resume(self, run_id: str, answer: str | None = None) -> dict[str, Any]:
        if run_id in self._tasks:
            raise ValueError(policy("research", "service.copy.resume_text", str))
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            record = await self.store.get("research_run", run_id)
            if record is None:
                raise ValueError(policy("research", "service.copy.resume_text_2", str))
            if record.get("settled"):
                return cast(dict[str, Any], record["result"])
            if record["status"] == "waiting_user":
                if not answer or not answer.strip():
                    raise ValueError(
                        policy("research", "service.copy.resume_text_4", str)
                    )
                checkpoint = record["activity"]["progress"]["exploration"]
                checkpoint["actions"].append(
                    {
                        "name": "user_answer",
                        "text": answer,
                        "question": checkpoint["waiting_question"],
                        "status": "success",
                        "ok": True,
                        "completed_at": time.time(),
                    }
                )
                checkpoint["state"] = "deciding"
                checkpoint["question"] += (
                    policy("research", "service.copy.resume_text_3", str) + answer
                )
                record["status"] = "deciding"
                await self.store.save("research_run", record)
        if record["origin"] == "desire" and self.resume_activity is not None:
            return await self.resume_activity(record)
        return await self.run(
            run_id, record["question"], record["origin"], record["correlation_id"]
        )

    async def cancel(self, run_id: str) -> dict[str, Any]:
        async with self.store.db.transaction():
            record = await self.store.get("research_run", run_id)
            if record is None:
                raise ValueError(policy("research", "service.copy.resume_text_2", str))
            if record.get("settled"):
                return cast(dict[str, Any], record["result"])
            self._cancel_requested.add(run_id)
            record["cancel_requested"] = True
            await self.store.save("research_run", record)
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            done, _ = await asyncio.wait(
                {task},
                timeout=policy("research", "service.parameters.cancel_timeout", int),
            )
            if not done:
                return {"run_id": run_id, "status": "cancel_requested"}
            # Retrieve exceptions; the owning caller still receives its own result.
            if not task.cancelled():
                task.exception()
        latest = await self.store.get("research_run", run_id)
        if latest is not None and latest.get("settled"):
            return cast(dict[str, Any], latest["result"])
        return await self.run(
            run_id, record["question"], record["origin"], record["correlation_id"]
        )
