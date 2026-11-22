"""Book understanding inside the reading module; raw dialogue never enters the bus.

The role's existing database owns the book document, session snapshots and graph
receipts. Slow work uses optimistic commits so leaving, corrections and deletion
can invalidate a generated candidate without losing its accepted evidence.
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import uuid4

from nyx.behavior.context import behavior_context
from nyx.behavior.store import skill_snapshot
from nyx.expression.pipeline import ReplyState
from nyx.policy import policy
from nyx.reading.outputs import SummaryTooLong, complete
from nyx.tools.execution import read_tool
from nyx.tools.registry import ToolRegistry
from nyx.types import Book, Message, Paragraph, Tool

if TYPE_CHECKING:
    from nyx.expression.facade import ExpressionFacade
    from nyx.reading.facade import ReadingFacade

_PARAMS = policy("reading", "study.parameters", dict[str, int])
_COPY = policy("reading", "study.copy", dict[str, str])
_PROMPTS = policy("reading", "study.prompts", dict[str, str])
_STATES = policy("reading", "study.states", tuple[str, ...])
_LOGGER = logging.getLogger(__name__)


class StudyConflictError(ValueError):
    """A lifecycle, selection or optimistic-write conflict; API maps it to 409."""


def _session(data: dict[str, Any], identity: str) -> dict[str, Any]:
    for session in data["sessions"]:
        if session["id"] == identity:
            return cast(dict[str, Any], session)
    raise StudyConflictError(_COPY["error_scope"])


def _job() -> dict[str, Any]:
    return {"state": "queued", "attempts": 0, "retry_at": 0, "error": ""}


def _selection_text(text: str) -> str:
    """Match the small Markdown renderer's visible text across formatting."""
    text = re.sub(r"\[([^\]\n]+)\]\([^\s)]+\)", r"\1", text)
    text = re.sub(
        r"^\s*(?:#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)", "", text, flags=re.MULTILINE
    )
    text = re.sub(r"^```[^\n]*\n?|\n?```$", "", text, flags=re.MULTILINE)
    text = re.sub(
        r"\*\*([^*\n]+)\*\*|\*([^*\n]+)\*|`([^`\n]+)`",
        lambda m: next(g for g in m.groups() if g is not None),
        text,
    )
    return " ".join(text.split())


def _terms(query: str) -> set[str]:
    words = re.findall(r"[\w]+", query.casefold())
    terms = set(words)
    for word in words:
        if any("\u4e00" <= char <= "\u9fff" for char in word):
            terms.update(word[i : i + 2] for i in range(len(word) - 1))
    return terms


def _lookup(items: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    terms = _terms(query)
    ranked = sorted(
        (
            (
                sum(
                    json.dumps(item, ensure_ascii=False).casefold().count(term)
                    for term in terms
                ),
                item,
            )
            for item in items
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    return [item for score, item in ranked if score][: _PARAMS["search_limit"]]


def _ranges(units: list[dict[str, Any]]) -> list[list[int]]:
    result: list[list[int]] = []
    for unit in sorted(units, key=lambda m: m["start"]):
        if result and unit["start"] <= result[-1][1] + 1:
            result[-1][1] = max(result[-1][1], unit["end"])
        else:
            result.append([unit["start"], unit["end"]])
    return result


def _frontier(data: dict[str, Any]) -> list[dict[str, Any]]:
    ready = {m["id"]: m for m in data["materials"] if m["state"] == "completed"}
    covered: set[str] = set()
    selected: list[dict[str, Any]] = []
    for unit in sorted(data["overviews"], key=lambda m: m["level"], reverse=True):
        leaves = set(unit.get("material_ids", []))
        if (
            unit["state"] == "completed"
            and leaves
            and leaves <= ready.keys()
            and not leaves & covered
        ):
            selected.append(unit)
            covered.update(leaves)
    selected.extend(m for key, m in ready.items() if key not in covered)
    return sorted(selected, key=lambda m: m["start"])


def _material_pack(
    data: dict[str, Any],
    units: list[dict[str, Any]],
) -> dict[str, Any]:
    ready = [m for m in data["materials"] if m["state"] == "completed"]
    ids = {
        identity
        for unit in units
        for identity in unit.get("material_ids", [unit["id"]])
    }
    provided = [m for m in ready if m["id"] in ids]
    return {
        "materials": [
            {k: m[k] for k in ("id", "start", "end", "summary")} for m in units
        ],
        "coverage": {
            "complete": len(provided) == len(data["materials"]),
            "prepared": len(ready),
            "provided": len(provided),
            "total": len(data["materials"]),
            "prepared_ranges": _ranges(ready),
            "provided_ranges": _ranges(provided),
            "gaps": [
                {k: m[k] for k in ("start", "end", "state")}
                for m in data["materials"]
                if m["state"] != "completed"
            ],
        },
    }


def _packed_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False))


def _model_reply(turn: dict[str, Any]) -> str:
    return json.dumps(
        {
            "body": turn.get("reply_body", _COPY["legacy_reply"]),
            "sources": turn.get("reply_sources", []),
            "reply_id": turn.get("reply_id", turn["id"]),
        },
        ensure_ascii=False,
    )


class ReadingSessions:
    def __init__(self, reader: ReadingFacade, expression: ExpressionFacade) -> None:
        self.reader, self.expression, self.store = reader, expression, reader._store
        self.connections: dict[str, tuple[str, float]] = {}
        self.replies: dict[str, asyncio.Task[None]] = {}
        self._wake = asyncio.Event()

    def occupied(self) -> bool:
        now = time.monotonic()
        return any(until > now for _, until in self.connections.values())

    def _connected(self, session_id: str, connection: str) -> bool:
        entry = self.connections.get(connection)
        return (
            entry is not None and entry[0] == session_id and entry[1] > time.monotonic()
        )

    async def _load(self, book_id: str) -> tuple[int, dict[str, Any]]:
        found = await self.store.load_study(book_id)
        if found is None:
            raise StudyConflictError(_COPY["error_scope"])
        return found

    async def enter(self, book_id: str, connection: str) -> dict[str, Any]:
        book = await self.reader.get_book(book_id)
        paragraphs = await self.store.list_paragraphs(book_id, 1, book.total_paragraphs)
        # Split only at real paragraph boundaries; chapter starts remain separate.
        materials: list[dict[str, Any]] = []
        for paragraph in paragraphs:
            if (
                not materials
                or paragraph.is_chapter_start
                or materials[-1]["chars"] + len(paragraph.text)
                > _PARAMS["material_chars"]
            ):
                materials.append(
                    {
                        "id": f"material:{paragraph.id}",
                        "start": paragraph.index,
                        "end": paragraph.index,
                        "chars": 0,
                        "summary": "",
                        **_job(),
                    }
                )
            materials[-1]["end"] = paragraph.index
            materials[-1]["chars"] += len(paragraph.text)
        actual_book, identity = await self.store.open_study(
            book_id,
            {
                "content_hash": book.content_hash,
                "materials": materials,
                "sessions": [],
                "graph": {"version": 0, "nodes": [], "edges": [], "deleted": []},
                "history": [],
                "candidates": [],
                "evidence_seq": 0,
                "consumed_seq": 0,
                "graph_work": _job(),
                "overviews": [],
            },
        )
        self.connections[connection] = (
            identity,
            time.monotonic() + _PARAMS["lease_seconds"],
        )
        self._wake.set()
        return await self.snapshot(actual_book, identity)

    async def heartbeat(self, book_id: str, identity: str, connection: str) -> None:
        _, data = await self._load(book_id)
        if _session(data, identity)["ended_at"] is not None:
            raise StudyConflictError(_COPY["error_scope"])
        self.connections[connection] = (
            identity,
            time.monotonic() + _PARAMS["lease_seconds"],
        )

    async def detach(self, book_id: str, identity: str, connection: str) -> None:
        self.connections.pop(connection, None)
        while True:
            revision, data = await self._load(book_id)
            session = _session(data, identity)
            changed = False
            for turn in session["turns"]:
                if turn["connection"] == connection and turn["status"] == "running":
                    turn.update(status="queued", token=None)
                    task = self.replies.get(turn["id"])
                    if task is not None:
                        task.cancel()
                    changed = True
            if not changed or await self.store.save_study(book_id, revision, data):
                return

    async def snapshot(
        self, book_id: str, identity: str | None = None
    ) -> dict[str, Any]:
        revision, data = await self._load(book_id)
        session = _session(data, identity) if identity else data["sessions"][-1]
        book = await self.reader.get_book(book_id)
        graph = data["graph"]
        history = data["history"]
        presentation = session["presentation"]
        if presentation["start"] is not None:
            nodes = [
                node
                for node in graph["nodes"]
                if all(
                    presentation["start"] <= source["index"] <= presentation["end"]
                    for source in node["sources"]
                )
            ]
            ids = {node["id"] for node in nodes}
            graph = {
                **graph,
                "nodes": nodes,
                "edges": [
                    edge
                    for edge in graph["edges"]
                    if edge["from"] in ids and edge["to"] in ids
                ],
            }
            history = []
        return {
            "revision": revision,
            "book": asdict(book),
            "session": session,
            "graph": graph,
            "history": history,
            "materials": data["materials"],
            "graph_work": data["graph_work"],
            "pending_evidence": (
                data["evidence_seq"] > data["consumed_seq"]
                or bool(data.get("graph_deferred"))
            ),
            "sessions": [
                {
                    "id": s["id"],
                    "created_at": s["created_at"],
                    "ended_at": s["ended_at"],
                    "results": s["results"],
                }
                for s in data["sessions"]
            ],
            "evidence": [
                {
                    "id": t["id"],
                    "session_id": s["id"],
                    "input": t["input"],
                    "reply": t["reply"],
                }
                for s in data["sessions"]
                for t in s["turns"]
                if t["status"] == "committed"
            ],
            "feedback_jobs": [
                t["feedback"]
                for s in data["sessions"]
                for t in s["turns"]
                if "feedback" in t
            ],
        }

    async def accept(
        self,
        book_id: str,
        identity: str,
        connection: str,
        input_id: str,
        message: str,
        notes: list[str],
        annotation: dict[str, Any] | None,
        presentation: str,
    ) -> dict[str, Any]:
        if not message.strip() and not notes and annotation is None:
            raise ValueError(_COPY["error_input"])
        payload = {
            "message": message,
            "notes": notes,
            "annotation": annotation,
            "presentation": presentation,
        }
        while True:
            revision, data = await self._load(book_id)
            session = _session(data, identity)
            prior = next((t for t in session["turns"] if t["id"] == input_id), None)
            if prior is not None:
                if prior["input"] != payload:
                    raise StudyConflictError(_COPY["error_replay"])
                return cast(dict[str, Any], prior)
            if session["ended_at"] is not None:
                raise StudyConflictError(_COPY["error_scope"])
            if not self._connected(identity, connection):
                raise StudyConflictError(_COPY["error_lease"])
            if annotation is not None:
                reply = next(
                    (
                        t
                        for t in session["turns"]
                        if t["id"] == annotation["reply_event_id"]
                        and t["status"] == "committed"
                    ),
                    None,
                )
                if (
                    reply is None
                    or not _selection_text(annotation["quote"])
                    or _selection_text(annotation["quote"])
                    not in _selection_text(reply["reply"])
                ):
                    raise ValueError(_COPY["error_quote"])
            turn = {
                "id": input_id,
                "input": payload,
                "connection": connection,
                "status": "queued",
                "created_at": time.time(),
                "reply": "",
                "token": None,
                "error": "",
                "evidence_seq": None,
            }
            session["turns"].append(turn)
            if await self.store.save_study(book_id, revision, data):
                self._wake.set()
                return turn

    async def retry_turn(
        self,
        book_id: str,
        identity: str,
        input_id: str,
        connection: str,
    ) -> None:
        while True:
            revision, data = await self._load(book_id)
            session = _session(data, identity)
            if session["ended_at"] is not None or not self._connected(
                identity, connection
            ):
                raise StudyConflictError(_COPY["error_scope"])
            turn = next((t for t in session["turns"] if t["id"] == input_id), None)
            if turn is None:
                raise StudyConflictError(_COPY["error_scope"])
            if turn["status"] in ("committed", "running"):
                return
            turn.update(status="queued", error="", token=None, connection=connection)
            if await self.store.save_study(book_id, revision, data):
                self._wake.set()
                return

    async def leave(self, book_id: str, identity: str) -> dict[str, Any]:
        while True:
            revision, data = await self._load(book_id)
            session = _session(data, identity)
            if session["ended_at"] is not None:
                return session
            session["ended_at"] = time.time()
            for turn in session["turns"]:
                if turn["status"] != "committed":
                    turn.update(status="incomplete", token=None)
            # This exact source snapshot, including early corrections, never changes.
            session["snapshot"] = {
                "session_id": identity,
                "book_id": book_id,
                "created_at": session["created_at"],
                "ended_at": session["ended_at"],
                "turns": json.loads(json.dumps(session["turns"])),
            }
            session["results"] = {
                kind: _job() for kind in ("summary", "memory", "reflection")
            }
            if await self.store.save_study(
                book_id, revision, data, end_session=identity
            ):
                for connection, (sid, _) in list(self.connections.items()):
                    if sid == identity:
                        self.connections.pop(connection, None)
                for turn in session["turns"]:
                    if turn["status"] == "incomplete" and turn["id"] in self.replies:
                        self.replies[turn["id"]].cancel()
                self._wake.set()
                return session

    async def retry(self, book_id: str) -> None:
        while True:
            revision, data = await self._load(book_id)
            jobs = [
                *data["materials"],
                *data["overviews"],
                data["graph_work"],
                *(job for s in data["sessions"] for job in s["results"].values()),
                *(
                    t["feedback"]
                    for s in data["sessions"]
                    for t in s["turns"]
                    if "feedback" in t
                ),
            ]
            for job in jobs:
                if job["state"] == "failed":
                    job.update(_job())
            if await self.store.save_study(book_id, revision, data):
                self._wake.set()
                return

    async def search(
        self,
        book_id: str,
        query: str,
        start: int | None = None,
        end: int | None = None,
    ) -> list[dict[str, Any]]:
        book = await self.reader.get_book(book_id)
        if (start is None) != (end is None) or (
            start is not None
            and not (1 <= start <= cast(int, end) <= book.total_paragraphs)
        ):
            raise ValueError("search range must belong to the current book")
        paragraphs = await self.store.list_paragraphs(
            book_id,
            start or 1,
            end or book.total_paragraphs,
        )
        # Candidates already belong to this book, including its final paragraphs.
        terms = _terms(query)
        ranked = sorted(
            paragraphs,
            key=lambda p: sum(p.text.casefold().count(term) for term in terms),
            reverse=True,
        )
        hits = [p for p in ranked if any(term in p.text.casefold() for term in terms)]
        return [self._source(p) for p in hits[: _PARAMS["search_limit"]]]

    @staticmethod
    def _source(paragraph: Paragraph) -> dict[str, Any]:
        return {
            "paragraph_id": paragraph.id,
            "index": paragraph.index,
            "text": paragraph.text[: _PARAMS["excerpt_chars"]],
            "section": asdict(paragraph.section) if paragraph.section else None,
            "promotion": bool(
                re.search(
                    policy("reading", "study.promotion_pattern", str), paragraph.text
                )
            ),
        }

    async def excerpt(self, book_id: str, start: int, end: int) -> list[dict[str, Any]]:
        paragraphs = await self.reader.list_paragraphs(book_id, start, end)
        return [self._source(p) for p in paragraphs[: _PARAMS["search_limit"]]]

    async def _complete(
        self,
        kind: str,
        payload: Any,
        correlation: str,
        validate: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        system = _PROMPTS[kind].replace(
            "{summary_chars}",
            str(
                payload.get("summary_chars", _PARAMS["overview_chars"])
                if kind == "overview"
                else _PARAMS["summary_chars"]
            ),
        )
        if kind in ("material", "graph", "overview"):
            system = _PROMPTS["material_boundary"] + system
        token = behavior_context.set({"scope": "reading", "scenario": kind})
        try:
            return await complete(
                self.reader._llm,
                self.reader._evaluator,
                kind,
                system,
                payload,
                correlation,
                validate,
            )
        finally:
            behavior_context.reset(token)

    async def _sources(self, book_id: str, nodes: list[dict[str, Any]]) -> None:
        book = await self.reader.get_book(book_id)
        paragraphs = {
            p.id: p
            for p in await self.store.list_paragraphs(book_id, 1, book.total_paragraphs)
        }
        for node in nodes:
            if not node.get("sources"):
                raise ValueError(_COPY["error_material"])
            for source in node["sources"]:
                paragraph = paragraphs.get(source["paragraph_id"])
                if paragraph is None:
                    raise ValueError(_COPY["error_material"])
                source["index"] = paragraph.index
                source["quote"] = paragraph.text

    async def _presentation(
        self,
        book: Book,
        prior: dict[str, Any],
        message: str,
        correlation: str,
    ) -> dict[str, Any]:
        structure = await self.store.list_paragraphs(book.id, 1, book.total_paragraphs)

        async def validate(value: dict[str, Any]) -> None:
            if value["scope"] or value["start"] is not None:
                if (
                    not value["user_requirement"]
                    or value["user_requirement"] not in message
                ):
                    raise ValueError("scope requires verbatim current user wording")
            elif value["user_requirement"]:
                raise ValueError("unrestricted scope has no user_requirement")
            if value["start"] is not None and not (
                isinstance(value["start"], int)
                and isinstance(value["end"], int)
                and 1 <= value["start"] <= value["end"] <= book.total_paragraphs
            ):
                raise ValueError(_COPY["error_material"])
            if (value["start"] is None) != (value["end"] is None):
                raise ValueError(_COPY["error_material"])
            if not isinstance(value["feedback"], bool):
                raise ValueError(_COPY["error_material"])

        return await self._complete(
            "scope",
            {
                "prior": prior if prior.get("user_requirement") else None,
                "message": message,
                "structure": [
                    {"index": p.index, "heading": p.text}
                    for p in structure
                    if p.is_chapter_start
                ],
                "total_paragraphs": book.total_paragraphs,
            },
            correlation,
            validate,
        )

    async def _reply(self, book_id: str, identity: str, input_id: str) -> None:
        execution = str(uuid4())
        claimed = [input_id]
        try:
            while True:
                revision, data = await self._load(book_id)
                session = _session(data, identity)
                turn = next(t for t in session["turns"] if t["id"] == input_id)
                if any(t["status"] == "running" for t in session["turns"]):
                    return
                if (
                    session["ended_at"] is not None
                    or turn["status"] != "queued"
                    or not self._connected(identity, turn["connection"])
                ):
                    return
                turn.update(status="running", token=execution)
                if await self.store.save_study(book_id, revision, data):
                    break
            payload = turn["input"]
            book = await self.reader.get_book(book_id)
            presentation = await self._presentation(
                book,
                session["presentation"],
                payload["message"],
                f"reading:{identity}:{input_id}",
            )
            feedback_ids: set[str] = {input_id} if presentation["feedback"] else set()
            while True:
                scope_revision, scope_data = await self._load(book_id)
                current_session = _session(scope_data, identity)
                current_turn = next(
                    t for t in current_session["turns"] if t["id"] == input_id
                )
                if (
                    current_session["ended_at"] is not None
                    or current_turn["token"] != execution
                ):
                    raise asyncio.CancelledError
                current_session["presentation"] = presentation
                if await self.store.save_study(book_id, scope_revision, scope_data):
                    break
            raw = json.dumps([{"input_id": input_id, **payload}], ensure_ascii=False)
            history = [
                Message(
                    role=cast(Literal["user", "nyx"], role),
                    content=text,
                    timestamp=t["created_at"],
                    event_id=t["id"],
                )
                for t in session["turns"]
                if t["status"] == "committed"
                for role, text in (
                    (
                        "user",
                        json.dumps(
                            {"input_id": t["id"], **t["input"]}, ensure_ascii=False
                        ),
                    ),
                    ("nyx", _model_reply(t)),
                )
            ][-_PARAMS["recent_turns"] * 2 :]
            while True:
                overview_revision, overview_data = await self._load(book_id)
                pack = _material_pack(overview_data, _frontier(overview_data))
                if _packed_chars(pack) <= _PARAMS["context_chars"]:
                    break
                await self._overview(book_id, overview_revision, overview_data)
                _, latest = await self._load(book_id)
                if _packed_chars(
                    _material_pack(latest, _frontier(latest))
                ) >= _packed_chars(pack):
                    if any(m["state"] == "queued" for m in latest["overviews"]):
                        await asyncio.sleep(_PARAMS["worker_ms"] / 1000)
                    elif any(m["state"] == "failed" for m in latest["overviews"]):
                        raise ValueError(_COPY["error_material"])
            structure = await self.store.list_paragraphs(
                book_id,
                1,
                book.total_paragraphs,
            )
            chapter_index = [
                {
                    "start": p.index,
                    "end": next(
                        (
                            q.index - 1
                            for q in structure
                            if q.is_chapter_start and q.index > p.index
                        ),
                        book.total_paragraphs,
                    ),
                    "title": p.section.title if p.section else p.text.splitlines()[0],
                }
                for p in structure
                if p.is_chapter_start
            ]
            context_data = {
                "book": asdict(book),
                **pack,
                "chapter_index": chapter_index,
                "presentation": presentation,
                # Missing only in documents written before staged replies existed.
                "active_requirements": session.get("requirements", []),
            }
            context = json.dumps(context_data, ensure_ascii=False)

            async def collect(state: ReplyState) -> dict[str, Any]:
                nonlocal presentation
                while True:
                    revision, current = await self._load(book_id)
                    current_session = _session(current, identity)
                    owned = [t for t in current_session["turns"] if t["id"] in claimed]
                    if current_session["ended_at"] is not None or any(
                        t["token"] != execution
                        or not self._connected(identity, t["connection"])
                        for t in owned
                    ):
                        raise asyncio.CancelledError
                    added = [
                        t
                        for t in current_session["turns"]
                        if t["status"] == "queued"
                        and self._connected(identity, t["connection"])
                    ]
                    if not added:
                        return {"needs_refresh": False}
                    for item in added:
                        item.update(status="running", token=execution)
                    if await self.store.save_study(book_id, revision, current):
                        claimed.extend(t["id"] for t in added)
                        break
                presentation = await self._presentation(
                    book,
                    presentation,
                    "\n".join(t["input"]["message"] for t in added),
                    f"reading:{identity}:{added[-1]['id']}",
                )
                if presentation["feedback"]:
                    feedback_ids.update(t["id"] for t in added)
                while True:
                    scope_revision, latest = await self._load(book_id)
                    active = _session(latest, identity)
                    if active["ended_at"] is not None or any(
                        t["token"] != execution
                        for t in active["turns"]
                        if t["id"] in claimed
                    ):
                        raise asyncio.CancelledError
                    active["presentation"] = presentation
                    if await self.store.save_study(book_id, scope_revision, latest):
                        break
                message = json.dumps(
                    [
                        {"input_id": t["id"], **t["input"]}
                        for t in current_session["turns"]
                        if t["id"] in claimed
                    ],
                    ensure_ascii=False,
                )
                return {
                    "message": message,
                    "reading_context": json.dumps(
                        {**context_data, "presentation": presentation},
                        ensure_ascii=False,
                    ),
                    "input_revision": state["input_revision"] + 1,
                    "think": [],
                    "speak": [],
                    "ask": None,
                    "round": 0,
                    "round_times": [],
                    "needs_continuation": False,
                    "draft_revision": -1,
                    "needs_refresh": False,
                    "next_action": "answer",
                }

            async def pending(ids: list[str], answered: str | None) -> bool:
                _, current = await self._load(book_id)
                return any(
                    t["status"] == "queued"
                    and self._connected(identity, t["connection"])
                    for t in _session(current, identity)["turns"]
                )

            async def commit(state: ReplyState) -> str:
                if state["fallback"]:
                    raise ValueError(_COPY["error_empty"])
                reply = "\n".join(state["speak"])
                body = reply
                selections = {
                    s["index"]: s["text"]
                    for s in json.loads(state["reading_context"]).get(
                        "quote_selections", []
                    )
                }
                quote_blocks: list[str] = []
                sources: list[dict[str, Any]] = []
                indices = dict.fromkeys(
                    int(index) for index in re.findall(r"nyx-book:(\d+)", reply)
                )
                for index in indices:
                    if not 1 <= index <= book.total_paragraphs:
                        raise ValueError(_COPY["error_material"])
                    paragraph = (
                        await self.reader.list_paragraphs(book_id, index, index)
                    )[0]
                    selected = selections.get(index, paragraph.text)
                    if not selected or selected not in paragraph.text:
                        raise ValueError("quote selection must be exact original text")
                    quote = "\n".join("> " + line for line in selected.split("\n"))
                    label = _COPY[
                        "source_selection_label"
                        if index in selections
                        else "source_full_label"
                    ].format(title=book.title, index=index)
                    quote_blocks.append(f"{quote}\n>\n> [{label}](nyx-book:{index})")
                    sources.append({"paragraph_id": paragraph.id, "index": index})
                reply = "\n\n".join([body, *quote_blocks])
                while True:
                    if await pending([], None):
                        return "refresh"
                    current_revision, current = await self._load(book_id)
                    current_session = _session(current, identity)
                    current_turn = next(
                        t for t in current_session["turns"] if t["id"] == input_id
                    )
                    if (
                        current_session["ended_at"] is not None
                        or current_turn["token"] != execution
                    ):
                        raise asyncio.CancelledError
                    owned = [t for t in current_session["turns"] if t["id"] in claimed]
                    if any(
                        t["token"] != execution
                        or not self._connected(identity, t["connection"])
                        for t in owned
                    ):
                        raise asyncio.CancelledError
                    if any(
                        t["status"] == "queued"
                        and self._connected(identity, t["connection"])
                        for t in current_session["turns"]
                    ):
                        return "refresh"
                    for item in owned:
                        current["evidence_seq"] += 1
                        item.update(
                            status="committed",
                            reply=reply if item is owned[-1] else "",
                            reply_body=body if item is owned[-1] else "",
                            reply_sources=sources if item is owned[-1] else [],
                            reply_quotes=quote_blocks if item is owned[-1] else [],
                            reply_selections=selections if item is owned[-1] else {},
                            answer_status=json.loads(state["reading_context"])[
                                "answer_status"
                            ],
                            reply_id=owned[-1]["id"],
                            evidence_seq=current["evidence_seq"],
                            committed_at=time.time(),
                            token=None,
                        )
                    current_session["requirements"] = json.loads(
                        state["reading_context"]
                    )["active_requirements"]
                    for item in owned:
                        if item["input"]["annotation"] or item["id"] in feedback_ids:
                            assert state["skill"] is not None
                            item["feedback"] = _job()
                            item["skill_snapshot"] = skill_snapshot(state["skill"])
                    current["graph_work"].update(_job())
                    if await self.store.save_study(book_id, current_revision, current):
                        self._wake.set()
                        return "committed"

            async def search_book(
                query: str,
                start: int | None = None,
                end: int | None = None,
            ) -> list[dict[str, Any]]:
                return await self.search(book_id, query, start, end)

            async def locate_book(query: str) -> list[dict[str, Any]]:
                _, latest = await self._load(book_id)
                chapters = [
                    {
                        **chapter,
                        "summary": "\n".join(
                            m["summary"]
                            for m in latest["materials"]
                            if m["state"] == "completed"
                            and chapter["start"] <= m["start"] <= chapter["end"]
                        ),
                    }
                    for chapter in chapter_index
                ]
                return [
                    {k: c[k] for k in ("title", "start", "end")}
                    for c in _lookup(chapters, query)
                ]

            async def excerpt_book(start: int, end: int) -> list[dict[str, Any]]:
                return await self.excerpt(book_id, start, end)

            tools = ToolRegistry(self.expression._tools._store)
            tools.register(
                Tool(
                    "book_search",
                    _PROMPTS["tool_search"],
                    {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "start": {"type": ["integer", "null"]},
                            "end": {"type": ["integer", "null"]},
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                    search_book,
                )
            )
            tools.register(
                Tool(
                    "book_excerpt",
                    _PROMPTS["tool_excerpt"],
                    {
                        "type": "object",
                        "properties": {
                            "start": {"type": "integer", "minimum": 1},
                            "end": {"type": "integer", "minimum": 1},
                        },
                        "required": ["start", "end"],
                        "additionalProperties": False,
                    },
                    excerpt_book,
                )
            )

            async def history_book(query: str) -> list[dict[str, Any]]:
                _, latest = await self._load(book_id)
                items = [
                    {
                        "id": t["id"],
                        "input": t["input"],
                        "reply": t.get("reply_body", t["reply"]),
                        "sources": t.get("reply_sources", []),
                    }
                    for t in _session(latest, identity)["turns"]
                    if t["status"] == "committed"
                ]
                return _lookup(items, query)

            async def understanding_book(query: str) -> list[dict[str, Any]]:
                _, latest = await self._load(book_id)
                return _lookup(latest["graph"]["nodes"], query)

            for name, handler in (
                ("book_locate", locate_book),
                ("book_history", history_book),
                ("book_understanding", understanding_book),
            ):
                tools.register(
                    Tool(
                        name,
                        _PROMPTS["tool_locate"]
                        if name == "book_locate"
                        else _PROMPTS[name],
                        {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                            "additionalProperties": False,
                        },
                        handler,
                    )
                )
            await self.expression.reply_in_reading(
                raw,
                f"reading:{identity}:{input_id}",
                history,
                context,
                tools,
                collect,
                commit,
                pending,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            while True:
                found = await self.store.load_study(book_id)
                if found is None:
                    return
                revision, data = found
                session = _session(data, identity)
                owned = [t for t in session["turns"] if t["token"] == execution]
                if not owned or session["ended_at"] is not None:
                    break
                for turn in owned:
                    turn.update(
                        status="failed",
                        error=f"{type(error).__name__}: {error}",
                        token=None,
                    )
                if await self.store.save_study(book_id, revision, data):
                    break
            _LOGGER.exception("Reading reply failed: %s", input_id)
        finally:
            if not self.store.db.is_closed:
                while True:
                    found = await self.store.load_study(book_id)
                    if found is None:
                        break
                    revision, data = found
                    session = _session(data, identity)
                    owned = [t for t in session["turns"] if t["token"] == execution]
                    if not owned or session["ended_at"] is not None:
                        break
                    for turn in owned:
                        turn.update(status="queued", token=None)
                    if await self.store.save_study(book_id, revision, data):
                        break

    async def _prepare(
        self,
        book_id: str,
        revision: int,
        data: dict[str, Any],
        material: dict[str, Any],
    ) -> None:
        passages = await self.store.list_paragraphs(
            book_id, material["start"], material["end"]
        )
        allowed = {p.id for p in passages}

        async def validate(value: dict[str, Any]) -> None:
            invalid = [
                s["paragraph_id"]
                for n in value["nodes"]
                for s in n["sources"]
                if s["paragraph_id"] not in allowed
            ]
            if invalid:
                raise ValueError(
                    "sources.paragraph_id unknown: "
                    + json.dumps(invalid)
                    + "; use exact IDs: "
                    + json.dumps(sorted(allowed))
                )
            if len(value["summary"]) > _PARAMS["summary_chars"]:
                raise ValueError(f"summary exceeds {_PARAMS['summary_chars']} chars")

        output = await self._complete(
            "material",
            [self._source(p) | {"text": p.text} for p in passages],
            material["id"],
            validate,
        )
        nodes = cast(list[dict[str, Any]], output["nodes"])
        if not isinstance(output["summary"], str) or not output["summary"].strip():
            raise ValueError(_COPY["error_material"])
        await self._sources(book_id, nodes)
        allowed = {p.id for p in passages}
        if any(
            source["paragraph_id"] not in allowed
            for n in nodes
            for source in n["sources"]
        ):
            raise ValueError(_COPY["error_material"])
        material.update(state="completed", summary=output["summary"], error="")
        graph = data["graph"]
        added: list[dict[str, Any]] = []
        for node in nodes:
            node_id = hashlib.sha256(
                (material["id"] + node["title"]).encode()
            ).hexdigest()
            if node_id in graph["deleted"] or any(
                n["id"] == node_id for n in graph["nodes"]
            ):
                continue
            node.update(
                id=node_id,
                version=1,
                state="unassessed",
                evidence=[],
                corrects=[],
                origin={"kind": "material", "id": material["id"]},
            )
            graph["nodes"].append(node)
            added.append(node)
        graph["version"] += 1
        data["history"].append(
            {
                "id": str(uuid4()),
                "version": graph["version"],
                "created_at": time.time(),
                "kind": "material",
                "source_id": material["id"],
                "operations": added,
            }
        )
        # A discussion or deletion during preparation invalidates the whole candidate.
        await self.store.save_study(book_id, revision, data)

    async def _overview(
        self,
        book_id: str,
        revision: int,
        data: dict[str, Any],
    ) -> None:
        units = _frontier(data)
        pack = _material_pack(data, units)
        if _packed_chars(pack) <= _PARAMS["context_chars"]:
            return
        by_id = {m["id"]: m for m in [*data["materials"], *data["overviews"]]}
        blocked = [
            m
            for m in data["overviews"]
            if m["state"] == "queued"
            and any(by_id[key]["state"] == "failed" for key in m["children"])
        ]
        if blocked:
            for unit in blocked:
                unit.update(state="failed", error=_COPY["error_material"])
            await self.store.save_study(book_id, revision, data)
            return
        pending = next(
            (
                m
                for m in sorted(data["overviews"], key=lambda m: m["attempts"])
                if self._due(m)
                and all(by_id[key]["state"] == "completed" for key in m["children"])
            ),
            None,
        )
        if pending is not None:
            children = [by_id[key] for key in pending["children"]]
            target = pending.get("summary_chars", _PARAMS["overview_chars"])

            async def validate(value: dict[str, Any]) -> None:
                if set(value["unit_ids"]) != set(pending["children"]):
                    raise ValueError("unit_ids must cover every child material")
                if len(value["summary"]) > target:
                    raise SummaryTooLong(len(value["summary"]), target)

            try:
                output = await self._complete(
                    "overview",
                    {**_material_pack(data, children), "summary_chars": target},
                    pending["id"],
                    validate,
                )
                pending.update(
                    summary=output["summary"],
                    state="completed",
                    error="",
                    level=max(m.get("level", 0) for m in children) + 1,
                )
                await self.store.save_study(book_id, revision, data)
            except SummaryTooLong as error:
                if len(children) == 1:
                    await self._failure(book_id, "overview", pending["id"], error)
                    return
                split_ids: list[str] = []
                midpoint = len(children) // 2
                for group in (children[:midpoint], children[midpoint:]):
                    key = str(uuid4())
                    split_ids.append(key)
                    data["overviews"].append(
                        {
                            "id": key,
                            "level": max(m.get("level", 0) for m in group) + 1,
                            "children": [m["id"] for m in group],
                            "material_ids": [
                                identity
                                for m in group
                                for identity in m.get("material_ids", [m["id"]])
                            ],
                            "start": group[0]["start"],
                            "end": group[-1]["end"],
                            "summary": "",
                            "summary_chars": target // 2,
                            **_job(),
                        }
                    )
                pending.update(
                    children=split_ids,
                    attempts=pending["attempts"] + 1,
                    error=str(error),
                )
                await self.store.save_study(book_id, revision, data)
            except Exception as error:
                await self._failure(book_id, "overview", pending["id"], error)
            return
        # A split parent waits for its children; cooling jobs must not create
        # overlapping replacement groups or starve other runnable groups.
        if any(m["state"] == "queued" for m in data["overviews"]):
            return
        groups: list[list[dict[str, Any]]] = []
        for unit in units:
            if not groups or _packed_chars(
                _material_pack(
                    data,
                    [*groups[-1], unit],
                )
            ) > min(_PARAMS["context_chars"], _PARAMS["overview_group_chars"]):
                groups.append([])
            groups[-1].append(unit)
        level = max((m["level"] for m in data["overviews"]), default=0) + 1
        existing = {tuple(m["children"]) for m in data["overviews"]}
        for group in groups:
            children = [m["id"] for m in group]
            if tuple(children) in existing:
                continue
            data["overviews"].append(
                {
                    "id": f"overview:{level}:{group[0]['start']}",
                    "level": level,
                    "children": children,
                    "material_ids": [
                        identity
                        for m in group
                        for identity in m.get("material_ids", [m["id"]])
                    ],
                    "start": group[0]["start"],
                    "end": group[-1]["end"],
                    "summary": "",
                    **_job(),
                }
            )
        await self.store.save_study(book_id, revision, data)

    async def _update_graph(
        self, book_id: str, revision: int, data: dict[str, Any]
    ) -> None:
        evidence = {
            t["id"]: t
            for s in data["sessions"]
            for t in s["turns"]
            if t["status"] == "committed"
        }
        pending = sorted(
            (
                t
                for t in evidence.values()
                if t["evidence_seq"] > data["consumed_seq"]
                or t["id"] in data.get("graph_deferred", [])
            ),
            key=lambda t: (
                t["id"] in data.get("graph_deferred", []),
                t["evidence_seq"],
            ),
        )[: _PARAMS["graph_pending"]]
        if not pending:
            return
        candidate_id = str(uuid4())
        correlation = f"reading-graph:{candidate_id}"
        all_nodes = {n["id"]: n for n in data["graph"]["nodes"]}
        query = "\n".join(t["input"]["message"] for t in pending)
        selected = {
            n["id"]: n
            for n in _lookup(
                list(all_nodes.values()),
                query,
            )[: _PARAMS["graph_nodes"]]
        }

        def user_source(turn: dict[str, Any]) -> dict[str, Any]:
            return {
                "id": turn["id"],
                "input": turn["input"],
                "book_sources": turn.get("reply_sources", []),
            }

        user_evidence = {t["id"]: user_source(t) for t in pending}
        source_indices = {
            source["paragraph_id"]: source["index"]
            for t in evidence.values()
            for source in t.get("reply_sources", [])
        }
        passages = {p["paragraph_id"]: p for p in await self.search(book_id, query)}
        seed_passages = dict(passages)
        graph: dict[str, Any] = {}
        deferred: set[str] = set()
        tool_feedback: list[dict[str, Any]] = []
        tools = ToolRegistry(self.expression._tools._store)

        async def read(
            node_ids: list[str], evidence_ids: list[str], paragraph_ids: list[str]
        ) -> dict[str, Any]:
            if (
                not set(node_ids) <= all_nodes.keys()
                or not set(evidence_ids) <= evidence.keys()
                or not set(paragraph_ids) <= source_indices.keys()
            ):
                raise ValueError(
                    "read requires existing book node/evidence IDs; "
                    f"valid nodes={sorted(all_nodes)[: _PARAMS['graph_nodes']]}, "
                    f"valid evidence={sorted(evidence)[: _PARAMS['graph_evidence']]}"
                )
            if (
                len(selected.keys() | set(node_ids)) > _PARAMS["graph_nodes"]
                or len(user_evidence.keys() | set(evidence_ids))
                > _PARAMS["graph_evidence"]
            ):
                raise ValueError("local graph read budget exceeded; narrow the change")
            old_selected, old_evidence = dict(selected), dict(user_evidence)
            old_passages = dict(seed_passages)
            for key in paragraph_ids:
                paragraph = (
                    await self.store.list_paragraphs(
                        book_id, source_indices[key], source_indices[key]
                    )
                )[0]
                if paragraph.id != key:
                    raise StudyConflictError(_COPY["superseded"])
                seed_passages[key] = self._source(paragraph)
            selected.update({key: all_nodes[key] for key in node_ids})
            user_evidence.update(
                {key: user_source(evidence[key]) for key in evidence_ids}
            )
            if _packed_chars(await graph_payload()) > _PARAMS["graph_chars"]:
                selected.clear()
                selected.update(old_selected)
                user_evidence.clear()
                user_evidence.update(old_evidence)
                seed_passages.clear()
                seed_passages.update(old_passages)
                raise ValueError("graph read exceeds request budget; request fewer IDs")
            local = await local_graph()
            return {
                "node_ids": node_ids,
                "evidence_ids": evidence_ids,
                "paragraph_ids": paragraph_ids,
                "passages": [passages[key] for key in paragraph_ids],
                "nodes": [n for n in local["nodes"] if n["id"] in node_ids],
                "evidence": [user_evidence[key] for key in evidence_ids],
            }

        tools.register(
            Tool(
                "book_graph_read",
                _PROMPTS["tool_graph_read"],
                {
                    "type": "object",
                    "properties": {
                        "node_ids": {"type": "array", "items": {"type": "string"}},
                        "evidence_ids": {"type": "array", "items": {"type": "string"}},
                        "paragraph_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["node_ids", "evidence_ids", "paragraph_ids"],
                },
                read,
            )
        )

        async def check_current() -> None:
            found = await self.store.load_study(book_id)
            if found is None:
                raise asyncio.CancelledError
            if found[1]["graph"]["version"] != data["graph"]["version"]:
                raise StudyConflictError(_COPY["superseded"])

        async def local_graph() -> dict[str, Any]:
            passages.clear()
            passages.update(seed_passages)
            for node in selected.values():
                for source in node["sources"]:
                    if source["paragraph_id"] not in passages:
                        paragraph = (
                            await self.store.list_paragraphs(
                                book_id,
                                source["index"],
                                source["index"],
                            )
                        )[0]
                        passages[paragraph.id] = self._source(paragraph)
            return {
                "version": data["graph"]["version"],
                "nodes": [
                    {
                        k: n[k]
                        for k in (
                            "id",
                            "version",
                            "title",
                            "content",
                            "state",
                            "evidence",
                            "corrects",
                        )
                    }
                    | {
                        "sources": [
                            {"paragraph_id": s["paragraph_id"], "index": s["index"]}
                            for s in n["sources"]
                        ]
                    }
                    for n in selected.values()
                ],
                "edges": [
                    e
                    for e in data["graph"]["edges"]
                    if e["from"] in selected and e["to"] in selected
                ],
            }

        async def graph_payload() -> dict[str, Any]:
            return {
                "graph": await local_graph(),
                "pending": [user_evidence[t["id"]] for t in pending],
                "evidence": list(user_evidence.values()),
                "passages": list(passages.values()),
                "tool_feedback": tool_feedback,
            }

        while _packed_chars(await graph_payload()) > _PARAMS["graph_chars"]:
            if not selected:
                raise ValueError("new user evidence exceeds graph request budget")
            selected.pop(next(reversed(selected)))

        async def validate(result: dict[str, Any]) -> None:
            nonlocal graph, deferred
            if result["action"] == "read":
                return
            if not set(result["deferred_evidence"]) <= {t["id"] for t in pending}:
                raise ValueError("deferred_evidence must reference pending user turns")
            deferred = set(result["deferred_evidence"])
            operations = result["operations"]
            if len({op["id"] for op in operations}) != len(operations):
                raise ValueError("operations contain duplicate node IDs")
            graph = deepcopy(data["graph"])
            by_id = {n["id"]: n for n in graph["nodes"]}
            for op in operations:
                key = op["id"]
                if key in all_nodes and key not in selected:
                    raise ValueError(f"read existing node before editing: {key}")
                if key in graph["deleted"]:
                    raise ValueError(f"deleted node identity cannot be revived: {key}")
                if op["op"] == "delete":
                    if key not in by_id:
                        raise ValueError(f"unknown node: {key}")
                    del by_id[key]
                    graph["deleted"].append(key)
                    continue
                if not set(op["evidence"] + op["corrects"]) <= user_evidence.keys():
                    raise ValueError("read referenced old evidence IDs before patching")
                if op["state"] != "unassessed" and not op["evidence"]:
                    raise ValueError("understanding state requires user evidence")
                if any(s["paragraph_id"] not in passages for s in op["sources"]):
                    raise ValueError(
                        "sources must use provided paragraph_id (not index): "
                        + json.dumps(list(passages), ensure_ascii=False)
                    )
                await self._sources(book_id, [op])
                previous = by_id.get(key)
                by_id[key] = {
                    **op,
                    "version": previous["version"] + 1 if previous else 1,
                    "origin": {"kind": "turns", "ids": [t["id"] for t in pending]},
                }
            for op in operations:
                if op["op"] == "delete" and op["replacement"] is not None:
                    if op["replacement"] not in by_id:
                        raise ValueError("replacement node is absent")

            def edge_key(e: dict[str, Any]) -> tuple[str, str, str]:
                return e["from"], e["to"], e["label"]

            touched = selected.keys() | {op["id"] for op in operations}
            if any(
                e["from"] not in touched or e["to"] not in touched
                for e in [*result["edges"], *result["remove_edges"]]
            ):
                raise ValueError(
                    "read edge endpoints before editing their relationship"
                )
            removed = {edge_key(e) for e in result["remove_edges"]}
            existing = {edge_key(e): e for e in graph["edges"]}
            if not removed <= existing.keys():
                raise ValueError("remove_edges contains an absent relationship")
            edges = {
                key: e
                for key, e in existing.items()
                if key not in removed and e["from"] in by_id and e["to"] in by_id
            }
            for edge in result["edges"]:
                if edge["from"] not in by_id or edge["to"] not in by_id:
                    raise ValueError("edge endpoint absent from full graph")
                edges[edge_key(edge)] = edge
            graph.update(
                nodes=list(by_id.values()),
                edges=list(edges.values()),
                version=graph["version"] + 1,
            )
            review = await self._complete(
                "graph_review",
                {
                    "graph": await local_graph(),
                    "candidate": result,
                    "user_evidence": list(user_evidence.values()),
                    "passages": list(passages.values()),
                },
                correlation,
            )
            if not review["valid"]:
                raise ValueError(json.dumps(review["issues"], ensure_ascii=False))
            upserts = {op["id"]: op for op in operations if op["op"] == "upsert"}
            if {c["id"] for c in review["checks"]} != upserts.keys():
                raise ValueError("graph review checks must cover each upsert ID")
            rejected = {
                c["id"]
                for c in review["checks"]
                if not c["user_supported"] or not c["source_supported"]
            }
            for key in rejected:
                deferred.update(upserts[key]["evidence"])
                if key in all_nodes:
                    by_id[key] = all_nodes[key]
                else:
                    del by_id[key]
            result["operations"] = [op for op in operations if op["id"] not in rejected]
            for field in ("edges", "remove_edges"):
                result[field] = [
                    e
                    for e in result[field]
                    if e["from"] not in rejected and e["to"] not in rejected
                ]
            if rejected:
                edges = {
                    key: e
                    for key, e in edges.items()
                    if e["from"] not in rejected and e["to"] not in rejected
                }
                edges.update(
                    {
                        edge_key(e): e
                        for e in data["graph"]["edges"]
                        if (e["from"] in rejected or e["to"] in rejected)
                        and e["from"] in by_id
                        and e["to"] in by_id
                    }
                )
                graph.update(nodes=list(by_id.values()), edges=list(edges.values()))
            result["deferred_evidence"] = sorted(deferred)

        must_patch = False
        for _ in range(_PARAMS["graph_steps"]):
            result = await self._complete(
                "graph",
                await graph_payload(),
                correlation,
                validate,
            )
            if result["action"] == "patch":
                break
            if must_patch:
                raise ValueError(_COPY["graph_no_progress"])
            new_nodes = set(result["node_ids"]) - selected.keys()
            new_evidence = set(result["evidence_ids"]) - user_evidence.keys()
            new_passages = set(result["paragraph_ids"]) - passages.keys()
            if not new_nodes and not new_evidence and not new_passages:
                must_patch = True
                tool_feedback.append(
                    {
                        "name": "book_graph_read",
                        "status": "already_provided",
                        "node_ids": result["node_ids"],
                        "evidence_ids": result["evidence_ids"],
                        "paragraph_ids": result["paragraph_ids"],
                        "message": _PROMPTS["graph_already_provided"],
                    }
                )
                continue
            outcome, receipt = await read_tool(
                tools,
                "book_graph_read",
                {
                    "node_ids": sorted(new_nodes),
                    "evidence_ids": sorted(new_evidence),
                    "paragraph_ids": sorted(new_passages),
                },
                correlation,
                check_current,
            )
            tool_feedback.append(
                {
                    **{k: receipt[k] for k in ("name", "args", "status", "errors")},
                    "log_id": receipt["log_id"],
                    "result": outcome,
                }
            )
        else:
            raise ValueError("local graph read budget exhausted")
        operations, edges = result["operations"], result["edges"]
        data["graph"] = graph
        data["consumed_seq"] = max(
            data["consumed_seq"], *(t["evidence_seq"] for t in pending)
        )
        data["graph_deferred"] = sorted(
            (set(data.get("graph_deferred", [])) - {t["id"] for t in pending})
            | deferred
        )
        data["graph_work"].update(
            state="completed"
            if data["consumed_seq"] == data["evidence_seq"]
            and not data["graph_deferred"]
            else "queued",
            error="",
        )
        data["history"].append(
            {
                "id": candidate_id,
                "version": graph["version"],
                "created_at": time.time(),
                "kind": "turns",
                "reason": result["reason"],
                "evidence": [t["id"] for t in pending],
                "operations": operations,
                "edges": edges,
            }
        )
        if not await self.store.save_study(book_id, revision, data):
            # Store the discarded identity, but consume none of its evidence.
            while True:
                found = await self.store.load_study(book_id)
                if found is None:
                    return
                current_revision, current = found
                current["candidates"].append(
                    {
                        "id": candidate_id,
                        "state": "superseded",
                        "base_version": graph["version"] - 1,
                        "result": result,
                        "evidence": [t["id"] for t in pending],
                    }
                )
                if await self.store.save_study(book_id, current_revision, current):
                    break

    async def _settle(self, book_id: str, identity: str, kind: str) -> None:
        _, data = await self._load(book_id)
        session = _session(data, identity)
        if session["results"][kind]["state"] == "completed":
            return
        snapshot = session["snapshot"]
        source = await self._settlement_source(book_id, identity, snapshot)
        book = await self.reader.get_book(book_id)
        if kind == "reflection":
            behavior = self.reader._memory.behavior
            if behavior is None:
                raise RuntimeError(_COPY["failure"])
            committed = [
                t
                for t in snapshot["turns"]
                if t["status"] == "committed" and "feedback" not in t
            ]
            if committed:
                event = await behavior.submit(
                    f"reading-settlement:{identity}",
                    "reading",
                    {
                        "scope": {
                            "kind": "reading",
                            "book_id": book_id,
                            "session_id": identity,
                        },
                        "trigger_type": "reading_session_end",
                        "topic_raw": _PROMPTS["reflection"],
                        "session_snapshot_raw": source,
                        "previously_reflected_ids": [
                            t["id"] for t in snapshot["turns"] if "feedback" in t
                        ],
                        "signal_ids": [t["id"] for t in committed],
                        "evidence_ids": [t["id"] for t in committed],
                        "execution_status": "ended",
                    },
                    f"reading-settlement:{identity}",
                )
                # Run the original reflection/version path; its stable ID resumes B4/B5.
                await behavior.apply_event(event, self.reader._memory)
            value: Any = {"session_id": identity}
        else:
            value = session["results"][kind].get("output")
            if value is None:
                value = await self._complete(
                    kind,
                    {"book": asdict(book), "snapshot": source},
                    f"reading-settlement:{identity}",
                )
                if kind == "summary":
                    ids = {t["id"] for t in snapshot["turns"]}
                    if (
                        not isinstance(value["summary"], str)
                        or set(value["turn_ids"]) != ids
                    ):
                        raise ValueError(_COPY["error_material"])
                while True:
                    revision, data = await self._load(book_id)
                    _session(data, identity)["results"][kind]["output"] = value
                    if await self.store.save_study(book_id, revision, data):
                        break
            if kind == "memory" and any(
                t["status"] == "committed" for t in snapshot["turns"]
            ):
                await self.reader._integration.integrate_session(
                    book_id, identity, value
                )
        while True:
            revision, data = await self._load(book_id)
            _session(data, identity)["results"][kind].update(
                state="completed", output=value, error=""
            )
            if await self.store.save_study(book_id, revision, data):
                break

    async def _feedback(self, book_id: str, identity: str, turn_id: str) -> None:
        _, data = await self._load(book_id)
        turn = next(t for t in _session(data, identity)["turns"] if t["id"] == turn_id)
        behavior = self.reader._memory.behavior
        if behavior is None:
            raise RuntimeError(_COPY["failure"])
        key = f"reading-feedback:{identity}:{turn_id}"
        event = await behavior.submit(
            key,
            "reading",
            {
                "scope": {
                    "kind": "reading",
                    "book_id": book_id,
                    "session_id": identity,
                },
                "trigger_type": "user_correction",
                "topic_raw": _PROMPTS["reflection"],
                "user_feedback_raw": turn["input"],
                "reply_raw": turn["reply"],
                "skill_snapshot_raw": turn["skill_snapshot"],
                "evidence_ids": [turn_id],
                "signal_ids": [turn_id],
            },
            key,
        )
        await behavior.apply_event(event, self.reader._memory)
        while True:
            revision, data = await self._load(book_id)
            current = next(
                t for t in _session(data, identity)["turns"] if t["id"] == turn_id
            )
            current["feedback"].update(state="completed", error="")
            if await self.store.save_study(book_id, revision, data):
                return

    async def _settlement_source(
        self,
        book_id: str,
        identity: str,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        """Checkpoint long-session summaries without dropping early corrections."""
        if len(json.dumps(snapshot, ensure_ascii=False)) <= _PARAMS["evidence_chars"]:
            return snapshot
        while True:
            revision, data = await self._load(book_id)
            session = _session(data, identity)
            units = session.setdefault("settlement_units", [])
            if not units:
                entries = [
                    {"turn_ids": [t["id"]], "turns": [t]} for t in snapshot["turns"]
                ]
                level = 0
            else:
                level = max(unit["level"] for unit in units)
                current = [unit for unit in units if unit["level"] == level]
                pending = next(
                    (unit for unit in current if unit["output"] is None), None
                )
                if pending is not None:
                    ids = set(pending["turn_ids"])
                    payload = {
                        **snapshot,
                        "turns": [t for t in snapshot["turns"] if t["id"] in ids],
                    }
                    if level:
                        payload = {
                            **snapshot,
                            "turns": [],
                            "segments": [
                                unit["output"]
                                for unit in units
                                if unit["id"] in pending["children"]
                            ],
                        }

                    async def validate(value: dict[str, Any]) -> None:
                        if set(value["turn_ids"]) != ids:
                            raise ValueError(
                                "turn_ids must include exactly "
                                + json.dumps(sorted(ids))
                            )
                        if len(value["summary"]) > _PARAMS["summary_chars"]:
                            raise ValueError(
                                f"summary exceeds {_PARAMS['summary_chars']} chars"
                            )

                    output = await self._complete(
                        "summary",
                        {"snapshot": payload},
                        f"reading-settlement:{identity}:{pending['id']}",
                        validate,
                    )
                    if (
                        set(output["turn_ids"]) != ids
                        or not isinstance(output["summary"], str)
                        or not output["summary"].strip()
                        or len(output["summary"]) > _PARAMS["summary_chars"]
                    ):
                        raise ValueError(_COPY["error_material"])
                    while True:
                        revision, data = await self._load(book_id)
                        saved = next(
                            unit
                            for unit in _session(data, identity)["settlement_units"]
                            if unit["id"] == pending["id"]
                        )
                        if saved["output"] is not None:
                            break
                        saved["output"] = output
                        if await self.store.save_study(book_id, revision, data):
                            break
                    continue
                segments = [unit["output"] for unit in current]
                if (
                    sum(len(segment["summary"]) for segment in segments)
                    <= _PARAMS["evidence_chars"]
                ):
                    return {**snapshot, "turns": [], "segments": segments}
                entries = [
                    {
                        "turn_ids": unit["turn_ids"],
                        "children": [unit["id"]],
                        "summary": unit["output"]["summary"],
                    }
                    for unit in current
                ]
                level += 1
            groups: list[list[dict[str, Any]]] = []
            for entry in entries:
                if (
                    not groups
                    or (
                        sum(len(item["summary"]) for item in [*groups[-1], entry])
                        if level
                        else len(json.dumps([*groups[-1], entry], ensure_ascii=False))
                    )
                    > _PARAMS["evidence_chars"]
                ):
                    groups.append([])
                groups[-1].append(entry)
            for index, group in enumerate(groups):
                units.append(
                    {
                        "id": f"segment:{level}:{index}",
                        "level": level,
                        "turn_ids": [
                            tid for entry in group for tid in entry["turn_ids"]
                        ],
                        "children": [
                            child
                            for entry in group
                            for child in entry.get("children", [])
                        ],
                        "output": None,
                    }
                )
            await self.store.save_study(book_id, revision, data)

    async def handoff(self) -> str:
        summaries: list[dict[str, Any]] = []
        for book_id in await self.store.study_books():
            found = await self.store.load_study(book_id)
            if found is None:
                continue
            _, data = found
            for session in data["sessions"]:
                result = session["results"].get("summary")
                if result and result["state"] == "completed":
                    summaries.append(
                        {
                            "session_id": session["id"],
                            "book_id": book_id,
                            "ended_at": session["ended_at"],
                            "summary": result["output"]["summary"],
                        }
                    )
        summaries.sort(key=lambda s: s["ended_at"])
        return (
            _PROMPTS["handoff"].format(
                summary=json.dumps(summaries, ensure_ascii=False)
            )
            if summaries
            else ""
        )

    async def _failure(
        self, book_id: str, section: str, key: str, error: Exception
    ) -> None:
        while True:
            found = await self.store.load_study(book_id)
            if found is None:
                return
            revision, data = found
            job = (
                next(m for m in data["materials"] if m["id"] == key)
                if section == "material"
                else next(m for m in data["overviews"] if m["id"] == key)
                if section == "overview"
                else data["graph_work"]
                if section == "graph"
                else next(
                    t
                    for t in _session(data, section.removeprefix("feedback:"))["turns"]
                    if t["id"] == key
                )["feedback"]
                if section.startswith("feedback:")
                else _session(data, section)["results"][key]
            )
            job["attempts"] += 1
            job.update(
                state="failed"
                if job["attempts"] >= _PARAMS["max_attempts"]
                else "queued",
                retry_at=time.time() + _PARAMS["retry_seconds"],
                error=f"{type(error).__name__}: {error}",
            )
            if await self.store.save_study(book_id, revision, data):
                return

    @staticmethod
    def _due(job: dict[str, Any]) -> bool:
        return job["state"] == "queued" and job["retry_at"] <= time.time()

    async def run(self) -> None:
        """The role runtime owns recovery, independent of its foreground page."""
        try:
            # A stopped process cannot retain an execution token or foreground lease.
            for book_id in await self.store.study_books():
                while True:
                    found = await self.store.load_study(book_id)
                    if found is None:
                        break
                    revision, data = found
                    for session in data["sessions"]:
                        for turn in session["turns"]:
                            if turn["status"] == "running":
                                turn.update(status="queued", token=None)
                    if await self.store.save_study(book_id, revision, data):
                        break
            while True:
                self._wake.clear()
                for book_id in await self.store.study_books():
                    try:
                        revision, data = await self._load(book_id)
                        for session in data["sessions"]:
                            for turn in session["turns"]:
                                if "feedback" in turn and self._due(turn["feedback"]):
                                    try:
                                        await self._feedback(
                                            book_id, session["id"], turn["id"]
                                        )
                                    except Exception as error:
                                        await self._failure(
                                            book_id,
                                            f"feedback:{session['id']}",
                                            turn["id"],
                                            error,
                                        )
                        revision, data = await self._load(book_id)
                        for session in data["sessions"]:
                            if session["ended_at"] is None:
                                turn = next(
                                    (
                                        t
                                        for t in session["turns"]
                                        if t["status"] in ("queued", "running")
                                    ),
                                    None,
                                )
                                if (
                                    turn
                                    and turn["id"] not in self.replies
                                    and self._connected(
                                        session["id"], turn["connection"]
                                    )
                                ):
                                    task = asyncio.create_task(
                                        self._reply(book_id, session["id"], turn["id"])
                                    )
                                    self.replies[turn["id"]] = task
                                    task.add_done_callback(
                                        lambda done, key=turn["id"]: self.replies.pop(
                                            key, None
                                        )
                                    )
                        # Each successful material is a durable checkpoint.
                        material = next(
                            (m for m in data["materials"] if self._due(m)), None
                        )
                        if material is not None:
                            try:
                                await self._prepare(book_id, revision, data, material)
                            except Exception as error:
                                await self._failure(
                                    book_id, "material", material["id"], error
                                )
                        revision, data = await self._load(book_id)
                        await self._overview(book_id, revision, data)
                        revision, data = await self._load(book_id)
                        if (
                            data["evidence_seq"] > data["consumed_seq"]
                            or bool(data.get("graph_deferred"))
                        ) and self._due(data["graph_work"]):
                            try:
                                await self._update_graph(book_id, revision, data)
                            except Exception as error:
                                await self._failure(book_id, "graph", "", error)
                        _, data = await self._load(book_id)
                        for session in data["sessions"]:
                            for kind, job in session["results"].items():
                                if self._due(job):
                                    try:
                                        await self._settle(book_id, session["id"], kind)
                                    except Exception as error:
                                        await self._failure(
                                            book_id, session["id"], kind, error
                                        )
                    except StudyConflictError:
                        continue  # Explicit deletion ends only this book's work.
                try:
                    await asyncio.wait_for(
                        self._wake.wait(), _PARAMS["worker_ms"] / 1000
                    )
                except TimeoutError:
                    pass
        finally:
            self.connections.clear()
            tasks = list(self.replies.values())
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
