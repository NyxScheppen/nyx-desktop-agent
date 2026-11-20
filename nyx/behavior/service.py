"""Durable behavior reflection and its downstream skill application."""

import asyncio
import difflib
import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any, cast

from nyx.behavior.context import behavior_context
from nyx.behavior.store import BehaviorStore, SkillStore, skill_snapshot
from nyx.enums import EventType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import internal_event
from nyx.llm.client import LlmClient
from nyx.policy import policy
from nyx.types import Event, Skill

if TYPE_CHECKING:
    from nyx.memory.facade import MemoryFacade

_LOGGER = logging.getLogger(__name__)
_SYSTEM = policy("behavior", "service._SYSTEM", str)


class BehaviorService:
    def __init__(
        self,
        store: BehaviorStore,
        skills: SkillStore,
        bus: EventBus,
        llm: LlmClient,
        evaluator: Evaluator | None = None,
    ) -> None:
        self.store, self.skills, self.bus, self.llm = store, skills, bus, llm
        self._lock = asyncio.Lock()
        self.evaluator = evaluator

    async def submit(
        self,
        key: str,
        target_skill: str,
        evidence: dict[str, Any],
        correlation_id: str,
        *,
        in_transaction: bool = False,
    ) -> Event:
        digest = hashlib.sha256(key.encode()).hexdigest()
        record_id = f"behavior:{digest}"
        current = await self.skills.load(target_skill)
        evidence = {
            "occurred_at": time.time(),
            "signal_ids": [key],
            **evidence,
        }
        record: dict[str, Any] = {
            "id": record_id,
            "status": "queued",
            "created_at": time.time(),
            "correlation_id": correlation_id,
            "target_skill": target_skill,
            "used_skill_version": evidence.get("skill_snapshot_raw", {}).get(
                "version", current.version
            ),
            "base_skill_version": current.version,
            "skill_update_status": "pending",
            "evidence": evidence,
            "logs": [],
        }
        event = internal_event(
            EventType.BEHAVIOR_REFLECTION,
            {"reflection_id": record_id},
            correlation_id,
        )
        event.id = f"{record_id}:requested"
        # Stable event content and timestamp across replay are required by the bus.
        event.timestamp = record["created_at"]
        prior = await self.store.get("behavior_reflection", record_id)
        if prior is not None:
            event.timestamp = prior["created_at"]
        if in_transaction:
            await self.store.save("behavior_reflection", record, insert_only=True)
            await self.bus.append_in_transaction(event)
        else:
            async with self.store.db.transaction():
                await self.store.save("behavior_reflection", record, insert_only=True)
                await self.bus.append_in_transaction(event)
            await self.bus.announce_committed(event)
        _LOGGER.info(
            policy("behavior", "service.copy.submit_text", str), record_id, target_skill
        )
        return event

    async def record_signal(
        self,
        kind: str,
        topic: str,
        summary: str,
        correlation_id: str,
        evidence: list[dict[str, str]] | None = None,
    ) -> None:
        context = behavior_context.get()
        if context is not None and context.get("scope") == "reading":
            return
        if context is None or not context.get("skill_snapshot_raw"):
            saved = await self.skills.execution_context(correlation_id)
            if saved is not None:
                context = {**saved, **(context or {}), "scenario": "reply"}
        # Raw research/reflection output is not a character-voice failure.
        if context is None or context.get("scenario") != "reply":
            return
        if kind == "ooc" and (
            context.get("output_module") != "expression"
            or context.get("output_type") not in {"speak", "think", "initiate_chat"}
        ):
            return
        raw: dict[str, Any] = {
            **context,
            "trigger_type": kind,
            "signal_topic": topic,
            "signal_raw": summary,
            "user_feedback_raw": evidence or [],
            "evidence_ids": ["signal", "reply", "skill"],
        }
        key = f"{kind}:{correlation_id}:{summary}"
        await self.submit(
            key,
            cast(dict[str, Any], context["skill_snapshot_raw"])["name"],
            raw,
            correlation_id,
        )

    async def _progress(
        self,
        record: dict[str, Any],
        stage: str,
        **details: Any,
    ) -> None:
        record["logs"].append({"stage": stage, "at": time.time(), **details})
        event = internal_event(
            EventType.BEHAVIOR_PROGRESS,
            {"reflection_id": record["id"], "stage": stage, **details},
            record["correlation_id"],
        )
        async with self.store.db.transaction():
            await self.store.save("behavior_reflection", record)
            await self.bus.append_in_transaction(event)
        _LOGGER.info(
            policy("behavior", "service.copy.progress_text", str), record["id"], stage
        )
        try:
            await self.bus.announce_committed(event)
        except Exception:
            _LOGGER.exception(
                policy("behavior", "service.copy.progress_text_2", str), record["id"]
            )

    async def apply_event(self, event: Event, memory: "MemoryFacade") -> None:
        """B4 commits before B5; retries recover either boundary independently."""
        async with self._lock:
            record = await self.store.get(
                "behavior_reflection", event.content["reflection_id"]
            )
            if record is None:
                return
            if record["status"] == "completed":
                if record.get("skill_update_status") != "invalid_output" and (
                    not record["logs"]
                    or record["logs"][-1]["stage"] != "reflection_completed"
                ):
                    await self._progress(
                        record,
                        "reflection_completed",
                        skill_update_status=record["skill_update_status"],
                    )
                return
            try:
                if record["status"] != "persisted":
                    current = await self.skills.load(record["target_skill"])
                    if not record.get("input_skill_snapshot"):
                        record["base_skill_version"] = current.version
                    record["reflection_started_at"] = time.time()
                    await self._progress(record, "reflection_started")
                    if not record.get("reflection"):
                        if not await self._reflect(record, current):
                            return
                    reflection = record["reflection"]
                    record["reflection_completed_at"] = time.time()
                    lesson = None
                    if reflection["evidence_refs"] and reflection["next_strategy"]:
                        record["experience_content_raw"] = self._content(record)
                        lesson = await memory.prepare_behavior_experience(
                            record["id"],
                            record["experience_content_raw"],
                            policy("behavior", "service.copy.apply_event_text", str)
                            + reflection["next_strategy"][
                                : policy(
                                    "behavior",
                                    "service.parameters.apply_event_lesson_chars_or_items",
                                    int,
                                )
                            ],
                        )
                    # B4: no skill writes in this transaction.
                    async with self.store.db.transaction():
                        if lesson is not None:
                            await memory.commit_behavior_experience(
                                lesson, record["correlation_id"]
                            )
                            record["experience_id"] = lesson.id
                        record["status"] = "persisted"
                        record["persisted_at"] = time.time()
                        await self.store.save("behavior_reflection", record)
                    await self._progress(record, "experience_persisted")
                await self._apply_skill(record)
            except asyncio.CancelledError:
                # Durable queued/persisted rows and event delivery remain resumable.
                raise
            except Exception as error:
                committed = await self.store.get("behavior_reflection", record["id"])
                if committed is not None:
                    record = committed
                record["last_error"] = f"{type(error).__name__}: {error}"
                await self._progress(record, "failed", error=record["last_error"])
                raise

    async def _reflect(self, record: dict[str, Any], current: Skill) -> bool:
        """Keep invalid originals; allow at most three corrected model attempts."""
        record.setdefault("input_skill_snapshot", skill_snapshot(current))
        attempts: list[dict[str, Any]] = record.setdefault("model_attempts", [])
        while len(attempts) < policy(
            "behavior", "service.parameters.reflect_value_limit", int
        ):
            raw = record.get("reflection_output_raw")
            if not raw:
                async with asyncio.timeout(
                    policy("behavior", "service.parameters.reflect_timeout", int)
                ):
                    output = await self.llm.complete(
                        [
                            {"role": "system", "content": _SYSTEM},
                            {
                                "role": "user",
                                "content": json.dumps(
                                    {
                                        "evidence": record["evidence"],
                                        "current_skill": record["input_skill_snapshot"],
                                        "previous_validation_error": record.get(
                                            "validation_error"
                                        ),
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                        ],
                        module="behavior",
                        output_type="behavior_reflection",
                        correlation_id=record["correlation_id"],
                        json_mode=True,
                    )
                raw = record["reflection_output_raw"] = output.content
                record["call_id"] = output.call_id
                await self.store.save("behavior_reflection", record)
                if self.evaluator is not None:
                    try:
                        await self.evaluator.evaluate(output)
                    except Exception:
                        _LOGGER.exception(
                            policy("behavior", "service.copy.reflect_text_2", str),
                            record["id"],
                        )
            try:
                parsed = self._validate(json.loads(raw), record)
            except (ValueError, TypeError) as error:
                attempts.append(
                    {
                        "output_raw": raw,
                        "call_id": record.get("call_id"),
                        "at": time.time(),
                        "validation_error": str(error),
                    }
                )
                record["validation_error"] = str(error)
                record.pop("reflection_output_raw", None)
                await self._progress(record, "validation_failed", error=str(error))
                continue
            if (
                parsed["skill_change_decision"] == "update"
                and parsed["proposed_skill_text"]
                == record["input_skill_snapshot"]["system_fragment"]
            ):
                parsed["skill_change_decision"] = "no_change"
                parsed["skill_change_reason"] = policy(
                    "behavior", "service.copy.reflect_text", str
                )
            record["reflection"] = parsed
            await self.store.save("behavior_reflection", record)
            return True
        record.update(
            status="completed",
            skill_update_status="invalid_output",
            last_error=record.get("validation_error"),
        )
        await self._progress(record, "reflection_failed", error=record["last_error"])
        return False

    @staticmethod
    def _validate(value: object, record: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError(policy("behavior", "service.copy.validate_text", str))
        data = cast(dict[str, Any], value)
        for field in (
            "observation",
            "cause_analysis",
            "effective_strategy",
            "next_strategy",
            "applicability",
            "skill_change_reason",
            "proposed_skill_text",
        ):
            if not isinstance(data.get(field), str):
                raise ValueError(
                    policy("behavior", "service.templates.validate_text", str).format(
                        p0=field
                    )
                )
        refs = data.get("evidence_refs")
        allowed = record["evidence"].get("evidence_ids", [])
        if not isinstance(refs, list) or any(
            not isinstance(ref, str) or ref not in allowed
            for ref in cast(list[object], refs)
        ):
            raise ValueError(policy("behavior", "service.copy.validate_text_2", str))
        decision = data.get("skill_change_decision")
        if decision not in {"update", "no_change", "insufficient_evidence"}:
            raise ValueError(policy("behavior", "service.copy.validate_text_3", str))
        if decision == "update" and (
            not refs
            or not data["proposed_skill_text"].strip()
            or len(data["proposed_skill_text"])
            > policy("behavior", "service.parameters.validate_value_limit", int)
        ):
            raise ValueError(policy("behavior", "service.copy.validate_text_4", str))
        return data

    async def _apply_skill(self, record: dict[str, Any]) -> None:
        decision = record["reflection"]["skill_change_decision"]
        if decision == "update":
            try:
                async with self.store.db.transaction():
                    updated = await self.skills.apply(
                        record["id"],
                        record["target_skill"],
                        record["base_skill_version"],
                        record["reflection"]["proposed_skill_text"],
                    )
                    before = record["input_skill_snapshot"]["system_fragment"]
                    record["skill_diff"] = "\n".join(
                        difflib.unified_diff(
                            before.splitlines(),
                            updated.system_fragment.splitlines(),
                            fromfile=f"v{updated.version - 1}",
                            tofile=f"v{updated.version}",
                            lineterm="",
                        )
                    )
                    record.update(
                        status="completed",
                        skill_update_status="updated",
                        applied_skill_version=updated.version,
                        skill_updated_at=time.time(),
                    )
                    await self.store.save("behavior_reflection", record)
            except ValueError as error:
                if str(error) != "skill_version_conflict":
                    raise
                # Only a new behavior reflection may reconsider a stale proposal.
                current = await self.skills.load(record["target_skill"])
                async with self.store.db.transaction():
                    record.update(status="completed", skill_update_status="conflict")
                    await self.store.save("behavior_reflection", record)
                    retry_event = await self.submit(
                        f"{record['id']}:recheck:{current.version}",
                        record["target_skill"],
                        {
                            **record["evidence"],
                            "supersedes_reflection_id": record["id"],
                        },
                        record["correlation_id"],
                        in_transaction=True,
                    )
                await self.bus.announce_committed(retry_event)
        else:
            record.update(status="completed", skill_update_status=decision)
            await self.store.save("behavior_reflection", record)
        await self._progress(
            record,
            "reflection_completed",
            skill_update_status=record["skill_update_status"],
            applied_skill_version=record.get("applied_skill_version"),
        )

    @staticmethod
    def _content(record: dict[str, Any]) -> str:
        reflection, evidence = record["reflection"], record["evidence"]
        if record["target_skill"] == "reading":
            # Only the abstract method may enter ordinary lesson recall.
            return json.dumps(
                {
                    "applicability": reflection["applicability"],
                    "next_strategy": reflection["next_strategy"],
                },
                ensure_ascii=False,
            )
        sections = [
            (
                policy("behavior", "service.copy.content_text", str),
                reflection["applicability"],
            ),
            (
                policy("behavior", "service.copy.content_text_2", str),
                evidence.get(
                    "topic_raw", policy("behavior", "service.copy.content_text_11", str)
                ),
            ),
            (
                policy("behavior", "service.copy.content_text_3", str),
                reflection["observation"],
            ),
            (
                policy("behavior", "service.copy.content_text_4", str),
                str(
                    evidence.get(
                        "execution_status",
                        policy("behavior", "service.copy.embedded_text", str),
                    )
                )
                + "；"
                + json.dumps(reflection["evidence_refs"], ensure_ascii=False),
            ),
            (
                policy("behavior", "service.copy.content_text_5", str),
                reflection["cause_analysis"],
            ),
            (
                policy("behavior", "service.copy.content_text_6", str),
                reflection["effective_strategy"]
                or policy("behavior", "service.copy.content_text_12", str),
            ),
            (
                policy("behavior", "service.copy.content_text_7", str),
                reflection["next_strategy"],
            ),
            (
                policy("behavior", "service.copy.content_text_8", str),
                json.dumps(
                    evidence.get("unresolved_questions", []), ensure_ascii=False
                ),
            ),
            (
                policy("behavior", "service.copy.content_text_9", str),
                json.dumps(evidence, ensure_ascii=False, indent=2),
            ),
            (
                policy("behavior", "service.copy.content_text_10", str),
                policy("behavior", "service.templates.content_text", str).format(
                    p0=evidence.get("run_id", record["correlation_id"]),
                    p1=record["id"],
                    p2=record["target_skill"],
                    p3=record["used_skill_version"],
                ),
            ),
        ]
        return "\n".join(f"【{title}】\n{text}" for title, text in sections)
