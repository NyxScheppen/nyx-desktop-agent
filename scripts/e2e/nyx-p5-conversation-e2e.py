"""Conversation steering through the real host, HTTP/SSE, disk DB and saver.

Models and external actions are controlled fixtures. --serve keeps the isolated
host on port 8000; type release, counts, ask, fallback or quit in its terminal.
--postgres uses NYX_DATABASE_URL with fresh, uniquely prefixed test schemas.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from unittest.mock import patch
from uuid import uuid4

import httpx
import uvicorn
from websockets.asyncio.server import serve as websocket_server

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from nyx import db
from nyx.api.routes import build_host_app
from nyx.app_context import _Host
from nyx.config import Config
from nyx.enums import EventType, Source
from nyx.types import Event, LLMOutput, Tool
from nyx.enums import InteractionKind


class Model:
    def __init__(self, mode="answer", pause=None, folder=None):
        self.mode, self.pause, self.folder = mode, pause, folder
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.calls, self.effects = [], []

    def fork(self):
        return self

    def can_initiate(self):
        return True

    async def gate(self, phase):
        if self.pause != phase:
            return
        self.pause = None
        self.entered.set()
        if self.folder:
            (self.folder / f"paused-{phase}").write_text("ready", encoding="utf-8")
        await self.release.wait()
        self.release.clear()

    async def complete(self, messages, *, module, output_type, correlation_id, **kwargs):
        self.calls.append((output_type, correlation_id, messages))
        await self.gate(output_type)
        calls = []
        if output_type == "reason":
            action = "tools" if self.mode in ("effect", "retry", "revoke") and "[工作摘要" not in messages[-1]["content"] else "answer"
            content = {"summary": "复核当前输入及已发生的动作。", "next_action": action}
        elif output_type == "plan":
            content = {"goal": "受控测试", "steps": [{"text": "执行测试动作", "expected_tool": None, "done_condition": "取得回执"}]}
        elif output_type == "tool":
            previous = sum(kind == "tool" and cid == correlation_id for kind, cid, _ in self.calls)
            if previous == 1 and self.mode == "effect":
                calls = [{"name": "fixture_effect", "args": {"number": n}} for n in (1, 2)]
            elif self.mode == "retry":
                calls = [{"name": "local_search", "args": {"query": "same"}}]
            elif self.mode == "revoke":
                calls = [{"name": "knowledge_search", "args": {"query": "old"}}]
            content = {}
        elif output_type == "reply":
            if self.mode == "fallback":
                raise ValueError("injected model failure")
            content = {"think": "核对输入与执行回执。", "speak": "还想继续聊吗？" if self.mode == "ask" else "统一回复，保留已确认的工具执行事实。", "needs_continuation": False}
        else:
            content = {"importance": "normal", "summary": "fixture", "topics": [], "entities": [], "facts": [], "is_correction": False, "evidence_event_ids": []}
        return LLMOutput(module=module, type=output_type, model="controlled-p5",
                         content=json.dumps(content, ensure_ascii=False), correlation_id=correlation_id,
                         tool_calls=calls, call_id=str(uuid4()), prompt_messages=messages)


async def until(check, timeout=15):
    async def wait():
        while True:
            value = await check()
            if value:
                return value
            await asyncio.sleep(.02)
    return await asyncio.wait_for(wait(), timeout)


@asynccontextmanager
async def fixture(folder, model, *, fast=False, port=0, postgres=False, crash=None, onebot_url=None, run_bus=True):
    folder.mkdir(parents=True, exist_ok=True)
    config = Config()
    config.expression.slow_threshold = 99 if fast else -1
    config.expression.slow_max_rounds = 1
    if onebot_url:
        config.onebot.enabled = True
        config.onebot.self_id, config.onebot.owner_id = 123, 456
        config.onebot.character_id = "nyx"
        config.onebot.ws_url = onebot_url
    original_connect = db.connect
    prefix = ""
    if postgres:
        prefix_file = folder / "schema-prefix"
        if not prefix_file.exists():
            prefix_file.write_text("p5_" + uuid4().hex[:12], encoding="utf-8")
        prefix = prefix_file.read_text(encoding="utf-8")

    async def connect(path=None, *, migrations=None, schema="public"):
        return await original_connect(path, migrations=migrations,
                                      schema=f"{prefix}_{schema}" if prefix else schema)

    environment = {"NYX_DB": str(folder / "role.db"), "NYX_AUTH_TOKEN": ""}
    if onebot_url:
        environment["ONEBOT_ACCESS_TOKEN"] = "fixture-token"
    if not postgres:
        environment["NYX_DATABASE_URL"] = ""
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(64)
    except OSError:
        sock.close()
        raise
    with patch.dict(os.environ, environment), patch("nyx.app_context.connect", connect), patch("nyx.app_context.LlmClient.from_config", return_value=model), patch("nyx.app_context.build_embed", return_value=None), patch("nyx.events.bus._DELIVERY_LEASE_SECONDS", .25):
        os.environ.pop("NYX_PUBLIC_ORIGIN", None)
        try:
            host = await _Host.open(config, folder)
        except BaseException:
            sock.close()
            raise
        app = host.apps["nyx"]
        database = app.database
        tools = app.expression._tools

        async def effect(number):
            count_path = folder / "effects.txt"
            with count_path.open("a", encoding="utf-8") as stream:
                stream.write(f"{number}\n")
                stream.flush()
                os.fsync(stream.fileno())
            model.effects.append(number)
            await model.gate("effect")
            return {"created": f"T{number}"}

        tools.register(Tool("fixture_effect", "controlled test action", {
            "type": "object", "properties": {"number": {"type": "integer"}}, "required": ["number"]}, effect))
        if crash == "receipt":
            finish = tools.finish_execution

            async def paused_receipt(log_id, result, error):
                await finish(log_id, result, error)
                await model.gate("receipt")
            tools.finish_execution = paused_receipt
        if crash == "archive":
            append = app.memory.append_conversation_turn
            archived = 0

            async def paused_archive(identity, content, timestamp=None):
                nonlocal archived
                await append(identity, content, timestamp)
                archived += 1
                if archived == 2:
                    await model.gate("archive")
            app.memory.append_conversation_turn = paused_archive
        bus_task = asyncio.create_task(app.bus.run()) if run_bus else None
        if app.transport:
            app.transport_task = asyncio.create_task(app.transport.run())
        selected_port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(build_host_app(host, launch_nonce=None), log_level="critical", lifespan="off"))
        server_task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async def started():
                return server.started
            await until(started)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{selected_port}", headers={"Host": "127.0.0.1:8000", "X-Nyx-Selection-Revision": str(host.revision)}, timeout=20) as client:
                yield app, client, selected_port
        finally:
            model.release.set()
            server.should_exit = True
            await asyncio.wait_for(server_task, 10)
            await host.close()
            if bus_task:
                await asyncio.wait_for(bus_task, 10)
            sock.close()


async def send(client, text, discussion=False, **extra):
    response = await client.post("/api/characters/nyx/chat", json={"message": text, "discussion": discussion, **extra})
    assert response.status_code == 200, response.text
    return response.json()["event_id"]


@asynccontextmanager
async def stream(client):
    async with client.stream("GET", "/api/characters/nyx/events") as response:
        assert response.status_code == 200
        queue = asyncio.Queue()

        async def consume():
            kind, cursor = "", ""
            async for line in response.aiter_lines():
                if line.startswith("id: "):
                    cursor = line[4:]
                elif line.startswith("event: "):
                    kind = line[7:]
                elif line.startswith("data: "):
                    await queue.put((kind, json.loads(line[6:]), cursor))
        reader = asyncio.create_task(consume())
        try:
            yield queue
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


async def terminal(queue, root):
    async def wait():
        while True:
            kind, value, cursor = await queue.get()
            if kind in ("speak", "ask") and value["correlation_id"] == root:
                assert cursor
                return value
    return await asyncio.wait_for(wait(), 15)


async def settled(app, ids):
    async def ready():
        async with app.database.lock:
            row = await (await app.database.conn.execute("SELECT status FROM event_delivery WHERE event_id=? AND consumer_id='expression.user_message'", (ids[0],))).fetchone()
            return row and row["status"] == "succeeded"
    await until(ready)
    outputs = await app.bus.list_events(correlation_id=ids[0])
    outputs = [e for e in outputs if e.type in (EventType.SPEAK, EventType.ASK)]
    assert len(outputs) == 1 and outputs[0].content["reply_to_event_ids"] == ids
    async with app.database.lock:
        archives = await (await app.database.conn.execute("SELECT turn_id FROM conversation_turn_archive")).fetchall()
        turns = [row["turn_id"] for row in archives]
        segments = await (await app.database.conn.execute("SELECT raw_event_ids FROM conversation_segment")).fetchall()
    raw = [identity for row in segments for identity in json.loads(row["raw_event_ids"])]
    for identity in [*[f"{i}:user" for i in ids], f"{ids[0]}:nyx"]:
        assert turns.count(identity) == raw.count(identity) == 1
    for identity in ids:
        assert await app.bus.has_effect(identity, "expression.user_message")
    return outputs[0]


def report(name, ids, *, tools=0, backend="sqlite", **extra):
    print(json.dumps({"scenario": name, "status": "PASS", "backend": backend, "test_lease": .25,
                      "inputs_in_commit_order": ids, "coverage": ids, "tool_calls": tools, **extra}), flush=True)


async def ordinary(folder, name, *, phase="reason", mode="answer", texts=None, fast=False, flags=None, postgres=False, lengths=None):
    model = Model(mode, phase)
    texts = texts or ["周末去哪玩？", "预算三百", "腿疼，尽量少走路"]
    flags = flags or [False] * len(texts)
    async with fixture(folder, model, fast=fast, postgres=postgres) as (app, client, _):
        async with stream(client) as queue:
            ids = [await send(client, texts[0], flags[0])]
            await asyncio.wait_for(model.entered.wait(), 10)
            for text, flag in zip(texts[1:], flags[1:]):
                ids.append(await send(client, text, flag))
            pending = (await client.get("/api/characters/nyx/chat/pending")).json()
            assert set(ids) <= {item["event_id"] for item in pending["items"]}
            model.release.set()
            sizes = lengths or ([1] * len(ids) if flags != [flags[0]] * len(flags) else [min(32, len(ids)), *([1] if len(ids) > 32 else [])])
            groups, offset = [], 0
            for size in sizes:
                groups.append(ids[offset:offset + size])
                offset += size
            for group in groups:
                value = await terminal(queue, group[0])
                assert value["reply_complete"] is True and value["reply_to_event_ids"] == group
                await settled(app, group)
            assert (await client.get("/api/characters/nyx/chat/pending")).json()["items"] == []
            if name == "E01":
                replies = [messages for kind, _, messages in model.calls if kind == "reply"]
                assert all(text in replies[-1][-1]["content"] for text in texts)
                assert "需复核" in replies[-1][-1]["content"]
            if mode == "fallback":
                assert sum(kind == "reply" for kind, _, _ in model.calls) == 1
                assert value.get("response_kind") == "fallback"
            if mode == "effect":
                assert model.effects == [1]
            if mode == "revoke":
                assert not model.effects and not any(kind == "tool" for kind, _, _ in model.calls)
            if len(texts) == 1 and fast:
                assert sum(kind == "reply" for kind, _, _ in model.calls) == 1
            assert sum(kind == "reason" for kind, _, _ in model.calls) <= 3 * len(groups)
            report(name, ids, tools=len(model.effects), backend="postgres" if postgres else "sqlite", groups=groups)


async def final_order(folder, postgres):
    model = Model("answer", "reply")
    async with fixture(folder, model, fast=True, postgres=postgres) as (app, client, _):
        async with stream(client) as queue:
            first = await send(client, "第一条")
            await model.entered.wait()
            model.release.set()
            await terminal(queue, first)
            await settled(app, [first])
            second = await send(client, "终局已提交后的新消息")
            await terminal(queue, second)
            await settled(app, [second])
            report("E06-terminal-first", [first, second], groups=[[first], [second]])


async def input_barriers(folder, postgres):
    model = Model("answer", "reply")
    async with fixture(folder, model, fast=True, postgres=postgres) as (app, client, _):
        first_question = await app.expression.register_question(
            "第一个问题？", InteractionKind.CHAT_ASK, "fixture1", "fixture1")
        second_question = await app.expression.register_question(
            "第二个问题？", InteractionKind.CHAT_ASK, "fixture2", "fixture2")
        anchor = Event(str(uuid4()), time.time(), Source.INTERNAL, EventType.SPEAK,
                       {"content": "用于批注的真实原文"}, "annotation-anchor")
        await app.bus.publish(anchor)
        async with stream(client) as queue:
            ids = [await send(client, "回答第一个", reply_to=first_question)]
            await model.entered.wait()
            ids.append(await send(client, "同一回答的补充", reply_to=first_question))
            ids.append(await send(client, "回答第二个", reply_to=second_question))
            ids.append(await send(client, "批注", annotation={"reply_event_id": anchor.id,
                "quote": "真实原文", "comment": "请说明依据"}))
            ids.append(await send(client, "批注后续"))
            model.release.set()
            groups = [ids[:2], ids[2:3], ids[3:4], ids[4:]]
            for group in groups:
                await terminal(queue, group[0])
                await settled(app, group)
            async def recorded():
                return await app.memory.has_annotation_feedback(anchor.id, "请说明依据")
            await until(recorded)
            report("E09-reply-annotation", ids, groups=groups)


async def mixed_entry(folder, postgres):
    model = Model("answer", "reason")
    push, logged_in = asyncio.Queue(), asyncio.Event()
    sent = []
    async def peer(ws):
        async def push_messages():
            while True:
                await ws.send(json.dumps(await push.get()))
        writer = asyncio.create_task(push_messages())
        try:
            async for raw in ws:
                value = json.loads(raw)
                if value["action"] == "get_login_info":
                    data = {"user_id": 123}
                    logged_in.set()
                else:
                    sent.append(value["params"]["message"][0]["data"]["text"])
                    data = {"message_id": len(sent)}
                await ws.send(json.dumps({"status": "ok", "retcode": 0, "data": data, "echo": value["echo"]}))
        finally:
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)
    async with websocket_server(peer, "127.0.0.1", 0) as peer_server:
        port = peer_server.sockets[0].getsockname()[1]
        async with fixture(folder, model, postgres=postgres, onebot_url=f"ws://127.0.0.1:{port}") as (app, client, _):
            await asyncio.wait_for(logged_in.wait(), 10)
            async with stream(client) as queue:
                first = await send(client, "今天的安排", None)
                await model.entered.wait()
                yesterday = int(time.time()) - 86400
                payload = {"post_type": "message", "message_type": "private", "self_id": 123,
                           "user_id": 456, "message_id": 7, "time": yesterday, "message": "昨天时间的补充"}
                await push.put(payload)
                await push.put(payload)
                async def admitted():
                    return await app.bus.get_event("onebot:123:7")
                event = await until(admitted)
                assert event.timestamp == yesterday
                third = await send(client, "再补一句", None)
                from tests.test_character_cards import package
                host = app.host
                await host.register(package())
                await host.start("alice", run=False)
                other = Event(str(uuid4()), time.time(), Source.EXTERNAL, EventType.USER_MESSAGE,
                              {"message": "另一个角色", "discussion": None}, str(uuid4()))
                await host.apps["alice"].bus.publish(other)
                model.release.set()
                await terminal(queue, first)
                await settled(app, [first, event.id, third])
                assert len([e for e in await app.bus.list_events(event_type=EventType.USER_MESSAGE) if e.id == event.id]) == 1
                assert await app.bus.get_event(other.id) is None
                async def forwarded():
                    return len(sent) >= 2
                try:
                    await until(forwarded)
                except TimeoutError:
                    raise AssertionError((sent, app.transport_task.done())) from None
                assert sent == ["💭 内心话\n核对输入与执行回执。", "统一回复，保留已确认的工具执行事实。"]
                report("E10-mixed-onebot", [first, event.id, third], source_time=yesterday, outbound=sent)
                model.pause, model.entered = "reply", asyncio.Event()
                payload.update(message_id=8, message="长" * 24001)
                await push.put(payload)
                await model.entered.wait()
                supplement = await send(client, "超长根后的下一条", None)
                model.release.set()
                await terminal(queue, "onebot:123:8")
                await settled(app, ["onebot:123:8"])
                await terminal(queue, supplement)
                await settled(app, [supplement])
                report("E10-oversized-root", ["onebot:123:8", supplement], groups=[["onebot:123:8"], [supplement]])


async def pending_recovery(folder, postgres):
    model = Model("answer", "reply")
    async with fixture(folder, model, fast=True, postgres=postgres) as (app, client, _):
        first = await send(client, "等待断线重连")
        await model.entered.wait()
        snapshot = (await client.get("/api/characters/nyx/chat/pending")).json()
        assert [item["event_id"] for item in snapshot["items"]] == [first]
        model.release.set()
        await settled(app, [first])
        async with stream(client) as queue:
            assert (await terminal(queue, first))["reply_to_event_ids"] == [first]
        assert (await client.get("/api/characters/nyx/chat/pending")).json()["items"] == []
        report("E11-reconnect", [first])
    async with fixture(folder / "truncation", Model(), run_bus=False, postgres=postgres) as (app, client, _):
        events = [Event(str(uuid4()), float(i), Source.EXTERNAL, EventType.USER_MESSAGE,
                        {"message": "fixture"}, str(uuid4())) for i in range(1002)]
        await app.bus.publish_many(events)
        async with app.database.transaction() as conn:
            await conn.execute("UPDATE event_delivery SET status='dead_letter' WHERE event_id=? AND consumer_id='expression.user_message'", (events[0].id,))
        snapshot = (await client.get("/api/characters/nyx/chat/pending")).json()
        assert snapshot["truncated"] and len(snapshot["items"]) == 1000
        assert snapshot["items"][0]["event_id"] == events[1].id
        async with app.database.transaction() as conn:
            await conn.execute("UPDATE event_delivery SET status='succeeded' WHERE event_id<>? AND consumer_id='expression.user_message'", (events[0].id,))
        snapshot = (await client.get("/api/characters/nyx/chat/pending")).json()
        assert not snapshot["truncated"] and snapshot["items"][0]["status"] == "dead_letter"
        report("E11-dead-letter-truncation", [events[0].id], snapshot_limit=1000)


async def rollback(folder, point, postgres):
    model = Model("ask" if point == "attempt" else "answer", "reply")
    async with fixture(folder, model, fast=True, postgres=postgres) as (app, client, _):
        fired = False
        if point == "effect":
            owner, method = app.bus, "try_mark_effect_in_transaction"
        elif point == "attempt":
            owner, method = app.expression._interaction_store, "create"
        elif point == "announce":
            owner, method = app.bus, "announce_committed"
        else:
            owner, method = app.bus, "append_in_transaction"
        original = getattr(owner, method)

        async def fail_once(*args, **kwargs):
            nonlocal fired
            result = await original(*args, **kwargs)
            if not fired:
                fired = True
                raise RuntimeError("injected boundary failure")
            return result
        async with stream(client) as queue:
            ids = [await send(client, "第一条" )]
            await asyncio.wait_for(model.entered.wait(), 10)
            ids.append(await send(client, "补充"))
            setattr(owner, method, fail_once)
            model.release.set()
            await settled(app, ids)
            assert fired
            reply_calls = sum(kind == "reply" for kind, _, _ in model.calls)
            if point == "announce":
                assert reply_calls == 2  # First draft invalidated, committed generation never repeats.
            assert (await client.get("/api/characters/nyx/chat/pending")).json()["items"] == []
            report("E07-" + point, ids)


async def crash_child(folder, point, postgres):
    model = Model("answer" if point == "archive" else "effect", "reason", folder)
    loop = asyncio.get_running_loop()
    def commands():
        for line in sys.stdin:
            if line.strip() == "release":
                loop.call_soon_threadsafe(model.release.set)
    threading.Thread(target=commands, daemon=True).start()
    async with fixture(folder, model, postgres=postgres, crash=point) as (_, _, port):
        (folder / "ready.json").write_text(json.dumps({"port": port}), encoding="utf-8")
        await model.entered.wait()
        model.entered.clear()
        model.release.clear()
        model.pause = "archive" if point == "archive" else "receipt" if point == "receipt" else "effect"
        await asyncio.Event().wait()


async def crash_restart(folder, point, postgres):
    folder.mkdir(parents=True)
    command = [sys.executable, str(Path(__file__).resolve()), "--child", point, "--root", str(folder)]
    if postgres:
        command.append("--postgres")
    with (folder / "child.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE, stdout=log, stderr=log, text=True)
        try:
            async def ready():
                if child.poll() is not None:
                    raise AssertionError((folder / "child.log").read_text(encoding="utf-8"))
                return (folder / "ready.json").exists()
            try:
                await until(ready, timeout=30)
            except TimeoutError as error:
                raise AssertionError("child startup timed out: " + (folder / "child.log").read_text(encoding="utf-8")) from error
            port = json.loads((folder / "ready.json").read_text(encoding="utf-8"))["port"]
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers={"Host": "127.0.0.1:8000", "X-Nyx-Selection-Revision": "0"}) as client:
                ids = [await send(client, "创建受控任务好吗？")]
                async def paused_reason():
                    return (folder / "paused-reason").exists()
                await until(paused_reason)
                if point == "archive":
                    ids.extend([await send(client, "预算三百"), await send(client, "尽量少走路")])
                child.stdin.write("release\n")
                child.stdin.flush()
                async def paused_commit():
                    return (folder / f"paused-{point}").exists()
                await until(paused_commit)
                child.kill()
                await asyncio.to_thread(child.wait, 5)
            model = Model()
            async with fixture(folder, model, postgres=postgres) as (app, client, _):
                if point != "archive":
                    ids.append(await send(client, "取消整个任务，不再创建"))
                output = await settled(app, ids)
                if point == "archive":
                    assert not any(kind == "reply" for kind, _, _ in model.calls)
                else:
                    assert (folder / "effects.txt").read_text(encoding="utf-8").splitlines() == ["1"]
                    prompts = "\n".join(str(messages) for kind, _, messages in model.calls if kind == "reply")
                    assert ("T1" if point == "receipt" else "uncertain") in prompts, prompts
                assert (await client.get("/api/characters/nyx/chat/pending")).json()["items"] == []
                report("E08" if point == "archive" else "E05-" + point, ids, tools=int(point != "archive"), killed_at=point, terminal=output.id)
        finally:
            if child.poll() is None:
                child.kill()
                await asyncio.to_thread(child.wait, 5)


async def direct_and_legacy(folder, postgres):
    if not postgres:
        folder.mkdir(parents=True)
        database = await db.connect(str(folder / "role.db"), migrations=[m for m in db._MIGRATIONS if m[0] <= 52])
        try:
            await database.conn.execute("INSERT INTO conversation_segment (id, started_at, ended_at, raw_event_ids, raw_content, summary, topics, active) VALUES ('old',1,2,'[\"old:user\"]','old','old','[]',0)")
            await database.conn.commit()
        finally:
            await database.close()
    model = Model()
    async with fixture(folder, model, fast=True, postgres=postgres) as (app, client, _):
        await app.expression.reply("无根事件的内部调用", "direct-fixture", discussion=False)
        outputs = await app.bus.list_events(correlation_id="direct-fixture")
        output = next(e for e in outputs if e.type is EventType.SPEAK)
        assert "reply_complete" not in output.content and "reply_to_event_ids" not in output.content
        assert await app.bus.get_event("direct-fixture") is None
        await app.memory.append_conversation_turn("direct-fixture:user", "无根事件的内部调用", 1)
        async with app.database.lock:
            rows = await (await app.database.conn.execute("SELECT turn_id FROM conversation_turn_archive")).fetchall()
        assert [r["turn_id"] for r in rows].count("direct-fixture:user") == 1
        if not postgres:
            assert "old:user" in [r["turn_id"] for r in rows]
        report("E12", [], backend="postgres" if postgres else "sqlite")


async def archive_dead_letter(folder, postgres):
    model = Model("answer", "reply")
    async with fixture(folder, model, fast=True, postgres=postgres) as (app, client, _):
        async def fail_archive(*args, **kwargs):
            raise OSError("injected archive failure")
        app.memory.append_conversation_turn = fail_archive
        identity = await send(client, "归档失败五次")
        await model.entered.wait()
        model.release.set()
        async def failed():
            async with app.database.lock:
                return await (await app.database.conn.execute(
                    "SELECT status, attempts FROM event_delivery WHERE event_id=? AND consumer_id='expression.user_message'", (identity,))).fetchone()
        async def dead():
            row = await failed()
            return row if row["status"] == "dead_letter" else None
        row = await until(dead, timeout=25)
        assert row["attempts"] == 5
        assert not await app.bus.has_effect(identity, "expression.user_message")
        assert len([e for e in await app.bus.list_events(correlation_id=identity) if e.type is EventType.SPEAK]) == 1
        assert sum(kind == "reply" for kind, _, _ in model.calls) == 1
        assert (await client.get("/api/characters/nyx/chat/pending")).json()["items"] == []
        report("E08-archive-dead-letter", [identity], attempts=5, delivery="dead_letter", pending=[])


async def serve(folder):
    model = Model("answer", "reason", folder)
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    def commands():
        for line in sys.stdin:
            command = line.strip()
            if command == "release":
                loop.call_soon_threadsafe(model.release.set)
            elif command in ("ask", "fallback"):
                model.mode = command
            elif command == "counts":
                print(json.dumps({"model_calls": len(model.calls), "tools": model.effects}), flush=True)
            elif command == "quit":
                loop.call_soon_threadsafe(stop.set)
    threading.Thread(target=commands, daemon=True).start()
    async with fixture(folder, model, port=8000) as (_, _, port):
        print(json.dumps({"status": "READY", "port": port, "isolated_data": str(folder), "commands": "release / counts / ask / fallback / quit"}), flush=True)
        await stop.wait()


async def main(args):
    if args.postgres and not os.environ.get("NYX_DATABASE_URL"):
        raise ValueError("--postgres requires the dedicated test NYX_DATABASE_URL")
    if args.child:
        await crash_child(args.root, args.child, args.postgres)
        return
    with tempfile.TemporaryDirectory(prefix="nyx-p5-") as temporary:
        folder = args.root or Path(temporary)
        try:
            if args.serve:
                await serve(folder)
                return
            await ordinary(folder / "E01", "E01", postgres=args.postgres)
            await ordinary(folder / "E02-single", "E02-single", phase="reply", fast=True, texts=["你好"], postgres=args.postgres)
            for mode in ("answer", "ask", "fallback"):
                await ordinary(folder / ("E02-" + mode), "E02-" + mode, phase="reply", mode=mode, fast=True, texts=["你好", "算了一下，预算三百"], postgres=args.postgres)
            await ordinary(folder / "E03", "E03", mode="revoke", phase="plan", texts=["分析一下为什么会这样", "取消刚才的任务，只陪我聊聊"], flags=[None, None], postgres=args.postgres)
            await ordinary(folder / "E04", "E04", mode="effect", phase="effect", texts=["创建一个任务好吗？", "取消整个任务，别再创建了"], postgres=args.postgres)
            for point in ("receipt", "effect", "archive"):
                await crash_restart(folder / ("crash-" + point), point, args.postgres)
            for point in ("text", "attempt", "effect", "announce"):
                await rollback(folder / ("E07-" + point), point, args.postgres)
            await ordinary(folder / "E06-input-first", "E06-input-first", phase="reply", fast=True, texts=["第一条", "提交前补充"], postgres=args.postgres)
            await final_order(folder / "E06-terminal-first", args.postgres)
            await archive_dead_letter(folder / "E08-dead-letter", args.postgres)
            await ordinary(folder / "E09-barrier", "E09-barrier", phase="reply", texts=["第一条", "第二条", "第三条"], flags=[None, True, None], postgres=args.postgres)
            await ordinary(folder / "E09-cap", "E09-cap", phase="reply", fast=True, texts=[f"预算第{i}条" for i in range(33)], postgres=args.postgres)
            await input_barriers(folder / "E09-references", args.postgres)
            for size in (23999, 24000, 24001):
                await ordinary(folder / f"E09-chars-{size}", f"E09-chars-{size}", phase="reply", fast=True,
                               texts=["甲" * 12000, "乙" * (size - 12001), "丙"],
                               lengths=[3] if size <= 24000 else [2, 1], postgres=args.postgres)
            await mixed_entry(folder / "E10", args.postgres)
            await pending_recovery(folder / "E11", args.postgres)
            await direct_and_legacy(folder / "E12", args.postgres)
            print(json.dumps({"result": "PASS", "real_browser": "NOT_RUN", "real_model": "NOT_RUN", "postgres": "PASS" if args.postgres else "NOT_RUN"}), flush=True)
        except BaseException:
            if args.root is None:
                diagnostic = Path(tempfile.mkdtemp(prefix="nyx-p5-failure-"))
                shutil.copytree(folder, diagnostic, dirs_exist_ok=True)
                print(json.dumps({"result": "FAIL", "diagnostics": str(diagnostic)}), flush=True)
            raise
        finally:
            if args.postgres:
                from psycopg import AsyncConnection, sql
                prefixes = {path.read_text(encoding="utf-8") for path in folder.rglob("schema-prefix")}
                async with await AsyncConnection.connect(db.resolve_postgres_url(), autocommit=True) as connection:
                    for prefix in prefixes:
                        assert len(prefix) == 15 and prefix.startswith("p5_") and all(c in "0123456789abcdef" for c in prefix[3:])
                        names = await (await connection.execute(
                            "SELECT nspname FROM pg_namespace WHERE left(nspname, %s)=%s",
                            (len(prefix) + 1, prefix + "_"))).fetchall()
                        for (name,) in names:
                            await connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--postgres", action="store_true")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--child", choices=("receipt", "effect", "archive"))
    asyncio.run(main(parser.parse_args()))
