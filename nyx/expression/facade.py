# pyright: reportPrivateUsage=false, reportUnknownMemberType=false
# _MUTTER_RATE 跨模块私有 import（spec 明确）；ainvoke 返回部分未知（langgraph）
"""ExpressionFacade：回复流程 + 碎碎念 + 搭话。

事件统一 publish，LLM 产出统一 evaluate。
"""

import asyncio
import logging
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, cast
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from nyx.activity.facade import ActivityFacade
from nyx.behavior.context import behavior_context
from nyx.behavior.store import SkillStore, skill_snapshot
from nyx.config import ExpressionConfig
from nyx.desire.facade import DesireFacade
from nyx.enums import (
    ContextMode,
    EventType,
    InteractionKind,
    InteractionStatus,
    MemoryKind,
)
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import internal_text_event
from nyx.expression.classifier import (
    classify_user_intent,
    derive_request_text,
    should_load_discussion,
)
from nyx.expression.mutter import (
    _LLM_MUTTER_RATE,
    _MUTTER_RATE,
    MutterCategory,
    activity_subject,
    clean_fragment,
    pick_mutter_category,
    pick_mutter_template,
)
from nyx.expression.pipeline import ReplyDeps, ReplyState, build_reply_graph
from nyx.expression.prompt import (
    MUTTER_WANDER_TASK,
    activity_summary,
    build_initiate_chat_prompt,
    build_system_prompt,
    build_temporal_block,
    format_prompt_time,
)
from nyx.expression.store import ExpressionInteractionStore
from nyx.inner_life.facade import InnerLifeFacade
from nyx.llm.client import InitiationSkipped, LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.tools.registry import ToolRegistry
from nyx.types import (
    Activity,
    ActivityContext,
    CurrentState,
    Event,
    ExpressionHistory,
    InteractionAttempt,
    Message,
    ReplyAnnotation,
    ShortTermDesire,
    Skill,
)

_LOGGER = logging.getLogger(__name__)
_RESTORABLE_HISTORY_EVENTS = (
    EventType.USER_MESSAGE,
    EventType.SPEAK,
    EventType.ASK,
    EventType.INITIATE_CHAT,
    EventType.READING_QUESTION,
    EventType.READING_ASSOCIATION,
)


class ExpressionFacade:
    """表达：回复 / 搭话 / 碎碎念。依赖经构造注入，会话历史内存维护。"""

    def __init__(
        self,
        bus: EventBus,
        llm: LlmClient,
        evaluator: Evaluator,
        memory: MemoryFacade,
        activity: ActivityFacade,
        desire: DesireFacade,
        inner_life: InnerLifeFacade,
        canon: str,
        ask_guidance: str,
        config: ExpressionConfig,
        tools: ToolRegistry,
        interaction_store: ExpressionInteractionStore | None = None,
        knowledge_boundary: str | None = None,
        observation_reader: Callable[[], Awaitable[Mapping[str, object]]] | None = None,
        claim_return: Callable[[], dict[str, float] | None] | None = None,
        finish_return: Callable[[dict[str, float] | None], None] | None = None,
        release_return: Callable[[dict[str, float] | None], None] | None = None,
        reading_context_reader: (Callable[[str, str], Awaitable[str]] | None) = None,
        skills: Mapping[str, Skill] | None = None,
        skill_store: SkillStore | None = None,
        checkpointer: BaseCheckpointSaver[str] | None = None,
    ) -> None:
        self._bus = bus
        self._llm = llm
        self._evaluator = evaluator
        self._memory = memory
        self._tools = tools
        self._activity = activity
        self._desire = desire
        self._inner_life = inner_life
        self._canon = canon
        self._ask_guidance = ask_guidance
        self._config = config
        self._interaction_store = interaction_store
        self._knowledge_boundary = knowledge_boundary
        self._observation_reader = observation_reader
        self._claim_return = claim_return
        self._finish_return = finish_return
        self._release_return = release_return
        self._reading_context_reader = reading_context_reader
        self._skills = skills or {}
        self._skill_store = skill_store
        self._checkpointer = checkpointer or InMemorySaver()
        self._history: deque[Message] = deque(maxlen=config.max_context_len)
        self._history_restored = False
        self._last_slow_at = 0.0
        # 待用户回应状态：问句（wait_user）与搭话（被忽略回灌）各一组。
        self._waiting_user = False
        self._ask_text = ""
        self._ask_at = 0.0
        self._ask_cid: str | None = None
        self._pending_chat_desire_id: str | None = None
        self._chat_at = 0.0
        # 近期说过的碎碎念（去重：同一句不连发）
        self._mutter_seen: deque[str] = deque(maxlen=8)
        # 图拓扑恒定（仅 history 跨 reply 变化），建一次复用（对齐 Exploration）
        self._graph = build_reply_graph(
            ReplyDeps(
                llm=self._llm,
                evaluator=self._evaluator,
                memory=self._memory,
                inner_life=self._inner_life,
                canon=self._canon,
                ask_guidance=self._ask_guidance,
                config=self._config,
                history=self._history,
                tools=tools,
                knowledge_boundary=knowledge_boundary,
                collect_reply_inputs=self._collect_reply_inputs,
                finalize_user_reply=self._finalize_user_reply,
                archive_user_reply=self._archive_user_reply,
                has_pending_reply_inputs=self._has_pending_reply_inputs,
                checkpointer=self._checkpointer,
            )
        )

    async def _execution_skill(
        self, correlation_id: str, message: str, discussion: bool | None,
    ) -> Skill | None:
        name = "discussion" if discussion is True or (
            discussion is None and should_load_discussion(message)
        ) else "conversation"
        if self._skill_store is not None:
            return await self._skill_store.for_execution(correlation_id, name, message)
        return self._skills.get(name)

    async def _pending_reply_inputs(
        self,
        inputs: list[Event],
        answered_attempt_id: str | None,
    ) -> tuple[list[Event], bool]:
        root = inputs[0]
        size = sum(len(item.content["message"]) for item in inputs)
        if (
            root.content.get("annotation") is not None
            or len(inputs) >= 32
            or size >= 24000
        ):
            return [], True
        pending = await self._bus.list_pending_after(
            "expression.user_message",
            inputs[-1].id,
            limit=33 - len(inputs),
        )
        accepted: list[Event] = []
        for event in pending:
            text = event.content.get("message")
            if event.type is not EventType.USER_MESSAGE or not isinstance(text, str):
                raise ValueError("补充消息缺少真实用户原文")
            if (
                event.content.get("annotation") is not None
                or event.content.get("discussion") != root.content.get("discussion")
                or (
                    event.content.get("reply_to") is not None
                    and event.content["reply_to"] != answered_attempt_id
                )
                or len(inputs) + len(accepted) >= 32
                or size + len(text) > 24000
            ):
                return accepted, True
            accepted.append(event)
            size += len(text)
        return accepted, len(inputs) + len(accepted) >= 32 or size >= 24000

    async def _has_pending_reply_inputs(
        self,
        input_event_ids: list[str],
        answered_attempt_id: str | None,
    ) -> bool:
        if not input_event_ids:
            return False
        inputs: list[Event] = []
        for identity in input_event_ids:
            event = await self._bus.get_event(identity)
            if event is None or event.type is not EventType.USER_MESSAGE:
                raise ValueError("回复输入证据已丢失")
            inputs.append(event)
        accepted, _ = await self._pending_reply_inputs(inputs, answered_attempt_id)
        return bool(accepted)

    async def _collect_reply_inputs(self, state: ReplyState) -> dict[str, Any]:
        inputs = state["input_events"]
        updates: dict[str, Any] = {"needs_refresh": False}
        if state["input_mode"] == "durable" and not state["input_closed"]:
            answered = state["answered_attempt"]
            accepted, closed = await self._pending_reply_inputs(
                inputs,
                answered.id if answered else None,
            )
            updates["input_closed"] = closed
            if accepted:
                inputs = [*inputs, *accepted]
                revision = state["input_revision"] + 1
                updates.update(
                    input_events=inputs,
                    input_revision=revision,
                    think=[],
                    speak=state["speak"] if state["fallback"] else [],
                    ask=None,
                    round=0,
                    round_times=[],
                    needs_continuation=False,
                    draft_revision=revision if state["fallback"] else -1,
                    draft_event_ids=[e.id for e in inputs] if state["fallback"] else [],
                    next_action="answer",
                )
        texts = (
            [item.content["message"] for item in inputs]
            if inputs
            else [state["message"]]
        )
        start, message = derive_request_text(texts)
        discussion = inputs[0].content.get("discussion") if inputs else None
        # Direct calls already received their explicit mode at the reply entrance.
        skill = (
            state["skill"]
            if not inputs
            else (
                await self._execution_skill(
                    state["correlation_id"], message, discussion
                )
            )
        )
        reading = state["reading_context"]
        answered = state["answered_attempt"]
        if (
            inputs
            and answered is not None
            and (
                answered.kind is InteractionKind.READING_QUESTION
                and self._reading_context_reader is not None
            )
        ):
            reading = await self._reading_context_reader(
                answered.source_id,
                f"{answered.text}\n{message}",
            )
        return {
            **updates,
            "message": message,
            "request_start_index": start,
            "intent": classify_user_intent(message),
            "skill": skill,
            "reading_context": reading,
        }

    async def _terminal_user_reply(self, root_event_id: str) -> Event | None:
        events = await self._bus.list_events(correlation_id=root_event_id)
        return next(
            (e for e in events if e.type in (EventType.SPEAK, EventType.ASK)), None
        )

    async def _delivered_reply_text(self, terminal: Event) -> str:
        if (
            "reply_complete" in terminal.content
            or "reply_to_event_ids" in terminal.content
        ):
            return terminal.content["content"]
        events = await self._bus.list_events(correlation_id=terminal.correlation_id)
        delivered = sorted(
            (e for e in events if e.type in (EventType.SPEAK, EventType.ASK)),
            key=lambda e: (e.timestamp, e.id),
        )
        return "\n".join(e.content["content"] for e in delivered)

    async def _covered_reply_inputs(self, terminal: Event) -> list[Event]:
        complete = terminal.content.get("reply_complete")
        ids: Any = terminal.content.get("reply_to_event_ids")
        if (
            "reply_complete" not in terminal.content
            and "reply_to_event_ids" not in terminal.content
        ):
            ids = [terminal.correlation_id]
        elif (
            complete is not True
            or not isinstance(ids, list)
            or not 1 <= len(cast(list[Any], ids)) <= 32
            or any(not isinstance(i, str) or not i for i in cast(list[Any], ids))
            or len(set(cast(list[Any], ids))) != len(cast(list[Any], ids))
            or ids[0] != terminal.correlation_id
        ):
            raise ValueError("终局覆盖协议非法")
        inputs: list[Event] = []
        for identity in cast(list[str], ids):
            event = await self._bus.get_event(identity)
            if (
                event is None
                or event.type is not EventType.USER_MESSAGE
                or not isinstance(event.content.get("message"), str)
            ):
                raise ValueError("终局覆盖的原始输入缺失或类型非法")
            inputs.append(event)
        database = getattr(self._bus, "_db", None)
        if database is not None:
            async with database.transaction():
                cursor = await database.conn.execute(
                    "SELECT id FROM event_log WHERE id IN ("
                    + ",".join("?" for _ in inputs)
                    + ") ORDER BY event_seq",
                    cast(list[str], ids),
                )
                if [row["id"] for row in await cursor.fetchall()] != ids:
                    raise ValueError("终局覆盖顺序非法")
        return inputs

    async def _finalize_user_reply(
        self,
        state: ReplyState,
    ) -> Literal["committed", "refresh", "replayed"]:
        body = "\n".join(state["speak"])
        if not body:
            raise ValueError("回复终局正文不能为空")
        correlation = state["correlation_id"]
        attempt: InteractionAttempt | None = None
        if state["ask"] is not None and not state["fallback"]:
            now = time.time()
            attempt = InteractionAttempt(
                id=str(uuid4()),
                kind=InteractionKind.CHAT_ASK,
                source_id=correlation,
                correlation_id=correlation,
                text=body,
                created_at=now,
                expires_at=now + self._config.ask_timeout,
            )
            terminal = _ask_event(
                body, attempt.id, correlation, attempt.kind, attempt.expires_at
            )
        else:
            terminal = internal_text_event(EventType.SPEAK, body, correlation)
        if state["fallback"]:
            terminal.content.update(response_kind="fallback", attempt_id=None)
        durable = state["input_mode"] == "durable"
        if durable:
            terminal.content.update(
                reply_complete=True,
                reply_to_event_ids=[e.id for e in state["input_events"]],
            )
        events: list[Event] = []
        thought = "\n".join(t for t in state["think"] if t)
        if thought and not state["fallback"]:
            events.append(internal_text_event(EventType.THINK, thought, correlation))
        events.append(terminal)
        claim = None if state["fallback"] else state["claimed_return"]
        database = getattr(self._bus, "_db", None)
        if database is None:
            if attempt is not None:
                await self._commit_attempt_events(
                    attempt, events, claim, state["answered_attempt"]
                )
            else:
                await self._commit_reply_events(
                    events, state["answered_attempt"], claim
                )
            state["committed_reply"] = terminal
            return "committed"
        appended = 0
        try:
            async with database.transaction():
                if durable:
                    existing = await self._terminal_user_reply(correlation)
                    if existing is not None:
                        await self._covered_reply_inputs(existing)
                        state["committed_reply"] = existing
                        return "replayed"
                    ids = [e.id for e in state["input_events"]]
                    if (
                        state["draft_revision"] != state["input_revision"]
                        or state["draft_event_ids"] != ids
                    ):
                        raise ValueError("终局草稿输入版本不一致")
                    for original in state["input_events"]:
                        if await self._bus.get_event(original.id) != original:
                            raise ValueError("终局原始输入不一致")
                    if not state["input_closed"]:
                        answered = state["answered_attempt"]
                        accepted, closed = await self._pending_reply_inputs(
                            state["input_events"],
                            answered.id if answered else None,
                        )
                        if accepted:
                            return "refresh"
                        state["input_closed"] = closed
                if self._interaction_store is not None:
                    if attempt is not None:
                        await self._interaction_store.create(attempt)
                    answered = state["answered_attempt"]
                    if (
                        answered is not None
                        and not await self._interaction_store.finish_answer(
                            answered.id,
                            answered.answer_event_id or "",
                        )
                    ):
                        raise RuntimeError("被回答的 interaction attempt 无法完成")
                for event in events:
                    before = self._bus.persisted_count
                    try:
                        await self._bus.append_in_transaction(event)
                    finally:
                        appended += self._bus.persisted_count - before
                if durable:
                    for event in state["input_events"][1:]:
                        await self._bus.try_mark_effect_in_transaction(
                            event.id, "expression.user_message"
                        )
        except BaseException as error:
            try:
                committed = await self._bus.is_durable(terminal.id)
            except Exception:
                raise error
            if committed:
                state["committed_reply"] = terminal
                self._finish_return_claim(claim)
            else:
                self._bus.persisted_count -= appended
            raise
        state["committed_reply"] = terminal
        self._finish_return_claim(claim)
        for event in events:
            await self._bus.announce_committed(event)
        return "committed"

    async def _merge_reply_history(self, messages: list[Message]) -> None:
        by_id = {m.event_id: m for m in self._history if m.event_id is not None}
        merged = list(self._history)
        for message in messages:
            if message.event_id is None or message.event_id not in by_id:
                merged.append(message)
                if message.event_id is not None:
                    by_id[message.event_id] = message
        database = getattr(self._bus, "_db", None)
        if database is not None and by_id:
            async with database.lock:
                cursor = await database.conn.execute(
                    "SELECT id, event_seq FROM event_log WHERE id IN ("
                    + ",".join("?" for _ in by_id)
                    + ")",
                    list(by_id),
                )
                positions = {r["id"]: r["event_seq"] for r in await cursor.fetchall()}
            # Direct originals have no event ID; keep them beside their reply.
            position = max(positions.values(), default=0) + 1
            ordered: list[tuple[int, Message]] = []
            for message in reversed(merged):
                position = positions.get(message.event_id, position)
                ordered.append((position, message))
            merged = [m for _, m in sorted(reversed(ordered), key=lambda item: item[0])]
        self._history.clear()
        self._history.extend(merged[-self._config.max_context_len :])

    async def _archive_user_reply(
        self,
        root_event_id: str,
        state: ReplyState | None = None,
    ) -> None:
        root = await self._bus.get_event(root_event_id)
        terminal = (
            state["committed_reply"] if state else None
        ) or await self._terminal_user_reply(root_event_id)
        if terminal is None:
            raise ValueError("归档缺少已提交终局")
        if root is None:
            if state is None or state["input_mode"] != "direct":
                raise ValueError("direct 归档缺少原话 state")
            users = [
                Message(
                    role="user",
                    content=state["message"],
                    timestamp=state["message_at"] or state["sampled_at"],
                )
            ]
            keys = [f"{root_event_id}:user"]
        else:
            inputs = await self._covered_reply_inputs(terminal)
            users = [
                Message(
                    role="user",
                    content=e.content["message"],
                    timestamp=e.timestamp,
                    event_id=e.id,
                )
                for e in inputs
            ]
            keys = [f"{e.id}:user" for e in inputs]
        body = await self._delivered_reply_text(terminal)
        if not body:
            raise ValueError("归档终局缺少正文")
        reply = Message(
            role="nyx",
            content=body,
            timestamp=terminal.timestamp,
            event_id=terminal.id,
            fast=state is not None and state["mode"] is ContextMode.FAST,
        )
        messages = [*users, reply]
        if state is not None:
            activity = (
                state["activity_context"].current
                or state["activity_context"].interrupted
            )
            if activity is not None:
                for message in messages:
                    message.activity_id, message.activity_type = (
                        activity.id,
                        activity.type,
                    )
                    message.activity_summary = activity_summary(activity)
        append = getattr(self._memory, "append_conversation_turn", None)
        if callable(append):
            append_fn = cast(Callable[[str, str, float], Awaitable[None]], append)
            for key, message in zip([*keys, f"{root_event_id}:nyx"], messages):
                await append_fn(key, message.content, message.timestamp)
        await self._merge_reply_history(messages)
        try:
            await self._checkpointer.adelete_thread(root_event_id)
            await self._tools.clear_executions(root_event_id)
        except Exception:
            _LOGGER.exception("回复归档后的工具 checkpoint 清理失败")

    async def register_question(
        self,
        text: str,
        kind: InteractionKind,
        source_id: str,
        correlation_id: str,
        *,
        claimed_return: dict[str, float] | None = None,
        preceding_events: list[Event] | None = None,
        followup_events: list[Event] | None = None,
        answered_attempt: InteractionAttempt | None = None,
    ) -> str:
        """Persist a question attempt and its canonical ASK atomically."""
        attempt_id = str(uuid4())
        now = time.time()
        attempt = InteractionAttempt(
            id=attempt_id,
            kind=kind,
            source_id=source_id,
            correlation_id=correlation_id,
            text=text,
            created_at=now,
            expires_at=now + self._config.ask_timeout,
        )
        event = _ask_event(text, attempt_id, correlation_id, kind, attempt.expires_at)
        await self._commit_attempt_events(
            attempt,
            [*(preceding_events or []), event, *(followup_events or [])],
            claimed_return,
            answered_attempt,
        )
        return attempt_id

    async def _commit_attempt_event(
        self,
        attempt: InteractionAttempt,
        event: Event,
        claimed_return: dict[str, float] | None = None,
    ) -> None:
        """Commit an interaction row and its event as one local transaction."""
        await self._commit_attempt_events(attempt, [event], claimed_return)

    async def _commit_attempt_events(
        self,
        attempt: InteractionAttempt,
        events: list[Event],
        claimed_return: dict[str, float] | None = None,
        answered_attempt: InteractionAttempt | None = None,
    ) -> None:
        """Commit one interaction attempt and all durable events atomically."""
        if self._interaction_store is None:
            try:
                await self._bus.publish_many(events)
            except BaseException as error:
                if claimed_return is not None:
                    try:
                        committed = await self._bus.is_durable(events[0].id)
                    except Exception:
                        raise error
                    if committed:
                        self._finish_return_claim(claimed_return)
                raise
            self._finish_return_claim(claimed_return)
            return
        appended_count = 0
        try:
            async with self._interaction_store.db.transaction():
                await self._interaction_store.create(attempt)
                if answered_attempt is not None and not (
                    await self._interaction_store.finish_answer(
                        answered_attempt.id,
                        answered_attempt.answer_event_id or "",
                    )
                ):
                    raise RuntimeError("被回答的 interaction attempt 无法完成")
                for event in events:
                    persisted_before = self._bus.persisted_count
                    try:
                        await self._bus.append_in_transaction(event)
                    finally:
                        appended_count += self._bus.persisted_count - persisted_before
        except BaseException as error:
            try:
                committed = await self._bus.is_durable(events[0].id)
            except Exception:
                raise error
            if not committed:
                self._bus.persisted_count -= appended_count
            elif claimed_return is not None:
                self._finish_return_claim(claimed_return)
            raise
        self._finish_return_claim(claimed_return)
        for event in events:
            await self._bus.announce_committed(event)

    async def _commit_reply_events(
        self,
        events: list[Event],
        answered_attempt: InteractionAttempt | None,
        claimed_return: dict[str, float] | None,
    ) -> None:
        """Atomically finish an answered attempt and admit one reply batch."""
        if not events:
            raise ValueError("回复终局批次不能为空")
        terminal = next(
            (
                event
                for event in reversed(events)
                if event.type in (EventType.SPEAK, EventType.ASK)
            ),
            events[-1],
        )
        if self._interaction_store is None:
            try:
                await self._bus.publish_many(events)
            except BaseException as error:
                if claimed_return is not None:
                    try:
                        committed = await self._bus.is_durable(terminal.id)
                    except Exception:
                        raise error
                    if committed:
                        self._finish_return_claim(claimed_return)
                raise
            self._finish_return_claim(claimed_return)
            return
        appended_count = 0
        try:
            async with self._interaction_store.db.transaction():
                if answered_attempt is not None and not (
                    await self._interaction_store.finish_answer(
                        answered_attempt.id,
                        answered_attempt.answer_event_id or "",
                    )
                ):
                    raise RuntimeError("被回答的 interaction attempt 无法完成")
                for event in events:
                    persisted_before = self._bus.persisted_count
                    try:
                        await self._bus.append_in_transaction(event)
                    finally:
                        appended_count += self._bus.persisted_count - persisted_before
        except BaseException as error:
            try:
                committed = await self._bus.is_durable(terminal.id)
            except Exception:
                raise error
            if not committed:
                self._bus.persisted_count -= appended_count
            elif claimed_return is not None:
                self._finish_return_claim(claimed_return)
            raise
        self._finish_return_claim(claimed_return)
        for event in events:
            await self._bus.announce_committed(event)

    async def commit_reading_question(
        self,
        text: str,
        source_id: str,
        correlation_id: str,
        event_content: dict[str, object],
    ) -> str:
        """Atomically admit the reading question attempt, ASK, and reading event."""
        attempt_id = str(uuid4())
        now = time.time()
        attempt = InteractionAttempt(
            id=attempt_id,
            kind=InteractionKind.READING_QUESTION,
            source_id=source_id,
            correlation_id=correlation_id,
            text=text,
            created_at=now,
            expires_at=now + self._config.ask_timeout,
        )
        ask = _ask_event(
            text,
            attempt_id,
            correlation_id,
            InteractionKind.READING_QUESTION,
            attempt.expires_at,
        )
        reading = Event(
            id=str(uuid4()),
            timestamp=now,
            source=ask.source,
            type=EventType.READING_QUESTION,
            content={
                **event_content,
                "attempt_id": attempt_id,
                "expires_at": attempt.expires_at,
            },
            correlation_id=correlation_id,
        )
        await self._commit_attempt_events(attempt, [ask, reading])
        self.record_proactive_turn(text, event=reading)
        return attempt_id

    async def answer_waiting(
        self, reply_event_id: str, reply_to: str | None = None
    ) -> InteractionAttempt | None:
        """Claim at most one interaction for this user event."""
        if self._interaction_store is None:
            return None
        attempt = await self._interaction_store.claim_reply(reply_event_id, reply_to)
        if attempt is None:
            return None
        try:
            if (
                attempt.kind is InteractionKind.INITIATE_CHAT
                and attempt.status is InteractionStatus.CLAIMED
            ):
                await self._desire.satisfy(attempt.source_id, True)
        except BaseException:
            await self._interaction_store.release_claim(attempt.id, reply_event_id)
            raise
        return attempt

    async def pending_questions(self) -> list[InteractionAttempt]:
        """Return durable unanswered prompts for restoring the chat UI."""
        if self._interaction_store is None:
            return []
        return await self._interaction_store.list_waiting()

    async def restore_history(self) -> None:
        """Restore the bounded expression history from the durable event log."""
        if self._history_restored:
            return
        self._history_restored = True
        try:
            events: list[Event] = []
            for event_type in _RESTORABLE_HISTORY_EVENTS:
                events.extend(await self._bus.list_events(
                    limit=self._config.max_context_len, event_type=event_type,
                ))
            restored: list[Message] = []
            seen_roots: set[str] = set()
            for event in events:
                if event.type in (EventType.SPEAK, EventType.ASK):
                    root = await self._bus.get_event(event.correlation_id)
                    if root is None or root.type is not EventType.USER_MESSAGE:
                        continue
                    if root.id in seen_roots:
                        continue
                    seen_roots.add(root.id)
                    terminal = await self._terminal_user_reply(root.id)
                    if terminal is None:
                        continue
                    inputs = await self._covered_reply_inputs(terminal)
                    restored.extend(
                        Message(
                            role="user",
                            content=e.content["message"],
                            timestamp=e.timestamp,
                            event_id=e.id,
                        )
                        for e in inputs
                    )
                    restored.append(
                        Message(
                            role="nyx",
                            content=await self._delivered_reply_text(terminal),
                            timestamp=terminal.timestamp,
                            event_id=terminal.id,
                        )
                    )
                elif event.type in (
                    EventType.INITIATE_CHAT,
                    EventType.READING_QUESTION,
                    EventType.READING_ASSOCIATION,
                ):
                    key = (
                        "snippet"
                        if event.type is EventType.READING_ASSOCIATION
                        else "content"
                    )
                    content = event.content.get(key)
                    if isinstance(content, str):
                        restored.append(
                            Message(
                                role="nyx",
                                content=content,
                                timestamp=event.timestamp,
                                event_id=event.id,
                            )
                        )
            restored.sort(key=lambda message: message.timestamp)
            await self._merge_reply_history(restored)
        except Exception:
            _LOGGER.exception("恢复表达历史失败，将使用空历史继续启动")

    async def pause_question(
        self, attempt_id: str, now: float | None = None
    ) -> InteractionAttempt | None:
        """Pause one waiting prompt while preserving its remaining duration."""
        if self._interaction_store is None:
            return None
        return await self._interaction_store.pause(
            attempt_id, time.time() if now is None else now
        )

    async def resume_question(
        self, attempt_id: str, now: float | None = None
    ) -> InteractionAttempt | None:
        """Resume one paused prompt and restore its remaining duration."""
        if self._interaction_store is None:
            return None
        return await self._interaction_store.resume(
            attempt_id, time.time() if now is None else now
        )

    async def latest_initiate_chat_at(self) -> float | None:
        """Return the latest durable proactive-chat commitment time."""
        if self._interaction_store is None:
            return None
        return await self._interaction_store.latest_created_at(
            InteractionKind.INITIATE_CHAT
        )

    async def expression_history(self, limit: int = 100) -> ExpressionHistory:
        """Return durable expression events grouped for the history panel."""
        all_types = (
            EventType.USER_MESSAGE,
            EventType.THINK,
            EventType.SPEAK,
            EventType.ASK,
            EventType.MUTTER,
            EventType.INITIATE_CHAT,
            EventType.READING_MUTTER,
            EventType.READING_QUESTION,
            EventType.READING_ASSOCIATION,
        )
        grouped = await asyncio.gather(
            *(
                self._bus.list_events(limit=limit, event_type=event_type)
                for event_type in all_types
            )
        )
        def valid(event: Event) -> bool:
            if event.type is EventType.USER_MESSAGE:
                key = "message"
            elif event.type is EventType.READING_ASSOCIATION:
                key = "snippet"
            else:
                key = "content"
            return isinstance(event.content.get(key), str) and bool(event.content[key])

        by_type = {
            event_type: sorted(
                [event for event in events if valid(event)],
                key=lambda event: (event.timestamp, event.id),
                reverse=True,
            )[:limit]
            for event_type, events in zip(all_types, grouped, strict=True)
        }
        all_events = sorted(
            (event for events in by_type.values() for event in events),
            key=lambda event: (event.timestamp, event.id),
            reverse=True,
        )[:limit]
        mutter = sorted(
            by_type[EventType.MUTTER] + by_type[EventType.READING_MUTTER],
            key=lambda event: (event.timestamp, event.id),
            reverse=True,
        )[:limit]
        initiate_chat = by_type[EventType.INITIATE_CHAT]
        return ExpressionHistory(
            all=all_events,
            mutter=mutter,
            initiate_chat=initiate_chat,
        )

    async def _last_dialogue_anchor(
        self, current_correlation_id: str | None
    ) -> tuple[Message, Message] | None:
        """Return the latest durable user turn with a terminal Nyx response."""
        terminals = [
            *await self._bus.list_events(limit=20, event_type=EventType.SPEAK),
            *await self._bus.list_events(limit=20, event_type=EventType.ASK),
        ]
        terminals.sort(key=lambda event: event.timestamp, reverse=True)
        for terminal in terminals:
            if terminal.correlation_id == current_correlation_id:
                continue
            root = await self._bus.get_event(terminal.correlation_id)
            if root is None or root.type is not EventType.USER_MESSAGE:
                continue
            inputs = await self._covered_reply_inputs(terminal)
            latest = inputs[-1]
            return (
                Message(
                    role="user",
                    content=latest.content["message"],
                    timestamp=latest.timestamp,
                    event_id=latest.id,
                ),
                Message(
                    role="nyx",
                    content=await self._delivered_reply_text(terminal),
                    timestamp=terminal.timestamp,
                    event_id=terminal.id,
                ),
            )
        return None

    async def _temporal_context(
        self,
        now: float,
        current_correlation_id: str | None,
        claimed_return: Mapping[str, float] | None,
    ) -> str:
        """Build one time snapshot for all LLM calls in an expression attempt."""
        anchor = await self._last_dialogue_anchor(current_correlation_id)
        observation: Mapping[str, object] = {}
        if self._observation_reader is not None:
            observation = await self._observation_reader()
        return build_temporal_block(now, anchor, observation, claimed_return)

    def _claim_pending_return(self) -> dict[str, float] | None:
        return self._claim_return() if self._claim_return is not None else None

    def _finish_return_claim(self, claim: dict[str, float] | None) -> None:
        if self._finish_return is not None:
            self._finish_return(claim)

    def _release_return_claim(self, claim: dict[str, float] | None) -> None:
        if self._release_return is not None:
            self._release_return(claim)

    async def _activity_context(
        self, context: ActivityContext | None = None
    ) -> ActivityContext:
        if context is not None:
            return context
        reader = getattr(self._activity, "get_current", None)
        if callable(reader):
            typed_reader = cast(Callable[[], Awaitable[Activity | None]], reader)
            current = await typed_reader()
        else:
            current = None
        return ActivityContext(
            current=current,
            interrupted=None,
            observed_at=time.time(),
        )

    async def reply(
        self,
        msg: str,
        correlation_id: str,
        reply_to: str | None = None,
        activity_context: ActivityContext | None = None,
        discussion: bool | None = None,
        annotation: ReplyAnnotation | None = None,
    ) -> None:
        """完整回复流程：跑 LangGraph 图，内部发布 think/speak/ask。"""
        claimed_return = self._claim_pending_return()
        answered: InteractionAttempt | None = None
        initial: ReplyState | None = None
        now = time.time()
        try:
            user_event = await self._bus.get_event(correlation_id)
            if user_event is not None and user_event.type is not EventType.USER_MESSAGE:
                raise ValueError("回复根事件不是 USER_MESSAGE")
            if user_event is not None:
                msg = user_event.content["message"]
                discussion = user_event.content.get("discussion")
            message_at = (
                user_event.timestamp
                if user_event is not None and user_event.type is EventType.USER_MESSAGE
                else None
            )
            quoted_event = (
                await self._bus.get_event(annotation["reply_event_id"])
                if annotation is not None else None
            )
            # 用户说话 = 回应了之前的问句/搭话；清等待状态（不判断是否真在答）。
            if annotation is None:
                answered = await self.answer_waiting(correlation_id, reply_to)
            reading_context = ""
            if (
                answered is not None
                and answered.kind is InteractionKind.READING_QUESTION
                and self._reading_context_reader is not None
            ):
                reading_context = await self._reading_context_reader(
                    answered.source_id, f"{answered.text}\n{msg}"
                )
            if self._interaction_store is None and annotation is None:
                self._waiting_user = False
                self._ask_cid = None
                if self._pending_chat_desire_id is not None:
                    await self._desire.satisfy(self._pending_chat_desire_id, True)
                self._pending_chat_desire_id = None
            state = await self._inner_life.get_state()
            activity_context = await self._activity_context(activity_context)
            temporal_context = await self._temporal_context(
                now, correlation_id, claimed_return
            )
            initial = {
                "input_mode": "durable" if user_event is not None else "direct",
                "input_events": [user_event] if user_event is not None else [],
                "input_revision": 0,
                "input_closed": user_event is None,
                "request_start_index": 0,
                "retrieval_query": "",
                "reasoning_steps": [],
                "reasoning_step_count": 0,
                "next_action": "answer",
                "draft_revision": -1,
                "draft_event_ids": [],
                "needs_refresh": False,
                "tool_exit_reason": "complete",
                "committed_reply": None,
                "message": msg,
                "message_at": message_at,
                "sampled_at": now,
                "annotation_at": (
                    quoted_event.timestamp if quoted_event is not None else None
                ),
                "round_times": [],
                "annotation": annotation,
                "skill": (
                    await self._execution_skill(correlation_id, msg, discussion)
                ),
                "mode": ContextMode.FAST,
                # 朴素回溯最近 max_context_len 条（快通道用，不含当前消息）；
                # 慢通道在 assemble 重截断。
                "context": list(self._history)[-self._config.max_context_len :],
                "conversation_segments": [],
                "memories": [],
                "facts": [],
                "state": state,
                "narrative": None,
                "think": [],
                "speak": [],
                "needs_continuation": False,
                "ask": None,
                "round": 0,
                "correlation_id": correlation_id,
                "last_slow_at": self._last_slow_at,
                "tool_outputs": [],
                "tool_feedback": "",
                "tool_recall_candidates": [],
                "intent": classify_user_intent(msg),
                "temporal_context": temporal_context,
                "reading_context": reading_context,
                "activity_context": activity_context,
                "answered_attempt": answered,
                "claimed_return": claimed_return,
                "fallback": False,
            }
            selected = initial["skill"]
            token = behavior_context.set({
                "scenario": "reply", "correlation_id": correlation_id,
                "topic_raw": msg,
                "skill_snapshot_raw": skill_snapshot(selected) if selected else {},
            } if selected else None)
            try:
                result = await self._graph.ainvoke(
                    initial,
                    {"recursion_limit": 32 * (12 + 2 * self._config.slow_max_rounds)
                     + 30},
                )
            finally:
                behavior_context.reset(token)
            if result["mode"] is ContextMode.SLOW:
                self._last_slow_at = time.time()
            if result["ask"] is not None and self._interaction_store is None:
                self._waiting_user = True
                self._ask_text = result["ask"]
                self._ask_at = time.time()
                self._ask_cid = correlation_id
            if result["fallback"]:
                self._release_return_claim(claimed_return)
        except BaseException:
            committed = initial is not None and initial["committed_reply"] is not None
            if not committed:
                committed = await self._terminal_user_reply(correlation_id) is not None
            if (
                not committed
                and answered is not None
                and self._interaction_store is not None
            ):
                await self._interaction_store.release_claim(answered.id, correlation_id)
            if not committed:
                self._release_return_claim(claimed_return)
            raise

    async def initiate_chat(self, desire: ShortTermDesire, state: CurrentState) -> bool:
        """搭话：快通道生成一句开场白。

        无话则发 False（组合根 据此不更新 last_chat_at）。
        """
        claimed_return = self._claim_pending_return()
        started_at = time.time()
        correlation_id = (
            str(uuid4()) if self._interaction_store is not None else desire.id
        )
        try:
            skill = await self._execution_skill(
                correlation_id, desire.description, False,
            )
            activity_context = await self._activity_context()
            temporal_context = await self._temporal_context(
                started_at, correlation_id, claimed_return
            )
            system = build_system_prompt(
                self._canon,
                state,
                ask_guidance=self._ask_guidance,
                knowledge_boundary=self._knowledge_boundary,
                temporal_context=temporal_context,
                activity_context=activity_context,
                discussion_fragment=skill.system_fragment if skill else None,
            )
            user = (
                f"[欲望创建时间={format_prompt_time(desire.created_at)}]\n"
                + build_initiate_chat_prompt(desire.description)
            )
            output = await self._llm.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                module="expression",
                output_type="initiate_chat",
                correlation_id=correlation_id,
            )
            await self._evaluator.evaluate(output)
            if not output.content.strip():
                self._release_return_claim(claimed_return)
                return False
            attempt_id = str(uuid4())
            now = time.time()
            attempt = InteractionAttempt(
                id=attempt_id,
                kind=InteractionKind.INITIATE_CHAT,
                source_id=desire.id,
                correlation_id=correlation_id,
                text=output.content,
                created_at=now,
                expires_at=now + self._config.chat_ignore_timeout,
            )
            event = internal_text_event(
                EventType.INITIATE_CHAT, output.content, correlation_id
            )
            event.content["attempt_id"] = attempt_id
            event.content["expires_at"] = attempt.expires_at
            event.content["desire_id"] = desire.id
            await self._commit_attempt_event(attempt, event, claimed_return)
            self._history.append(
                Message(
                    role="nyx",
                    content=output.content,
                    timestamp=event.timestamp,
                    event_id=event.id,
                    activity_id=(
                        activity_context.current.id
                        if activity_context.current is not None
                        else None
                    ),
                    activity_type=(
                        activity_context.current.type
                        if activity_context.current is not None
                        else None
                    ),
                    activity_summary=(
                        activity_summary(activity_context.current)
                        if activity_context.current is not None
                        else None
                    ),
                )
            )
            if self._interaction_store is None:
                self._pending_chat_desire_id = desire.id
                self._chat_at = time.time()
            return True
        except InitiationSkipped:
            self._release_return_claim(claimed_return)
            return False
        except BaseException:
            self._release_return_claim(claimed_return)
            raise

    def record_proactive_turn(self, text: str, *, event: Event | None = None) -> None:
        """把 Nyx 主动产出（读书提问/联想）追加进会话历史，供后续 reply() 引用。"""
        self._history.append(
            Message(
                role="nyx",
                content=text,
                timestamp=event.timestamp if event else time.time(),
                event_id=event.id if event else None,
            )
        )

    async def mutter(self, state: CurrentState, correlation_id: str) -> None:
        """碎碎念：空闲 + 随机命中才发；低频走 LLM 即兴（走神），否则模板填空。
        无话可说 / 近期说过同一句则不发。"""
        if state.current_activity is not None:
            return  # 忙，不碎碎念
        if random.random() >= _MUTTER_RATE:
            return
        claimed_return: dict[str, float] | None = None
        generated = False
        try:
            activity_context = await self._activity_context()
            if random.random() < _LLM_MUTTER_RATE:
                claimed_return = self._claim_pending_return()
                temporal_context = await self._temporal_context(
                    time.time(), correlation_id, claimed_return
                )
                text = await self._mutter_wander(
                    state, correlation_id, temporal_context, activity_context
                )
                generated = True
                if text is None:
                    self._release_return_claim(claimed_return)
                    claimed_return = None
            else:
                text = None
            if text is None:  # 没走 LLM，或 LLM 即兴空 → 回退模板
                category = pick_mutter_category(random.random())
                if category is None:
                    return
                text = await self._mutter_text(category, state)
            if text is None or text in self._mutter_seen:
                self._release_return_claim(claimed_return)
                return
            if not generated and not self._llm.can_initiate():
                return
            event = internal_text_event(EventType.MUTTER, text, correlation_id)
            try:
                await self._bus.publish(event)
            except BaseException as error:
                try:
                    committed = await self._bus.is_durable(event.id)
                except Exception:
                    raise error
                if committed:
                    self._mutter_seen.append(text)
                    self._finish_return_claim(claimed_return)
                raise
            self._mutter_seen.append(text)
            self._finish_return_claim(claimed_return)
        except InitiationSkipped:
            self._release_return_claim(claimed_return)
        except BaseException:
            self._release_return_claim(claimed_return)
            raise

    async def _mutter_wander(
        self,
        state: CurrentState,
        correlation_id: str,
        temporal_context: str,
        activity_context: ActivityContext,
    ) -> str | None:
        """LLM 即兴碎碎念（低频走神）：一句自然口语，可停顿/离题；空则回退模板。"""
        system = build_system_prompt(
            self._canon,
            state,
            knowledge_boundary=self._knowledge_boundary,
            temporal_context=temporal_context,
            activity_context=activity_context,
            allow_wander=True,
        )
        user = MUTTER_WANDER_TASK
        output = await self._llm.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            module="expression",
            output_type="mutter_wander",
            correlation_id=correlation_id,
        )
        await self._evaluator.evaluate(output)
        return output.content.strip() or None

    async def _mutter_text(
        self, category: MutterCategory, state: CurrentState
    ) -> str | None:
        """按类取最近「具体内容」填空；该类无数据返回 None。"""
        subject: str | None
        if category is MutterCategory.ACTIVITY:
            results = await self._activity.get_results(limit=1)
            if not results:
                return None
            subject = activity_subject(
                results[0].type, results[0].progress.get("result", {})
            )
        elif category is MutterCategory.MEMORY:
            mems = await self._memory.list_memories(limit=1)
            if not mems:
                return None
            subject = clean_fragment(mems[0].content or mems[0].summary or "")
        elif category is MutterCategory.DESIRE:
            if not state.active_desires:
                return None
            subject = state.active_desires[0].description.strip()
        else:  # USER
            profile = await self._memory.list_memories(
                kind=MemoryKind.USER_PROFILE, limit=1
            )
            if not profile:
                return None
            subject = clean_fragment(profile[0].content or profile[0].summary or "")
        if not subject:
            return None
        template = pick_mutter_template(category, random.random())
        if template is None:
            return None
        return template.format(subject=subject)

    async def check_timeouts(self, now: float) -> None:
        """超时收尾（tick 心跳直呼）：问句到期仅结束等待；
        搭话被忽略 → 淘汰该互动欲（expire 内值回灌 +0.3）。"""
        if self._interaction_store is not None:
            while True:
                attempt = await self._interaction_store.claim_expired(now)
                if attempt is None:
                    return
                try:
                    if attempt.kind is InteractionKind.INITIATE_CHAT:
                        await self._desire.expire(attempt.source_id)
                except Exception:
                    await self._interaction_store.release_claim(attempt.id)
                    raise
                if not await self._interaction_store.finish_expired(attempt.id):
                    return
        if self._waiting_user and now - self._ask_at >= self._config.ask_timeout:
            self._waiting_user = False
            self._ask_cid = None
        if (
            self._pending_chat_desire_id is not None
            and now - self._chat_at >= self._config.chat_ignore_timeout
        ):
            await self._desire.expire(self._pending_chat_desire_id)
            self._pending_chat_desire_id = None


def _ask_event(
    text: str,
    attempt_id: str,
    correlation_id: str,
    kind: InteractionKind,
    expires_at: float,
):
    event = internal_text_event(EventType.ASK, text, correlation_id)
    event.content["attempt_id"] = attempt_id
    event.content["kind"] = kind.value
    event.content["expires_at"] = expires_at
    return event
