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
from nyx.enums import ActivityStatus, ActivityType, EventType, MemoryKind
from nyx.eval.evaluator import Evaluator
from nyx.events.event import internal_event
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
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
            "summary": str(result.get("summary", ""))[:300],
        }
        if result.get("error"):
            compact["error"] = str(result["error"])[:500]
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
                    "summary": "研究写入尚未确认，恢复时将核对持久回执。",
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
            lessons = await self.memory.search(question, kind=MemoryKind.LESSON)
            recent_reflections = await self.store.list_records("behavior_reflection")
            strategies = [
                item["experience_content_raw"].split("【原文证据】", 1)[0][:2000]
                for item in recent_reflections
                if item["target_skill"] == "discussion"
                and item.get("experience_id")
                and item.get("experience_content_raw")
            ][:3]
            strategies.extend(
                item.content.split("【原文证据】", 1)[0][:2000] for item in lessons[:5]
            )
            history = await self.store.list_records("research_run", 20)
            initial_progress: dict[str, Any] = (
                dict(activity.progress) if activity else {"intent": question}
            )
            checkpoint: dict[str, Any] = dict(initial_progress.get("exploration") or {})
            checkpoint.update(
                skill_fragment=snapshot["system_fragment"],
                require_citations=True,
                strategy_lessons=list(dict.fromkeys(strategies))[:5],
                previous_research=[
                    {
                        "question": item["question"],
                        "status": item["status"],
                        "conclusion": cast(
                            dict[str, Any], item.get("result") or {}
                        ).get("summary", ""),
                        "failure_reason": cast(
                            dict[str, Any], item.get("result") or {}
                        ).get("error", ""),
                        "unresolved_questions": item["activity"]["progress"]
                        .get("exploration", {})
                        .get("unresolved_questions", []),
                    }
                    for item in history[:5]
                    if item["status"] in {"completed", "failed"}
                ],
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
            _LOGGER.info("研究已受理 run=%s origin=%s", run_id, origin)
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
                _LOGGER.exception("研究通知失败 run_id=%s", run_id)

        if record.get("cancel_requested"):
            record["status"] = "cancelled"
            return await self._settle(
                record,
                activity,
                {
                    "error": "研究已取消",
                    "outcome": "cancelled",
                    "summary": "用户取消了研究，保留此前已确认结果。",
                },
            )

        if origin == "dialogue" and not self.config.web_enabled:
            record["status"] = "failed"
            return await self._settle(
                record,
                activity,
                {
                    "outcome": "failed",
                    "error": "联网没开，无法执行深度研究",
                    "summary": "联网没开，研究未执行。",
                    "report": [],
                },
            )

        engine = ResearchEngine(
            self.llm,
            self.evaluator,
            self.tools,
            _RunCheckpointStore(self, record),
            None,
            self.memory,
            self.config,
            self.canon,
            fetch_source=self.fetch_source,
            notify_progress=progress,
        )
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
                "notice": "研究等待用户补充信息，尚未完成。",
            }
        except asyncio.CancelledError:
            if run_id not in self._cancel_requested:
                record["status"] = "paused"
                await self.store.save("research_run", record)
                raise
            record["status"] = "cancelled"
            result = {
                "error": "研究已取消",
                "outcome": "cancelled",
                "summary": "用户取消了研究，保留此前已确认结果。",
            }
        except DATABASE_ERRORS:
            raise
        except Exception as error:
            record["status"] = "failed"
            result = {
                "error": str(error) or type(error).__name__,
                "outcome": "failed",
                "summary": "研究失败，已保留此前提交的有效知识。",
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
        _LOGGER.info("研究已结算 run=%s status=%s", record["id"], record["status"])
        return cast(dict[str, Any], record["result"])

    async def resume(self, run_id: str, answer: str | None = None) -> dict[str, Any]:
        if run_id in self._tasks:
            raise ValueError("研究仍在执行，请等待当前步骤结束")
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            record = await self.store.get("research_run", run_id)
            if record is None:
                raise ValueError("研究记录不存在")
            if record.get("settled"):
                return cast(dict[str, Any], record["result"])
            if record["status"] == "waiting_user":
                if not answer or not answer.strip():
                    raise ValueError("需要回答研究中的待澄清问题")
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
                checkpoint["question"] += "\n用户补充：" + answer
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
                raise ValueError("研究记录不存在")
            if record.get("settled"):
                return cast(dict[str, Any], record["result"])
            self._cancel_requested.add(run_id)
            record["cancel_requested"] = True
            await self.store.save("research_run", record)
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=10)
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
