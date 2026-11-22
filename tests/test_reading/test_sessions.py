"""Real stores and reply graph; controlled model for races, never teaching quality."""
# pyright: reportPrivateUsage=false

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
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
        self.responses: dict[str, list[dict[str, Any]]] = {}

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
        payload: Any = (
            json.loads(messages[1]["content"])["input"]
            if output_type != "behavior_reflection"
            else None
        )
        if output_type == "study_understand":
            value: Any = {
                "question": "修桥为何失败",
                "requirements": [],
                "carry_requirements": [],
                "whole_book": False,
                "subquestions": ["修桥为何失败"],
                "history_query": None,
                "understanding_query": None,
            }
        elif output_type == "study_evidence":
            value = {
                "action": "finish",
                "summary": "暴雨影响施工",
                "gaps": [],
                "checks": [
                    {"question": q, "status": "supported", "sources": [2], "gap": ""}
                    for q in payload["task_subquestions"]
                ],
                "claims": [
                    {"text": "暴雨使施工失败", "sources": [2], "limitation": "仅第一次"}
                ],
            }
        elif output_type == "study_review":
            value = {"next_step": "accept", "issues": []}
        elif output_type == "study_graph_review":
            value = {"valid": True, "issues": []}
        elif output_type == "study_scope":
            value = {"scope": "", "start": None, "end": None, "feedback": False}
        elif output_type == "reason":
            value = {"summary": "依据当前书籍回答", "next_action": "answer"}
        elif output_type == "reply":
            value = {
                "think": "",
                "speak": "暴雨使第一次施工失败。[第2段](nyx-book:2)",
                "claims": [
                    {
                        "text": "暴雨使第一次施工失败",
                        "sources": [2],
                        "limitation": "仅第一次",
                    }
                ],
            }
        elif output_type == "study_material":
            source = payload[0]
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
        elif output_type == "study_overview":
            value = {
                "summary": " | ".join(m["summary"][:25] for m in payload["materials"]),
                "unit_ids": [m["id"] for m in payload["materials"]],
            }
        elif output_type == "study_summary":
            snapshot = payload["snapshot"]
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
            value = {
                "action": "patch",
                "remove_edges": [],
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
        if self.responses.get(output_type):
            value = self.responses[output_type].pop(0)
        if output_type == "study_graph" and value.get("action") == "read":
            value.setdefault("paragraph_ids", [])
        if output_type == "study_graph" and value.get("action") == "patch":
            value.setdefault("deferred_evidence", [])
        if output_type == "study_graph_review":
            value.setdefault(
                "checks",
                [
                    {
                        "id": op["id"],
                        "user_supported": True,
                        "source_supported": True,
                        "reason": "受控审核通过",
                    }
                    for op in payload["candidate"]["operations"]
                    if op["op"] == "upsert"
                ],
            )
        if output_type == "study_scope":
            value.setdefault("user_requirement", "")
        if output_type == "study_review":
            value.setdefault("interpretations", [])
            value.setdefault(
                "conclusions",
                [
                    {"text": c["text"], "supported": True, "excess": "", "contrary": ""}
                    for c in payload["answer"]["claims"]
                ],
            )
            value.setdefault(
                "questions",
                [
                    {
                        "question": q,
                        "status": "missing"
                        if any(
                            c["question"] == q and c["status"] == "missing"
                            for c in payload["evidence"]["checks"]
                        )
                        else "answered",
                    }
                    for q in payload["understanding"]["subquestions"]
                ],
            )
            value.setdefault(
                "tasks", value["issues"] if value["next_step"] == "evidence" else []
            )
            if value["tasks"] and not any(
                q["status"] == "missing" for q in value["questions"]
            ):
                value["questions"][0]["status"] = "missing"
            value["tasks"] = [
                {
                    "question": next(
                        q["question"]
                        for q in value["questions"]
                        if q["status"] == "missing"
                    ),
                    "user_requirement": payload["original"]["current_inputs"][0][
                        "message"
                    ],
                    "gap": t,
                    "task": t,
                }
                if isinstance(t, str)
                else t
                for t in value["tasks"]
            ]
        if output_type == "study_evidence":
            if payload.get("focus_task"):
                value.setdefault("repair_question", payload["focus_task"]["question"])
            value.setdefault("checks", [])
            for check in value["checks"]:
                check.setdefault("direct_sources", list(check["sources"]))
                check.setdefault("speaker", "叙述者")
                check.setdefault("statement_type", "narration")
                check.setdefault("contradictions", [])
                check.setdefault("context_gap", "")
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
        kind: str,
        payload: Any,
        correlation: str,
        validate: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
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
        kind: str,
        payload: Any,
        correlation: str,
        validate: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
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
    host, model, book_id = study_host
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
    first = str(uuid4())
    await reader.study.accept(
        book_id, identity, connection, first, "为什么失败", [], None, ""
    )
    await reader.study._reply(book_id, identity, first)
    model.responses["study_understand"] = [
        {
            "question": "修桥为何失败",
            "requirements": ["请用比喻重讲，先别问我"],
            "carry_requirements": [],
            "whole_book": False,
            "subquestions": ["修桥为何失败"],
            "history_query": None,
            "understanding_query": None,
        }
    ]
    second = str(uuid4())
    await reader.study.accept(
        book_id,
        identity,
        connection,
        second,
        "围绕选区",
        [],
        {
            "reply_event_id": first,
            "quote": "暴雨使第一次施工失败",
            "comment": "请用比喻重讲，先别问我",
        },
        "",
    )
    await reader.study._reply(book_id, identity, second)
    snapshot = await reader.study.snapshot(book_id, identity)
    assert snapshot["session"]["turns"][-1]["status"] == "committed"


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


async def test_local_field_retry_and_semantic_graph_retry_preserve_reply(
    study_host: tuple[_Host, StudyModel, str],
) -> None:
    host, model, book_id = study_host
    reader = host.apps["nyx"].reading
    reading = reader.study
    connection, input_id = str(uuid4()), str(uuid4())
    session_id = (await reading.enter(book_id, connection))["session"]["id"]
    model.responses["study_scope"] = [{"scope": "", "start": None, "end": None}]
    model.responses["reply"] = [
        {
            "think": "",
            "speak": "暴雨使施工失败。[来源](nyx-book:N)",
            "claims": [{"text": "施工失败", "sources": [2], "limitation": ""}],
        }
    ]
    await reading.accept(
        book_id, session_id, connection, input_id, "为什么第一次失败？", [], None, ""
    )
    await reading._reply(book_id, session_id, input_id)
    before = await reading.snapshot(book_id, session_id)
    assert before["session"]["turns"][0]["status"] == "committed"
    kinds = [kind for kind, _ in model.calls]
    assert kinds.count("study_scope") == kinds.count("reply") == 2
    assert kinds.count("study_understand") == kinds.count("study_evidence") == 1
    paragraph = (await reader.list_paragraphs(book_id, 2, 2))[0]
    bad = {
        "action": "patch",
        "remove_edges": [],
        "reason": "误把提问当掌握",
        "operations": [
            {
                "op": "upsert",
                "id": "wrong-mastery",
                "title": "施工失败",
                "content": "用户只问过",
                "state": "explaining",
                "sources": [{"paragraph_id": paragraph.id}],
                "evidence": [input_id],
                "corrects": [],
            }
        ],
        "edges": [],
    }
    model.responses["study_graph"] = [
        {"reason": "漏字段", "operations": []},
        bad,
    ]
    model.responses["study_graph_review"] = [
        {
            "valid": False,
            "issues": ["用户只提问，不能标 explaining"],
        }
    ]
    revision, data = await reading._load(book_id)
    await reading._update_graph(book_id, revision, data)
    after = await reading.snapshot(book_id, session_id)
    assert not after["graph"]["nodes"]
    assert (
        after["session"]["turns"][0]["reply"] == before["session"]["turns"][0]["reply"]
    )
    kinds = [kind for kind, _ in model.calls]
    assert kinds.count("study_graph") == 3
    assert kinds.count("reply") == 2
    graph_prompts = [prompt for kind, prompt in model.calls if kind == "study_graph"]
    assert "edges" in graph_prompts[1] and "不能标 explaining" in graph_prompts[2]


async def test_exhausted_final_validation_leaves_input_retryable(
    study_host: tuple[_Host, StudyModel, str],
) -> None:
    from nyx.policy import policy

    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    connection, input_id = str(uuid4()), str(uuid4())
    session_id = (await reading.enter(book_id, connection))["session"]["id"]
    model.responses["reply"] = [{"speak": "缺少必填字段"}] * policy(
        "llm",
        "validation.attempts",
        int,
    )
    await reading.accept(
        book_id, session_id, connection, input_id, "为何施工失败？", [], None, ""
    )
    await reading._reply(book_id, session_id, input_id)
    snapshot = await reading.snapshot(book_id, session_id)
    turn = snapshot["session"]["turns"][0]
    assert turn["status"] == "failed" and turn["reply"] == ""
    assert "think" in turn["error"] and not snapshot["pending_evidence"]
    await reading.retry_turn(book_id, session_id, input_id, connection)
    await reading._reply(book_id, session_id, input_id)
    snapshot = await reading.snapshot(book_id, session_id)
    assert len(snapshot["session"]["turns"]) == 1
    assert snapshot["session"]["turns"][0]["status"] == "committed"


async def test_delegated_reads_return_sources_and_reject_child_recursion(
    study_host: tuple[_Host, StudyModel, str],
) -> None:
    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    connection, input_id = str(uuid4()), str(uuid4())
    session_id = (await reading.enter(book_id, connection))["session"]["id"]
    model.responses["study_evidence"] = [
        {"action": "delegate", "tasks": ["暴雨为何使修桥失败"]},
        {"action": "delegate", "tasks": ["不允许递归"]},
    ]
    await reading.accept(
        book_id, session_id, connection, input_id, "查证修桥失败的原因", [], None, ""
    )
    await reading._reply(book_id, session_id, input_id)
    snapshot = await reading.snapshot(book_id, session_id)
    assert snapshot["session"]["turns"][0]["status"] == "committed"
    prompts = [prompt for kind, prompt in model.calls if kind == "study_evidence"]
    assert len(prompts) == 4 and "child depth" in prompts[2]
    payload = json.loads(prompts[-1].split("\n")[-1])["input"]
    assert 2 in payload["children"][0]["read_scope"]
    assert '"paragraph_id"' in prompts[-1] and '"gaps"' in prompts[-1]
    answer_prompt = next(p for kind, p in model.calls if kind == "reply")
    answer_input = json.loads(answer_prompt.split("\n")[-1])["input"]
    assert 2 in answer_input["evidence"]["children"][0]["read_scope"]
    assert answer_input["evidence"]["children"][0]["task"] == "暴雨为何使修桥失败"


async def test_recalled_original_survives_understanding_and_answer_retry(
    study_host: tuple[_Host, StudyModel, str],
) -> None:
    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    connection = str(uuid4())
    session_id = (await reading.enter(book_id, connection))["session"]["id"]
    for message in ("OLD_TOPIC_桥梁资料", "修桥第一问", "修桥第二问", "修桥第三问"):
        input_id = str(uuid4())
        await reading.accept(
            book_id, session_id, connection, input_id, message, [], None, ""
        )
        await reading._reply(book_id, session_id, input_id)
    model.calls.clear()
    model.responses["study_understand"] = [
        {
            "question": "修桥为何失败",
            "requirements": [],
            "carry_requirements": [],
            "whole_book": False,
            "subquestions": ["修桥为何失败"],
            "history_query": "OLD_TOPIC_桥梁资料",
            "understanding_query": None,
        }
    ]
    model.responses["study_review"] = [
        {
            "next_step": "answer",
            "issues": ["请把解释说得直接一点"],
        }
    ]
    input_id = str(uuid4())
    await reading.accept(
        book_id, session_id, connection, input_id, "回到最初那个话题", [], None, ""
    )
    await reading._reply(book_id, session_id, input_id)
    kinds = [kind for kind, _ in model.calls]
    assert kinds.count("study_understand") == 2
    assert kinds.count("study_evidence") == 1 and kinds.count("reply") == 2
    first = json.loads(model.calls[1][1].split("\n")[-1])["input"]
    assert "OLD_TOPIC" not in json.dumps(first["recent_dialogue"], ensure_ascii=False)
    for kind, prompt in model.calls:
        if kind == "reply":
            payload = json.loads(prompt.split("\n")[-1])["input"]
            recalled = payload["original"]["recalled"]["history_query"]
            assert recalled[0]["input"]["message"] == "OLD_TOPIC_桥梁资料"


async def test_specified_passage_is_read_and_last_search_must_finish(
    study_host: tuple[_Host, StudyModel, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx.reading.dialogue import _PARAMS

    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    monkeypatch.setitem(_PARAMS, "evidence_steps", 2)
    connection, input_id = str(uuid4()), str(uuid4())
    session_id = (await reading.enter(book_id, connection))["session"]["id"]
    model.responses["study_evidence"] = [
        {"action": "search", "query": "暴雨", "start": None, "end": None},
        {"action": "search", "query": "暴雨", "start": None, "end": None},
    ]
    await reading.accept(
        book_id, session_id, connection, input_id, "第3段证明施工失败吗？", [], None, ""
    )
    await reading._reply(book_id, session_id, input_id)
    snapshot = await reading.snapshot(book_id, session_id)
    assert snapshot["session"]["turns"][0]["status"] == "committed"
    prompts = [p for kind, p in model.calls if kind == "study_evidence"]
    payload = json.loads(prompts[0].split("\n")[-1])["input"]
    assert payload["original"]["specified_paragraphs"] == [3]
    assert any(p["index"] == 3 and "最终建成" in p["text"] for p in payload["passages"])
    assert len(prompts) == 3
    final = json.loads(prompts[-1].split("\n")[-1])
    assert final["input"]["budget_exhausted"]
    assert final["output_schema"]["title"] == "Evidence"


async def test_evidence_recheck_keeps_previous_passages_and_read_log(
    study_host: tuple[_Host, StudyModel, str],
) -> None:
    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    connection, input_id = str(uuid4()), str(uuid4())
    session_id = (await reading.enter(book_id, connection))["session"]["id"]
    model.responses["study_evidence"] = [
        {"action": "excerpt", "start": 1, "end": 1},
    ]
    model.responses["study_review"] = [
        {
            "next_step": "evidence",
            "issues": ["补查另一环节，保留已读原文"],
            "tasks": ["核对村民实际提出修桥的行为"],
        },
    ]
    await reading.accept(
        book_id, session_id, connection, input_id, "修桥为何失败", [], None, ""
    )
    await reading._reply(book_id, session_id, input_id)
    saved = await reading.snapshot(book_id, session_id)
    assert saved["session"]["turns"][0]["status"] == "committed"
    prompts = [p for kind, p in model.calls if kind == "study_evidence"]
    assert len(prompts) == 3
    payload = json.loads(prompts[-1].split("\n")[-1])["input"]
    assert 1 in {p["index"] for p in payload["passages"]}
    assert payload["read_log"][0]["returned_indices"] == [1]
    assert payload["issues"] == ["补查另一环节，保留已读原文"]
    assert payload["task"] == "核对村民实际提出修桥的行为"
    assert [t["task"] for t in payload["repair_tasks"]] == [
        "核对村民实际提出修桥的行为"
    ]


async def test_overview_covers_tail_and_gap_using_actual_pack_budget(
    study_host: tuple[_Host, StudyModel, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx.reading.sessions import _PARAMS, _frontier, _material_pack, _packed_chars

    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    await reading.enter(book_id, str(uuid4()))
    revision, data = await reading._load(book_id)
    data["materials"] = [
        {
            "id": f"unit-{i}",
            "start": i,
            "end": i,
            "summary": f"chapter-{i}: " + "材料" * 130,
            "state": "failed" if i == 8 else "completed",
            "retry_at": 0,
        }
        for i in range(1, 22)
    ]
    monkeypatch.setitem(_PARAMS, "context_chars", 1700)
    await reading.store.save_study(book_id, revision, data)
    for _ in range(25):
        revision, data = await reading._load(book_id)
        pack = _material_pack(data, _frontier(data))
        if _packed_chars(pack) <= _PARAMS["context_chars"]:
            break
        await reading._overview(book_id, revision, data)
    else:
        pytest.fail("overview did not converge")
    assert pack["coverage"]["prepared"] == pack["coverage"]["provided"] == 20
    assert pack["coverage"]["provided_ranges"] == [[1, 7], [9, 21]]
    assert pack["coverage"]["gaps"] == [{"start": 8, "end": 8, "state": "failed"}]
    assert "chapter-1:" in json.dumps(pack) and "chapter-21:" in json.dumps(pack)
    for kind, prompt in model.calls:
        if kind == "study_overview":
            payload = json.loads(prompt.split("\n")[-1])["input"]
            assert _packed_chars(payload) <= _PARAMS["context_chars"]


async def test_graph_patch_is_local_and_old_evidence_read_by_id(
    study_host: tuple[_Host, StudyModel, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from copy import deepcopy

    host, model, book_id = study_host
    reader = host.apps["nyx"].reading
    reading = reader.study
    connection = str(uuid4())
    identity = (await reading.enter(book_id, connection))["session"]["id"]
    turn_ids: list[str] = []
    for message in ("暴雨使第一次失败", "我能解释：暴雨使施工失败"):
        tid = str(uuid4())
        turn_ids.append(tid)
        await reading.accept(book_id, identity, connection, tid, message, [], None, "")
        await reading._reply(book_id, identity, tid)
    revision, data = await reading._load(book_id)
    paragraph = (await reader.list_paragraphs(book_id, 2, 2))[0]
    node: dict[str, Any] = {
        "id": "related",
        "version": 1,
        "title": "暴雨与施工失败",
        "content": "暴雨",
        "state": "unassessed",
        "evidence": [turn_ids[0]],
        "corrects": [],
        "sources": [
            {"paragraph_id": paragraph.id, "index": 2, "quote": paragraph.text}
        ],
        "origin": {"kind": "material", "id": "test"},
    }
    unrelated: list[dict[str, Any]] = [
        {
            **deepcopy(node),
            "id": f"other-{i}",
            "title": "无关",
            "content": "无关资料" * 100,
            "evidence": [],
        }
        for i in range(600)
    ]
    untouched_edge = {"from": "other-0", "to": "other-1", "label": "保留关系"}
    data["graph"].update(nodes=[node, *unrelated], edges=[untouched_edge])
    data["consumed_seq"] = 1
    await reading.store.save_study(book_id, revision, data)
    model.calls.clear()
    model.responses["study_graph"] = [
        {"action": "read", "node_ids": [], "evidence_ids": [turn_ids[0]]},
        {"action": "read", "node_ids": [], "evidence_ids": [turn_ids[0]]},
        {
            "action": "patch",
            "reason": "用户自己解释",
            "operations": [
                {
                    "op": "upsert",
                    "id": "related",
                    "title": node["title"],
                    "content": "用户自己准确解释暴雨造成失败",
                    "state": "explaining",
                    "evidence": turn_ids,
                    "corrects": [],
                    "sources": [{"paragraph_id": paragraph.id}],
                }
            ],
            "edges": [],
            "remove_edges": [],
        },
    ]
    from nyx.tools.registry import ToolRegistry

    original_call = ToolRegistry.call
    read_calls: list[str] = []

    async def call(
        self: ToolRegistry, name: str, args: dict[str, Any], **kwargs: Any
    ) -> Any:
        if name == "book_graph_read":
            read_calls.append(name)
        return await original_call(self, name, args, **kwargs)

    monkeypatch.setattr(ToolRegistry, "call", call)
    revision, data = await reading._load(book_id)
    await reading._update_graph(book_id, revision, data)
    _, saved = await reading._load(book_id)
    assert saved["graph"]["nodes"][1:] == unrelated
    assert saved["graph"]["edges"] == [untouched_edge]
    assert saved["graph"]["nodes"][0]["version"] == 2
    prompts = [p for kind, p in model.calls if kind == "study_graph"]
    assert max(map(len, prompts)) < 18000
    first = json.loads(prompts[0].split("\n")[-1])["input"]
    second = json.loads(prompts[1].split("\n")[-1])["input"]
    third = json.loads(prompts[2].split("\n")[-1])["input"]
    assert [e["id"] for e in first["evidence"]] == [turn_ids[1]]
    assert {e["id"] for e in second["evidence"]} == set(turn_ids)
    assert read_calls == ["book_graph_read"]
    assert second["tool_feedback"][0]["status"] == "success"
    assert second["tool_feedback"][0]["result"]["evidence"][0]["id"] == turn_ids[0]
    assert third["tool_feedback"][-1]["status"] == "already_provided"


async def test_invalid_material_source_retries_only_producer_with_exact_ids(
    study_host: tuple[_Host, StudyModel, str],
) -> None:
    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    await reading.enter(book_id, str(uuid4()))
    revision, data = await reading._load(book_id)
    model.responses["study_material"] = [
        {
            "summary": "资料",
            "nodes": [
                {
                    "title": "说明",
                    "content": "说明",
                    "sources": [{"paragraph_id": "bad-id"}],
                }
            ],
        }
    ]
    await reading._prepare(book_id, revision, data, data["materials"][0])
    prompts = [p for kind, p in model.calls if kind == "study_material"]
    assert len(prompts) == 2 and "bad-id" in prompts[1] and "exact IDs" in prompts[1]
    _, saved = await reading._load(book_id)
    assert saved["materials"][0]["state"] == "completed"


async def test_missing_subquestion_and_read_failure_return_to_shared_retry(
    study_host: tuple[_Host, StudyModel, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    connection, input_id = str(uuid4()), str(uuid4())
    identity = (await reading.enter(book_id, connection))["session"]["id"]
    original_search = reading.search
    search_attempts = 0

    async def search(
        book_id: str,
        query: str,
        start: int | None = None,
        end: int | None = None,
    ) -> list[dict[str, Any]]:
        nonlocal search_attempts
        search_attempts += 1
        if search_attempts == 1:
            raise TimeoutError("transient read failure")
        return await original_search(book_id, query, start, end)

    monkeypatch.setattr(reading, "search", search)
    model.responses["study_evidence"] = [
        {
            "action": "finish",
            "summary": "还没查到",
            "claims": [],
            "gaps": ["缺原因"],
            "checks": [
                {
                    "question": "修桥为何失败",
                    "status": "missing",
                    "sources": [],
                    "gap": "缺原因",
                }
            ],
        }
    ]
    await reading.accept(
        book_id, identity, connection, input_id, "修桥为何失败", [], None, ""
    )
    await reading._reply(book_id, identity, input_id)
    saved = await reading.snapshot(book_id, identity)
    assert saved["session"]["turns"][0]["status"] == "committed"
    assert search_attempts == 2
    prompts = [p for kind, p in model.calls if kind == "study_evidence"]
    assert len(prompts) == 2
    progress = json.loads(prompts[1].split("\n")[-1])["input"]["checks"]
    assert progress[0]["status"] == "missing" and progress[0]["gap"] == "缺原因"
    payload = json.loads(prompts[0].split("\n")[-1])["input"]
    receipt = next(r for r in payload["tool_feedback"] if r["name"] == "book_search")
    assert receipt["attempts"] == 2 and "transient read failure" in receipt["errors"][0]


@pytest.mark.parametrize("attached", [False, True])
async def test_explicit_note_requirement_and_compact_recent_reply(
    study_host: tuple[_Host, StudyModel, str],
    attached: bool,
) -> None:
    host, model, book_id = study_host
    reader = host.apps["nyx"].reading
    reading = reader.study
    connection, first = str(uuid4()), str(uuid4())
    identity = (await reading.enter(book_id, connection))["session"]["id"]
    private = await reader._store.study_note(book_id)
    await reader._store.save_study_note(
        book_id, private["revision"], "PRIVATE_DRAFT_X8"
    )
    requirement = "请区分供述与叙述，先别问我"
    model.responses["study_understand"] = [
        {
            "question": "修桥为何失败",
            "requirements": [requirement],
            "carry_requirements": [],
            "whole_book": False,
            "subquestions": ["修桥为何失败"],
            "history_query": None,
            "understanding_query": None,
        }
    ]
    await reading.accept(
        book_id,
        identity,
        connection,
        first,
        "修桥为何失败" if attached else requirement,
        [requirement] if attached else [],
        None,
        "",
    )
    await reading._reply(book_id, identity, first)
    before = await reading.snapshot(book_id, identity)
    turn = before["session"]["turns"][0]
    assert turn["status"] == "committed"
    assert turn["reply"] == "\n\n".join([turn["reply_body"], *turn["reply_quotes"]])
    assert turn["reply_sources"][0]["index"] == 2
    model.calls.clear()
    second = str(uuid4())
    await reading.accept(book_id, identity, connection, second, "再解释", [], None, "")
    await reading._reply(book_id, identity, second)
    prompt = next(p for kind, p in model.calls if kind == "study_understand")
    recent = json.loads(prompt.split("\n")[-1])["input"]["recent_dialogue"]
    user = json.loads(recent[0]["content"])
    assert user["input_id"] == first
    assert user["notes"] == ([requirement] if attached else [])
    answer = json.loads(recent[-1]["content"])
    assert answer["body"] == turn["reply_body"]
    assert answer["sources"] == turn["reply_sources"]
    assert turn["reply_quotes"][0] not in prompt
    assert all("PRIVATE_DRAFT_X8" not in p for _, p in model.calls)
    recovered = ReadingSessions(reader, host.apps["nyx"].expression)
    assert (await recovered.snapshot(book_id, identity))["session"]["turns"][0] == turn


async def test_unread_sources_are_read_in_evidence_and_answer_without_restart(
    study_host: tuple[_Host, StudyModel, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    connection, tid = str(uuid4()), str(uuid4())
    identity = (await reading.enter(book_id, connection))["session"]["id"]
    original_search = reading.search

    async def search(
        book_id: str,
        query: str,
        start: int | None = None,
        end: int | None = None,
    ) -> list[dict[str, Any]]:
        return [
            p
            for p in await original_search(book_id, query, start, end)
            if p["index"] == 2
        ]

    monkeypatch.setattr(reading, "search", search)
    model.responses["study_evidence"] = [
        {
            "action": "finish",
            "summary": "最终修成",
            "gaps": [],
            "claims": [{"text": "桥最终修成", "sources": [3], "limitation": "结尾"}],
            "checks": [
                {
                    "question": "修桥为何失败",
                    "status": "supported",
                    "sources": [3],
                    "gap": "",
                }
            ],
        }
    ]
    model.responses["reply"] = [
        {
            "think": "",
            "speak": "村民提出修桥。[第1段](nyx-book:1)",
            "claims": [{"text": "村民提出修桥", "sources": [1], "limitation": "开篇"}],
        }
    ]
    await reading.accept(
        book_id, identity, connection, tid, "修桥为何失败", [], None, ""
    )
    await reading._reply(book_id, identity, tid)
    saved = await reading.snapshot(book_id, identity)
    assert saved["session"]["turns"][0]["status"] == "committed"
    kinds = [kind for kind, _ in model.calls]
    assert kinds.count("study_understand") == 1
    assert kinds.count("study_evidence") == 2 and kinds.count("reply") == 2
    evidence = json.loads(
        next(p for k, p in reversed(model.calls) if k == "study_evidence").split("\n")[
            -1
        ]
    )["input"]
    receipt = next(r for r in evidence["tool_feedback"] if r["name"] == "book_excerpt")
    assert receipt["args"] == {"start": 3, "end": 3}
    assert receipt["status"] == "success" and receipt["log_id"]
    answer = json.loads(
        next(p for k, p in reversed(model.calls) if k == "reply").split("\n")[-1]
    )["input"]
    assert 1 in {p["index"] for p in answer["passages"]}


async def test_evidence_budget_returns_partial_with_explicit_missing_checks(
    study_host: tuple[_Host, StudyModel, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx.reading.dialogue import _PARAMS

    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    monkeypatch.setitem(_PARAMS, "evidence_steps", 1)
    monkeypatch.setitem(_PARAMS, "review_evidence_steps", 0)
    model.responses["study_review"] = [
        {
            "next_step": "evidence",
            "issues": ["必须继续读整章"],
            "tasks": ["读完整章"],
        }
    ]
    connection, tid = str(uuid4()), str(uuid4())
    identity = (await reading.enter(book_id, connection))["session"]["id"]
    model.responses["study_evidence"] = [
        {"action": "search", "query": "缺少的直接证据", "start": None, "end": None},
        {
            "action": "finish",
            "summary": "只知道暴雨，后续原因未证实",
            "gaps": ["缺直接证据"],
            "claims": [
                {"text": "暴雨影响施工", "sources": [2], "limitation": "仅背景"}
            ],
            "checks": [
                {
                    "question": "修桥为何失败",
                    "status": "missing",
                    "sources": [2],
                    "gap": "缺直接证据",
                }
            ],
        },
    ]
    model.responses["reply"] = [
        {
            "think": "",
            "speak": "原文说暴雨影响施工。[第2段](nyx-book:2)"
            " 尚缺直接证据，这部分未完成。",
            "claims": [
                {"text": "暴雨影响施工", "sources": [2], "limitation": "仅背景"}
            ],
        }
    ]
    await reading.accept(
        book_id, identity, connection, tid, "修桥为何失败", [], None, ""
    )
    await reading._reply(book_id, identity, tid)
    turn = (await reading.snapshot(book_id, identity))["session"]["turns"][0]
    assert turn["status"] == "committed" and "未完成" in turn["reply"]
    payload = json.loads(
        next(p for k, p in model.calls if k == "reply").split("\n")[-1]
    )["input"]
    assert payload["budget_exhausted"]
    assert payload["evidence"]["checks"][0]["status"] == "missing"
    assert [k for k, _ in model.calls].count("study_evidence") == 2
    reviews = [p for k, p in model.calls if k == "study_review"]
    assert len(reviews) == 2
    assert (
        json.loads(reviews[0].split("\n")[-1])["output_schema"]["title"]
        == "PartialReview"
    )


async def test_graph_no_progress_keeps_unconsumed_evidence(
    study_host: tuple[_Host, StudyModel, str],
) -> None:
    from nyx.reading.sessions import _PARAMS

    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    connection, tid = str(uuid4()), str(uuid4())
    identity = (await reading.enter(book_id, connection))["session"]["id"]
    await reading.accept(book_id, identity, connection, tid, "只是问问", [], None, "")
    await reading._reply(book_id, identity, tid)
    revision, data = await reading._load(book_id)
    model.responses["study_graph"] = [
        {"action": "read", "node_ids": [], "evidence_ids": [tid]}
        for _ in range(_PARAMS["graph_steps"])
    ]
    with pytest.raises(ValueError, match="重复读取已提供证据"):
        await reading._update_graph(book_id, revision, data)
    saved = await reading.snapshot(book_id, identity)
    assert saved["pending_evidence"]
    _, after = await reading._load(book_id)
    assert after["consumed_seq"] == 0 and after["evidence_seq"] == 1
    assert after["graph"] == data["graph"]
    prompts = [p for k, p in model.calls if k == "study_graph"]
    feedback = json.loads(prompts[-1].split("\n")[-1])["input"]["tool_feedback"]
    assert len(prompts) == 2
    assert all(r["status"] == "already_provided" for r in feedback)


async def test_review_budget_keeps_only_read_sources_and_explicit_partial_status(
    study_host: tuple[_Host, StudyModel, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx.reading.dialogue import _PARAMS

    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    monkeypatch.setitem(_PARAMS, "evidence_steps", 0)
    monkeypatch.setitem(_PARAMS, "review_evidence_steps", 0)
    model.responses["study_review"] = [
        {"next_step": "answer", "issues": ["该解释尚未核实"], "tasks": []}
        for _ in range(_PARAMS["stage_attempts"])
    ]
    connection, tid = str(uuid4()), str(uuid4())
    identity = (await reading.enter(book_id, connection))["session"]["id"]
    await reading.accept(
        book_id, identity, connection, tid, "修桥为何失败", [], None, ""
    )
    await reading._reply(book_id, identity, tid)
    turn = (await reading.snapshot(book_id, identity))["session"]["turns"][0]
    assert turn["status"] == "committed" and turn["answer_status"] == "partial"
    assert "尚未完成" in turn["reply_body"] and "已实际读取" in turn["reply_body"]
    assert "暴雨使第一次施工失败" not in turn["reply_body"]
    assert "暴雨使第一次施工失败" in turn["reply"]
    assert turn["reply_sources"][0]["index"] == 2


async def test_oversized_overview_splits_only_failed_group_and_runs_other_groups(
    study_host: tuple[_Host, StudyModel, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx.llm.validation import policy
    from nyx.reading.sessions import _PARAMS, _frontier, _material_pack, _packed_chars

    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    await reading.enter(book_id, str(uuid4()))
    revision, data = await reading._load(book_id)
    data["materials"] = [
        {
            "id": f"unit-{i}",
            "start": i,
            "end": i,
            "summary": f"part-{i}:" + "文" * 400,
            "state": "completed",
            "retry_at": 0,
        }
        for i in range(1, 13)
    ]
    monkeypatch.setitem(_PARAMS, "context_chars", 1700)
    await reading.store.save_study(book_id, revision, data)
    revision, data = await reading._load(book_id)
    await reading._overview(book_id, revision, data)
    revision, data = await reading._load(book_id)
    first, other = data["overviews"][:2]
    original_children = list(first["children"])
    model.responses["study_overview"] = [
        {"summary": "长" * 2500, "unit_ids": first["children"]}
        for _ in range(policy("llm", "validation.attempts", int))
    ]
    await reading._overview(book_id, revision, data)
    revision, split = await reading._load(book_id)
    parent = next(m for m in split["overviews"] if m["id"] == first["id"])
    assert len(parent["children"]) == 2
    assert parent["children"] != original_children
    assert other["children"] == split["overviews"][1]["children"]
    await reading._overview(book_id, revision, split)
    _, after = await reading._load(book_id)
    assert after["overviews"][1]["state"] == "completed"
    prompts = [p for k, p in model.calls if k == "study_overview"]
    assert "actual_chars=2500" in prompts[1] and "target_chars=2200" in prompts[1]
    for _ in range(40):
        revision, data = await reading._load(book_id)
        pack = _material_pack(data, _frontier(data))
        if _packed_chars(pack) <= 1700:
            break
        await reading._overview(book_id, revision, data)
    else:
        pytest.fail("split overview did not converge")
    assert pack["coverage"]["provided_ranges"] == [[1, 12]]
    assert "part-1:" in json.dumps(pack) and "part-12:" in json.dumps(pack)


async def test_graph_rejects_only_unsupported_node_and_retains_its_evidence(
    study_host: tuple[_Host, StudyModel, str],
) -> None:
    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    connection = str(uuid4())
    identity = (await reading.enter(book_id, connection))["session"]["id"]
    ids = [str(uuid4()), str(uuid4())]
    for tid, message in zip(ids, ["暴雨造成第一次失败", "他们凭空学会了飞行"]):
        await reading.accept(book_id, identity, connection, tid, message, [], None, "")
        await reading._reply(book_id, identity, tid)
    paragraph = (await reading.reader.list_paragraphs(book_id, 2, 2))[0]
    model.responses["study_graph"] = [
        {
            "action": "patch",
            "reason": "分别检查两项解释",
            "deferred_evidence": [],
            "operations": [
                {
                    "op": "upsert",
                    "id": key,
                    "title": key,
                    "content": content,
                    "state": "explaining",
                    "sources": [{"paragraph_id": paragraph.id}],
                    "evidence": [tid],
                    "corrects": [],
                }
                for key, content, tid in zip(
                    ["rain", "flight"], ["暴雨造成第一次失败", "他们会飞行"], ids
                )
            ],
            "edges": [],
            "remove_edges": [],
        }
    ]
    model.responses["study_graph_review"] = [
        {
            "valid": True,
            "issues": [],
            "checks": [
                {
                    "id": "rain",
                    "user_supported": True,
                    "source_supported": True,
                    "reason": "用户解释与暴雨原文一致",
                },
                {
                    "id": "flight",
                    "user_supported": True,
                    "source_supported": False,
                    "reason": "暴雨段落不能支持飞行",
                },
            ],
        }
    ]
    revision, data = await reading._load(book_id)
    await reading._update_graph(book_id, revision, data)
    _, saved = await reading._load(book_id)
    assert {n["id"] for n in saved["graph"]["nodes"]} == {"rain"}
    assert saved["graph_deferred"] == [ids[1]]
    assert (await reading.snapshot(book_id, identity))["pending_evidence"]
    assert saved["history"][-1]["operations"][0]["id"] == "rain"
    new_id = str(uuid4())
    await reading.accept(
        book_id, identity, connection, new_id, "我修正了新的解释", [], None, ""
    )
    await reading._reply(book_id, identity, new_id)
    revision, current = await reading._load(book_id)
    await reading._update_graph(book_id, revision, current)
    latest = next(p for kind, p in reversed(model.calls) if kind == "study_graph")
    request = json.loads(latest.split("\n")[-1])["input"]
    assert [item["id"] for item in request["pending"]] == [new_id, ids[1]]


async def test_graph_reads_actual_book_source_before_using_reply_source_id(
    study_host: tuple[_Host, StudyModel, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    connection, tid = str(uuid4()), str(uuid4())
    identity = (await reading.enter(book_id, connection))["session"]["id"]
    await reading.accept(
        book_id, identity, connection, tid, "我认为暴雨造成失败", [], None, ""
    )
    await reading._reply(book_id, identity, tid)
    paragraph = (await reading.reader.list_paragraphs(book_id, 2, 2))[0]

    async def empty_search(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(reading, "search", empty_search)
    model.responses["study_graph"] = [
        {
            "action": "read",
            "node_ids": [],
            "evidence_ids": [],
            "paragraph_ids": [paragraph.id],
        },
        {
            "action": "patch",
            "reason": "补读后核对解释",
            "deferred_evidence": [],
            "operations": [
                {
                    "op": "upsert",
                    "id": "rain",
                    "title": "暴雨",
                    "content": "用户解释暴雨使施工失败",
                    "state": "explaining",
                    "sources": [{"paragraph_id": paragraph.id}],
                    "evidence": [tid],
                    "corrects": [],
                }
            ],
            "edges": [],
            "remove_edges": [],
        },
    ]
    revision, data = await reading._load(book_id)
    await reading._update_graph(book_id, revision, data)
    prompts = [p for kind, p in model.calls if kind == "study_graph"]
    first = json.loads(prompts[-2].split("\n")[-1])["input"]
    second = json.loads(prompts[-1].split("\n")[-1])["input"]
    assert first["passages"] == []
    assert first["evidence"][0]["book_sources"][0]["paragraph_id"] == paragraph.id
    receipt = second["tool_feedback"][0]
    assert receipt["status"] == "success" and receipt["log_id"]
    assert receipt["result"]["paragraph_ids"] == [paragraph.id]
    assert receipt["result"]["passages"][0]["text"] == paragraph.text
    assert not (await reading.snapshot(book_id, identity))["pending_evidence"]


async def test_review_can_spend_two_targeted_steps_after_normal_budget(
    study_host: tuple[_Host, StudyModel, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx.reading.dialogue import _PARAMS

    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    monkeypatch.setitem(_PARAMS, "evidence_steps", 1)
    connection, tid = str(uuid4()), str(uuid4())
    identity = (await reading.enter(book_id, connection))["session"]["id"]
    model.responses["study_evidence"] = [
        {"action": "excerpt", "start": 2, "end": 2},
        {
            "action": "finish",
            "summary": "暴雨",
            "gaps": [],
            "claims": [{"text": "暴雨影响施工", "sources": [2], "limitation": ""}],
            "checks": [
                {
                    "question": "修桥为何失败",
                    "status": "supported",
                    "sources": [2],
                    "gap": "",
                }
            ],
        },
        {"action": "search", "query": "桥最终建成", "start": None, "end": None},
        {
            "action": "finish",
            "summary": "暴雨后改变方案",
            "gaps": [],
            "claims": [{"text": "改变方案后建成", "sources": [3], "limitation": ""}],
            "checks": [
                {
                    "question": "修桥为何失败",
                    "status": "supported",
                    "sources": [2, 3],
                    "gap": "",
                }
            ],
        },
    ]
    model.responses["study_review"] = [
        {
            "next_step": "evidence",
            "issues": ["尚缺修正方案的结果"],
            "tasks": ["核对改变方案后的结果"],
            "questions": [{"question": "修桥为何失败", "status": "missing"}],
        }
    ]
    await reading.accept(
        book_id, identity, connection, tid, "修桥为何失败", [], None, ""
    )
    await reading._reply(book_id, identity, tid)
    saved = (await reading.snapshot(book_id, identity))["session"]["turns"][0]
    assert saved["status"] == "committed" and saved["answer_status"] == "complete"
    payloads = [
        json.loads(p.split("\n")[-1])["input"]
        for kind, p in model.calls
        if kind == "study_evidence"
    ]
    repairs = [p for p in payloads if p["targeted"]]
    assert repairs and repairs[0]["steps_remaining"] == _PARAMS["review_evidence_steps"]
    assert [t["task"] for t in repairs[0]["repair_tasks"]] == ["核对改变方案后的结果"]
    assert repairs[0]["agents_remaining"] == 0
    assert 3 in repairs[-1]["passages"][-1].values()


async def test_current_question_cannot_inherit_unstated_scope(
    study_host: tuple[_Host, StudyModel, str],
) -> None:
    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    book = await reading.reader.get_book(book_id)
    prior = {
        "scope": "第一段",
        "start": 1,
        "end": 1,
        "feedback": False,
        "user_requirement": "只谈第一段",
    }
    model.responses["study_scope"] = [{**prior}]
    current = await reading._presentation(book, prior, "后来桥修成了吗", "scope-test")
    assert current["start"] is None and current["user_requirement"] == ""
    assert len([p for kind, p in model.calls if kind == "study_scope"]) == 2


async def test_targeted_repeated_read_stops_and_keeps_missing_status(
    study_host: tuple[_Host, StudyModel, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx.reading.dialogue import _PARAMS

    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    monkeypatch.setitem(_PARAMS, "evidence_steps", 1)
    connection, tid = str(uuid4()), str(uuid4())
    identity = (await reading.enter(book_id, connection))["session"]["id"]
    finish = {
        "action": "finish",
        "summary": "只有暴雨证据",
        "gaps": [],
        "claims": [{"text": "暴雨", "sources": [2], "limitation": ""}],
        "checks": [
            {
                "question": "修桥为何失败",
                "status": "missing",
                "sources": [2],
                "gap": "没有核对结果",
            }
        ],
    }
    model.responses["study_evidence"] = [
        {"action": "search", "query": "暴雨", "start": None, "end": None},
        finish,
        {"action": "search", "query": "暴雨", "start": None, "end": None},
        {"action": "search", "query": "暴雨", "start": None, "end": None},
        finish,
    ]
    model.responses["study_review"] = [
        {
            "next_step": "evidence",
            "issues": ["尚缺结果"],
            "tasks": ["查最后结果"],
            "questions": [{"question": "修桥为何失败", "status": "missing"}],
        },
        {
            "next_step": "accept",
            "issues": [],
            "tasks": [],
            "questions": [{"question": "修桥为何失败", "status": "missing"}],
        },
    ]
    from nyx.tools.registry import ToolRegistry

    original = ToolRegistry.call
    actual: list[dict[str, Any]] = []

    async def traced(
        self: ToolRegistry, name: str, args: dict[str, Any], **kw: Any
    ) -> Any:
        if name == "book_search" and args["query"] == "暴雨":
            actual.append(args)
        return await original(self, name, args, **kw)

    monkeypatch.setattr(ToolRegistry, "call", traced)
    await reading.accept(
        book_id, identity, connection, tid, "修桥为何失败", [], None, ""
    )
    await reading._reply(book_id, identity, tid)
    turn = (await reading.snapshot(book_id, identity))["session"]["turns"][0]
    assert turn["status"] == "committed" and turn["answer_status"] == "partial"
    assert actual == [{"query": "暴雨", "start": None, "end": None}]
    payloads = [
        json.loads(p.split("\n")[-1])["input"]
        for kind, p in model.calls
        if kind == "study_evidence"
    ]
    assert len([p for p in payloads if p["targeted"]]) == 3
    assert payloads[-1]["steps_remaining"] == 0
    assert payloads[-1]["tool_feedback"][-1]["status"] == "already_provided"


async def test_semantic_review_accept_conflict_rewrites_answer_not_review_format(
    study_host: tuple[_Host, StudyModel, str],
) -> None:
    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    connection, tid = str(uuid4()), str(uuid4())
    identity = (await reading.enter(book_id, connection))["session"]["id"]
    model.responses["study_review"] = [
        {
            "next_step": "accept",
            "issues": ["解释过度"],
            "tasks": [],
            "conclusions": [
                {
                    "text": "暴雨使第一次施工失败",
                    "supported": False,
                    "excess": "不要扩大到所有施工",
                    "contrary": "",
                },
                {
                    "text": "正文还说所有施工都失败",
                    "supported": False,
                    "excess": "正文的额外断言也需修改",
                    "contrary": "",
                },
            ],
            "questions": [{"question": "修桥为何失败", "status": "inaccurate"}],
        },
        {
            "next_step": "accept",
            "issues": [],
            "tasks": [],
            "questions": [{"question": "修桥为何失败", "status": "answered"}],
        },
    ]
    await reading.accept(
        book_id, identity, connection, tid, "修桥为何失败", [], None, ""
    )
    await reading._reply(book_id, identity, tid)
    assert (await reading.snapshot(book_id, identity))["session"]["turns"][0][
        "status"
    ] == "committed"
    kinds = [kind for kind, _ in model.calls]
    assert kinds.count("study_review") == kinds.count("reply") == 2
    assert kinds.count("study_evidence") == 1


async def test_selected_quote_keeps_original_paragraph_and_link(
    study_host: tuple[_Host, StudyModel, str],
) -> None:
    host, model, book_id = study_host
    reader = host.apps["nyx"].reading
    reading = reader.study
    original = "作者序\n关注公众号获取书单\n暴雨使第一次施工失败"
    async with reading.store.db.transaction() as db:
        await db.execute(
            'UPDATE paragraphs SET text=? WHERE book_id=? AND "index"=2',
            (original, book_id),
        )
    connection, tid = str(uuid4()), str(uuid4())
    identity = (await reading.enter(book_id, connection))["session"]["id"]
    answer = {
        "think": "",
        "speak": "暴雨使第一次施工失败。[第2段](nyx-book:2)",
        "claims": [{"text": "暴雨使第一次施工失败", "sources": [2], "limitation": ""}],
        "quote_selections": [{"index": 2, "text": "伪造的原文"}],
    }
    model.responses["reply"] = [
        answer,
        {**answer, "quote_selections": [{"index": 2, "text": "暴雨使第一次施工失败"}]},
    ]
    await reading.accept(
        book_id, identity, connection, tid, "解释修桥失败，略过推广", [], None, ""
    )
    await reading._reply(book_id, identity, tid)
    turn = (await reading.snapshot(book_id, identity))["session"]["turns"][0]
    assert turn["status"] == "committed"
    assert "关注公众号" not in turn["reply"]
    assert "暴雨使第一次施工失败" in turn["reply_quotes"][0]
    assert "查看完整原段](nyx-book:2)" in turn["reply_quotes"][0]
    assert (await reader.list_paragraphs(book_id, 2, 2))[0].text == original
    assert sum(k == "reply" for k, _ in model.calls) == 2


async def test_targeted_checks_visit_each_missing_question_first(
    study_host: tuple[_Host, StudyModel, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx.reading.dialogue import _PARAMS

    host, model, book_id = study_host
    reading = host.apps["nyx"].reading.study
    monkeypatch.setitem(_PARAMS, "evidence_steps", 1)
    monkeypatch.setitem(_PARAMS, "search_limit", 1)
    questions = ["失败原因", "最终结果"]
    model.responses["study_understand"] = [
        {
            "question": "失败原因和最终结果",
            "requirements": [],
            "carry_requirements": [],
            "whole_book": False,
            "subquestions": questions,
            "history_query": None,
            "understanding_query": None,
        }
    ]
    finish: dict[str, Any] = {
        "action": "finish",
        "summary": "待补查",
        "claims": [],
        "gaps": [],
        "checks": [
            {"question": q, "status": "missing", "sources": [], "gap": "未核对"}
            for q in questions
        ],
    }
    model.responses["study_evidence"] = [
        {"action": "excerpt", "start": 1, "end": 3},
        finish,
        {"action": "search", "query": "未命中甲", "start": None, "end": None},
        {"action": "search", "query": "桥最终建成", "start": None, "end": None},
    ]
    model.responses["study_review"] = [
        {
            "next_step": "evidence",
            "issues": ["额外研究"],
            "questions": [{"question": q, "status": "missing"} for q in questions],
            "tasks": [
                {
                    "question": "未来研究",
                    "user_requirement": "用户没有要求的事项",
                    "gap": "额外研究",
                    "task": "通读全书",
                }
            ],
        },
        {
            "next_step": "evidence",
            "issues": ["两项缺证"],
            "questions": [{"question": q, "status": "missing"} for q in questions],
            "tasks": [
                {
                    "question": q,
                    "user_requirement": q,
                    "gap": "未核对",
                    "task": "查" + q,
                }
                for q in questions
            ],
        },
    ]
    connection, tid = str(uuid4()), str(uuid4())
    identity = (await reading.enter(book_id, connection))["session"]["id"]
    await reading.accept(
        book_id, identity, connection, tid, "失败原因和最终结果", [], None, ""
    )
    await reading._reply(book_id, identity, tid)
    assert (await reading.snapshot(book_id, identity))["session"]["turns"][0][
        "status"
    ] == "committed"
    payloads = [
        json.loads(p.split("\n")[-1])["input"]
        for k, p in model.calls
        if k == "study_evidence"
    ]
    reads = [p for p in payloads if p["targeted"] and p["steps_remaining"]]
    assert [p["focus_task"]["question"] for p in reads] == questions
    receipt = next(
        r for p in payloads for r in p["tool_feedback"] if "requested_args" in r
    )
    assert receipt["requested_args"] == {"start": 1, "end": 3}
    assert receipt["args"] == {"start": 1, "end": 1}
    assert receipt["returned_indices"] == [1]
