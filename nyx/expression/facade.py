# pyright: reportPrivateUsage=false, reportUnknownMemberType=false
# _MUTTER_RATE 跨模块私有 import（spec 明确）；ainvoke 返回部分未知（langgraph）
"""ExpressionFacade：回复流程 + 碎碎念 + 搭话。

事件统一 publish，LLM 产出统一 evaluate。
"""
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from uuid import uuid4

from nyx.activity.facade import ActivityFacade
from nyx.config import ExpressionConfig
from nyx.desire.facade import DesireFacade
from nyx.enums import ContextMode, EventType, InteractionKind, MemoryKind
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import internal_text_event
from nyx.expression.classifier import classify_user_intent
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
from nyx.expression.prompt import build_system_prompt, build_temporal_block
from nyx.expression.store import ExpressionInteractionStore
from nyx.inner_life.facade import InnerLifeFacade
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.tools.registry import ToolRegistry
from nyx.types import CurrentState, Event, InteractionAttempt, Message, ShortTermDesire


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
    ) -> None:
        self._bus = bus
        self._llm = llm
        self._evaluator = evaluator
        self._memory = memory
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
        self._history: deque[Message] = deque(maxlen=config.max_context_len)
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
                bus=self._bus,
                canon=self._canon,
                ask_guidance=self._ask_guidance,
                config=self._config,
                history=self._history,
                tools=tools,
                knowledge_boundary=knowledge_boundary,
                register_question=self.register_question,
                finish_return=self._finish_return_claim,
            )
        )

    async def register_question(
        self,
        text: str,
        kind: InteractionKind,
        source_id: str,
        correlation_id: str,
        *,
        claimed_return: dict[str, float] | None = None,
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
        event = _ask_event(text, attempt_id, correlation_id, kind)
        await self._commit_attempt_event(attempt, event, claimed_return)
        return attempt_id

    async def _commit_attempt_event(
        self, attempt: InteractionAttempt, event: Event,
        claimed_return: dict[str, float] | None = None,
    ) -> None:
        """Commit an interaction row and its event as one local transaction."""
        await self._commit_attempt_events(attempt, [event], claimed_return)

    async def _commit_attempt_events(
        self, attempt: InteractionAttempt, events: list[Event],
        claimed_return: dict[str, float] | None = None,
    ) -> None:
        """Commit one interaction attempt and all durable events atomically."""
        if self._interaction_store is None:
            for event in events:
                try:
                    await self._bus.publish(event)
                except BaseException as error:
                    if claimed_return is not None:
                        try:
                            committed = await self._bus.is_durable(event.id)
                        except Exception:
                            raise error
                        if committed:
                            self._finish_return_claim(claimed_return)
                    raise
            self._finish_return_claim(claimed_return)
            return
        try:
            async with self._interaction_store.db.transaction():
                await self._interaction_store.create(attempt)
                for event in events:
                    await self._bus.append_in_transaction(event)
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
        return await self._commit_companion_question(
            text, source_id, correlation_id, event_content,
            InteractionKind.READING_QUESTION, EventType.READING_QUESTION,
        )

    async def commit_browsing_question(
        self, text: str, source_id: str, correlation_id: str,
        event_content: dict[str, object],
    ) -> str:
        """Atomically admit a browsing attempt, canonical ASK and display event."""
        return await self._commit_companion_question(
            text, source_id, correlation_id, event_content,
            InteractionKind.BROWSING_QUESTION, EventType.BROWSING_QUESTION,
        )

    async def _commit_companion_question(
        self, text: str, source_id: str, correlation_id: str,
        event_content: dict[str, object], kind: InteractionKind, event_type: EventType,
    ) -> str:
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
        ask = _ask_event(
            text, attempt_id, correlation_id, kind
        )
        reading = Event(
            id=str(uuid4()),
            timestamp=now,
            source=ask.source,
            type=event_type,
            content={**event_content, "attempt_id": attempt_id},
            correlation_id=correlation_id,
        )
        await self._commit_attempt_events(attempt, [ask, reading])
        return attempt_id

    async def answer_waiting(
        self, reply_event_id: str, reply_to: str | None = None
    ) -> InteractionAttempt | None:
        """Atomically claim and finish at most one waiting interaction."""
        if self._interaction_store is None:
            return None
        attempt = await self._interaction_store.claim_reply(reply_event_id, reply_to)
        if attempt is None:
            return None
        try:
            if attempt.kind is InteractionKind.INITIATE_CHAT:
                await self._desire.satisfy(attempt.source_id, True)
            if not await self._interaction_store.finish_answer(
                attempt.id, reply_event_id
            ):
                await self._interaction_store.release_claim(attempt.id)
                return None
        except BaseException:
            await self._interaction_store.release_claim(attempt.id)
            raise
        return attempt

    async def latest_initiate_chat_at(self) -> float | None:
        """Return the latest durable proactive-chat commitment time."""
        if self._interaction_store is None:
            return None
        return await self._interaction_store.latest_created_at(
            InteractionKind.INITIATE_CHAT
        )

    async def _last_dialogue_anchor(
        self, current_correlation_id: str | None
    ) -> tuple[Message, Message] | None:
        """Return the latest durable user turn with a terminal Nyx response."""
        candidates = await self._bus.list_events(
            limit=20, event_type=EventType.USER_MESSAGE
        )
        for user_event in candidates:
            if user_event.correlation_id == current_correlation_id:
                continue
            message = user_event.content.get("message")
            if not isinstance(message, str):
                continue
            turn_events = await self._bus.list_events(
                correlation_id=user_event.correlation_id
            )
            terminal = next(
                (
                    event
                    for event in turn_events
                    if event.type in (EventType.SPEAK, EventType.ASK)
                    and isinstance(event.content.get("content"), str)
                ),
                None,
            )
            if terminal is None:
                continue
            content = terminal.content["content"]
            if not isinstance(content, str):
                continue
            return (
                Message(
                    role="user", content=message, timestamp=user_event.timestamp
                ),
                Message(role="nyx", content=content, timestamp=terminal.timestamp),
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

    async def reply(
        self,
        msg: str,
        correlation_id: str,
        reply_to: str | None = None,
        browsing_context: dict[str, str] | None = None,
    ) -> None:
        """完整回复流程：跑 LangGraph 图，内部发布 think/speak/ask。"""
        claimed_return = self._claim_pending_return()
        now = time.time()
        try:
            # 用户说话 = 回应了之前的问句/搭话；清等待状态（不判断是否真在答）。
            await self.answer_waiting(correlation_id, reply_to)
            if self._interaction_store is None:
                self._waiting_user = False
                self._ask_cid = None
                if self._pending_chat_desire_id is not None:
                    await self._desire.satisfy(self._pending_chat_desire_id, True)
                self._pending_chat_desire_id = None
            state = await self._inner_life.get_state()
            temporal_context = await self._temporal_context(
                now, correlation_id, claimed_return
            )
            initial: ReplyState = {
                "message": msg,
                "mode": ContextMode.FAST,
                # 朴素回溯最近 max_context_len 条（快通道用，不含当前消息）；
                # 慢通道在 assemble 重截断。
                "context": list(self._history)[-self._config.max_context_len:],
                "memories": [],
                "state": state,
                "narrative": None,
                "think": [],
                "speak": [],
                "ask": None,
                "round": 0,
                "correlation_id": correlation_id,
                "last_slow_at": self._last_slow_at,
                "tool_outputs": [],
                "intent": classify_user_intent(msg),
                "temporal_context": temporal_context,
                "claimed_return": claimed_return,
                "fallback": False,
                "browsing_context": browsing_context,
            }
            result = await self._graph.ainvoke(initial)
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
            temporal_context = await self._temporal_context(
                started_at, correlation_id, claimed_return
            )
            system = build_system_prompt(
                self._canon,
                state,
                ask_guidance=self._ask_guidance,
                knowledge_boundary=self._knowledge_boundary,
                temporal_context=temporal_context,
            )
            user = (
                f"你想主动和用户说点什么。基于这个念头：{desire.description}。"
                "说一句自然的开场白。"
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
            event.content["desire_id"] = desire.id
            await self._commit_attempt_event(attempt, event, claimed_return)
            self._history.append(
                Message(role="nyx", content=output.content, timestamp=time.time())
            )
            if self._interaction_store is None:
                self._pending_chat_desire_id = desire.id
                self._chat_at = time.time()
            return True
        except BaseException:
            self._release_return_claim(claimed_return)
            raise

    def record_proactive_turn(self, text: str) -> None:
        """把 Nyx 主动产出（读书提问/联想）追加进会话历史，供后续 reply() 引用。"""
        self._history.append(Message(role="nyx", content=text, timestamp=time.time()))

    async def mutter(self, state: CurrentState, correlation_id: str) -> None:
        """碎碎念：空闲 + 随机命中才发；低频走 LLM 即兴（走神），否则模板填空。
        无话可说 / 近期说过同一句则不发。"""
        if state.current_activity is not None:
            return  # 忙，不碎碎念
        if random.random() >= _MUTTER_RATE:
            return
        claimed_return: dict[str, float] | None = None
        try:
            if random.random() < _LLM_MUTTER_RATE:
                claimed_return = self._claim_pending_return()
                temporal_context = await self._temporal_context(
                    time.time(), correlation_id, claimed_return
                )
                text = await self._mutter_wander(
                    state, correlation_id, temporal_context
                )
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
        except BaseException:
            self._release_return_claim(claimed_return)
            raise

    async def _mutter_wander(
        self,
        state: CurrentState,
        correlation_id: str,
        temporal_context: str,
    ) -> str | None:
        """LLM 即兴碎碎念（低频走神）：一句自然口语，可停顿/离题；空则回退模板。"""
        system = build_system_prompt(
            self._canon,
            state,
            knowledge_boundary=self._knowledge_boundary,
            temporal_context=temporal_context,
        )
        user = (
            "你闲下来了，心里冒出一句碎碎念。说一句自然、口语的话，"
            "可以有点走神或停顿，一两句就好，别太正式。"
        )
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
        """超时收尾（tick 心跳直呼）：问句无人答 → 记「用户未回答」；
        搭话被忽略 → 淘汰该互动欲（expire 内值回灌 +0.3）。"""
        if self._interaction_store is not None:
            while True:
                attempt = await self._interaction_store.claim_expired(now)
                if attempt is None:
                    return
                try:
                    if attempt.kind is InteractionKind.INITIATE_CHAT:
                        await self._desire.expire(attempt.source_id)
                    else:
                        await self._memory.record_no_answer(
                            attempt.text, attempt.correlation_id
                        )
                except Exception:
                    await self._interaction_store.release_claim(attempt.id)
                    raise
                if not await self._interaction_store.finish_expired(attempt.id):
                    return
        if self._waiting_user and now - self._ask_at >= self._config.ask_timeout:
            await self._memory.record_no_answer(self._ask_text, self._ask_cid or "")
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
):
    event = internal_text_event(EventType.ASK, text, correlation_id)
    event.content["attempt_id"] = attempt_id
    event.content["kind"] = kind.value
    return event
