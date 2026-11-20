"""Real stores and reply graph; controlled model for races, never teaching quality."""
# pyright: reportPrivateUsage=false

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from nyx.api.routes import build_host_app
from nyx.app_context import _Host
from nyx.config import Config, LlmConfig
from nyx.llm.client import LlmClient
from nyx.reading.segmenter import Segment
from nyx.reading.sessions import ReadingSessions, StudyConflictError
from nyx.reading.store import ProgressConflictError
from nyx.types import LlmMessage, LLMOutput


class StudyModel(LlmClient):
    def __init__(self) -> None:
        super().__init__(None, "controlled-study")
        self.calls: list[tuple[str, str]] = []
        self.block: str | None = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    def fork(self) -> LlmClient:
        return self

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMOutput:
        self.calls.append((output_type, "\n".join(m["content"] for m in messages)))
        if self.block == output_type:
            self.started.set()
            await self.release.wait()
        if output_type == "study_scope":
            value: Any = {"scope": "", "start": None, "end": None, "feedback": False}
        elif output_type == "reason":
            value = {"summary": "依据当前书籍回答", "next_action": "answer"}
        elif output_type == "reply":
            value = {
                "think": "",
                "speak": "暴雨使第一次施工失败。[第2段](nyx-book:2)",
                "needs_continuation": False,
            }
        elif output_type == "study_material":
            source = json.loads(messages[-1]["content"])[0]
            value = {
                "summary": source["text"],
                "nodes": [
                    {
                        "title": source["text"],
                        "content": source["text"],
                        "sources": [
                            {
                                "paragraph_id": source["paragraph_id"],
                            }
                        ],
                    }
                ],
            }
        elif output_type == "study_summary":
            snapshot = json.loads(messages[-1]["content"])["snapshot"]
            value = {
                "summary": "讨论了修桥受挫。",
                "turn_ids": [t["id"] for t in snapshot["turns"]]
                + [
                    tid
                    for segment in snapshot.get("segments", [])
                    for tid in segment["turn_ids"]
                ],
            }
        elif output_type == "study_graph":
            payload = json.loads(messages[-1]["content"])
            value = {
                "reason": "仅提问不足以判断掌握。",
                "operations": [],
                "edges": payload["graph"]["edges"],
            }
        elif output_type == "study_memory":
            value = {"content": "村民调整方案修成桥。", "summary": "修桥的阅读反应"}
        elif output_type == "behavior_reflection":
            value = {
                key: ""
                for key in (
                    "observation",
                    "cause_analysis",
                    "effective_strategy",
                    "next_strategy",
                    "applicability",
                    "skill_change_reason",
                    "proposed_skill_text",
                )
            }
            value.update(
                skill_change_decision="insufficient_evidence", evidence_refs=[]
            )
        else:
            raise AssertionError(f"Unexpected model call: {output_type}")
        return LLMOutput(
            module,
            output_type,
            "controlled-study",
            json.dumps(value, ensure_ascii=False),
            correlation_id,
        )


@pytest.fixture
async def study_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[_Host, StudyModel, str]]:
    monkeypatch.setenv("NYX_DB", str(tmp_path / "role.db"))
    monkeypatch.setenv("NYX_DATABASE_URL", "")
    monkeypatch.setenv("NYX_AUTH_TOKEN", "")
    monkeypatch.setenv("NYX_PUBLIC_ORIGIN", "")
    model = StudyModel()

    def make_model(cls: type[LlmClient], config: LlmConfig) -> LlmClient:
        return model

    def no_embed(name: str) -> None:
        return None

    monkeypatch.setattr(LlmClient, "from_config", classmethod(make_model))
    monkeypatch.setattr("nyx.app_context.build_embed", no_embed)
    host = await _Host.open(Config(), tmp_path / "host")
    reader = host.apps["nyx"].reading
    book, _ = await reader._store.insert_book_with_paragraphs(
        "修桥",
        "测试",
        "bridge.epub",
        "bridge",
        [
            Segment("村民提出修桥", True),
            Segment("暴雨使第一次施工失败", True),
            Segment("村民改变方案，桥最终建成", True),
        ],
    )
    try:
        yield host, model, book.id
    finally:
        await host.close()


async def test_reply_isolation_note_snapshot_and_end_commit(
    study_host: tuple[_Host, StudyModel, str],
) -> None:
    host, model, book_id = study_host
    app = host.apps["nyx"]
    reading = app.reading.study
    connection, input_id = str(uuid4()), str(uuid4())
    snapshot = await reading.enter(book_id, connection)
    session_id = snapshot["session"]["id"]
    note = await app.reading._store.study_note(book_id)
    await app.reading._store.save_study_note(
        book_id, note["revision"], "未发送的秘密笔记"
    )
    await reading.accept(
        book_id,
        session_id,
        connection,
        input_id,
        "修桥为何失败？",
        ["明确发送的笔记"],
        None,
        "",
    )
    await reading._reply(book_id, session_id, input_id)
    current = await reading.snapshot(book_id, session_id)
    assert current["session"]["turns"][0]["status"] == "committed"
    assert (
        "> 暴雨使第一次施工失败\n>\n> [《修桥》·第 2 段](nyx-book:2)"
        in current["session"]["turns"][0]["reply"]
    )
    assert current["session"]["turns"][0]["input"]["notes"] == ["明确发送的笔记"]
    assert not app.expression._history
    assert not await app.expression.pending_questions()
    assert (await app.reading.get_progress(book_id)).nyx_position == 1
    assert app.database is not None
    cursor = await app.database.conn.execute(
        "SELECT COUNT(*) FROM event_log WHERE type IN ('speak','ask','user_message')"
    )
    row = await cursor.fetchone()
    assert row is not None and row[0] == 0
    assert all("未发送的秘密笔记" not in prompt for _, prompt in model.calls)
    assert any(
        "明确发送的笔记" in prompt for kind, prompt in model.calls if kind == "reply"
    )
    ended = await reading.leave(book_id, session_id)
    assert (await reading.leave(book_id, session_id))["snapshot"] == ended["snapshot"]
    assert not reading.occupied()
    with pytest.raises(StudyConflictError):
        await reading.accept(
            book_id, session_id, connection, str(uuid4()), "迟到输入", [], None, ""
        )
    await reading._settle(book_id, session_id, "summary")
    assert "讨论了修桥受挫" in await reading.handoff()
    assert not app.expression._history


async def test_leaving_during_generation_rejects_draft(
    study_host: tuple[_Host, StudyModel, str],
) -> None:
    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    connection, input_id = str(uuid4()), str(uuid4())
    session_id = (await reading.enter(book_id, connection))["session"]["id"]
    await reading.accept(
        book_id, session_id, connection, input_id, "为什么失败", [], None, ""
    )
    model.block = "reply"
    task = asyncio.create_task(reading._reply(book_id, session_id, input_id))
    await asyncio.wait_for(model.started.wait(), 10)
    await reading.leave(book_id, session_id)
    model.release.set()
    await asyncio.gather(task, return_exceptions=True)
    session = (await reading.snapshot(book_id, session_id))["session"]
    assert session["turns"][0]["status"] == "incomplete"
    assert session["turns"][0]["reply"] == ""
    assert session["snapshot"]["turns"][0]["status"] == "incomplete"


async def test_graph_cas_keeps_new_evidence_and_deleted_book(
    study_host: tuple[_Host, StudyModel, str],
) -> None:
    host, model, book_id = study_host
    reader = host.apps["nyx"].reading
    reading = reader.study
    connection = str(uuid4())
    session_id = (await reading.enter(book_id, connection))["session"]["id"]
    input_id = str(uuid4())
    await reading.accept(
        book_id, session_id, connection, input_id, "努力保证成功吗", [], None, ""
    )
    await reading._reply(book_id, session_id, input_id)
    revision, old = await reading._load(book_id)
    model.block = "study_graph"
    task = asyncio.create_task(reading._update_graph(book_id, revision, old))
    await asyncio.wait_for(model.started.wait(), 10)
    await reading.leave(book_id, session_id)
    next_session = (await reading.enter(book_id, connection))["session"]["id"]
    second = str(uuid4())
    await reading.accept(
        book_id,
        next_session,
        connection,
        second,
        "纠正：共同实践只创造可能",
        [],
        None,
        "",
    )
    await reading._reply(book_id, next_session, second)
    model.release.set()
    await task
    revision, current = await reading._load(book_id)
    assert current["evidence_seq"] == 2 and current["consumed_seq"] == 0
    assert current["candidates"][-1]["state"] == "superseded"
    model.block = None
    await reading._update_graph(book_id, revision, current)
    revision, current = await reading._load(book_id)
    assert current["consumed_seq"] == 2
    assert {input_id, second} == set(current["history"][-1]["evidence"])
    async with reader._store.db.transaction():
        await reader._store.db.conn.execute("DELETE FROM books WHERE id=?", (book_id,))
    assert not await reader._store.save_study(book_id, revision, current)
    assert await reader._store.load_study(book_id) is None


async def test_ignored_material_completes_without_blocking_book_content(
    study_host: tuple[_Host, StudyModel, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    host, _, _ = study_host
    reader = host.apps["nyx"].reading
    reading = reader.study
    book, _ = await reader._store.insert_book_with_paragraphs(
        "修桥",
        "测试",
        "mixed.epub",
        "mixed",
        [
            Segment("关注公众号，添加好友领取书单。", True),
            Segment("作者序言：本书记录村民改方案修成桥的经过。", True),
        ],
    )
    await reading.enter(book.id, str(uuid4()))
    revision, data = await reading._load(book.id)

    async def ignored_material(
        kind: str, payload: Any, correlation: str
    ) -> dict[str, Any]:
        assert kind == "material"
        assert payload[0]["index"] == 1
        assert correlation == data["materials"][0]["id"]
        return {"summary": "已略过夹带推广，无作品内容。", "nodes": []}

    with monkeypatch.context() as patcher:
        patcher.setattr(reading, "_complete", ignored_material)
        await reading._prepare(book.id, revision, data, data["materials"][0])
    revision, data = await reading._load(book.id)
    assert data["materials"][0]["state"] == "completed"
    assert data["materials"][0]["attempts"] == 0
    assert data["graph"]["nodes"] == []
    await reading._prepare(book.id, revision, data, data["materials"][1])
    _, data = await reading._load(book.id)
    assert all(m["state"] == "completed" for m in data["materials"])
    assert len(data["graph"]["nodes"]) == 1
    assert data["graph"]["nodes"][0]["content"].startswith("作者序言")


async def test_sources_use_complete_stored_text_and_enforce_material_scope(
    study_host: tuple[_Host, StudyModel, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    host, _, other_book_id = study_host
    reader = host.apps["nyx"].reading
    reading = reader.study
    text = "人们自己创造\n 自己的历史。\n(1)\n但不是随心所欲地创造。"
    book, _ = await reader._store.insert_book_with_paragraphs(
        "历史",
        "测试",
        "history.epub",
        "history",
        [Segment(text, True), Segment("共同利益不等于共同组织。", True)],
    )
    await reading.enter(book.id, str(uuid4()))
    revision, data = await reading._load(book.id)
    await reading._prepare(book.id, revision, data, data["materials"][0])
    revision, data = await reading._load(book.id)
    source = data["graph"]["nodes"][0]["sources"][0]
    assert source["quote"] == text
    assert source["index"] == 1
    source.update(quote="模型改写，不能成为原文。", index=99)
    await reading._sources(book.id, [{"sources": [source]}])
    assert source["quote"] == text and source["index"] == 1
    other = (await reader.list_paragraphs(other_book_id, 1, 1))[0]
    with pytest.raises(ValueError):
        await reading._sources(book.id, [{"sources": [{"paragraph_id": other.id}]}])

    async def wrong_material(
        kind: str, payload: Any, correlation: str
    ) -> dict[str, Any]:
        assert kind == "material" and payload[0]["index"] == 2
        assert correlation == data["materials"][1]["id"]
        return {
            "summary": "错误地引用另一个单元。",
            "nodes": [{"sources": [{"paragraph_id": source["paragraph_id"]}]}],
        }

    monkeypatch.setattr(reading, "_complete", wrong_material)
    with pytest.raises(ValueError):
        await reading._prepare(book.id, revision, data, data["materials"][1])
    _, saved = await reading._load(book.id)
    assert saved["materials"][1]["state"] == "queued"


async def test_note_cas_and_reply_quote_scope(
    study_host: tuple[_Host, StudyModel, str],
) -> None:
    host, _, book_id = study_host
    reader = host.apps["nyx"].reading
    connection = str(uuid4())
    identity = (await reader.study.enter(book_id, connection))["session"]["id"]
    note = await reader._store.study_note(book_id)
    await reader._store.save_study_note(book_id, note["revision"], "新的正文")
    with pytest.raises(ProgressConflictError):
        await reader._store.save_study_note(book_id, note["revision"], "过期正文")
    with pytest.raises(ValueError):
        await reader.study.accept(
            book_id,
            identity,
            connection,
            str(uuid4()),
            "批注",
            [],
            {
                "reply_event_id": "other-session",
                "quote": "伪造引用",
                "comment": "不同意",
            },
            "",
        )
    assert (await reader._store.study_note(book_id))["content"] == "新的正文"


async def test_inputs_merge_and_reconnect_keeps_identity(
    study_host: tuple[_Host, StudyModel, str],
) -> None:
    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    connection, other = str(uuid4()), str(uuid4())
    identity = (await reading.enter(book_id, connection))["session"]["id"]
    assert (await reading.enter(book_id, other))["session"]["id"] == identity
    first, second = str(uuid4()), str(uuid4())
    accepted = await reading.accept(
        book_id, identity, connection, first, "为什么失败", [], None, ""
    )
    assert (
        await reading.accept(
            book_id, identity, connection, first, "为什么失败", [], None, ""
        )
        == accepted
    )
    model.block = "reply"
    task = asyncio.create_task(reading._reply(book_id, identity, first))
    await asyncio.wait_for(model.started.wait(), 10)
    await reading.accept(
        book_id, identity, other, second, "请联系最后改方案来讲", [], None, ""
    )
    model.release.set()
    await task
    turns = (await reading.snapshot(book_id, identity))["session"]["turns"]
    assert [t["status"] for t in turns] == ["committed", "committed"]
    assert sum(bool(t["reply"]) for t in turns) == 1
    assert turns[0]["reply_id"] == turns[1]["id"]
    assert any(
        "请联系最后改方案来讲" in prompt
        for kind, prompt in model.calls
        if kind == "reply"
    )
    await reading.detach(book_id, identity, connection)
    assert reading.occupied()  # Other foreground connection still owns its lease.
    assert (await reading.enter(book_id, connection))["session"]["id"] == identity


async def test_deleted_book_cannot_commit_reply_or_failure(
    study_host: tuple[_Host, StudyModel, str],
) -> None:
    host, model, book_id = study_host
    reader = host.apps["nyx"].reading
    reading = reader.study
    connection, input_id = str(uuid4()), str(uuid4())
    identity = (await reading.enter(book_id, connection))["session"]["id"]
    await reading.accept(
        book_id, identity, connection, input_id, "为什么失败", [], None, ""
    )
    model.block = "reply"
    task = asyncio.create_task(reading._reply(book_id, identity, input_id))
    await asyncio.wait_for(model.started.wait(), 10)
    async with reader._store.db.transaction():
        await reader._store.db.conn.execute("DELETE FROM books WHERE id=?", (book_id,))
    model.release.set()
    await task
    await reading._failure(book_id, "graph", "", ValueError("deleted"))
    assert await reader._store.load_study(book_id) is None


async def test_settlement_partial_success_and_recovery(
    study_host: tuple[_Host, StudyModel, str],
) -> None:
    host, model, book_id = study_host
    reader = host.apps["nyx"].reading
    reading = reader.study
    connection, input_id = str(uuid4()), str(uuid4())
    identity = (await reading.enter(book_id, connection))["session"]["id"]
    await reading.accept(
        book_id, identity, connection, input_id, "为什么失败", [], None, ""
    )
    await reading._reply(book_id, identity, input_id)
    await reading.leave(book_id, identity)
    await reading._failure(
        book_id, identity, "reflection", ValueError("controlled failure")
    )
    await reading._settle(book_id, identity, "memory")
    recovered = ReadingSessions(reader, host.apps["nyx"].expression)
    await recovered._settle(book_id, identity, "memory")
    await recovered._settle(book_id, identity, "summary")
    session = (await recovered.snapshot(book_id, identity))["session"]
    assert session["results"]["memory"]["state"] == "completed"
    assert session["results"]["summary"]["state"] == "completed"
    assert session["results"]["reflection"]["error"]
    assert len([call for call in model.calls if call[0] == "study_memory"]) == 1
    cursor = await reader._store.db.conn.execute(
        "SELECT COUNT(*) FROM reading_memory_receipt"
    )
    row = await cursor.fetchone()
    assert row is not None and row[0] == 1
    assert "讨论了修桥受挫" in await recovered.handoff()


async def test_feedback_and_long_settlement_resume(
    study_host: tuple[_Host, StudyModel, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    host, model, book_id = study_host
    reader = host.apps["nyx"].reading
    reading = reader.study
    connection = str(uuid4())
    identity = (await reading.enter(book_id, connection))["session"]["id"]
    first = str(uuid4())
    await reading.accept(
        book_id, identity, connection, first, "修桥为何失败", [], None, ""
    )
    await reading._reply(book_id, identity, first)
    revision, data = await reading._load(book_id)
    data["sessions"][-1]["turns"][-1]["reply"] = (
        "**暴雨**使第一次施工失败。[第2段](nyx-book:2)"
    )
    await reader._store.save_study(book_id, revision, data)
    correction = str(uuid4())
    await reading.accept(
        book_id,
        identity,
        connection,
        correction,
        "先别问我，重讲一遍。" + "长对话材料。" * 200,
        [],
        {
            "reply_event_id": first,
            "quote": "暴雨使第一次施工失败。第2段",
            "comment": "把因果讲具体",
        },
        "",
    )
    await reading._reply(book_id, identity, correction)
    await reading._feedback(book_id, identity, correction)
    await reading._feedback(book_id, identity, correction)
    assert (await reading.snapshot(book_id, identity))["feedback_jobs"][0][
        "state"
    ] == "completed"
    await reading.leave(book_id, identity)
    from nyx.reading.sessions import _PARAMS

    monkeypatch.setitem(_PARAMS, "evidence_chars", 1000)
    await reading._settle(book_id, identity, "summary")
    before = len(model.calls)
    recovered = ReadingSessions(reader, host.apps["nyx"].expression)
    await recovered._settle(book_id, identity, "summary")
    assert len(model.calls) == before
    session = (await recovered.snapshot(book_id, identity))["session"]
    assert set(session["results"]["summary"]["output"]["turn_ids"]) == {
        first,
        correction,
    }
    assert all(unit["output"] is not None for unit in session["settlement_units"])
    cursor = await reader._store.db.conn.execute(
        "SELECT COUNT(*) FROM behavior_reflection"
    )
    row = await cursor.fetchone()
    assert row is not None and row[0] == 1


async def test_study_http_lifecycle_and_note_conflict(
    study_host: tuple[_Host, StudyModel, str],
) -> None:
    host, _, book_id = study_host
    transport = ASGITransport(app=build_host_app(host, launch_nonce=None))
    async with AsyncClient(
        transport=transport, base_url="http://localhost:8000"
    ) as client:
        connection = str(uuid4())
        response = await client.post(
            "/api/reading/sessions", json={"book_id": book_id, "connection": connection}
        )
        assert response.status_code == 200, response.text
        identity = response.json()["session"]["id"]
        active = await client.get("/api/reading/sessions")
        assert active.json()["session"]["id"] == identity
        note_url = f"/api/books/{book_id}/study-note"
        note = (await client.get(note_url)).json()
        payload = {"content": "书级笔记", "revision": note["revision"]}
        assert (await client.put(note_url, json=payload)).status_code == 200
        assert (await client.put(note_url, json=payload)).status_code == 409
        prefix = f"/api/books/{book_id}/study/{identity}"
        turn: dict[str, object] = {
            "connection": connection,
            "input_id": str(uuid4()),
            "message": "为什么失败",
            "notes": [],
            "annotation": None,
            "presentation": "",
        }
        assert (await client.post(prefix + "/turns", json=turn)).status_code == 200
        assert (await client.post(prefix + "/leave")).status_code == 200
        turn["input_id"] = str(uuid4())
        assert (await client.post(prefix + "/turns", json=turn)).status_code == 409
        assert (await client.get("/api/reading/sessions")).json() is None
