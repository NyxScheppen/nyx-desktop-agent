"""应用组件装配与运行期上下文。"""
import asyncio
import math
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from nyx.activity.facade import ActivityFacade
from nyx.activity.material_store import MaterialStore
from nyx.activity.screen import ScreenObserver, capture_screen
from nyx.activity.store import ActivityStore
from nyx.bootstrap import (
    load_ask,
    load_canon,
    load_prompt_files,
    seed_desire,
    seed_inner_life,
)
from nyx.config import Config
from nyx.db import Database, connect
from nyx.desire.facade import DesireFacade
from nyx.desire.store import DesireStore
from nyx.eval.evaluator import Evaluator
from nyx.eval.store import EvalStore
from nyx.events.bus import EventBus
from nyx.expression.facade import ExpressionFacade
from nyx.expression.store import ExpressionInteractionStore
from nyx.inner_life.facade import InnerLifeFacade
from nyx.inner_life.store import InnerLifeStore
from nyx.llm.client import LlmClient
from nyx.llm.vision import VisionClient
from nyx.memory.facade import MemoryFacade
from nyx.memory.retrieval import MemoryRetrieval, build_embed
from nyx.memory.store import MemoryStore
from nyx.reading.facade import ReadingFacade
from nyx.reading.store import ReadingStore
from nyx.tools.file_io import build_file_io_tool
from nyx.tools.local_search import build_local_search_tool
from nyx.tools.registry import ToolRegistry
from nyx.tools.web_fetch import build_web_fetch_tool
from nyx.tools.web_search import build_web_search_tool
from nyx.types import CurrentState, Event, ReflectionOutcome


@dataclass
class _App:
    """组合根装配产物：组件引用与运行期状态。"""

    bus: EventBus
    inner_life: InnerLifeFacade
    desire: DesireFacade
    memory: MemoryFacade
    activity: ActivityFacade
    expression: ExpressionFacade
    reading: ReadingFacade
    evaluator: Evaluator
    eval_store: EvalStore
    config: Config
    last_chat_at: float = 0.0
    last_presence: str = "away"
    last_window_title: str = ""
    last_screen_summary: str = ""
    presence_initialized: bool = False
    presence_changed_at: float = 0.0
    presence_observed_at: float = 0.0
    presence_received_at: float = 0.0
    away_started_at: float | None = None
    last_returned_at: float | None = None
    last_away_duration_seconds: float | None = None
    pending_return: dict[str, float] | None = None
    claimed_return: dict[str, float] | None = None
    presence_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    screen_observer: ScreenObserver | None = None
    database: Database | None = None

    async def publish_observation(self, event: Event, idle_seconds: float) -> bool:
        """Persist an observation before committing its in-memory snapshot."""
        presence = event.content.get("presence")
        window_title = event.content.get("window_title")
        if not isinstance(presence, str) or not isinstance(window_title, str):
            raise ValueError("observation payload 非法")
        sampled_at = float(event.content.get("sampled_at", event.timestamp))
        async with self.presence_lock:
            observed_now = time.time()
            if sampled_at > observed_now:
                return False
            clock_reset = observed_now < self.presence_received_at
            if (
                not clock_reset
                and self.presence_initialized
                and sampled_at <= self.presence_observed_at
            ):
                return False
            previous = (
                self.last_presence
                if self.presence_initialized and not clock_reset
                else None
            )
            transition: str | None
            away_duration: float | None = None
            if previous is None:
                transition = "initial"
            elif previous == presence:
                transition = None
            elif previous == "away" and presence == "online":
                transition = "returned"
                away_duration = max(
                    0.0,
                    sampled_at
                    - (
                        self.away_started_at
                        if self.away_started_at is not None
                        else self.presence_changed_at
                    ),
                )
            elif presence == "away":
                transition = "became_away"
            elif presence == "busy":
                transition = "became_busy"
            else:
                transition = None

            event.content.update(
                {
                    "idle_seconds": idle_seconds,
                    "sampled_at": sampled_at,
                    "previous_presence": previous,
                    "transition": transition,
                    "away_duration_seconds": away_duration,
                }
            )
            cancelled: asyncio.CancelledError | None = None
            try:
                await self.bus.publish(event)
            except asyncio.CancelledError as error:
                try:
                    committed = await self.bus.is_durable(event.id)
                except Exception:
                    raise error
                if not committed:
                    raise
                cancelled = error

            changed = previous is None or previous != presence
            self.presence_initialized = True
            self.presence_observed_at = sampled_at
            self.presence_received_at = max(
                event.timestamp, sampled_at,
                0.0 if clock_reset else self.presence_received_at,
            )
            self.last_presence = presence
            self.last_window_title = window_title
            if changed:
                self.presence_changed_at = sampled_at
            if clock_reset or (presence == "away" and changed):
                self.pending_return = None
                self.claimed_return = None
            if clock_reset:
                self.last_returned_at = None
                self.last_away_duration_seconds = None
                self.away_started_at = None
            if presence == "away" and changed:
                self.away_started_at = max(0.0, sampled_at - idle_seconds)
            elif previous == "away" and presence != "away":
                self.away_started_at = None
            if transition == "returned" and away_duration is not None:
                returned = {
                    "returned_at": sampled_at,
                    "away_duration_seconds": away_duration,
                }
                self.last_returned_at = sampled_at
                self.last_away_duration_seconds = away_duration
                self.pending_return = returned
            if cancelled is not None:
                raise cancelled
            return True

    async def record_user_online(self, timestamp: float) -> None:
        """Treat a durable user message as immediate, idempotent online evidence."""
        async with self.presence_lock:
            if not math.isfinite(timestamp) or timestamp < 0 or timestamp > time.time():
                return
            if self.presence_initialized and timestamp <= self.presence_observed_at:
                return
            self.presence_observed_at = timestamp
            self.presence_received_at = max(self.presence_received_at, timestamp)
            if not self.presence_initialized:
                self.presence_initialized = True
                self.last_presence = "online"
                self.presence_changed_at = timestamp
                return
            if self.last_presence != "away":
                if self.last_presence != "online":
                    self.last_presence = "online"
                    self.presence_changed_at = timestamp
                return
            away_duration = max(
                0.0,
                timestamp
                - (
                    self.away_started_at
                    if self.away_started_at is not None
                    else self.presence_changed_at
                ),
            )
            self.last_presence = "online"
            self.presence_changed_at = timestamp
            self.away_started_at = None
            self.last_returned_at = timestamp
            self.last_away_duration_seconds = away_duration
            self.pending_return = {
                "returned_at": timestamp,
                "away_duration_seconds": away_duration,
            }

    def claim_return_context(self) -> dict[str, float] | None:
        """Reserve the pending return fact for one expression attempt."""
        if self.claimed_return is not None or self.pending_return is None:
            return None
        claim = self.pending_return
        self.pending_return = None
        self.claimed_return = claim
        return claim

    def finish_return_context(self, claim: dict[str, float] | None) -> None:
        """Consume a matching return claim after successful expression."""
        if claim is not None and self.claimed_return is claim:
            self.claimed_return = None

    def release_return_context(self, claim: dict[str, float] | None) -> None:
        """Release a failed claim without overwriting a newer return fact."""
        if claim is None or self.claimed_return is not claim:
            return
        self.claimed_return = None
        if self.pending_return is None:
            self.pending_return = claim


def build_tools(config: Config) -> ToolRegistry:
    """Register always-on local tools and opt-in web tools."""
    tools = ToolRegistry()
    tools.register(build_local_search_tool())
    tools.register(build_file_io_tool())
    if config.exploration.web_enabled:
        tools.register(build_web_search_tool())
        tools.register(build_web_fetch_tool())
    return tools


async def build_app_context(
    config: Config,
    *,
    canon_files: tuple[str, ...],
    ask_files: tuple[str, ...],
) -> _App:
    """Build stores and facades in dependency order, then seed local state."""
    db: Database = await connect()
    llm = LlmClient.from_config(config.llm)
    bus = EventBus(db)
    tools = build_tools(config)

    memory_store = MemoryStore(db)
    embed = build_embed(config.embedding.model)
    eval_store = EvalStore(db)
    evaluator = Evaluator(embed, eval_store)
    retrieval = MemoryRetrieval(memory_store, embed)
    memory = MemoryFacade(
        memory_store, retrieval, bus, llm, evaluator, config.memory, embed
    )

    desire_store = DesireStore(db)
    desire = DesireFacade(
        desire_store,
        bus,
        llm,
        evaluator,
        config.desire,
        lambda: memory.list_memories(),
        embed,
    )

    inner_life_store = InnerLifeStore(db)
    activity_store = ActivityStore(db)
    material_store = MaterialStore(db)
    state_reader: Callable[[], Awaitable[CurrentState]] | None = None
    reflection_runner: (
        Callable[[str | None], Awaitable[ReflectionOutcome | None]] | None
    ) = None
    observation_reader: Callable[[], Awaitable[dict[str, str]]] | None = None
    return_context_owner: _App | None = None

    async def get_state() -> CurrentState:
        if state_reader is None:
            raise RuntimeError("inner_life state reader 尚未绑定")
        return await state_reader()

    async def reflect(correlation_id: str | None) -> ReflectionOutcome | None:
        if reflection_runner is None:
            raise RuntimeError("inner_life reflection runner 尚未绑定")
        return await reflection_runner(correlation_id)

    async def get_observation() -> dict[str, str]:
        if observation_reader is None:
            raise RuntimeError("runtime observation reader 尚未绑定")
        return await observation_reader()

    def claim_return() -> dict[str, float] | None:
        if return_context_owner is None:
            raise RuntimeError("runtime return context 尚未绑定")
        return return_context_owner.claim_return_context()

    def finish_return(claim: dict[str, float] | None) -> None:
        if return_context_owner is None:
            raise RuntimeError("runtime return context 尚未绑定")
        return_context_owner.finish_return_context(claim)

    def release_return(claim: dict[str, float] | None) -> None:
        if return_context_owner is None:
            raise RuntimeError("runtime return context 尚未绑定")
        return_context_owner.release_return_context(claim)

    prompt_dir = Path(os.environ.get("NYX_CANON_DIR", "prompts"))
    canon = load_canon(prompt_dir, canon_files)
    ask = load_ask(prompt_dir, ask_files)
    knowledge_boundary = load_prompt_files(
        prompt_dir, ("knowledge-boundary.md",)
    )
    activity = ActivityFacade(
        activity_store,
        material_store,
        bus,
        llm,
        evaluator,
        tools,
        desire,
        memory,
        get_state,
        reflect,
        get_observation,
        config.activity,
        config.exploration,
        canon,
    )
    await activity.recover_stale_running()
    inner_life = InnerLifeFacade(
        inner_life_store, activity, desire, memory, bus, llm, evaluator, config
    )
    state_reader = inner_life.get_state
    reflection_runner = inner_life.reflect

    await seed_inner_life(inner_life_store)
    await seed_desire(desire_store)
    expression = ExpressionFacade(
        bus,
        llm,
        evaluator,
        memory,
        activity,
        desire,
        inner_life,
        canon,
        ask,
        config.expression,
        tools,
        interaction_store=ExpressionInteractionStore(db),
        knowledge_boundary=knowledge_boundary,
        observation_reader=get_observation,
        claim_return=claim_return,
        finish_return=finish_return,
        release_return=release_return,
    )
    reading = ReadingFacade(
        ReadingStore(db),
        inner_life,
        desire,
        memory,
        llm,
        evaluator,
        bus,
        canon,
        expression,
    )
    app = _App(
        bus,
        inner_life,
        desire,
        memory,
        activity,
        expression,
        reading,
        evaluator,
        eval_store,
        config,
        database=db,
    )
    return_context_owner = app

    async def read_observation() -> dict[str, str]:
        return {
            "presence": app.last_presence,
            "window_title": app.last_window_title,
            "screen_summary": app.last_screen_summary,
        }

    observation_reader = read_observation
    if config.vision.enabled:
        vision = VisionClient.from_config(config.vision)
        app.screen_observer = ScreenObserver(
            capture_screen, vision.describe, config.vision.interval_seconds
        )
    return app
