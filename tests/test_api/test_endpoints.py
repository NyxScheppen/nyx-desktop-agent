# pyright: reportPrivateUsage=false
import asyncio
import json
from collections.abc import AsyncGenerator
from typing import cast

import pytest
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from nyx.activity.facade import ActivityFacade
from nyx.config import Config
from nyx.desire.facade import DesireFacade
from nyx.enums import (
    EmotionCategory,
    EnergyState,
    EventType,
    MemoryKind,
    MemoryType,
    Source,
)
from nyx.eval.evaluator import Evaluator
from nyx.eval.store import EvalStore
from nyx.events.bus import EventAdmissionError, EventBus
from nyx.expression.facade import ExpressionFacade
from nyx.inner_life.facade import InnerLifeFacade
from nyx.main import _App, build_app
from nyx.memory.facade import MemoryFacade
from nyx.reading.facade import ReadingFacade
from nyx.types import (
    CurrentState,
    EvalRecord,
    EvalStats,
    Event,
    Material,
    Memory,
)


def _mk_state() -> CurrentState:
    return CurrentState(
        valence=0.5,
        arousal=0.5,
        emotion=EmotionCategory.NEUTRAL,
        personality={
            "openness": 5.0, "conscientiousness": 5.0, "extraversion": 5.0,
            "agreeableness": 5.0, "neuroticism": 5.0,
        },
        values={
            "attitude_to_human": 5.0, "ai_identity_acceptance": 5.0,
            "altruism": 5.0, "optimism": 5.0,
        },
        aesthetic={
            "ornate": 7.0, "lyrical": 7.0, "classical": 6.0, "somber": 6.0,
        },
        energy=80.0,
        energy_state=EnergyState.OKAY,
        current_activity=None,
        active_desires=[],
    )


def _mem() -> Memory:
    return Memory(
        id="m1",
        created_at=0.0,
        content="内容",
        kind=MemoryKind.USER_PROFILE,
        summary="摘要",
        freshness=1.0,
        type=MemoryType.SHORT_TERM,
    )


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[Event] = []
        self.failure: Exception | None = None
        self.sinks: list[asyncio.Queue[Event]] = []

    async def publish(self, event: Event) -> None:
        if self.failure is not None:
            raise self.failure
        self.published.append(event)
        for sink in self.sinks:
            sink.put_nowait(event)

    def add_sse_sink(self, sink: asyncio.Queue[Event]) -> None:
        self.sinks.append(sink)

    def remove_sse_sink(self, sink: asyncio.Queue[Event]) -> None:
        self.sinks.remove(sink)


class _FakeInnerLife:
    def __init__(self, state: CurrentState) -> None:
        self.state = state

    async def get_state(self) -> CurrentState:
        return self.state


class _FakeMemory:
    def __init__(self) -> None:
        self.list_calls: list[tuple[MemoryKind | None, MemoryType | None]] = []
        self.export_calls: list[str] = []
        self.search_calls: list[str] = []

    async def list_memories(
        self, kind: MemoryKind | None = None, type: MemoryType | None = None
    ) -> list[Memory]:
        self.list_calls.append((kind, type))
        return [_mem()]

    async def search(self, query: str) -> list[Memory]:
        self.search_calls.append(query)
        return [_mem()]

    async def export(self, fmt: str) -> str:
        self.export_calls.append(fmt)
        if fmt not in ("json", "md"):
            raise ValueError(f"不支持的导出格式：{fmt}")
        return f"exported:{fmt}"


class _FakeActivity:
    def __init__(self) -> None:
        self.list_calls = 0
        self.registered: list[tuple[str, str, int]] = []

    async def list_materials(self) -> list[Material]:
        self.list_calls += 1
        return [
            Material(
                path="workspace/uploads/a.txt", filename="a.txt",
                total_chars=100, read_chars=40,
                created_at=1.0, updated_at=2.0,
            )
        ]

    async def register_material(
        self, path: str, filename: str, total_chars: int
    ) -> None:
        self.registered.append((path, filename, total_chars))


def _app(state: CurrentState, bus: _FakeBus, memory: _FakeMemory) -> _App:
    return _App(
        bus=cast(EventBus, bus),
        inner_life=cast(InnerLifeFacade, _FakeInnerLife(state)),
        desire=cast(DesireFacade, object()),
        memory=cast(MemoryFacade, memory),
        activity=cast(ActivityFacade, object()),
        expression=cast(ExpressionFacade, object()),
        reading=cast(ReadingFacade, object()),
        evaluator=cast(Evaluator, object()),
        eval_store=cast(EvalStore, object()),
        config=Config(),
    )


def _client(app: _App) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=build_app(app)), base_url="http://test"
    )


class _FakeEvalStore:
    def __init__(self) -> None:
        self.recent_calls: list[int] = []
        self.records: list[EvalRecord] = []
        self.stats = EvalStats(
            total_tokens=42, prompt_tokens=30, completion_tokens=12,
        )
        self.prompts: dict[str, tuple[bool, list[dict[str, str]] | None]] = {}
        self.prompt_error: ValueError | None = None

    async def list_recent(self, limit: int = 5) -> list[EvalRecord]:
        self.recent_calls.append(limit)
        return self.records

    async def total_tokens(self) -> EvalStats:
        return self.stats

    async def get_prompt(
        self, record_id: str
    ) -> tuple[bool, list[dict[str, str]] | None]:
        if self.prompt_error is not None:
            raise self.prompt_error
        return self.prompts.get(record_id, (False, None))


async def test_state_endpoint() -> None:
    async with _client(_app(_mk_state(), _FakeBus(), _FakeMemory())) as client:
        resp = await client.get("/api/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["emotion"] == "neutral"
    assert body["energy_state"] == "okay"
    assert body["energy"] == 80.0
    assert body["aesthetic"] == {
        "ornate": 7.0, "lyrical": 7.0, "classical": 6.0, "somber": 6.0,
    }


async def test_chat_endpoint() -> None:
    bus = _FakeBus()
    async with _client(_app(_mk_state(), bus, _FakeMemory())) as client:
        resp = await client.post("/api/chat", json={"message": "你好"})
    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == {"event_id"}
    [event] = bus.published
    assert event.type is EventType.USER_MESSAGE
    assert event.source is Source.EXTERNAL
    assert event.correlation_id == event.id
    assert data["event_id"] == event.id


async def test_memories_endpoint() -> None:
    memory = _FakeMemory()
    async with _client(_app(_mk_state(), _FakeBus(), memory)) as client:
        resp = await client.get(
            "/api/memories",
            params={"kind": "user_profile", "type": "long_term"},
        )
    assert resp.status_code == 200
    assert [m["type"] for m in resp.json()] == ["short_term"]
    assert memory.list_calls == [(MemoryKind.USER_PROFILE, MemoryType.LONG_TERM)]


async def test_memory_search_endpoint() -> None:
    memory = _FakeMemory()
    async with _client(_app(_mk_state(), _FakeBus(), memory)) as client:
        resp = await client.get("/api/memories/search", params={"q": "猫"})
    assert resp.status_code == 200
    assert [m["type"] for m in resp.json()] == ["short_term"]
    assert memory.search_calls == ["猫"]


async def test_observe_endpoint() -> None:
    bus = _FakeBus()
    app = _app(_mk_state(), bus, _FakeMemory())
    async with _client(app) as client:
        resp = await client.post(
            "/api/observe",
            json={"presence": "online", "window_title": "编辑器", "idle_seconds": 0},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == {"event_id"}
    [event] = bus.published
    assert event.type is EventType.OBSERVATION_STATE
    assert event.content == {
        "presence": "online",
        "window_title": "编辑器",
        "idle_seconds": 0.0,
        "previous_presence": None,
        "transition": "initial",
        "away_duration_seconds": None,
    }
    assert app.presence_initialized is True
    assert app.last_presence == "online"
    assert app.last_window_title == "编辑器"


async def test_sse_frame_uses_backend_event_timestamp() -> None:
    bus = _FakeBus()
    fast = build_app(_app(_mk_state(), bus, _FakeMemory()))
    route = next(
        route
        for route in fast.routes
        if isinstance(route, APIRoute) and route.path == "/api/events"
    )
    response = cast(StreamingResponse, await route.endpoint())
    event = Event(
        id="event-with-time",
        timestamp=1234.5,
        source=Source.INTERNAL,
        type=EventType.SPEAK,
        content={"content": "你好"},
        correlation_id="corr-time",
    )
    await bus.publish(event)
    iterator = cast(AsyncGenerator[str, None], response.body_iterator)
    chunk = await iterator.__anext__()
    await iterator.aclose()
    data_line = next(line for line in chunk.splitlines() if line.startswith("data: "))

    assert json.loads(data_line.removeprefix("data: "))["timestamp"] == 1234.5


async def test_observe_away_to_online_records_return_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timestamps = iter((500.0, 800.0))

    def _root_event(
        type_: EventType, content: dict[str, object], source: Source
    ) -> Event:
        timestamp = next(timestamps)
        return Event(
            id=str(timestamp),
            timestamp=timestamp,
            source=source,
            type=type_,
            content=content,
            correlation_id=str(timestamp),
        )

    monkeypatch.setattr("nyx.main.root_event", _root_event)
    bus = _FakeBus()
    app = _app(_mk_state(), bus, _FakeMemory())
    async with _client(app) as client:
        first = await client.post(
            "/api/observe",
            json={"presence": "away", "window_title": "编辑器", "idle_seconds": 300},
        )
        second = await client.post(
            "/api/observe",
            json={"presence": "online", "window_title": "编辑器", "idle_seconds": 0},
        )
    assert first.status_code == second.status_code == 200
    assert bus.published[1].content["transition"] == "returned"
    assert bus.published[1].content["away_duration_seconds"] == 600.0
    assert app.pending_return == {
        "returned_at": 800.0,
        "away_duration_seconds": 600.0,
    }


async def test_observe_admission_failure_preserves_presence_snapshot() -> None:
    bus = _FakeBus()
    app = _app(_mk_state(), bus, _FakeMemory())
    bus.failure = EventAdmissionError("closed")
    async with _client(app) as client:
        resp = await client.post(
            "/api/observe",
            json={"presence": "online", "window_title": "编辑器", "idle_seconds": 0},
        )
    assert resp.status_code == 503
    assert app.presence_initialized is False
    assert app.last_presence == "away"
    assert app.pending_return is None


def test_release_old_return_claim_does_not_overwrite_new_return() -> None:
    app = _app(_mk_state(), _FakeBus(), _FakeMemory())
    old = {"returned_at": 10.0, "away_duration_seconds": 300.0}
    new = {"returned_at": 20.0, "away_duration_seconds": 600.0}
    app.pending_return = old
    claim = app.claim_return_context()
    app.pending_return = new

    app.release_return_context(claim)

    assert app.pending_return == new
    assert app.claimed_return is None


async def test_export_endpoint() -> None:
    memory = _FakeMemory()
    async with _client(_app(_mk_state(), _FakeBus(), memory)) as client:
        j = await client.post("/api/export", json={"format": "json"})
        m = await client.post("/api/export", json={"format": "md"})
    assert j.text == "exported:json"  # 原始字符串，不二次 json.dumps
    assert m.text == "exported:md"
    assert j.headers["content-type"].startswith("application/json")
    assert m.headers["content-type"].startswith("text/markdown")
    assert memory.export_calls == ["json", "md"]


async def test_export_bogus_raises() -> None:
    memory = _FakeMemory()
    transport = ASGITransport(
        app=build_app(_app(_mk_state(), _FakeBus(), memory)),
        raise_app_exceptions=False,
    )
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/export", json={"format": "bogus"})
    assert resp.status_code == 500
    assert memory.export_calls == ["bogus"]


async def test_chat_missing_message_returns_422() -> None:
    async with _client(_app(_mk_state(), _FakeBus(), _FakeMemory())) as client:
        resp = await client.post("/api/chat", json={})
    assert resp.status_code == 422


async def test_observe_invalid_presence_returns_422() -> None:
    app = _app(_mk_state(), _FakeBus(), _FakeMemory())
    async with _client(app) as client:
        resp = await client.post("/api/observe", json={"presence": "Online"})
    assert resp.status_code == 422
    assert app.last_presence == "away"  # 校验失败不更新状态


async def test_materials_endpoint_returns_progress() -> None:
    """GET /api/materials：书库进度（read_chars/total_chars），不再是纯文件名。"""
    fake_activity = _FakeActivity()
    app = _app(_mk_state(), _FakeBus(), _FakeMemory())
    app.activity = cast(ActivityFacade, fake_activity)
    async with _client(app) as client:
        resp = await client.get("/api/materials")
    assert resp.status_code == 200
    assert resp.json() == {
        "materials": [
            {
                "path": "workspace/uploads/a.txt",
                "filename": "a.txt",
                "total_chars": 100,
                "read_chars": 40,
                "created_at": 1.0,
                "updated_at": 2.0,
            }
        ]
    }
    assert fake_activity.list_calls == 1


async def test_eval_recent_endpoint() -> None:
    store = _FakeEvalStore()
    store.records = [
        EvalRecord(
            id="e1", created_at=1.0, call_id="c1", module="expression",
            output_type="speak", model="x", correlation_id="k",
            ooc_keyword=1.0, ooc_embed=None, prompt_tokens=5, completion_tokens=2,
        )
    ]
    app = _app(_mk_state(), _FakeBus(), _FakeMemory())
    app.eval_store = cast(EvalStore, store)
    async with _client(app) as client:
        resp = await client.get("/api/eval/recent", params={"limit": 3})
    assert resp.status_code == 200
    assert store.recent_calls == [3]
    assert resp.json()[0]["call_id"] == "c1"


@pytest.mark.parametrize("limit", [0, -1, 101])
async def test_eval_recent_rejects_out_of_range_limit(limit: int) -> None:
    store = _FakeEvalStore()
    app = _app(_mk_state(), _FakeBus(), _FakeMemory())
    app.eval_store = cast(EvalStore, store)
    async with _client(app) as client:
        resp = await client.get("/api/eval/recent", params={"limit": limit})
    assert resp.status_code == 422
    assert store.recent_calls == []


async def test_eval_prompt_endpoint() -> None:
    store = _FakeEvalStore()
    prompt = [{"role": "user", "content": "你好\nNyx"}]
    store.prompts["e1"] = (True, prompt)
    app = _app(_mk_state(), _FakeBus(), _FakeMemory())
    app.eval_store = cast(EvalStore, store)
    async with _client(app) as client:
        resp = await client.get("/api/eval/e1/prompt")
    assert resp.status_code == 200
    assert resp.json() == prompt
    assert resp.headers["cache-control"] == "no-store"


async def test_eval_prompt_legacy_and_missing() -> None:
    store = _FakeEvalStore()
    store.prompts["legacy"] = (True, None)
    app = _app(_mk_state(), _FakeBus(), _FakeMemory())
    app.eval_store = cast(EvalStore, store)
    async with _client(app) as client:
        legacy = await client.get("/api/eval/legacy/prompt")
        missing = await client.get("/api/eval/missing/prompt")
    assert legacy.status_code == 200 and legacy.json() is None
    assert missing.status_code == 404


async def test_eval_prompt_corruption_returns_controlled_error() -> None:
    store = _FakeEvalStore()
    store.prompt_error = ValueError("raw prompt must not leak")
    app = _app(_mk_state(), _FakeBus(), _FakeMemory())
    app.eval_store = cast(EvalStore, store)
    async with _client(app) as client:
        resp = await client.get("/api/eval/e1/prompt")
    assert resp.status_code == 500
    assert resp.json() == {"detail": "stored prompt is invalid"}


async def test_eval_total_tokens_endpoint() -> None:
    store = _FakeEvalStore()
    app = _app(_mk_state(), _FakeBus(), _FakeMemory())
    app.eval_store = cast(EvalStore, store)
    async with _client(app) as client:
        resp = await client.get("/api/eval/total_tokens")
    assert resp.status_code == 200
    assert resp.json() == {
        "total_tokens": 42, "prompt_tokens": 30, "completion_tokens": 12,
    }


async def test_upload_endpoint_registers_material(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /api/upload：只写文件 + 注册书库，不触发读书（无 ACTIVITY_START）。"""
    async def _fake_file_io(
        action: str, path: str, content: str | None = None
    ) -> dict[str, object]:
        return {"path": f"workspace/{path}", "written": len(content or "")}

    monkeypatch.setattr("nyx.main.file_io", _fake_file_io)
    fake_activity = _FakeActivity()
    bus = _FakeBus()
    app = _app(_mk_state(), bus, _FakeMemory())
    app.activity = cast(ActivityFacade, fake_activity)
    async with _client(app) as client:
        resp = await client.post(
            "/api/upload",
            files={"file": ("book.txt", "骑士团的历史".encode("utf-8"), "text/plain")},
        )
    assert resp.status_code == 200
    assert resp.json() == {
        "filename": "book.txt",
        "path": "workspace/uploads/book.txt",
    }
    assert fake_activity.registered == [
        ("workspace/uploads/book.txt", "book.txt", 6)
    ]
    assert bus.published == []
