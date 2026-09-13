"""应用组件装配与运行期上下文。"""
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from nyx.activity.facade import ActivityFacade
from nyx.activity.material_store import MaterialStore
from nyx.activity.screen import ScreenObserver, capture_screen
from nyx.activity.store import ActivityStore
from nyx.bootstrap import load_ask, load_canon, seed_desire, seed_inner_life
from nyx.config import Config
from nyx.db import Database, connect
from nyx.desire.facade import DesireFacade
from nyx.desire.store import DesireStore
from nyx.eval.evaluator import Evaluator
from nyx.eval.store import EvalStore
from nyx.events.bus import EventBus
from nyx.expression.facade import ExpressionFacade
from nyx.inner_life.facade import InnerLifeFacade
from nyx.inner_life.store import InnerLifeStore
from nyx.llm.client import LlmClient
from nyx.llm.vision import VisionClient
from nyx.memory.facade import MemoryFacade
from nyx.memory.retrieval import MemoryRetrieval, build_embed
from nyx.memory.store import MemoryStore
from nyx.reading.facade import ReadingFacade
from nyx.reading.store import ReadingStore
from nyx.subscriptions import subscribe
from nyx.tools.file_io import build_file_io_tool
from nyx.tools.local_search import build_local_search_tool
from nyx.tools.registry import ToolRegistry
from nyx.tools.web_fetch import build_web_fetch_tool
from nyx.tools.web_search import build_web_search_tool
from nyx.types import CurrentState, ReflectionOutcome


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
    screen_observer: ScreenObserver | None = None


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
    """Build stores and facades in dependency order, then seed and subscribe."""
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
    state_holder: list[Callable[[], Awaitable[CurrentState]]] = []
    reflect_holder: list[
        Callable[[str | None], Awaitable[ReflectionOutcome | None]]
    ] = []
    observation_holder: list[Callable[[], Awaitable[dict[str, str]]]] = []

    async def get_state() -> CurrentState:
        return await state_holder[0]()

    async def reflect(correlation_id: str | None) -> ReflectionOutcome | None:
        return await reflect_holder[0](correlation_id)

    async def get_observation() -> dict[str, str]:
        return await observation_holder[0]()

    prompt_dir = Path(os.environ.get("NYX_CANON_DIR", "prompts"))
    canon = load_canon(prompt_dir, canon_files)
    ask = load_ask(prompt_dir, ask_files)
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
    inner_life = InnerLifeFacade(
        inner_life_store, activity, desire, memory, bus, llm, evaluator, config
    )
    state_holder.append(inner_life.get_state)
    reflect_holder.append(inner_life.reflect)

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
    )

    async def read_observation() -> dict[str, str]:
        return {
            "presence": app.last_presence,
            "window_title": app.last_window_title,
            "screen_summary": app.last_screen_summary,
        }

    observation_holder.append(read_observation)
    if config.vision.enabled:
        vision = VisionClient.from_config(config.vision)
        app.screen_observer = ScreenObserver(
            capture_screen, vision.describe, config.vision.interval_seconds
        )
    subscribe(app)
    return app
