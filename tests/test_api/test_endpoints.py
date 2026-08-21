# pyright: reportPrivateUsage=false
from typing import cast

from httpx import ASGITransport, AsyncClient

from nyx.activity.facade import ActivityFacade
from nyx.config import Config
from nyx.desire.facade import DesireFacade
from nyx.enums import (
    EmotionCategory,
    EnergyState,
    EventType,
    MemoryType,
    Source,
)
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.expression.facade import ExpressionFacade
from nyx.inner_life.facade import InnerLifeFacade
from nyx.main import _App, build_app
from nyx.memory.facade import MemoryFacade
from nyx.types import CurrentState, Event, Memory


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
        energy=80.0,
        energy_state=EnergyState.OKAY,
        current_activity=None,
        active_desires=[],
    )


def _mem() -> Memory:
    return Memory(
        id="m1", created_at=0.0, content="内容", tag="user", summary="摘要",
        freshness=1.0, type=MemoryType.SHORT_TERM,
    )


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.published.append(event)


class _FakeInnerLife:
    def __init__(self, state: CurrentState) -> None:
        self.state = state

    async def get_state(self) -> CurrentState:
        return self.state


class _FakeMemory:
    def __init__(self) -> None:
        self.list_calls: list[tuple[str | None, MemoryType | None]] = []
        self.export_calls: list[str] = []

    async def list_memories(
        self, tag: str | None = None, type: MemoryType | None = None
    ) -> list[Memory]:
        self.list_calls.append((tag, type))
        return [_mem()]

    async def export(self, fmt: str) -> str:
        self.export_calls.append(fmt)
        if fmt not in ("json", "md"):
            raise ValueError(f"不支持的导出格式：{fmt}")
        return f"exported:{fmt}"


def _app(state: CurrentState, bus: _FakeBus, memory: _FakeMemory) -> _App:
    return _App(
        bus=cast(EventBus, bus),
        inner_life=cast(InnerLifeFacade, _FakeInnerLife(state)),
        desire=cast(DesireFacade, object()),
        memory=cast(MemoryFacade, memory),
        activity=cast(ActivityFacade, object()),
        expression=cast(ExpressionFacade, object()),
        evaluator=cast(Evaluator, object()),
        config=Config(),
    )


def _client(app: _App) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=build_app(app)), base_url="http://test"
    )


async def test_state_endpoint() -> None:
    async with _client(_app(_mk_state(), _FakeBus(), _FakeMemory())) as client:
        resp = await client.get("/api/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["emotion"] == "neutral"
    assert body["energy_state"] == "okay"
    assert body["energy"] == 80.0


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
            "/api/memories", params={"tag": "user", "type": "long_term"}
        )
    assert resp.status_code == 200
    assert [m["type"] for m in resp.json()] == ["short_term"]
    assert memory.list_calls == [("user", MemoryType.LONG_TERM)]


async def test_observe_endpoint() -> None:
    bus = _FakeBus()
    app = _app(_mk_state(), bus, _FakeMemory())
    async with _client(app) as client:
        resp = await client.post(
            "/api/observe", json={"presence": "online", "window_title": "编辑器"}
        )
    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == {"event_id"}
    [event] = bus.published
    assert event.type is EventType.OBSERVATION_STATE
    assert event.content == {"presence": "online", "window_title": "编辑器"}
    assert app.last_presence == "online"
    assert app.last_window_title == "编辑器"


async def test_export_endpoint() -> None:
    memory = _FakeMemory()
    async with _client(_app(_mk_state(), _FakeBus(), memory)) as client:
        j = await client.post("/api/export", json={"format": "json"})
        m = await client.post("/api/export", json={"format": "md"})
    assert j.json() == "exported:json"
    assert m.json() == "exported:md"
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
