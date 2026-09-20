import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast
from uuid import uuid4

from nyx.activity import creation as _creation
from nyx.activity import lifecycle as _activity_lifecycle
from nyx.activity import paths as _activity_paths
from nyx.activity.exploration import Exploration
from nyx.activity.llm_result import parse_activity_result as _parse_activity_result
from nyx.activity.material_store import MaterialStore
from nyx.activity.observe import build_observation_summary
from nyx.activity.reading_runner import ReadingActivityRunner
from nyx.activity.starter import ActivityStarter, schedule_block_id
from nyx.activity.store import ActivityStore
from nyx.config import ActivityConfig, ExplorationConfig
from nyx.desire.facade import DesireFacade
from nyx.enums import (
    ActivityStatus,
    ActivityType,
    EventType,
    GameProfile,
    MemoryKind,
    TickType,
)
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import SECONDS_PER_DAY, internal_event
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.tools.file_io import file_io
from nyx.tools.registry import ToolRegistry
from nyx.types import (
    AcceptedObservationSnapshot,
    Activity,
    ChoiceCorrectionValue,
    CorrectionField,
    CurrentState,
    DialogueCorrectionValue,
    Event,
    GameChoiceConfirmation,
    GameCorrectionResult,
    Material,
    ReflectionOutcome,
    ShortTermDesire,
    SpeakerCorrectionValue,
    WindowIdentity,
)

_CREATION_STYLES = _creation.CREATION_STYLES
_build_creation_context = _creation.build_creation_context
_build_creation_system = _creation.build_creation_system
_pick_creation_style = _creation.pick_creation_style
_correlation_id = _activity_lifecycle.correlation_id
_goal_met = _activity_lifecycle.goal_met
_path_hash_suffix = _activity_paths.path_hash_suffix
_sanitize_filename = _activity_paths.sanitize_filename

_logger = logging.getLogger(__name__)
_MAX_OBSERVATION_EVENT_INDEX = 128


def _accepted_fields(snapshot: AcceptedObservationSnapshot) -> list[str]:
    fields: list[str] = []
    if snapshot.speaker is not None:
        fields.append("speaker")
    if snapshot.dialogue:
        fields.append("dialogue")
    if snapshot.choices:
        fields.append("choices")
    if snapshot.visible_entities:
        fields.append("visible_entities")
    if snapshot.scene_summary is not None:
        fields.append("scene_summary")
    return fields


def _validate_correction_value(
    field: CorrectionField, value: dict[str, Any]
) -> DialogueCorrectionValue | SpeakerCorrectionValue | ChoiceCorrectionValue:
    if field in {CorrectionField.DIALOGUE, CorrectionField.SPEAKER}:
        if set(value) != {"text"} or not isinstance(value.get("text"), str):
            raise ValueError("invalid_correction_value")
        text = str(value["text"]).strip()
        if not text or len(text) > 512:
            raise ValueError("invalid_correction_value")
        if field is CorrectionField.DIALOGUE:
            return DialogueCorrectionValue(text)
        return SpeakerCorrectionValue(text)
    if set(value) != {"choice_id", "text", "order"}:
        raise ValueError("invalid_correction_value")
    choice_id = value.get("choice_id")
    order = value.get("order")
    if choice_id is not None and (
        not isinstance(choice_id, str) or len(choice_id) > 64
    ):
        raise ValueError("invalid_correction_value")
    if not isinstance(value.get("text"), str) or not str(value["text"]).strip():
        raise ValueError("invalid_correction_value")
    if len(str(value["text"]).strip()) > 256:
        raise ValueError("invalid_correction_value")
    if order is not None and (not isinstance(order, int) or order < 0 or order > 7):
        raise ValueError("invalid_correction_value")
    return ChoiceCorrectionValue(choice_id, str(value["text"]).strip(), order)


def _correction_value_from_dict(
    field: str, value: dict[str, Any]
) -> DialogueCorrectionValue | SpeakerCorrectionValue | ChoiceCorrectionValue:
    return _validate_correction_value(CorrectionField(field), value)

def _day_start(now: float) -> float:
    """当日零点（UTC 日边界，MVP 可推翻为本地时区）。纯函数。"""
    return now - now % SECONDS_PER_DAY


def _creation_checkpoint(activity: Activity) -> dict[str, Any]:
    raw = activity.progress.get("creation")
    if isinstance(raw, dict):
        checkpoint = cast(dict[str, Any], raw)
    else:
        checkpoint = {}
    return {
        "style": checkpoint.get("style"),
        "llm_done": bool(checkpoint.get("llm_done")),
        "title": checkpoint.get("title"),
        "content": checkpoint.get("content"),
        "file_written": bool(checkpoint.get("file_written")),
        "path": checkpoint.get("path"),
    }


_schedule_block_id = schedule_block_id


class ActivityFacade:
    """活动模块门面：消费欲望 → 选活动 → 后台执行 → 完成/打断 → 发布事件。

    依赖注入解环：不持有 InnerLifeFacade，注入 get_state 回调
    （组合根绑 inner_life.get_state）。
    """

    def __init__(
        self,
        store: ActivityStore,
        material_store: MaterialStore,
        bus: EventBus,
        llm: LlmClient,
        evaluator: Evaluator,
        tools: ToolRegistry,
        desire: DesireFacade,
        memory: MemoryFacade,
        get_state: Callable[[], Awaitable[CurrentState]],
        reflect: Callable[[str | None], Awaitable[ReflectionOutcome | None]],
        get_observation: Callable[[], Awaitable[dict[str, str]]],
        config: ActivityConfig,
        exploration_config: ExplorationConfig,
        canon: str,
        vision_enabled: bool = True,
    ) -> None:
        self._store = store
        self._material_store = material_store
        self._bus = bus
        self._llm = llm
        self._evaluator = evaluator
        self._desire = desire
        self._memory = memory
        self._get_state = get_state
        self._reflect = reflect
        self._get_observation = get_observation
        self._config = config
        self._vision_enabled = vision_enabled
        self._canon = canon
        self._exploration = Exploration(
            llm,
            evaluator,
            tools,
            store,
            desire,
            memory,
            exploration_config,
        )
        self._reading_runner = ReadingActivityRunner(
            material_store, llm, evaluator, memory, file_io, store.update
        )
        self._lifecycle = _activity_lifecycle.ActivityLifecycle(
            store, bus, desire, config
        )
        self._starter = ActivityStarter(
            store,
            material_store,
            desire,
            get_state,
            self._execute,
            config,
            exploration_config,
            time.time,
        )
        self._task: asyncio.Task[None] | None = None

    # ---- 事件入口 ----

    async def on_tick(self, tick_type: TickType) -> None:
        """SCHEDULE_BLOCK_START：日程块开始，有空闲就消费欲望。"""
        if tick_type is TickType.SCHEDULE_BLOCK_START:
            await self._maybe_start_activity()

    async def on_desire_generated(self, event: Event) -> None:
        """DESIRE_GENERATED：欲望刚生成，有空闲就立即消费。"""
        await self._maybe_start_activity()

    # ---- 决策 ----

    def select_activity(
        self, desires: list[ShortTermDesire], state: CurrentState
    ) -> Activity | None:
        return self._starter.select_activity(desires, state)

    # ---- 生命周期 ----

    async def complete_activity(self, activity: Activity) -> None:
        """完成：goal 判定 + 收尾 + 发布 activity_end（desire/inner_life 消费）。"""
        await self._lifecycle.complete(activity)

    async def interrupt(self, activity_id: str, by_event: EventType) -> None:
        """抢占即暂停：校验目标 RUNNING → cancel 执行 task 并 await 其彻底结束
        → 重读守卫（窗口内已自行完成/失败则不覆盖）→ 置终态落库
        （可续活动 PAUSED，其余 ABANDONED）+ 发布 activity_interrupted。

        执行中的 result 尚未写入，故仅落终态（不持久化部分进度）；
        读书的 read_chars 已 advance 进 material 层，恢复时从那里续读。
        """
        await self._lifecycle.interrupt(activity_id, by_event, self._task)

    async def recover_stale_running(self) -> list[Activity]:
        """启动恢复：清理 DB 中没有后台 task 承接的 RUNNING 活动。"""
        return await self._lifecycle.recover_stale_running()

    # ---- 读 ----

    async def get_current(self) -> Activity | None:
        return await self._store.get_current()

    async def get_schedule(self) -> list[Activity]:
        return await self._store.list_schedule(_day_start(time.time()))

    async def get_results(self, limit: int = 100) -> list[Activity]:
        """跨天历史产出（读书笔记/探索发现/创作内容），按结束时间倒序。"""
        return await self._store.list_results(limit)

    async def list_materials(self) -> list[Material]:
        """书库全量（含已读进度），供资料面板展示「读到哪了」。"""
        return await self._material_store.list_all()

    async def register_material(
        self, path: str, filename: str, total_chars: int
    ) -> None:
        """注册一本读物进书库（只登记，不立即读）。

        读书由欲望驱动的 _maybe_start_activity 在活动时按 find_by_topic /
        next_readable 选书决定读不读；本方法不建活动、不发事件。
        """
        await self._material_store.upsert(path, filename, total_chars, time.time())

    # ---- 游戏陪玩 ----

    async def start_game_companion(
        self,
        profile: GameProfile,
        game_id: str,
        window_id: str,
        remote_vision_enabled: bool = False,
        *,
        window_identity: WindowIdentity,
    ) -> Activity:
        """Create the explicit GAME_COMPANION activity and its start event."""
        current = await self._find_game_activity()
        if current is not None and current.status in {
            ActivityStatus.PENDING, ActivityStatus.RUNNING, ActivityStatus.PAUSED,
        }:
            return current
        session_id = str(uuid4())
        progress: dict[str, Any] = {
            "game_companion": {
                "session_id": session_id,
                "game_id": game_id,
                "profile": profile.value,
                "profile_version": 1,
                "threshold_version": 1,
                "remote_vision_enabled": (
                    remote_vision_enabled and getattr(self, "_vision_enabled", True)
                ),
                "window_identity": window_identity.__dict__,
                "status": "observing",
                "last_accepted_revision": 0,
                "last_observation_hash": None,
                "last_observation": None,
                "observation_events": {},
                "corrections": [],
                "correction_events": {},
                "confirmed_choice_keys": [],
                "confirmed_choice_events": {},
                "checkpoint_seq": 0,
                "memory_cursor": {
                    "last_event_id": None,
                    "last_event_kind": None,
                    "last_revision": None,
                    "last_correction_id": None,
                },
                "ended_at": None,
            }
        }
        activity = Activity(
            id=str(uuid4()),
            type=ActivityType.GAME_COMPANION,
            schedule_block_id=_schedule_block_id(
                time.time(), self._config.grid_minutes
            ),
            status=ActivityStatus.RUNNING,
            progress=progress,
            started_at=time.time(),
        )
        activity_start = internal_event(
            EventType.ACTIVITY_START,
            {
                "activity_id": activity.id,
                "type": activity.type.value,
                "schedule_block_id": activity.schedule_block_id,
            },
            session_id,
        )
        session_started = internal_event(
            EventType.GAME_SESSION_STARTED,
            {
                "session_id": session_id,
                "activity_id": activity.id,
                "game_id": game_id,
                "profile": profile.value,
                "profile_version": 1,
                "threshold_version": 1,
                "revision": 0,
                "remote_vision_enabled": (
                    remote_vision_enabled and getattr(self, "_vision_enabled", True)
                ),
                "window_identity": progress["game_companion"]["window_identity"],
            },
            session_id,
        )
        append = getattr(self._bus, "append_in_transaction", None)
        announce = getattr(self._bus, "announce_committed", None)
        if callable(append) and callable(announce):
            append_event = cast(Callable[[Any], Awaitable[tuple[str, ...]]], append)
            announce_event = cast(Callable[[Event], Awaitable[None]], announce)
            async with self._store.db.transaction():
                await self._store.insert(activity)
                await append_event(activity_start)
                await append_event(session_started)
            await announce_event(activity_start)
            await announce_event(session_started)
        else:
            await self._store.insert(activity)
            await self._bus.publish(activity_start)
            await self._bus.publish(session_started)
        return activity

    async def record_game_observation(
        self,
        session_id: str,
        snapshot: AcceptedObservationSnapshot,
    ) -> str | None:
        """Commit one accepted immutable observation, or no-op for its hash."""
        from nyx.activity.game_observer import (
            canonical_observation_hash,
            snapshot_to_dict,
        )

        activity = await self._find_game_activity(session_id)
        if activity is None:
            raise ValueError("session_not_found")
        game = cast(dict[str, Any], activity.progress["game_companion"])
        current_revision = int(game.get("last_accepted_revision", 0))
        if snapshot.session_id != session_id:
            raise ValueError("session_mismatch")
        if game.get("last_observation_hash") == snapshot.observation_hash:
            events = cast(dict[str, str], game.get("observation_events", {}))
            return events.get(snapshot.observation_hash)
        if snapshot.revision != current_revision + 1:
            raise ValueError("stale_observation")
        if canonical_observation_hash(snapshot) != snapshot.observation_hash:
            raise ValueError("invalid_observation_hash")
        snapshot_dict = snapshot_to_dict(snapshot)
        encoded_snapshot = json.dumps(
            snapshot_dict,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(encoded_snapshot) > 12 * 1024:
            raise ValueError("observation_payload_too_large")
        event = internal_event(
            EventType.GAME_OBSERVATION,
            {
                "session_id": session_id,
                "game_id": snapshot.game_id,
                "revision": snapshot.revision,
                "phase": snapshot.phase.value,
                "profile_version": snapshot.profile_version,
                "threshold_version": snapshot.threshold_version,
                "observation_hash": snapshot.observation_hash,
                "observation_snapshot": snapshot_dict,
                "evidence_summary": {
                    "accepted_fields": _accepted_fields(snapshot),
                    "evidence_count": len(snapshot.evidence),
                    "status": "accepted",
                    "profile_version": snapshot.profile_version,
                    "threshold_version": snapshot.threshold_version,
                },
            },
            session_id,
        )
        envelope = json.dumps(
            event.content,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(envelope) > 16 * 1024:
            raise ValueError("observation_payload_too_large")
        game["last_accepted_revision"] = snapshot.revision
        game["last_observation_hash"] = snapshot.observation_hash
        game["last_observation"] = snapshot_dict
        game["checkpoint_seq"] = int(game.get("checkpoint_seq", 0)) + 1
        game["confirmed_choice_keys"] = []
        game["confirmed_choice_events"] = {}
        events = cast(dict[str, str], game.setdefault("observation_events", {}))
        events[snapshot.observation_hash] = event.id
        while len(events) > _MAX_OBSERVATION_EVENT_INDEX:
            events.pop(next(iter(events)))
        await self._commit_game_event(activity, event)
        return event.id

    async def accept_game_observation(
        self, session_id: str, snapshot: AcceptedObservationSnapshot
    ) -> str | None:
        """Compatibility alias for the observation pipeline."""
        return await self.record_game_observation(session_id, snapshot)

    async def correct_game_observation(
        self,
        session_id: str,
        revision: int,
        correction_id: str,
        field: CorrectionField,
        value: dict[str, Any],
        reason: str,
    ) -> GameCorrectionResult:
        """Append a bounded correction without rewriting observation facts."""
        activity = await self._find_game_activity(session_id)
        if activity is None:
            raise ValueError("session_not_found")
        game = cast(dict[str, Any], activity.progress["game_companion"])
        current_revision = int(game.get("last_accepted_revision", 0))
        if revision != current_revision:
            raise ValueError("stale_observation")
        if not correction_id.strip() or not reason.strip():
            raise ValueError("invalid_correction_value")
        typed_value = _validate_correction_value(field, value)
        correction_events = cast(
            dict[str, dict[str, Any]],
            game.setdefault("correction_events", {}),
        )
        existing = correction_events.get(correction_id)
        if existing is not None:
            return GameCorrectionResult(
                session_id,
                revision,
                correction_id,
                CorrectionField(existing["field"]),
                _correction_value_from_dict(existing["field"], existing["value"]),
                str(existing["reason"]),
                str(existing["event_id"]),
                True,
            )
        corrections = cast(list[dict[str, Any]], game.setdefault("corrections", []))
        if len(corrections) >= 64:
            raise ValueError("correction_storage_limit")
        raw_snapshot = game.get("last_observation")
        if not isinstance(raw_snapshot, dict):
            raise ValueError("stale_observation")
        raw_snapshot = cast(dict[str, Any], raw_snapshot)
        event = internal_event(
            EventType.GAME_OBSERVATION_CORRECTED,
            {
                "session_id": session_id,
                "base_revision": revision,
                "base_observation_hash": str(raw_snapshot["observation_hash"]),
                "correction_id": correction_id,
                "field": field.value,
                "value": value,
                "reason": reason.strip(),
                "source": "user",
            },
            session_id,
        )
        overlay = {
            "correction_id": correction_id,
            "base_revision": revision,
            "base_observation_hash": raw_snapshot["observation_hash"],
            "field": field.value,
            "value": value,
            "reason": reason.strip(),
            "event_id": event.id,
            "event_timestamp": event.timestamp,
        }
        corrections.append(overlay)
        correction_events[correction_id] = {
            "field": field.value,
            "value": value,
            "reason": reason.strip(),
            "event_id": event.id,
        }
        game["checkpoint_seq"] = int(game.get("checkpoint_seq", 0)) + 1
        await self._commit_game_event(activity, event)
        return GameCorrectionResult(
            session_id, revision, correction_id, field, typed_value,
            reason.strip(), event.id, True,
        )

    async def pause_game_companion(self, session_id: str) -> None:
        activity = await self._find_game_activity(session_id)
        if activity is None or activity.status is not ActivityStatus.RUNNING:
            return
        game = cast(dict[str, Any], activity.progress["game_companion"])
        game["status"] = "paused"
        activity.status = ActivityStatus.PAUSED
        activity.ended_at = time.time()
        game["checkpoint_seq"] = int(game.get("checkpoint_seq", 0)) + 1
        event = internal_event(
            EventType.ACTIVITY_INTERRUPTED,
            {"activity_id": activity.id, "by": EventType.GAME_OBSERVATION.value},
            session_id,
        )
        await self._commit_game_event(activity, event)

    async def resume_game_companion(self, session_id: str) -> None:
        activity = await self._find_game_activity(session_id)
        if activity is None or activity.status is not ActivityStatus.PAUSED:
            return
        game = cast(dict[str, Any], activity.progress["game_companion"])
        game["status"] = "observing"
        activity.status = ActivityStatus.RUNNING
        activity.ended_at = None
        game["checkpoint_seq"] = int(game.get("checkpoint_seq", 0)) + 1
        expected = int(game["checkpoint_seq"]) - 1
        update_cas = getattr(self._store, "update_game_if_checkpoint", None)
        if callable(update_cas):
            update_cas = cast(
                Callable[[Activity, int], Awaitable[bool]], update_cas
            )
            async with self._store.db.transaction():
                if not await update_cas(activity, expected):
                    raise ValueError("game_state_conflict")
        else:
            await self._store.update(activity)

    async def stop_game_companion(self, session_id: str) -> None:
        activity = await self._find_game_activity(session_id)
        if activity is None or activity.status is ActivityStatus.COMPLETED:
            return
        game = cast(dict[str, Any], activity.progress["game_companion"])
        game["status"] = "ended"
        game["ended_at"] = time.time()
        game["checkpoint_seq"] = int(game.get("checkpoint_seq", 0)) + 1
        activity.status = ActivityStatus.COMPLETED
        activity.ended_at = time.time()
        event = internal_event(
            EventType.ACTIVITY_END,
            {
                "activity_id": activity.id,
                "type": activity.type.value,
                "desire_id": None,
                "goal_met": True,
                "energy_delta": 0,
                "result": {"session_id": session_id},
            },
            session_id,
        )
        await self._commit_game_event(activity, event)

    async def confirm_game_choice(
        self, session_id: str, revision: int, choice_id: str
    ) -> GameChoiceConfirmation:
        activity = await self._find_game_activity(session_id)
        if activity is None:
            raise ValueError("session_not_found")
        game = cast(dict[str, Any], activity.progress["game_companion"])
        if int(game.get("last_accepted_revision", 0)) != revision:
            raise ValueError("stale_choice")
        raw_snapshot = game.get("last_observation")
        if not isinstance(raw_snapshot, dict):
            raise ValueError("choice_not_found")
        raw_snapshot = cast(dict[str, Any], raw_snapshot)
        raw_choices: list[dict[str, Any]] = [
            item for item in cast(list[Any], raw_snapshot.get("choices", []))
            if isinstance(item, dict)
        ]
        choice = next(
            (item for item in raw_choices if item.get("id") == choice_id),
            None,
        )
        if choice is None:
            raise ValueError("choice_not_found")
        key = f"{revision}:{choice_id}"
        event_map = cast(dict[str, str], game.setdefault("confirmed_choice_events", {}))
        if key in event_map:
            from nyx.activity.game_observer import snapshot_from_dict
            return GameChoiceConfirmation(
                session_id, revision, choice_id, str(choice["text"]), True,
                event_map[key], snapshot_from_dict(raw_snapshot),
            )
        confirmed = cast(list[str], game.setdefault("confirmed_choice_keys", []))
        if any(item.startswith(f"{revision}:") for item in confirmed):
            raise ValueError("choice_already_confirmed")
        event = internal_event(
            EventType.GAME_CHOICE_CONFIRMED,
            {
                "session_id": session_id,
                "revision": revision,
                "game_id": game.get("game_id"),
                "profile": game.get("profile"),
                "choice_id": choice_id,
                "choice_text": str(choice["text"]),
                "confirmation_source": "user_click",
            },
            session_id,
        )
        confirmed.append(key)
        event_map[key] = event.id
        game["checkpoint_seq"] = int(game.get("checkpoint_seq", 0)) + 1
        await self._commit_game_event(activity, event)
        from nyx.activity.game_observer import snapshot_from_dict
        return GameChoiceConfirmation(
            session_id, revision, choice_id, str(choice["text"]), True,
            event.id, snapshot_from_dict(raw_snapshot),
        )

    # ---- 内部 ----

    async def _maybe_start_activity(self) -> None:
        self._task = await self._starter.start_next_if_idle(self._task)

    async def _find_game_activity(
        self, session_id: str | None = None
    ) -> Activity | None:
        if session_id is not None:
            return await self._store.get_game_session(session_id)
        activities = await self._store.list_unfinished()
        activities.extend(await self._store.list_schedule(0.0))
        seen: set[str] = set()
        for activity in activities:
            if activity.id in seen or activity.type is not ActivityType.GAME_COMPANION:
                continue
            seen.add(activity.id)
            game = activity.progress.get("game_companion")
            if isinstance(game, dict) and (
                session_id is None
                or cast(dict[str, Any], game).get("session_id") == session_id
            ):
                return activity
        return None

    async def get_game_session(self, session_id: str) -> Activity | None:
        """Return the activity carrying a game companion session."""
        return await self._find_game_activity(session_id)

    async def _commit_game_event(self, activity: Activity, event: Event) -> None:
        append = getattr(self._bus, "append_in_transaction", None)
        announce = getattr(self._bus, "announce_committed", None)
        if callable(append) and callable(announce):
            append_event = cast(Callable[[Event], Awaitable[tuple[str, ...]]], append)
            announce_event = cast(Callable[[Event], Awaitable[None]], announce)
            async with self._store.db.transaction():
                update_cas = getattr(self._store, "update_game_if_checkpoint", None)
                if callable(update_cas):
                    update_cas = cast(
                        Callable[[Activity, int], Awaitable[bool]], update_cas
                    )
                    game = cast(dict[str, Any], activity.progress["game_companion"])
                    new_seq = int(game.get("checkpoint_seq", 0))
                    if not await update_cas(activity, new_seq - 1):
                        raise ValueError("game_state_conflict")
                else:
                    await self._store.update(activity)
                await append_event(event)
            await announce_event(event)
            return
        await self._store.update(activity)
        await self._bus.publish(event)

    async def _execute(self, activity: Activity) -> None:
        await self._lifecycle.start(activity)
        try:
            if activity.type is ActivityType.FREE_EXPLORATION:
                await self._start_exploration_run(activity)
                return
            result = await self._run_activity(activity)
        except Exception:
            # fail-fast：失败态落库后仍上抛（不吞异常），但活动不卡 RUNNING
            await self._lifecycle.fail(activity)
            _logger.exception(
                "活动执行失败 activity_id=%s type=%s",
                activity.id,
                activity.type.value,
            )
            raise
        activity.progress["result"] = result
        await self.complete_activity(activity)

    async def _start_exploration_run(self, activity: Activity) -> None:
        """探索启动：交给 Exploration 状态机跑完并结算。"""
        result = await self._exploration.run(activity)
        activity.progress["result"] = result
        await self.complete_activity(activity)

    async def _run_activity(self, activity: Activity) -> dict[str, Any]:
        t = activity.type
        if t is ActivityType.READING:
            source = activity.progress.get("source")
            if source is None:
                # READING 必须有真实读物；缺 source 说明上游决策出错，fail-fast
                raise ValueError("读书活动缺 source：已禁止凭空编造")
            return await self._run_reading_source(activity, str(source))
        if t is ActivityType.CREATION:
            return await self._run_creation(activity)
        if t is ActivityType.IDLE_REFLECTION:
            outcome = await self._reflect(_correlation_id(activity))
            return {"summary": outcome.story if outcome is not None else None}
        if t is ActivityType.FREE_EXPLORATION:
            raise ValueError("自由探索改走 _execute 的 _start_exploration_run 分叉")
        if t is ActivityType.OBSERVE_USER:
            obs = await self._get_observation()
            presence = obs.get("presence", "")
            window_title = obs.get("window_title", "")
            screen_summary = obs.get("screen_summary", "")
            return {
                "presence": presence,
                "window_title": window_title,
                "screen_summary": screen_summary,
                "summary": build_observation_summary(
                    presence, window_title, screen_summary
                ),
            }
        if t is ActivityType.REST:
            return {}
        raise ValueError(f"未知活动类型 {t!r}")

    async def _run_creation(self, activity: Activity) -> dict[str, Any]:
        checkpoint = _creation_checkpoint(activity)
        if not checkpoint.get("style"):
            checkpoint["style"] = _pick_creation_style()
            await self._save_creation_checkpoint(activity, checkpoint)
        if not bool(checkpoint.get("llm_done")):
            knowledge = await self._memory.list_memories(
                kind=MemoryKind.KNOWLEDGE, limit=3
            )
            obs = await self._get_observation()
            state = await self._get_state()
            context = _build_creation_context(
                activity, str(checkpoint["style"]), knowledge, obs
            )
            system = _build_creation_system(self._canon, state)
            result = await self._run_llm_activity(
                activity,
                "creation",
                extra_context=context,
                context_label="创作参考",
                system=system,
            )
            checkpoint["title"] = str(result["title"])
            checkpoint["content"] = str(result["content"])
            checkpoint["llm_done"] = True
            await self._save_creation_checkpoint(activity, checkpoint)
        title = str(checkpoint["title"])
        content = str(checkpoint["content"])
        if not bool(checkpoint.get("file_written")):
            path = f"creations/{_sanitize_filename(title)}.md"
            written = await file_io("write", path, content)
            checkpoint["path"] = written["path"]
            checkpoint["file_written"] = True
            await self._save_creation_checkpoint(activity, checkpoint)
        path_value = str(checkpoint["path"])
        return {
            "title": title,
            "content": content,
            "path": path_value,
            "tools": [
                {
                    "name": "file_io",
                    "args": {
                        "action": "write",
                        "path": f"creations/{_sanitize_filename(title)}.md",
                    },
                    "ok": True,
                }
            ],
        }

    async def _save_creation_checkpoint(
        self, activity: Activity, checkpoint: dict[str, Any]
    ) -> None:
        activity.progress["creation"] = checkpoint
        await self._store.update(activity)

    async def _run_reading_source(
        self, activity: Activity, source: str
    ) -> dict[str, Any]:
        """兼容旧内部调用；读书实现位于 `ReadingActivityRunner`。"""
        return await self._reading_runner.run(activity, source)

    async def _run_llm_activity(
        self,
        activity: Activity,
        output_type: str,
        extra_context: str | None = None,
        context_label: str = "读物信息",
        system: str | None = None,
    ) -> dict[str, Any]:
        user_msg = f"活动类型：{activity.type.value}"
        if extra_context:
            user_msg += f"\n{context_label}：\n{extra_context}"
        output = await self._llm.complete(
            [
                {"role": "system", "content": system or _ACTIVITY_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            module="activity",
            output_type=output_type,
            correlation_id=_correlation_id(activity),
            json_mode=True,
        )
        await self._evaluator.evaluate(output)
        return _parse_activity_result(output.content, output_type)


_ACTIVITY_SYSTEM = (
    "你是尼克斯，正在读书。只输出 JSON，键："
    "book（书名，非空字符串）、note（本次读书笔记，非空字符串）。"
    "note 自然承接已读片段，不重复概括已读部分、只续写本次新读内容；"
    "note 正文里不要写「上次读到第 X 字」这类位置字样。"
)
