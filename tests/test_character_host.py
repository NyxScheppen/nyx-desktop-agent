# pyright: reportPrivateUsage=false
import asyncio
import importlib
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from nyx.config import Config, LlmConfig
from nyx.enums import EventType
from nyx.types import Activity, CurrentState


async def test_onebot_character_lifecycle_and_private_reply(
    tmp_path: Path, resources: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from websockets.asyncio.server import ServerConnection, serve

    from nyx.app_context import _Host
    from nyx.llm.client import LlmClient, LlmMessage
    from nyx.types import LLMOutput
    from tests.test_character_cards import package

    async def complete(
        self: LlmClient, messages: list[LlmMessage], *, module: str,
        output_type: str, correlation_id: str, json_mode: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMOutput:
        text = '{"think":"私聊","speak":"私聊回复","needs_continuation":false}'
        if output_type == "plan":
            text = '{"goal":"回复","steps":[]}'
        elif output_type != "reply":
            text = "{}"
        return LLMOutput(module=module, type=output_type, model="fixture",
                         content=text, correlation_id=correlation_id)

    monkeypatch.setattr(LlmClient, "complete", complete)
    monkeypatch.setenv("ONEBOT_ACCESS_TOKEN", "fixture-token")
    config = Config()
    config.onebot.enabled = True
    config.onebot.self_id, config.onebot.owner_id = 123, 456
    config.onebot.character_id = "alice"
    reply = asyncio.Event()
    text_messages: list[str] = []

    async def server(ws: ServerConnection) -> None:
        async for raw in ws:
            request = json.loads(raw)
            if request["action"] == "get_login_info":
                await ws.send(json.dumps({"status": "ok", "retcode": 0,
                    "data": {"user_id": 123}, "echo": request["echo"]}))
                payload = {"post_type": "message", "message_type": "private",
                    "self_id": 123, "user_id": 456, "message_id": 7,
                    "time": int(time.time()), "message": [
                        {"type": "text", "data": {"text": "你好"}},
                    ]}
                await ws.send(json.dumps(payload))
                await ws.send(json.dumps(payload))
            else:
                text_messages.append(request["params"]["message"][0]["data"]["text"])
                await ws.send(json.dumps({"status": "ok", "retcode": 0,
                    "data": {"message_id": 8}, "echo": request["echo"]}))
                if text_messages[-1] == "私聊回复":
                    reply.set()

    async with serve(server, "127.0.0.1", 0) as socket:
        port = list(socket.sockets)[0].getsockname()[1]
        config.onebot.ws_url = f"ws://127.0.0.1:{port}"
        host = await _Host.open(config, tmp_path)
        try:
            await host.register(package())
            await host.start("alice")
            await asyncio.wait_for(reply.wait(), 15)
            app = host.apps["alice"]
            assert host.current_id == "nyx" and app.can_initiate()
            assert text_messages == ["💭 内心话\n私聊", "私聊回复"]
            events = await app.bus.list_events(event_type=EventType.USER_MESSAGE)
            assert len(events) == 1 and events[0].id == "onebot:123:7"
            assert app.database is not None
            async with asyncio.timeout(5):
                while True:
                    async with app.database.lock:
                        row = await (await app.database.conn.execute(
                            "SELECT COUNT(*) AS pending FROM transport_outbox "
                            "WHERE status!='sent'"
                        )).fetchone()
                    if row is not None and row["pending"] == 0:
                        break
                    await asyncio.sleep(0.02)
            await host.unload("alice")
            assert host.records["alice"]["status"] == "ARCHIVED"
            assert app.database is not None and app.database.is_closed
            await host.restore("alice")
            await asyncio.sleep(0.7)
            assert text_messages == ["💭 内心话\n私聊", "私聊回复"]
        finally:
            await host.close()


async def test_second_character_runs_through_harness_and_keeps_isolated_state(
    tmp_path: Path,
    resources: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    import httpx
    import uvicorn

    from nyx import runtime
    from nyx.api.routes import build_host_app
    from nyx.app_context import _Host
    from nyx.llm.client import LlmClient, LlmMessage
    from nyx.types import LLMOutput
    from tests.test_character_cards import package

    prompts: list[str] = []

    async def complete(
        self: LlmClient, messages: list[LlmMessage], *, module: str,
        output_type: str, correlation_id: str, json_mode: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMOutput:
        text = "{}"
        if output_type == "reply":
            prompts.append(str(messages))
            text = ('{"think":"Alice在想","speak":"Alice收到",'
                    '"needs_continuation":false}')
        elif output_type == "plan":
            text = '{"goal":"回复","steps":[]}'
        elif output_type == "fact_extraction":
            text = '{"facts":[]}'
        return LLMOutput(module=module, type=output_type, model="fixture",
                         content=text, correlation_id=correlation_id)

    async def tick(app: Any, **kwargs: Any) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(LlmClient, "complete", complete)
    monkeypatch.setattr(runtime, "tick_loop", tick)
    monkeypatch.delenv("NYX_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("NYX_PUBLIC_ORIGIN", raising=False)
    host = await _Host.open(Config(), tmp_path)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        build_host_app(host, launch_nonce=None), lifespan="off", log_level="error",
        ws="none",
    ))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", headers={"Host": "127.0.0.1:8000"},
            trust_env=False,
        ) as client:
            registered = await client.post("/api/characters", files={
                "file": ("alice.nyxcard", package(), "application/zip")
            })
            assert registered.status_code == 202, registered.text
            async with asyncio.timeout(15):
                while host.records["alice"]["status"] == "STARTING":
                    await asyncio.sleep(0.01)
            selection = await client.put(
                "/api/characters/current", json={"character_id": "alice"}
            )
            revision = str(selection.json()["selection_revision"])
            accepted = await client.post("/api/characters/alice/chat",
                json={"message": "你好", "discussion": False},
                headers={"X-Nyx-Selection-Revision": revision})
            assert accepted.status_code == 200
            async with asyncio.timeout(15):
                async with client.stream("GET", "/api/characters/alice/events") as sse:
                    event_type = ""
                    async for line in sse.aiter_lines():
                        if line.startswith("event: "):
                            event_type = line[7:]
                        elif event_type == "speak" and line.startswith("data: "):
                            payload = json.loads(line[6:])
                            assert payload["character_id"] == "alice"
                            assert payload["content"] == "Alice收到"
                            break
            assert prompts and all("你是 Alice。" in prompt for prompt in prompts)
            assert await host.apps["nyx"].bus.list_events(
                event_type=EventType.USER_MESSAGE
            ) == []
            await client.put("/api/characters/current", json={"character_id": "nyx"})
            await host.unload("alice")
            await host.restore("alice", run=False)
            assert len(await host.apps["alice"].bus.list_events(
                event_type=EventType.SPEAK
            )) == 1
    finally:
        server.should_exit = True
        await serving
        listener.close()
        await host.close()


@pytest.fixture
def resources(monkeypatch: pytest.MonkeyPatch) -> None:
    from nyx import app_context
    from nyx.llm.client import LlmClient

    def client(cls: type[LlmClient], config: LlmConfig) -> LlmClient:
        return LlmClient(None, "fake")

    def embed(model: str) -> Callable[[str], Awaitable[list[float]]]:
        async def encode(text: str) -> list[float]:
            return [1.0, 0.0]

        return encode

    monkeypatch.setattr(LlmClient, "from_config", classmethod(client))
    monkeypatch.setattr(app_context, "build_embed", embed)


async def test_role_task_failure_does_not_end_host_loop(
    tmp_path: Path,
    resources: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx.app_context import _Host
    from nyx.runtime import host_presence_loop

    host = await _Host.open(Config(), tmp_path)
    entered = asyncio.Event()

    async def broken(identity: str, *, force: bool = False) -> bool:
        entered.set()
        raise ValueError("role database failed")

    monkeypatch.setattr(host, "flush_presence", broken)
    loop = asyncio.create_task(host_presence_loop(host))
    try:
        await entered.wait()
        await asyncio.sleep(0.6)
        assert not loop.done()
        assert host.records["nyx"]["status"] == "RUNNING"
    finally:
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)
        await host.close()


@pytest.mark.parametrize("task_name", ["tick_task", "transport_task"])
async def test_unload_retains_runtime_when_tick_ignores_cancellation(
    tmp_path: Path,
    resources: None,
    monkeypatch: pytest.MonkeyPatch,
    task_name: str,
) -> None:
    from nyx.app_context import _Host
    from tests.test_character_cards import package

    host = await _Host.open(Config(), tmp_path)
    release, entered = asyncio.Event(), asyncio.Event()
    original_wait = asyncio.wait

    async def bounded_wait(tasks: Any, **kwargs: Any) -> Any:
        kwargs["timeout"] = 0.01
        return await original_wait(tasks, **kwargs)

    async def stubborn() -> None:
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass

    await host.register(package())
    await host.start("alice", run=False)
    app = host.apps["alice"]
    producer = asyncio.create_task(stubborn())
    setattr(app, task_name, producer)
    await entered.wait()
    monkeypatch.setattr(asyncio, "wait", bounded_wait)
    stop = asyncio.create_task(host.unload("alice"))
    try:
        await original_wait([stop], timeout=0.2)
        assert stop.done(), "unload must return even if the tick ignores cancellation"
        await stop
        assert host.records["alice"]["status"] == "STOPPING"
        assert app.database is not None and not app.database._closed
    finally:
        release.set()
        await producer
        await stop
        await host.close()


async def test_unload_drain_timeout_keeps_worker_until_accepted_work_finishes(
    tmp_path: Path,
    resources: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx import runtime
    from nyx.app_context import _Host
    from nyx.enums import Source
    from nyx.events.bus import EventBus
    from nyx.types import Event
    from tests.test_character_cards import package

    host = await _Host.open(Config(), tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original_drain = EventBus.drain

    async def tick(app: Any, **kwargs: Any) -> None:
        await asyncio.Event().wait()

    async def handler(event: Event) -> None:
        entered.set()
        await release.wait()

    async def drain(bus: EventBus, timeout: float = 15) -> bool:
        return await original_drain(bus, timeout=timeout if release.is_set() else 0.02)

    monkeypatch.setattr(runtime, "tick_loop", tick)
    monkeypatch.setattr(EventBus, "drain", drain)
    monkeypatch.setattr("nyx.events.bus._SCAN_INTERVAL_SECONDS", 0.005)
    try:
        await host.register(package())
        await host.start("alice")
        app = host.apps["alice"]
        app.bus.subscribe(EventType.SPEAK, handler)
        event = runtime.root_event(
            EventType.SPEAK, {"text": "accepted"}, Source.INTERNAL
        )
        await app.bus.publish(event)
        await asyncio.wait_for(entered.wait(), timeout=2)
        await host.unload("alice")
        assert host.records["alice"]["status"] == "STOPPING"
        assert app.database is not None and not app.database._closed
        await host.unload("alice")
        assert host.records["alice"]["status"] == "STOPPING"
        release.set()
        async with asyncio.timeout(2):
            while host.records["alice"].get("status") != "ARCHIVED":
                await host.unload("alice")
                await asyncio.sleep(0.01)
        await host.restore("alice", run=False)
        restored = host.apps["alice"]
        assert restored.database is not None
        row = await (
            await restored.database.conn.execute(
                "SELECT status FROM event_delivery WHERE event_id=?", (event.id,)
            )
        ).fetchone()
        assert row is not None and row["status"] == "succeeded"
    finally:
        release.set()
        await host.close()


async def test_host_close_waits_for_timed_out_delivery_then_closes_databases(
    tmp_path: Path,
    resources: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx import runtime
    from nyx.app_context import _Host
    from nyx.enums import Source
    from nyx.events.bus import EventBus
    from nyx.types import Event

    host = await _Host.open(Config(), tmp_path)
    entered, release, timed_out = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_drain = EventBus.drain

    async def tick(app: Any, **kwargs: Any) -> None:
        await asyncio.Event().wait()

    async def handler(event: Event) -> None:
        entered.set()
        await release.wait()

    async def drain(bus: EventBus, timeout: float = 15) -> bool:
        result = await original_drain(
            bus, timeout=timeout if release.is_set() else 0.02,
        )
        if not result:
            timed_out.set()
        return result

    monkeypatch.setattr(runtime, "tick_loop", tick)
    monkeypatch.setattr(EventBus, "drain", drain)
    app = host.apps["nyx"]
    app.bus.subscribe(EventType.SPEAK, handler)
    host.activate("nyx")
    await app.bus.publish(
        runtime.root_event(EventType.SPEAK, {"text": "accepted"}, Source.INTERNAL)
    )
    await asyncio.wait_for(entered.wait(), timeout=2)
    close = asyncio.create_task(host.close())
    try:
        await asyncio.wait_for(timed_out.wait(), timeout=2)
        await asyncio.sleep(0.05)
        assert not close.done(), "close must retain the event loop while writers exist"
        release.set()
        await asyncio.wait_for(close, timeout=2)
        assert app.database is not None and app.database._closed
        assert host.database._closed
        assert not host.apps
    finally:
        release.set()
        await close
        await host.close()


async def test_fatal_role_failure_preserves_other_role_and_releases_stopped_lease(
    tmp_path: Path,
    resources: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx import runtime
    from nyx.app_context import _Host
    from nyx.enums import Source
    from nyx.types import Event
    from tests.test_character_cards import package

    host = await _Host.open(Config(), tmp_path)
    entered, fail = asyncio.Event(), asyncio.Event()

    async def tick(app: Any, **kwargs: Any) -> None:
        if app.character_id == "alice":
            await fail.wait()
            raise RuntimeError("role fatal failure")
        await asyncio.Event().wait()

    async def handler(event: Event) -> None:
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runtime, "tick_loop", tick)
    other = asyncio.create_task(runtime.run_character(host, "nyx"))
    host.lifecycle["nyx"] = other
    try:
        await host.register(package())
        await host.start("alice")
        app = host.apps["alice"]
        app.bus.subscribe(EventType.SPEAK, handler)
        event = runtime.root_event(
            EventType.SPEAK, {"text": "accepted"}, Source.INTERNAL
        )
        await app.bus.publish(event)
        await asyncio.wait_for(entered.wait(), timeout=2)
        fail.set()
        await asyncio.wait_for(host.lifecycle["alice"], timeout=2)
        assert host.records["alice"]["status"] == "FAILED"
        assert host.records["nyx"]["status"] == "RUNNING" and not other.done()
        await host.retry_start("alice")
        await host.start("alice", run=False)
        restored = host.apps["alice"]
        assert restored.database is not None
        row = await (
            await restored.database.conn.execute(
                "SELECT status, lease_until FROM event_delivery WHERE event_id=?",
                (event.id,),
            )
        ).fetchone()
        assert (
            row is not None
            and row["status"] == "pending"
            and row["lease_until"] is None
        )
    finally:
        await host.close()


async def test_delayed_presence_replays_admitted_clock_reset(
    tmp_path: Path,
    resources: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx.app_context import _Host
    from tests.test_character_cards import package

    host = await _Host.open(Config(), tmp_path)
    try:
        await host.register(package())
        await host.start("alice", run=False)
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        await host.observe(
            {
                "sample_id": "before-reset",
                "presence": "online",
                "sampled_at": 1000.0,
                "idle_seconds": 0,
            }
        )
        await host.flush_presence("nyx", force=True)
        await host.flush_presence("alice", force=True)
        monkeypatch.setattr(time, "time", lambda: 999.5)
        await host.observe(
            {
                "sample_id": "reset",
                "presence": "away",
                "sampled_at": 999.5,
                "idle_seconds": 400,
            }
        )
        await host.flush_presence("nyx", force=True)
        monkeypatch.setattr(time, "time", lambda: 1001.0)
        await host.flush_presence("alice", force=True)
        assert host.apps["nyx"].last_presence == "away"
        assert host.apps["alice"].last_presence == "away"
    finally:
        await host.close()


async def test_role_switch_preserves_runtime_and_independent_save(
    tmp_path: Path,
    resources: None,
) -> None:
    from tests.test_character_cards import package

    context = importlib.import_module("nyx.app_context")
    host = await context._Host.open(Config(), tmp_path)
    try:
        await host.register(package())
        await host.start("alice", run=False)
        nyx = host.apps["nyx"]
        alice = host.apps["alice"]
        await host.select("alice")
        assert host.apps["nyx"] is nyx
        assert nyx.database is not alice.database
        assert (await alice.inner_life.get_state()).personality["openness"] == 5
    finally:
        await host.close()


async def test_role_tools_cannot_read_another_save(
    tmp_path: Path,
    resources: None,
) -> None:
    from tests.test_character_cards import package

    context = importlib.import_module("nyx.app_context")
    host = await context._Host.open(Config(), tmp_path)
    try:
        await host.register(package())
        await host.start("alice", run=False)
        target = tmp_path / "workspace" / "private.txt"
        target.parent.mkdir(exist_ok=True)
        target.write_text("private", encoding="utf-8")
        tools = context.build_tools(
            Config(),
            workspace=tmp_path / "alice-workspace",
            blocked=host.blocked_paths("alice"),
        )
        with pytest.raises(ValueError, match="内部"):
            await tools.call("file_io", {"action": "read", "path": str(target)})
    finally:
        await host.close()


async def test_presence_partial_failure_keeps_per_role_order(
    tmp_path: Path,
    resources: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_character_cards import package

    context = importlib.import_module("nyx.app_context")
    host = await context._Host.open(Config(), tmp_path)
    try:
        await host.register(package())
        await host.start("alice", run=False)
        original = host.apps["alice"].apply_presence

        async def broken(*args: object, **kwargs: object) -> None:
            raise OSError("alice database unavailable")

        monkeypatch.setattr(host.apps["alice"], "apply_presence", broken)
        now = time.time() - 1
        await host.observe(
            {
                "sample_id": "away",
                "presence": "away",
                "sampled_at": now,
                "idle_seconds": 400,
            }
        )
        await host.flush_presence("nyx")
        await host.observe(
            {
                "sample_id": "online",
                "presence": "online",
                "sampled_at": now + 0.5,
                "idle_seconds": 0,
            }
        )
        await host.flush_presence("nyx")
        assert host.apps["nyx"].last_presence == "online"
        monkeypatch.setattr(host.apps["alice"], "apply_presence", original)
        await host.flush_presence("alice", force=True)
        events = await host.apps["alice"].bus.list_events(
            limit=10, event_type=EventType.OBSERVATION_STATE
        )
        assert [event.content["presence"] for event in reversed(events)][-2:] == [
            "away",
            "online",
        ]
        assert host.apps["alice"].pending_return is not None
    finally:
        await host.close()


async def test_concurrent_import_limit_and_restore_save(
    tmp_path: Path,
    resources: None,
) -> None:
    from nyx.app_context import _Host
    from tests.test_character_cards import package

    host = await _Host.open(Config(), tmp_path)
    try:
        bundles = [package()]
        import zipfile
        from io import BytesIO

        for identity in ("bob", "carol", "dana"):
            with zipfile.ZipFile(BytesIO(package())) as source:
                output = BytesIO()
                with zipfile.ZipFile(output, "w") as target:
                    for name in source.namelist():
                        target.writestr(
                            name,
                            source.read(name).replace(
                                b"id: alice", f"id: {identity}".encode()
                            ),
                        )
                bundles.append(output.getvalue())
        results = await asyncio.gather(
            *(host.register(b) for b in bundles), return_exceptions=True
        )
        assert sum(isinstance(r, ValueError) for r in results) == 1
        await host.start("alice", run=False)
        app = host.apps["alice"]
        assert app.database is not None
        async with app.database.transaction() as conn:
            await conn.execute("UPDATE personality SET openness=9 WHERE id='self'")
        await host.unload("alice")
        assert host.records["alice"]["status"] == "ARCHIVED"
        await host.restore("alice", run=False)
        assert (await host.apps["alice"].inner_life.get_state()).personality[
            "openness"
        ] == 9
    finally:
        await host.close()


async def test_root_token_guards_host_role_asset_and_alias(
    tmp_path: Path, resources: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from nyx.api.routes import build_host_app
    from nyx.app_context import _Host

    monkeypatch.setenv("NYX_AUTH_TOKEN", "fixture-root-token")
    monkeypatch.setenv("NYX_PUBLIC_ORIGIN", "https://localhost:8443")
    host = await _Host.open(Config(), tmp_path)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=build_host_app(host, launch_nonce=None)),
            base_url="http://127.0.0.1:8000",
        ) as client:
            for path in (
                "/api/characters", "/api/characters/nyx/state",
                "/api/characters/nyx/assets/neutral", "/api/state",
            ):
                assert (await client.get(path)).status_code == 401
                assert (await client.get(path, headers={
                    "Authorization": "Bearer wrong",
                })).status_code == 401
                assert (await client.get(path, headers={
                    "Authorization": "Bearer fixture-root-token",
                    "Origin": "https://localhost:8443",
                })).status_code == 200
            assert (await client.get("/api/characters/nyx/events")).status_code == 401
            assert (await client.get("/api/characters", headers={
                "Authorization": "Bearer fixture-root-token",
                "Origin": "https://other.example",
            })).status_code == 403
    finally:
        await host.close()


async def test_device_pairing_is_one_time_and_revocable(
    tmp_path: Path, resources: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from nyx.api.routes import build_host_app
    from nyx.app_context import _Host

    monkeypatch.setenv("NYX_AUTH_TOKEN", "fixture-root-token")
    monkeypatch.setenv("NYX_PUBLIC_ORIGIN", "https://localhost:8443")
    host = await _Host.open(Config(), tmp_path)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=build_host_app(host, launch_nonce=None)),
            base_url="http://127.0.0.1:8000",
        ) as client:
            root = {"Authorization": "Bearer fixture-root-token"}
            created = await client.post(
                "/api/devices/pairing", json={"name": "手机"}, headers=root
            )
            assert created.status_code == 200
            code = created.json()["code"]
            async with host.database.lock:
                row = await (
                    await host.database.conn.execute(
                        "SELECT code_hash FROM pairing_code"
                    )
                ).fetchone()
            assert row is not None and code not in row["code_hash"]
            paired = await client.post(
                "/api/devices/pair", json={"code": code, "name": "手机"}
            )
            assert paired.status_code == 200
            device = paired.json()["access_token"]
            assert (await client.get(
                "/api/state", headers={"Authorization": f"Bearer {device}"}
            )).status_code == 200
            assert (await client.post(
                "/api/devices/pair", json={"code": code, "name": "再次"}
            )).status_code == 401
            listed = await client.get("/api/devices", headers=root)
            assert listed.status_code == 200
            assert listed.json()[0]["name"] == "手机"
            device_id = listed.json()[0]["id"]
            assert (await client.delete(
                f"/api/devices/{device_id}", headers=root
            )).status_code == 200
            assert (await client.get(
                "/api/state", headers={"Authorization": f"Bearer {device}"}
            )).status_code == 401
            assert (await client.get(
                "/api/devices", headers={"Authorization": f"Bearer {device}"}
            )).status_code == 401
    finally:
        await host.close()


async def test_device_pairing_is_disabled_without_root_token(
    tmp_path: Path, resources: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from nyx.api.routes import build_host_app
    from nyx.app_context import _Host

    monkeypatch.delenv("NYX_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("NYX_PUBLIC_ORIGIN", raising=False)
    host = await _Host.open(Config(), tmp_path)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=build_host_app(host, launch_nonce=None)),
            base_url="http://127.0.0.1:8000",
        ) as client:
            assert (await client.post(
                "/api/devices/pairing", json={"name": "手机"}
            )).status_code == 404
            assert (await client.post(
                "/api/devices/pair", json={"code": "unknown", "name": "手机"}
            )).status_code == 404
    finally:
        await host.close()


async def test_expired_pairing_code_cannot_issue_a_device(
    tmp_path: Path,
) -> None:
    from nyx.app_context import _Host

    host = await _Host.open(Config(), tmp_path)
    try:
        code, _ = await host.create_pairing_code("phone")
        async with host.database.transaction() as conn:
            await conn.execute("UPDATE pairing_code SET expires_at=0")
        assert await host.redeem_pairing_code(code, "phone") is None
        async with host.database.lock:
            count = await (
                await host.database.conn.execute("SELECT count(*) FROM device_token")
            ).fetchone()
        assert count is not None
        assert count[0] == 0
    finally:
        await host.close()


async def test_scoped_api_rejects_stale_selection_and_keeps_role_results(
    tmp_path: Path,
    resources: None,
) -> None:
    import httpx

    from nyx.api import routes
    from nyx.app_context import _Host
    from tests.test_character_cards import package

    host = await _Host.open(Config(), tmp_path)
    try:
        await host.register(package())
        await host.start("alice", run=False)
        api = getattr(routes, "build_host_app")(host, launch_nonce="test")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api), base_url="http://127.0.0.1:8000"
        ) as client:
            headers = {"X-Nyx-Selection-Revision": "0"}
            accepted = await client.post(
                "/api/characters/nyx/chat",
                json={"message": "only nyx"},
                headers=headers,
            )
            assert accepted.status_code == 200
            await client.put("/api/characters/current", json={"character_id": "alice"})
            stale = await client.post(
                "/api/characters/nyx/chat", json={"message": "late"}, headers=headers
            )
            assert stale.status_code == 409
            missing = await client.get("/api/characters/missing/state")
            assert missing.status_code == 404
            events = await host.apps["alice"].bus.list_events(
                event_type=EventType.USER_MESSAGE
            )
            assert events == []
    finally:
        await host.close()


async def test_restart_replays_pending_presence_without_skipping_away(
    tmp_path: Path,
    resources: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx.app_context import _Host

    host = await _Host.open(Config(), tmp_path)
    now = time.time() - 2
    await host.observe(
        {
            "sample_id": "away",
            "presence": "away",
            "sampled_at": now,
            "idle_seconds": 400,
        }
    )
    await host.flush_presence("nyx")
    await host.observe(
        {
            "sample_id": "online",
            "presence": "online",
            "sampled_at": now + 1,
            "idle_seconds": 0,
        }
    )
    await host.close()
    restored = await _Host.open(Config(), tmp_path)
    try:
        events = await restored.apps["nyx"].bus.list_events(
            event_type=EventType.OBSERVATION_STATE
        )
        assert [e.content["transition"] for e in reversed(events)] == [
            "initial",
            "returned",
        ]
        assert restored.apps["nyx"].last_presence == "online"
    finally:
        await restored.close()


async def test_presence_checkpoint_failure_rolls_back_snapshot_and_counter(
    tmp_path: Path,
    resources: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx.app_context import _Host

    host = await _Host.open(Config(), tmp_path)
    try:
        app = host.apps["nyx"]
        assert app.database is not None
        async with app.database.transaction() as conn:
            await conn.execute(
                "CREATE TRIGGER reject_presence BEFORE INSERT ON "
                "presence_checkpoint BEGIN SELECT RAISE(ABORT, 'checkpoint "
                "failed'); END"
            )
        count = app.bus.persisted_count
        with pytest.raises(Exception, match="checkpoint failed"):
            await app.apply_presence(
                1,
                {
                    "presence": "away",
                    "sampled_at": time.time() - 1,
                    "idle_seconds": 400,
                },
            )
        assert app.bus.persisted_count == count
        assert app.presence_initialized is False
        assert await app.bus.list_events(event_type=EventType.OBSERVATION_STATE) == []
    finally:
        await host.close()


async def test_host_presence_does_not_overwrite_newer_chat_evidence(
    tmp_path: Path,
    resources: None,
) -> None:
    from nyx.app_context import _Host

    host = await _Host.open(Config(), tmp_path)
    try:
        await host.online_before_chat("nyx", "chat")
        await host.observe(
            {
                "sample_id": "late-away",
                "presence": "away",
                "sampled_at": time.time() - 10,
                "idle_seconds": 400,
            }
        )
        await host.flush_presence("nyx", force=True)
        assert host.apps["nyx"].last_presence == "online"
        from tests.test_character_cards import package

        await host.register(package())
        await host.start("alice", run=False)
        assert host.apps["alice"].last_presence == "online"
    finally:
        await host.close()


async def test_private_title_checks_revision_after_waiting_for_role_lock(
    tmp_path: Path,
    resources: None,
) -> None:
    from nyx.app_context import _Host
    from tests.test_character_cards import package

    host = await _Host.open(Config(), tmp_path)
    try:
        await host.register(package())
        await host.start("alice", run=False)
        app = host.apps["nyx"]
        await app.presence_lock.acquire()
        await host.observe(
            {
                "sample_id": "title",
                "presence": "online",
                "sampled_at": time.time() - 1,
                "idle_seconds": 0,
                "window_title": "private",
                "character_id": "nyx",
                "selection_revision": host.revision,
            }
        )
        flush = asyncio.create_task(host.flush_presence("nyx"))
        await asyncio.sleep(0)
        await host.select("alice")
        await host.select("nyx")
        app.presence_lock.release()
        await flush
        assert app.last_window_title == ""
    finally:
        await host.close()


async def test_presence_loop_does_not_wait_for_another_recipient(
    tmp_path: Path,
    resources: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx.app_context import _Host
    from nyx.runtime import host_presence_loop
    from tests.test_character_cards import package

    host = await _Host.open(Config(), tmp_path)
    release = asyncio.Event()
    entered = asyncio.Event()
    original = host.flush_presence

    async def flush(identity: str, *, force: bool = False) -> bool:
        if identity == "alice":
            entered.set()
            await release.wait()
        return await original(identity, force=force)

    loop: asyncio.Task[None] | None = None
    try:
        await host.register(package())
        await host.start("alice", run=False)
        monkeypatch.setattr(host, "flush_presence", flush)
        loop = asyncio.create_task(host_presence_loop(host))
        await entered.wait()
        await host.observe(
            {
                "sample_id": "next",
                "presence": "online",
                "sampled_at": time.time() - 1,
                "idle_seconds": 0,
            }
        )
        async with asyncio.timeout(2):
            while host.apps["nyx"].last_presence != "online":
                await asyncio.sleep(0.01)
        assert not release.is_set()
    finally:
        release.set()
        if loop is not None:
            loop.cancel()
            await asyncio.gather(loop, return_exceptions=True)
        await host.close()


async def test_unload_waits_for_runner_started_by_admitted_request(
    tmp_path: Path,
    resources: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx.app_context import _Host
    from tests.test_character_cards import package

    host = await _Host.open(Config(), tmp_path)
    release = asyncio.Event()
    entered = asyncio.Event()
    app = None
    try:
        await host.register(package())
        await host.start("alice", run=False)
        app = host.apps["alice"]
        original = app.activity._starter._get_state

        async def state() -> CurrentState:
            entered.set()
            await release.wait()
            return await original()

        async def execute(activity: Activity) -> None:
            await asyncio.Event().wait()

        monkeypatch.setattr(app.activity._starter, "_get_state", state)
        monkeypatch.setattr(app.activity._starter, "_execute", execute)
        request = asyncio.create_task(app.activity._maybe_start_activity())
        host.requests["alice"].add(request)
        await entered.wait()
        stop = asyncio.create_task(host.unload("alice"))
        while not app.activity._quiescing:
            await asyncio.sleep(0)
        release.set()
        await request
        await stop
        assert app.activity._task is None or app.activity._task.done()
        assert host.records["alice"]["status"] == "ARCHIVED"
    finally:
        release.set()
        if app is not None and app.activity._task is not None:
            app.activity._task.cancel()
            await asyncio.gather(app.activity._task, return_exceptions=True)
        await host.close()


async def test_open_failure_closes_already_loaded_role(
    tmp_path: Path,
    resources: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx import app_context
    from tests.test_character_cards import package

    host = await app_context._Host.open(Config(), tmp_path)
    await host.register(package())
    await host.close()
    original = app_context.build_app_context
    constructed: list[app_context._App] = []

    async def build(*args: Any, **kwargs: Any) -> app_context._App:
        if kwargs["character"]["id"] == "alice":
            raise asyncio.CancelledError
        app = await original(*args, **kwargs)
        constructed.append(app)
        return app

    monkeypatch.setattr(app_context, "build_app_context", build)
    try:
        with pytest.raises(asyncio.CancelledError):
            await app_context._Host.open(Config(), tmp_path)
        assert constructed[0].database is not None
        assert constructed[0].database._closed
    finally:
        for app in constructed:
            assert app.database is not None
            await app.database.close()


async def test_cancelled_install_keeps_slot_until_disk_finishes(
    tmp_path: Path,
    resources: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    from nyx.app_context import _Host
    from tests.test_character_cards import package

    host = await _Host.open(Config(), tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = Path.write_bytes

    def write(path: Path, data: bytes) -> int:
        if path.name == "canon.md":
            entered.set()
            release.wait(3)
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", write)
    task = asyncio.create_task(host.register(package()))
    stop: asyncio.Task[None] | None = None
    try:
        await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        stop = asyncio.create_task(host.unload("alice"))
        await asyncio.sleep(0.05)
        assert not task.done()
        assert not stop.done()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        if stop is not None:
            await stop
        await host.close()
    assert (tmp_path / "data/characters/alice/card/canon.md").exists()


async def test_restore_syncs_presence_without_rewriting_historical_return(
    tmp_path: Path,
    resources: None,
) -> None:
    from nyx.app_context import _Host
    from tests.test_character_cards import package

    host = await _Host.open(Config(), tmp_path)
    try:
        await host.register(package())
        await host.start("alice", run=False)
        now = time.time() - 2
        for sample, presence in enumerate(("away", "online")):
            await host.observe(
                {
                    "sample_id": str(sample),
                    "presence": presence,
                    "sampled_at": now + sample,
                    "idle_seconds": 0,
                }
            )
            await host.flush_presence("alice")
        before = await host.apps["alice"].bus.list_events(
            event_type=EventType.OBSERVATION_STATE
        )
        await host.unload("alice")
        await host.restore("alice", run=False)
        assert host.records["alice"]["status"] == "RUNNING"
        app = host.apps["alice"]
        assert app.last_presence == "online" and app.pending_return is None
        assert (
            await app.bus.list_events(event_type=EventType.OBSERVATION_STATE) == before
        )
    finally:
        await host.close()
