"""应用组件装配与运行期上下文。"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nyx.activity.facade import ActivityFacade
from nyx.activity.screen import ScreenObserver, capture_screen
from nyx.activity.store import ActivityStore
from nyx.bootstrap import (
    initialize_card,
    load_ask,
    load_canon,
    load_prompt_files,
)
from nyx.character_cards import BUILTIN_CARD, load_card, read_package
from nyx.config import Config
from nyx.db import Database, connect, resolve_db_path
from nyx.desire.facade import DesireFacade
from nyx.desire.store import DesireStore
from nyx.enums import EventType, Source
from nyx.eval.evaluator import Evaluator
from nyx.eval.store import EvalStore
from nyx.events.bus import EventBus
from nyx.expression.facade import ExpressionFacade
from nyx.expression.store import ExpressionInteractionStore
from nyx.inner_life.facade import InnerLifeFacade
from nyx.inner_life.store import InnerLifeStore
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.memory.facts import MemoryFactStore
from nyx.memory.retrieval import MemoryRetrieval, build_embed
from nyx.memory.store import MemoryStore
from nyx.reading.facade import ReadingFacade
from nyx.reading.store import ReadingStore
from nyx.tools.activity_tasks import build_activity_task_tools
from nyx.tools.file_io import build_file_io_tool
from nyx.tools.local_search import build_local_search_tool
from nyx.tools.registry import ToolRegistry
from nyx.tools.web_fetch import build_web_fetch_tool
from nyx.tools.web_search import build_web_search_tool
from nyx.types import Book, CurrentState, Event, ReadingProgress

_PRESENCE_FIELDS = (
    "last_presence",
    "presence_initialized",
    "presence_changed_at",
    "presence_observed_at",
    "presence_received_at",
    "away_started_at",
    "last_returned_at",
    "last_away_duration_seconds",
    "pending_return",
    "claimed_return",
)


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
    character_id: str = "nyx"
    host: _Host | None = field(default=None, repr=False)
    tick_task: asyncio.Task[None] | None = field(default=None, repr=False)
    memory_task: asyncio.Task[None] | None = field(default=None, repr=False)

    def can_initiate(self) -> bool:
        """Admission uses the selected runtime; admitted work keeps its owner."""
        return self.host is None or (
            self.host.current_id == self.character_id
            and self.host.records[self.character_id]["status"] == "RUNNING"
        )

    async def apply_presence(self, sequence: int, payload: dict[str, Any]) -> None:
        """Atomically commit a host fact, checkpoint and its role-local event."""
        event = Event(
            id=f"presence:{self.character_id}:{sequence}",
            timestamp=payload["sampled_at"],
            source=Source.EXTERNAL,
            type=EventType.OBSERVATION_STATE,
            content=dict(payload),
            correlation_id=f"presence:{sequence}",
        )
        event.content.setdefault("window_title", "")
        await self.publish_observation(
            event, payload["idle_seconds"], sequence=sequence
        )

    async def publish_observation(
        self,
        event: Event,
        idle_seconds: float,
        *,
        sequence: int = 0,
    ) -> bool:
        """Persist an observation before committing its in-memory snapshot."""
        presence = event.content.get("presence")
        window_title = event.content.get("window_title")
        if not isinstance(presence, str) or not isinstance(window_title, str):
            raise ValueError("observation payload 非法")
        sampled_at = float(event.content.get("sampled_at", event.timestamp))
        async with self.presence_lock:
            if sequence and self.database is not None:
                async with self.database.lock:
                    cursor = await self.database.conn.execute(
                        "SELECT sequence, snapshot FROM presence_checkpoint WHERE "
                        "id='self'"
                    )
                    row = await cursor.fetchone()
                if row is not None and row["sequence"] >= sequence:
                    return False
            before = {key: getattr(self, key) for key in _PRESENCE_FIELDS}
            observed_now = time.time()
            admitted = event.content.get("_presence_accepted") if sequence else None
            if admitted is None and sampled_at > observed_now:
                return False
            clock_reset = (
                bool(event.content.get("_clock_reset"))
                if admitted is not None
                else observed_now < self.presence_received_at
            )
            if admitted is False or (
                admitted is None
                and not clock_reset
                and self.presence_initialized
                and sampled_at <= self.presence_observed_at
            ):
                if sequence and self.database is not None:
                    async with self.database.transaction() as conn:
                        await conn.execute(
                            "INSERT OR REPLACE INTO presence_checkpoint VALUES "
                            "('self', ?, ?)",
                            (sequence, json.dumps(before)),
                        )
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
            snapshot = dict(before)
            changed = previous is None or previous != presence
            snapshot["presence_initialized"] = True
            snapshot["presence_observed_at"] = sampled_at
            snapshot["presence_received_at"] = max(
                event.timestamp,
                sampled_at,
                0.0 if clock_reset else snapshot["presence_received_at"],
            )
            snapshot["last_presence"] = presence
            snapshot["last_window_title"] = window_title
            if changed:
                snapshot["presence_changed_at"] = sampled_at
            if clock_reset or (presence == "away" and changed):
                snapshot["pending_return"] = None
                snapshot["claimed_return"] = None
            if clock_reset:
                snapshot["last_returned_at"] = None
                snapshot["last_away_duration_seconds"] = None
                snapshot["away_started_at"] = None
            if presence == "away" and changed:
                snapshot["away_started_at"] = max(0.0, sampled_at - idle_seconds)
            elif previous == "away" and presence != "away":
                snapshot["away_started_at"] = None
            if transition == "returned" and away_duration is not None:
                returned = {
                    "returned_at": sampled_at,
                    "away_duration_seconds": away_duration,
                }
                snapshot["last_returned_at"] = sampled_at
                snapshot["last_away_duration_seconds"] = away_duration
                snapshot["pending_return"] = returned
            cancelled: asyncio.CancelledError | None = None
            appended = False
            try:
                if sequence and self.database is not None:
                    async with self.database.transaction() as conn:
                        existed = await self.bus.is_durable(event.id)
                        await self.bus.append_in_transaction(event)
                        appended = not existed
                        await conn.execute(
                            "INSERT OR REPLACE INTO presence_checkpoint VALUES "
                            "('self', ?, ?)",
                            (sequence, json.dumps(snapshot)),
                        )
                else:
                    await self.bus.publish(event)
            except BaseException as error:
                committed = await self.bus.is_durable(event.id)
                if not committed:
                    if appended:
                        self.bus.persisted_count -= 1
                    raise
                if isinstance(error, asyncio.CancelledError):
                    cancelled = error
                else:
                    raise
            for key, value in snapshot.items():
                setattr(self, key, value)
            private_current = self.can_initiate() and (
                self.host is None
                or (
                    event.content.get("character_id") == self.character_id
                    and event.content.get("selection_revision") == self.host.revision
                )
            )
            self.last_window_title = window_title if private_current else ""
            if sequence:
                await self.bus.announce_committed(event)
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


def build_tools(
    config: Config,
    *,
    workspace: Path = Path("workspace"),
    blocked: list[Path] | tuple[Path, ...] = (),
) -> ToolRegistry:
    """Register always-on local tools and opt-in web tools."""
    tools = ToolRegistry()
    tools.register(build_local_search_tool(blocked=blocked))
    tools.register(build_file_io_tool(workspace, blocked=blocked))
    if config.exploration.web_enabled:
        tools.register(build_web_search_tool())
        tools.register(build_web_fetch_tool())
    return tools


async def build_app_context(
    config: Config,
    *,
    canon_files: tuple[str, ...],
    ask_files: tuple[str, ...],
    host: _Host | None = None,
    character: dict[str, Any] | None = None,
) -> _App:
    """Build stores and facades in dependency order, then seed local state."""
    db: Database = await connect(character["db_path"] if character else None)
    try:
        llm = host.llm.fork() if host else LlmClient.from_config(config.llm)
        bus = EventBus(db)
        tools = build_tools(
            config,
            workspace=Path(character["workspace"]) if character else Path("workspace"),
            blocked=host.blocked_paths(character["id"]) if host and character else (),
        )

        memory_store = MemoryStore(db)
        fact_store = MemoryFactStore(db)
        embed = host.embed if host else build_embed(config.embedding.model)
        eval_store = EvalStore(db)
        evaluator = Evaluator(embed, eval_store)
        retrieval = MemoryRetrieval(memory_store, embed)
        memory = MemoryFacade(
            memory_store,
            retrieval,
            bus,
            llm,
            evaluator,
            config.memory,
            embed,
            fact_store,
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
        state_reader: Callable[[], Awaitable[CurrentState]] | None = None
        reading_context_reader: Callable[[str, str], Awaitable[str]] | None = None
        reading_owner: ReadingFacade | None = None
        observation_reader: Callable[[], Awaitable[dict[str, str]]] | None = None
        return_context_owner: _App | None = None

        async def get_state() -> CurrentState:
            if state_reader is None:
                raise RuntimeError("inner_life state reader 尚未绑定")
            return await state_reader()

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

        async def read_reading_context(source_id: str, query: str) -> str:
            if reading_context_reader is None:
                return ""
            return await reading_context_reader(source_id, query)

        async def list_reader_books() -> list[Book]:
            if reading_owner is None:
                raise RuntimeError("reading facade 尚未绑定")
            return await reading_owner.list_readable_books()

        async def read_reader_book(
            book_id: str, target_paragraph: int | None, correlation_id: str
        ) -> dict[str, Any]:
            if reading_owner is None:
                raise RuntimeError("reading facade 尚未绑定")
            return await reading_owner.read_for_activity(
                book_id, target_paragraph, correlation_id
            )

        async def get_reader_book(book_id: str) -> Book:
            if reading_owner is None:
                raise RuntimeError("reading facade 尚未绑定")
            return await reading_owner.get_book(book_id)

        async def get_reader_progress(book_id: str) -> ReadingProgress:
            if reading_owner is None:
                raise RuntimeError("reading facade 尚未绑定")
            return await reading_owner.get_progress(book_id)

        prompt_dir = Path(
            os.environ.get("NYX_CANON_DIR", Path(__file__).parent / "prompts")
        )
        card = (
            load_card(Path(character["card_path"]))
            if character
            else load_card(BUILTIN_CARD)
        )
        canon = card.canon if character else load_canon(prompt_dir, canon_files)
        ask = card.ask if character else load_ask(prompt_dir, ask_files)
        knowledge_boundary = load_prompt_files(prompt_dir, ("knowledge-boundary.md",))
        activity = ActivityFacade(
            activity_store,
            bus,
            llm,
            evaluator,
            tools,
            desire,
            memory,
            get_state,
            get_observation,
            config.activity,
            config.exploration,
            canon,
            list_reader_books,
            read_reader_book,
            get_reader_book,
            get_reader_progress,
        )
        await activity.recover_stale_running()
        task_tools = build_activity_task_tools(
            activity.assign_web_task,
            activity.assign_book_task_by_title,
            activity.assign_creation_task,
            activity.assign_browser_survey_task,
        )
        if config.exploration.web_enabled:
            tools.register(task_tools[0])
            tools.register(task_tools[3])
        tools.register(task_tools[1])
        tools.register(task_tools[2])
        inner_life = InnerLifeFacade(
            inner_life_store, activity, desire, memory, bus, llm, evaluator, config
        )
        state_reader = inner_life.get_state

        await initialize_card(db, card.manifest)
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
            reading_context_reader=read_reading_context,
        )
        await expression.restore_history()
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
        reading_owner = reading
        reading_context_reader = reading.build_reply_context
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
            host=host,
            character_id=card.manifest.id,
        )
        return_context_owner = app
        llm.can_initiate = app.can_initiate

        async def read_observation() -> dict[str, str]:
            return {
                "presence": app.last_presence,
                "window_title": app.last_window_title,
                "screen_summary": app.last_screen_summary,
            }

        observation_reader = read_observation
        if config.vision.enabled and host is None:
            from nyx.llm.vision import VisionClient

            vision = VisionClient.from_config(config.vision)
            app.screen_observer = ScreenObserver(
                capture_screen, vision.describe, config.vision.interval_seconds
            )
        return app
    except BaseException:
        await db.close()
        raise


_HOST_MIGRATIONS: list[tuple[int, list[str]]] = [
    (
        1,
        [
            "CREATE TABLE characters (id TEXT PRIMARY KEY, record TEXT NOT NULL)",
            "CREATE TABLE selection (id TEXT PRIMARY KEY, character_id TEXT "
            "NOT NULL, revision INTEGER NOT NULL)",
            "CREATE TABLE presence_samples (sequence INTEGER PRIMARY KEY "
            "AUTOINCREMENT, sample_id TEXT UNIQUE NOT NULL, payload TEXT NOT "
            "NULL)",
            "CREATE TABLE presence_delivery (sequence INTEGER NOT NULL "
            "REFERENCES presence_samples(sequence), character_id TEXT NOT "
            "NULL, attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT "
            "NULL DEFAULT 0, error TEXT, status TEXT NOT NULL DEFAULT "
            "'pending', PRIMARY KEY(sequence, character_id))",
        ],
    ),
    (
        2,
        [
            "CREATE TABLE presence_current (id TEXT PRIMARY KEY, sequence "
            "INTEGER NOT NULL, payload TEXT NOT NULL, received_at REAL NOT "
            "NULL)",
            "INSERT INTO presence_current SELECT 'self', sequence, payload, "
            "json_extract(payload, '$.sampled_at') FROM presence_samples "
            "ORDER BY json_extract(payload, '$.sampled_at') DESC, sequence "
            "DESC LIMIT 1",
        ],
    ),
]


class _Host:
    """Composition root for registered runtimes and shared desktop resources."""

    def __init__(self, config: Config, root: Path, db: Database) -> None:
        self.config, self.root, self.database = config, root.resolve(), db
        self.records: dict[str, dict[str, Any]] = {}
        self.apps: dict[str, _App] = {}
        self.current_id = "nyx"
        self.revision = 0
        self.llm = LlmClient.from_config(config.llm)
        self.embed = build_embed(config.embedding.model)
        self.lock = asyncio.Lock()
        self.presence_locks: dict[str, asyncio.Lock] = {}
        self.lifecycle: dict[str, asyncio.Task[None]] = {}
        self.requests: dict[str, set[asyncio.Task[Any]]] = {}
        self.background: set[asyncio.Task[None]] = set()
        self.closing = False
        self.boundaries: dict[str, list[Path]] = {}
        self.operations: dict[str, asyncio.Lock] = {}
        self.stopping: dict[str, asyncio.Task[None]] = {}
        self.presence_tasks: dict[str, asyncio.Task[bool]] = {}

    @classmethod
    async def open(cls, config: Config, root: Path) -> _Host:
        """Load registry, retain legacy Nyx paths and recover registered saves."""
        db = await connect(
            str(root / "data/characters/host.db"), migrations=_HOST_MIGRATIONS
        )
        host: _Host | None = None
        try:
            host = cls(config, root, db)
            async with db.lock:
                rows = await (
                    await db.conn.execute("SELECT record FROM characters")
                ).fetchall()
                selection = await (
                    await db.conn.execute("SELECT * FROM selection WHERE id='self'")
                ).fetchone()
            host.records = {
                r["id"]: r for r in (json.loads(row["record"]) for row in rows)
            }
            if selection:
                host.current_id, host.revision = (
                    selection["character_id"],
                    selection["revision"],
                )
            if "nyx" not in host.records:
                legacy = Path(resolve_db_path())
                if not legacy.is_absolute():
                    legacy = root / (
                        "nyx.db"
                        if not os.environ.get("NYX_DB") and (root / "nyx.db").is_file()
                        else legacy
                    )
                card = load_card(BUILTIN_CARD)
                record = {
                    "id": "nyx",
                    "name": card.manifest.name,
                    "status": "STARTING",
                    "error": None,
                    "card_path": str(BUILTIN_CARD.resolve()),
                    "db_path": str(legacy.resolve()),
                    "workspace": str((root / "workspace").resolve()),
                }
                await host.save_record(record)
                async with db.transaction() as conn:
                    await conn.execute(
                        "INSERT OR IGNORE INTO selection VALUES ('self', 'nyx', 0)"
                    )
            for identity, record in list(host.records.items()):
                if record["status"] == "STOPPING":
                    record["status"] = "ARCHIVED"
                    await host.save_record(record)
                    await host.cancel_presence(identity)
                elif record["status"] != "ARCHIVED":
                    await host.start(identity, run=False)
            return host
        except BaseException:
            if host is not None:
                for app in host.apps.values():
                    if app.database is not None:
                        await app.database.close()
            await db.close()
            raise

    async def save_record(self, record: dict[str, Any]) -> None:
        """Commit lifecycle state before publishing it to admission readers."""
        snapshot = dict(record)
        async with self.database.transaction() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO characters VALUES (?, ?)",
                (record["id"], json.dumps(snapshot)),
            )
        self.records[record["id"]] = snapshot

    def blocked_paths(self, identity: str) -> list[Path]:
        """Protect internal saves; allow this role's card and workspace."""
        roots = [
            self.root / ("data/characters/host.db" + suffix)
            for suffix in ("", "-wal", "-shm")
        ]
        for role, record in self.records.items():
            roots.append(Path(record["db_path"]))
            roots.extend(
                Path(record["db_path"] + suffix) for suffix in ("-wal", "-shm")
            )
            if role != identity:
                roots.extend((Path(record["card_path"]), Path(record["workspace"])))
        # New IDs under the characters directory are blocked even before registration.
        for directory in (self.root / "data/characters").iterdir():
            if directory.is_dir() and directory.name != identity:
                roots.append(directory)
        boundaries = self.boundaries.setdefault(identity, [])
        boundaries[:] = roots
        return boundaries

    async def register(self, package: bytes) -> dict[str, Any]:
        """Validate, reserve a slot atomically, then install a fixed snapshot."""
        card = await asyncio.to_thread(read_package, package)
        identity = card.manifest.id
        async with self.lock:
            if identity in self.records:
                raise ValueError("角色 ID 已存在；请恢复已有存档")
            if sum(r["status"] != "ARCHIVED" for r in self.records.values()) >= 4:
                raise ValueError("最多注册四个角色")
            base = self.root / "data/characters" / identity
            record = {
                "id": identity,
                "name": card.manifest.name,
                "status": "STARTING",
                "error": None,
                "card_path": str(base / "card"),
                "db_path": str(base / "nyx.db"),
                "workspace": str(base / "workspace"),
            }
            await self.save_record(record)
            for role in self.apps:
                self.blocked_paths(role)
            try:

                def install() -> None:
                    target = base / "card"
                    target.mkdir(parents=True, exist_ok=False)
                    for name, payload in card.files.items():
                        path = target / name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(payload)

                worker = asyncio.get_running_loop().run_in_executor(None, install)
                cancelled = False
                while True:
                    try:
                        await asyncio.shield(worker)
                        break
                    except asyncio.CancelledError:
                        cancelled = True
                        if worker.cancelled():
                            raise
                if cancelled:
                    record.update(
                        status="FAILED", error="卡片安装请求已取消；可重试启动或卸载"
                    )
                    await self.save_record(record)
                    raise asyncio.CancelledError
            except Exception as error:
                record.update(status="FAILED", error=str(error))
                await self.save_record(record)
            # Previously constructed tools must learn the newly reserved boundary.
            for role in self.apps:
                self.blocked_paths(role)
            return self.records[identity]

    async def start(self, identity: str, *, run: bool = True) -> None:
        """Construct exactly one runtime; failures retain their reserved slot."""
        async with self.operations.setdefault(identity, asyncio.Lock()):
            record = dict(self.records[identity])
            if record["status"] in ("STOPPING", "ARCHIVED"):
                raise ValueError("角色尚未准备挂载")
            if identity in self.apps or (
                identity in self.lifecycle and not self.lifecycle[identity].done()
            ):
                raise ValueError("旧角色实例尚未结束")
            record.update(status="STARTING", error=None)
            await self.save_record(record)
            app: _App | None = None
            try:
                app = await build_app_context(
                    self.config,
                    canon_files=(),
                    ask_files=(),
                    host=self,
                    character=record,
                )
                from nyx.subscriptions import subscribe

                subscribe(app)
                self.apps[identity] = app
                self.presence_locks[identity] = asyncio.Lock()
                self.requests[identity] = set()
                async with self.database.lock:
                    latest = await (
                        await self.database.conn.execute(
                            "SELECT sequence, payload FROM presence_current WHERE "
                            "id='self'"
                        )
                    ).fetchone()
                if latest:
                    assert app.database is not None
                    async with app.database.lock:
                        checkpoint = await (
                            await app.database.conn.execute(
                                "SELECT snapshot FROM presence_checkpoint WHERE "
                                "id='self'"
                            )
                        ).fetchone()
                    if record.get("reset_presence", False):
                        baseline = record.get("presence_baseline")
                        snapshot = {key: getattr(app, key) for key in _PRESENCE_FIELDS}
                        if baseline:
                            payload = baseline["payload"]
                            sampled_at = payload["sampled_at"]
                            snapshot.update(
                                last_presence=payload["presence"],
                                presence_initialized=True,
                                presence_changed_at=sampled_at,
                                presence_observed_at=sampled_at,
                                presence_received_at=sampled_at,
                                away_started_at=(
                                    max(0.0, sampled_at - payload["idle_seconds"])
                                    if payload["presence"] == "away"
                                    else None
                                ),
                            )
                        async with app.database.transaction() as conn:
                            await conn.execute("DELETE FROM presence_checkpoint")
                            if baseline:
                                # A baseline is not a new historical observation.
                                # Never rewrite a returned event using its stable ID.
                                await conn.execute(
                                    "INSERT INTO presence_checkpoint VALUES "
                                    "('self', ?, ?)",
                                    (baseline["sequence"], json.dumps(snapshot)),
                                )
                        record.pop("reset_presence", None)
                        record.pop("presence_baseline", None)
                        checkpoint = None
                        if baseline:
                            checkpoint = {"snapshot": json.dumps(snapshot)}
                    if checkpoint:
                        for key, value in json.loads(checkpoint["snapshot"]).items():
                            setattr(app, key, value)
                        app.last_window_title = ""
                        app.pending_return = app.claimed_return = None
                    else:
                        async with self.database.lock:
                            pending = await (
                                await self.database.conn.execute(
                                    "SELECT 1 FROM presence_delivery WHERE "
                                    "character_id=? AND "
                                    "status='pending' LIMIT 1",
                                    (identity,),
                                )
                            ).fetchone()
                        if pending is None:
                            payload = json.loads(latest["payload"])
                            payload["window_title"] = ""
                            await app.apply_presence(latest["sequence"], payload)
                    await self.save_record(record)
                    await self.flush_presence(identity, force=True, starting=True)
                record.update(status="RUNNING", error=None)
                await self.save_record(record)
                if run:
                    self.activate(identity)
            except BaseException as error:
                self.apps.pop(identity, None)
                if app is not None and app.database is not None:
                    await app.database.close()
                record.update(status="FAILED", error=str(error))
                await self.save_record(record)
                logging.getLogger(__name__).exception(
                    "角色启动失败 character_id=%s", identity
                )
                if not isinstance(error, Exception):
                    raise

    def activate(self, identity: str) -> None:
        """Start supervised loops with a lifetime independent of other roles."""
        from nyx.runtime import run_character

        if identity not in self.lifecycle or self.lifecycle[identity].done():
            self.lifecycle[identity] = asyncio.create_task(
                run_character(self, identity)
            )

    async def select(self, identity: str) -> int:
        """Persist foreground selection; never reinitialize or stop a runtime."""
        async with self.lock:
            if identity not in self.records:
                raise KeyError(identity)
            if self.records[identity]["status"] != "RUNNING":
                raise ValueError("角色不可运行")
            revision = self.revision + 1
            async with self.database.transaction() as conn:
                await conn.execute(
                    "UPDATE selection SET character_id=?, revision=? WHERE id='self'",
                    (identity, revision),
                )
            for app in self.apps.values():
                app.last_window_title = app.last_screen_summary = ""
            self.current_id, self.revision = identity, revision
            return revision

    async def observe(self, payload: dict[str, Any]) -> int:
        """Persist one ordered sample and a separate recipient record for each role."""
        async with self.lock:
            payload = dict(payload)
            payload.setdefault("window_title", "")
            payload.setdefault("sampled_at", time.time())
            async with self.database.transaction() as conn:
                existing = await (
                    await conn.execute(
                        "SELECT sequence FROM presence_samples WHERE sample_id=?",
                        (payload["sample_id"],),
                    )
                ).fetchone()
                if existing:
                    return existing["sequence"]
                current = await (
                    await conn.execute(
                        "SELECT payload, received_at FROM presence_current WHERE "
                        "id='self'"
                    )
                ).fetchone()
                received_at = time.time()
                clock_reset = (
                    current is not None and received_at < current["received_at"]
                )
                accepted = payload["sampled_at"] <= received_at and (
                    current is None
                    or clock_reset
                    or payload["sampled_at"]
                    > json.loads(current["payload"])["sampled_at"]
                )
                payload.update(_presence_accepted=accepted, _clock_reset=clock_reset)
                cursor = await conn.execute(
                    "INSERT INTO presence_samples(sample_id, payload) VALUES (?, ?)",
                    (payload["sample_id"], json.dumps(payload)),
                )
                sequence = cursor.lastrowid
                assert sequence is not None
                if accepted:
                    public = dict(payload, window_title="")
                    await conn.execute(
                        "INSERT OR REPLACE INTO presence_current VALUES ('self', "
                        "?, ?, ?)",
                        (sequence, json.dumps(public), received_at),
                    )
                for identity, record in self.records.items():
                    if record["status"] in ("STARTING", "RUNNING", "FAILED"):
                        await conn.execute(
                            "INSERT INTO presence_delivery(sequence, character_id) "
                            "VALUES (?, "
                            "?)",
                            (sequence, identity),
                        )
            return sequence

    async def flush_presence(
        self,
        identity: str,
        *,
        force: bool = False,
        starting: bool = False,
    ) -> bool:
        """Only the failed recipient retries, FIFO, with durable per-role backoff."""
        app = self.apps.get(identity)
        allowed = ("RUNNING", "STARTING") if starting else ("RUNNING",)
        if app is None or self.records[identity]["status"] not in allowed:
            return False
        async with self.presence_locks[identity]:
            if self.records[identity]["status"] not in allowed:
                return False
            while True:
                async with self.database.lock:
                    row = await (
                        await self.database.conn.execute(
                            "SELECT d.*, s.payload FROM presence_delivery d JOIN "
                            "presence_samples s USING(sequence) "
                            "WHERE character_id=? AND status='pending' ORDER BY "
                            "sequence "
                            "LIMIT 1",
                            (identity,),
                        )
                    ).fetchone()
                if row is None:
                    return True
                if not force and row["retry_at"] > time.time():
                    return False
                payload = json.loads(row["payload"])
                if (
                    payload.get("character_id") != self.current_id
                    or payload.get("selection_revision") != self.revision
                    or identity != self.current_id
                ):
                    payload["window_title"] = ""
                try:
                    await app.apply_presence(row["sequence"], payload)
                    async with self.database.transaction() as conn:
                        await conn.execute(
                            "UPDATE presence_delivery SET status='succeeded', "
                            "error=NULL "
                            "WHERE sequence=? AND character_id=?",
                            (row["sequence"], identity),
                        )
                except Exception as error:
                    attempts = int(row["attempts"]) + 1
                    delay = (1, 2, 4, 8, 30)[min(attempts - 1, 4)]
                    async with self.database.transaction() as conn:
                        await conn.execute(
                            "UPDATE presence_delivery SET attempts=?, retry_at=?, "
                            "error=? "
                            "WHERE sequence=? AND character_id=?",
                            (
                                attempts,
                                time.time() + delay,
                                str(error),
                                row["sequence"],
                                identity,
                            ),
                        )
                    logging.getLogger(__name__).warning(
                        "在线状态补投失败 character_id=%s attempt=%s: %s",
                        identity,
                        attempts,
                        error,
                    )
                    return False

    async def online_before_chat(self, identity: str, sample_id: str) -> None:
        await self.observe(
            {
                "sample_id": sample_id,
                "presence": "online",
                "idle_seconds": 0,
                "sampled_at": time.time(),
            }
        )
        if not await self.flush_presence(identity, force=True):
            raise RuntimeError("角色在线状态尚未同步，聊天未受理")

    async def cancel_presence(self, identity: str) -> None:
        async with self.database.transaction() as conn:
            await conn.execute(
                "UPDATE presence_delivery SET status='cancelled' WHERE "
                "character_id=? AND status='pending'",
                (identity,),
            )

    async def unload(self, identity: str) -> None:
        """Retain slot and database until every admitted writer has finished."""
        async with self.operations.setdefault(identity, asyncio.Lock()):
            await self._unload(identity)

    async def _unload(self, identity: str) -> None:
        async with self.lock:
            if identity == self.current_id and not self.closing:
                raise ValueError("不能卸载当前角色")
            record = dict(self.records[identity])
            if record["status"] == "ARCHIVED":
                return
            if not self.closing:
                record.update(status="STOPPING", error=None)
                await self.save_record(record)
        app = self.apps.get(identity)
        if app is not None:
            from nyx.runtime import stop_character

            if not await stop_character(self, identity):
                record["error"] = "停止超时，等待已受理工作结束"
                await self.save_record(record)
                return
            presence_lock = self.presence_locks[identity]
            try:
                await asyncio.wait_for(presence_lock.acquire(), timeout=5)
            except asyncio.TimeoutError:
                record["error"] = "停止超时，等待在线状态补投结束"
                await self.save_record(record)
                return
            try:
                await app.bus.stop()
                try:
                    await asyncio.wait_for(
                        app.memory.finalize_conversation_segment(), timeout=5
                    )
                except Exception:
                    logging.getLogger(__name__).exception(
                        "角色卸载时对话段收尾失败 character_id=%s", identity
                    )
                assert app.database is not None
                await app.database.close()
                self.apps.pop(identity)
            finally:
                presence_lock.release()
        if not self.closing or record["status"] == "STOPPING":
            await self.cancel_presence(identity)
            record.update(status="ARCHIVED", error=None)
            await self.save_record(record)

    async def restore(
        self, identity: str, *, run: bool = True, start: bool = True
    ) -> None:
        async with self.lock:
            record = dict(self.records[identity])
            if record["status"] != "ARCHIVED":
                raise ValueError("角色尚未卸载完成")
            if sum(r["status"] != "ARCHIVED" for r in self.records.values()) >= 4:
                raise ValueError("最多注册四个角色")
            async with self.database.lock:
                latest = await (
                    await self.database.conn.execute(
                        "SELECT sequence, payload FROM presence_current WHERE id='self'"
                    )
                ).fetchone()
            baseline = None
            if latest:
                payload = json.loads(latest["payload"])
                payload["window_title"] = ""
                baseline = {"sequence": latest["sequence"], "payload": payload}
            record.update(
                status="STARTING",
                error=None,
                reset_presence=True,
                presence_baseline=baseline,
            )
            await self.save_record(record)
        if start:
            await self.start(identity, run=run)

    async def retry_start(self, identity: str) -> None:
        """Serialize failed-instance cleanup and reserve exactly one new start."""
        async with self.operations.setdefault(identity, asyncio.Lock()):
            record = dict(self.records[identity])
            if record["status"] != "FAILED":
                raise ValueError("只可重试失败角色")
            app = self.apps.get(identity)
            if app is not None:
                from nyx.runtime import stop_character

                if not await stop_character(self, identity):
                    raise ValueError("旧角色实例尚未结束")
                async with self.presence_locks[identity]:
                    assert app.database is not None
                    await app.database.close()
                    self.apps.pop(identity)
            record.update(status="STARTING", error=None)
            await self.save_record(record)

    async def close(self) -> None:
        """Close host only after its role-local writers have stopped."""
        self.closing = True
        background: list[asyncio.Task[Any]] = [
            *self.background,
            *self.stopping.values(),
            *self.presence_tasks.values(),
        ]
        for task in background:
            task.cancel()
        if background:
            _, pending = await asyncio.wait(background, timeout=5)
            if pending:
                logging.getLogger(__name__).error(
                    "宿主停止超时，保留仍被后台工作使用的数据库"
                )
            await asyncio.gather(*background, return_exceptions=True)
        while self.apps:
            await asyncio.gather(
                *(self.unload(identity) for identity in list(self.apps))
            )
            if self.apps:
                # Keep the event loop alive until every SQLite writer has settled.
                await asyncio.sleep(0.5)
        await self.database.close()
