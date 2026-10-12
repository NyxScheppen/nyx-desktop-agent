"""FastAPI 请求模型与 REST/SSE 路由注册。"""

# pyright: reportPrivateUsage=false, reportUnusedFunction=false
import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, field_validator, model_validator
from starlette.middleware.base import RequestResponseEndpoint
from starlette.routing import Match, Mount
from starlette.types import Receive, Scope, Send

from nyx.activity.facade import WebTasksDisabledError
from nyx.activity.observe import classify_presence
from nyx.app_context import _App, _Host
from nyx.character_cards import MAX_CARD_BYTES, load_card
from nyx.enums import ActivityType, BoundaryResult, EventType, MemoryKind, MemoryType
from nyx.events.bus import EventAdmissionError
from nyx.reading.facade import (
    BookmarkNotFoundError,
    BookNotFoundError,
    DuplicateBookError,
    NoteNotFoundError,
)
from nyx.reading.store import ProgressConflictError
from nyx.types import (
    Activity,
    Annotation,
    AssignedTask,
    Book,
    BookListItem,
    Bookmark,
    CurrentState,
    DesireState,
    EvalRecord,
    EvalStats,
    Event,
    InteractionAttempt,
    LlmMessage,
    Memory,
    MemoryFact,
    Paragraph,
    ReadingProgress,
    SelfNarrative,
    UserNote,
)

_MAX_ACTIVITY_RESULTS_OFFSET = 100_000
_MAX_EVAL_OFFSET = 100_000

RootEvent = Callable[[EventType, dict[str, Any]], Event]


async def _validation_error(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, RequestValidationError)
    if request.url.path != "/api/observe":
        return await request_validation_exception_handler(request, error)
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {key: item[key] for key in ("loc", "msg", "type")}
                for item in error.errors()
            ],
        },
    )


def _question_payload(
    attempt: InteractionAttempt, server_now: float | None = None
) -> dict[str, Any]:
    return {
        "attempt_id": attempt.id,
        "kind": attempt.kind.value,
        "text": attempt.text,
        "created_at": attempt.created_at,
        "expires_at": attempt.expires_at,
        "status": attempt.status.value,
        "paused_at": attempt.paused_at,
        "server_now": time.time() if server_now is None else server_now,
    }


class _ChatPayload(BaseModel):
    message: str = Field(..., min_length=1, max_length=12000, strict=True)
    reply_to: str | None = Field(default=None, min_length=1, strict=True)

    @field_validator("message")
    @classmethod
    def reject_blank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must contain non-whitespace text")
        return value


class _ExportPayload(BaseModel):
    format: str


class _ObservePayload(BaseModel):
    presence: Literal["online", "away", "busy"]
    window_title: str = Field(default="", max_length=512, strict=True)
    idle_seconds: float = Field(
        ..., ge=0, le=253402300799, strict=True, allow_inf_nan=False
    )
    sampled_at: float = Field(
        ..., ge=0, le=253402300799, strict=True, allow_inf_nan=False
    )

    @model_validator(mode="after")
    def validate_sample(self) -> "_ObservePayload":
        if self.idle_seconds > self.sampled_at:
            raise ValueError("idle_seconds exceeds sampled_at")
        if self.presence != classify_presence(self.idle_seconds):
            raise ValueError("presence disagrees with idle_seconds")
        return self


class _ProgressPayload(BaseModel):
    user_position: int = Field(..., ge=1)
    nyx_position: int = Field(..., ge=1)
    reading_speed: int = Field(..., ge=10, le=200)
    expected_revision: int = Field(..., ge=0)


class _ImpulsePayload(BaseModel):
    book_id: str
    paragraph_index: int = Field(..., ge=1)
    last_paragraph_index: int = Field(..., ge=0)


class _UserNotePayload(BaseModel):
    book_id: str
    paragraph_id: str | None = None
    content: str = Field(..., max_length=4000)
    selected_text: str | None = Field(default=None, max_length=4000)
    selection_start: int | None = Field(default=None, ge=0)
    selection_end: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_note_or_highlight(self) -> "_UserNotePayload":
        if self.content:
            return self
        if (
            self.paragraph_id is None
            or not self.selected_text
            or self.selection_start is None
            or self.selection_end is None
        ):
            raise ValueError("空笔记必须包含有效划线")
        return self


class _UpdateNotePayload(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)


class _BoundaryPayload(BaseModel):
    book_id: str
    nyx_position: int = Field(..., ge=1)


class _BookmarkPayload(BaseModel):
    book_id: str
    paragraph_id: str


class _WebTaskPayload(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)

    @model_validator(mode="after")
    def validate_http_url(self) -> "_WebTaskPayload":
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("只支持 HTTP(S) URL")
        return self


class _BookTaskPayload(BaseModel):
    book_id: str = Field(..., min_length=1)
    target_paragraph: int = Field(..., ge=1)


def sanitize_filename(name: str) -> str:
    """Remove path, control, and HTML-dangerous characters from an upload name."""
    cleaned = "".join(
        char for char in Path(name).name if char.isprintable() and char not in '<>"'
    )
    return cleaned[:255] or "book.epub"


def build_app(
    app: _App,
    *,
    root_event: RootEvent,
    max_epub_bytes: int,
    sse_queue_size: int,
    launch_nonce: str | None,
) -> FastAPI:
    """Build the FastAPI application around an assembled app context."""
    fast = FastAPI(title="Nyx Agent")

    @fast.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        if request.url.path != "/api/observe":
            return await request_validation_exception_handler(request, error)
        # Rejected input may contain NaN/Infinity, which JSONResponse cannot encode.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {key: item[key] for key in ("loc", "msg", "type")}
                    for item in error.errors()
                ],
            },
        )

    @fast.get("/api/state")
    async def api_state() -> CurrentState:
        return await app.inner_life.get_state()

    @fast.get("/api/ready")
    async def api_ready() -> dict[str, str | None]:
        return {"service": "nyx-agent", "launch_nonce": launch_nonce}

    @fast.post("/api/chat")
    async def api_chat(payload: _ChatPayload) -> dict[str, str]:
        content: dict[str, Any] = {"message": payload.message}
        if payload.reply_to is not None:
            content["reply_to"] = payload.reply_to
        event = root_event(EventType.USER_MESSAGE, content)
        try:
            if app.host is not None:
                await app.host.online_before_chat(app.character_id, f"chat:{event.id}")
            await app.bus.publish(event)
        except (EventAdmissionError, RuntimeError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return {"event_id": event.id}

    @fast.get("/api/questions/pending")
    async def api_pending_questions() -> dict[str, Any]:
        attempts = await app.expression.pending_questions()
        return {
            "server_now": time.time(),
            "questions": [_question_payload(attempt) for attempt in attempts],
        }

    @fast.post("/api/questions/{attempt_id}/pause")
    async def api_pause_question(attempt_id: str) -> dict[str, Any]:
        attempt = await app.expression.pause_question(attempt_id)
        if attempt is None:
            raise HTTPException(status_code=404, detail="提问不存在")
        if attempt.status.value != "waiting" or attempt.paused_at is None:
            raise HTTPException(status_code=409, detail="提问当前不可暂停")
        return _question_payload(attempt)

    @fast.post("/api/questions/{attempt_id}/resume")
    async def api_resume_question(attempt_id: str) -> dict[str, Any]:
        attempt = await app.expression.resume_question(attempt_id)
        if attempt is None:
            raise HTTPException(status_code=404, detail="提问不存在")
        if attempt.status.value != "waiting" or attempt.paused_at is not None:
            raise HTTPException(status_code=409, detail="提问当前不可恢复")
        return _question_payload(attempt)

    @fast.get("/api/memories")
    async def api_memories(
        kind: MemoryKind | None = None, type: MemoryType | None = None
    ) -> list[Memory]:
        return await app.memory.list_memories(kind, type)

    @fast.get("/api/memories/search")
    async def api_memory_search(q: str) -> list[Memory]:
        return await app.memory.search(q)

    @fast.get("/api/memories/facts")
    async def api_memory_facts(
        limit: int = Query(64, ge=0, le=64),
    ) -> list[MemoryFact]:
        return await app.memory.recent_facts(limit)

    @fast.get("/api/desires")
    async def api_desires() -> DesireState:
        return await app.desire.get_all()

    @fast.get("/api/activity")
    async def api_activity() -> dict[str, Any]:
        return {
            "current": await app.activity.get_current(),
            "schedule": await app.activity.get_schedule(),
        }

    @fast.get("/api/activity/results")
    async def api_activity_results(
        limit: int = Query(100, ge=1, le=100),
        offset: int = Query(0, ge=0, le=_MAX_ACTIVITY_RESULTS_OFFSET),
        activity_type: Literal["creation"] | None = None,
    ) -> list[Activity]:
        selected_type = ActivityType.CREATION if activity_type == "creation" else None
        return await app.activity.get_results(limit, offset, selected_type)

    @fast.get("/api/tasks")
    async def api_tasks() -> list[AssignedTask]:
        return await app.activity.list_tasks()

    @fast.post("/api/tasks/web", status_code=201)
    async def api_assign_web_task(payload: _WebTaskPayload) -> AssignedTask:
        try:
            return await app.activity.assign_web_task(payload.url)
        except WebTasksDisabledError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @fast.post("/api/tasks/book", status_code=201)
    async def api_assign_book_task(payload: _BookTaskPayload) -> AssignedTask:
        try:
            return await app.activity.assign_book_task(
                payload.book_id, payload.target_paragraph
            )
        except BookNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @fast.get("/api/events/log")
    async def api_events_log(
        limit: int = 100,
        event_type: EventType | None = None,
        correlation_id: str | None = None,
        after: str | None = None,
    ) -> list[Event]:
        if app.host is not None and after is not None:
            prefix = app.character_id + ":"
            if not after.startswith(prefix):
                raise HTTPException(409, "事件游标不属于该角色")
            after = after[len(prefix) :]
        return await app.bus.list_events(limit, event_type, correlation_id, after)

    @fast.get("/api/eval/recent")
    async def api_eval_recent(
        limit: int = Query(5, ge=1, le=100),
        offset: int = Query(0, ge=0, le=_MAX_EVAL_OFFSET),
    ) -> list[EvalRecord]:
        return await app.eval_store.list_recent(limit, offset)

    @fast.get("/api/eval/total_tokens")
    async def api_eval_total_tokens() -> EvalStats:
        return await app.eval_store.total_tokens()

    @fast.get("/api/eval/{record_id}/prompt")
    async def api_eval_prompt(
        record_id: str,
        response: Response,
    ) -> list[LlmMessage] | None:
        response.headers["Cache-Control"] = "no-store"
        try:
            found, prompt = await app.eval_store.get_prompt(record_id)
        except ValueError as error:
            raise HTTPException(
                status_code=500, detail="stored prompt is invalid"
            ) from error
        if not found:
            raise HTTPException(status_code=404, detail="eval record not found")
        return prompt

    @fast.get("/api/narrative")
    async def api_narrative() -> SelfNarrative:
        return await app.inner_life.get_narrative()

    @fast.post("/api/export")
    async def api_export(payload: _ExportPayload) -> Response:
        content = await app.memory.export(payload.format)
        media_type = "application/json" if payload.format == "json" else "text/markdown"
        return Response(content=content, media_type=media_type)

    @fast.post("/api/books", status_code=201)
    async def api_books(file: UploadFile = File(...)) -> Book:
        filename = sanitize_filename(file.filename or "book.epub")
        if Path(filename).suffix.lower() != ".epub":
            raise HTTPException(status_code=400, detail="仅支持 .epub 文件")
        data = bytearray()
        total = 0
        while chunk := await file.read(1 << 20):
            total += len(chunk)
            if total > max_epub_bytes:
                raise HTTPException(status_code=400, detail="文件过大")
            data += chunk
        try:
            return await app.reading.import_book(filename, bytes(data))
        except DuplicateBookError as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "existing_book_id": error.existing_book_id,
                    "title": error.title,
                },
            ) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception:
            logging.getLogger(__name__).exception("导入 EPUB 失败: %s", filename)
            raise HTTPException(status_code=500, detail="EPUB 解析失败") from None

    @fast.get("/api/books")
    async def api_books_list() -> list[BookListItem]:
        return await app.reading.list_books()

    @fast.get("/api/books/{book_id}/paragraphs")
    async def api_book_paragraphs(
        book_id: str,
        from_: int = Query(..., ge=1, alias="from"),
        to: int = Query(..., ge=1),
    ) -> list[Paragraph]:
        if to < from_:
            raise HTTPException(status_code=422, detail="to 必须 >= from")
        try:
            return await app.reading.list_paragraphs(book_id, from_, to)
        except BookNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @fast.get("/api/progress/{book_id}")
    async def api_get_progress(book_id: str) -> ReadingProgress:
        try:
            return await app.reading.get_progress(book_id)
        except BookNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @fast.put("/api/progress/{book_id}")
    async def api_put_progress(
        book_id: str, payload: _ProgressPayload
    ) -> ReadingProgress:
        try:
            progress = await app.reading.save_progress(
                book_id,
                payload.user_position,
                payload.nyx_position,
                payload.reading_speed,
                payload.expected_revision,
            )
        except BookNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ProgressConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return progress

    @fast.post("/api/impulse/evaluate")
    async def api_impulse_evaluate(
        payload: _ImpulsePayload,
    ) -> dict[str, list[str]]:
        triggered = await app.reading.evaluate_paragraph(
            payload.book_id, payload.paragraph_index, payload.last_paragraph_index
        )
        return {"triggered": [behavior.value for behavior in triggered]}

    @fast.get("/api/notes/{book_id}")
    async def api_list_notes(book_id: str) -> list[UserNote]:
        try:
            return await app.reading.list_user_notes(book_id)
        except BookNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @fast.post("/api/notes/user", status_code=201)
    async def api_add_user_note(payload: _UserNotePayload) -> UserNote:
        try:
            return await app.reading.add_user_note(
                payload.book_id,
                payload.paragraph_id,
                payload.content,
                payload.selected_text,
                payload.selection_start,
                payload.selection_end,
            )
        except BookNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @fast.put("/api/notes/user/{note_id}")
    async def api_update_user_note(
        note_id: str, payload: _UpdateNotePayload
    ) -> UserNote:
        try:
            return await app.reading.update_user_note(note_id, payload.content)
        except NoteNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @fast.delete("/api/notes/user/{note_id}", status_code=204)
    async def api_delete_user_note(note_id: str) -> None:
        try:
            await app.reading.delete_user_note(note_id)
        except NoteNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @fast.post("/api/notes/{user_note_id}/show-to-nyx")
    async def api_show_to_nyx(user_note_id: str) -> Annotation | None:
        try:
            return await app.reading.show_to_nyx(user_note_id)
        except NoteNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @fast.get("/api/bookmarks/{book_id}")
    async def api_list_bookmarks(book_id: str) -> list[Bookmark]:
        try:
            return await app.reading.list_bookmarks(book_id)
        except BookNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @fast.post("/api/bookmarks", status_code=201)
    async def api_add_bookmark(payload: _BookmarkPayload) -> Bookmark:
        try:
            return await app.reading.add_bookmark(payload.book_id, payload.paragraph_id)
        except BookNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @fast.delete("/api/bookmarks/{bookmark_id}", status_code=204)
    async def api_delete_bookmark(bookmark_id: str) -> None:
        try:
            await app.reading.delete_bookmark(bookmark_id)
        except BookmarkNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @fast.post("/api/notes/check-chapter-boundary")
    async def api_check_chapter_boundary(
        payload: _BoundaryPayload,
    ) -> dict[str, bool]:
        try:
            result = await app.reading.check_chapter_boundary(
                payload.book_id, payload.nyx_position
            )
        except BookNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {
            "is_boundary": result is BoundaryResult.CHAPTER_END,
            "book_finished": result is BoundaryResult.BOOK_FINISHED,
        }

    @fast.post("/api/observe")
    async def api_observe(payload: _ObservePayload) -> dict[str, str]:
        event = root_event(
            EventType.OBSERVATION_STATE,
            {
                "presence": payload.presence,
                "window_title": payload.window_title,
                "sampled_at": payload.sampled_at,
            },
        )
        if payload.sampled_at > event.timestamp:
            raise HTTPException(status_code=422, detail="sampled_at is in the future")
        try:
            accepted = await app.publish_observation(event, payload.idle_seconds)
        except EventAdmissionError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        if not accepted:
            raise HTTPException(status_code=409, detail="stale presence sample")
        return {"event_id": event.id}

    @fast.get("/api/events")
    async def api_events(
        request: Request = cast(Request, None),
    ) -> StreamingResponse:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=sse_queue_size)
        last_event_id = request.headers.get("last-event-id") if request else None
        if app.host is not None and last_event_id:
            prefix = app.character_id + ":"
            if not last_event_id.startswith(prefix):
                raise HTTPException(409, "SSE 游标不属于该角色")
            last_event_id = last_event_id[len(prefix) :]
        app.bus.add_sse_sink(queue)
        list_events = getattr(app.bus, "list_events", None)
        replay: list[Event] = []
        if callable(list_events):
            load_replay = cast(
                Callable[..., Awaitable[list[Event]]],
                list_events,
            )
            try:
                replay = await load_replay(limit=1000, after=last_event_id)
            except BaseException:
                app.bus.remove_sse_sink(queue)
                raise

        async def generate():
            try:
                pending = iter(replay)
                while True:
                    try:
                        event = next(pending)
                    except StopIteration:
                        event = await queue.get()
                    data = {
                        **event.content,
                        "event_id": event.id,
                        "correlation_id": event.correlation_id,
                        "timestamp": event.timestamp,
                        "character_id": app.character_id,
                    }
                    payload = json.dumps(data, ensure_ascii=False, default=str)
                    yield (
                        f"id: {app.character_id + ':' if app.host else ''}{event.id}\n"
                        f"event: {event.type.value}\n"
                        f"data: {payload}\n\n"
                    )
            finally:
                app.bus.remove_sse_sink(queue)

        return StreamingResponse(generate(), media_type="text/event-stream")

    install_local_guard(fast)
    return fast


def install_local_guard(fast: FastAPI) -> None:
    """Apply the same localhost and origin guard to host and bound role APIs."""

    trusted_origins = frozenset(
        (
            "http://localhost:5173",
            "http://tauri.localhost",
            "tauri://localhost",
        )
    )

    @fast.middleware("http")
    async def local_api_guard(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if not request.url.path.startswith("/api"):
            return await call_next(request)
        origin = request.headers.get("origin")

        def reject(status: int, code: str) -> JSONResponse:
            return JSONResponse(
                status_code=status,
                content={
                    "detail": {
                        "code": code,
                        "message": "Request rejected",
                        "retryable": False,
                    }
                },
            )

        try:
            host = urlsplit("http://" + request.headers.get("host", ""))
            valid_host = (
                host.hostname in ("localhost", "127.0.0.1", "::1")
                and host.port == 8000
                and not host.username
                and not host.password
                and not host.path
                and not host.query
                and not host.fragment
            )
        except ValueError:
            valid_host = False
        if not valid_host:
            return reject(400, "invalid_host")
        if origin is not None and origin not in trusted_origins:
            return reject(403, "origin_forbidden")
        if (
            request.headers.get("sec-fetch-site") == "cross-site"
            and origin not in trusted_origins
        ):
            return reject(403, "origin_forbidden")

        method = (
            request.headers.get("access-control-request-method", "")
            if request.method == "OPTIONS"
            else request.method
        )
        scope = {**request.scope, "method": method}
        route = next(
            (
                item
                for item in fast.routes
                if isinstance(item, (APIRoute, Mount))
                and item.matches(scope)[0] is Match.FULL
            ),
            None,
        )
        if request.method == "OPTIONS":
            headers = {
                item.strip().lower()
                for item in request.headers.get(
                    "access-control-request-headers", ""
                ).split(",")
                if item.strip()
            }
            if (
                origin not in trusted_origins
                or route is None
                or headers - {"content-type", "x-nyx-selection-revision"}
                or method not in {"GET", "POST", "PUT", "DELETE"}
            ):
                return reject(403, "origin_forbidden")
            response: Response | None = Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Methods": ", ".join(
                        sorted(
                            (
                                route.methods
                                if isinstance(route, APIRoute)
                                else {"GET", "POST", "PUT", "DELETE"}
                            )
                            | {"OPTIONS"}
                        )
                    ),
                    "Access-Control-Allow-Headers": (
                        "Content-Type, X-Nyx-Selection-Revision"
                    ),
                },
            )
            if request.headers.get("access-control-request-private-network") == "true":
                response.headers["Access-Control-Allow-Private-Network"] = "true"
        else:
            response = None
            if (
                response is None
                and request.method in ("POST", "PUT", "DELETE")
                and isinstance(route, APIRoute)
            ):
                content_type = request.headers.get("content-type", "")
                media = content_type.split(";", 1)[0].strip().lower()
                if route.body_field is not None:
                    required = getattr(
                        route.body_field.field_info, "media_type", "application/json"
                    )
                    if media != required or (
                        media == "multipart/form-data"
                        and "boundary=" not in content_type.lower()
                    ):
                        response = reject(415, "unsupported_media_type")
                elif await request.body():
                    response = reject(415, "unsupported_media_type")
            if response is None:
                response = await call_next(request)
        if origin in trusted_origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            vary = response.headers.get("Vary")
            response.headers["Vary"] = f"{vary}, Origin" if vary else "Origin"
        return response


class _SelectionPayload(BaseModel):
    character_id: str = Field(min_length=1, strict=True)


class _HostObservePayload(_ObservePayload):
    sample_id: str = Field(min_length=1, max_length=200, strict=True)
    character_id: str | None = None
    selection_revision: int | None = Field(default=None, ge=0)


def build_host_app(host: _Host, *, launch_nonce: str | None) -> FastAPI:
    """Route each request to a fixed runtime, including legacy Nyx aliases."""
    fast = FastAPI(title="Nyx Agent")
    fast.add_exception_handler(RequestValidationError, _validation_error)
    bound: dict[str, tuple[_App, FastAPI]] = {}

    @fast.get("/api/ready")
    async def ready() -> dict[str, str | None]:
        return {"service": "nyx-agent", "launch_nonce": launch_nonce}

    @fast.get("/api/characters")
    async def characters() -> dict[str, Any]:
        async with host.database.lock:
            errors = await (
                await host.database.conn.execute(
                    "SELECT character_id, error FROM presence_delivery WHERE "
                    "status='pending' AND attempts>=5 ORDER BY sequence"
                )
            ).fetchall()
        sync_errors = {row["character_id"]: row["error"] for row in errors}
        result: list[dict[str, Any]] = []
        for record in host.records.values():
            assets: dict[str, str] = {}
            try:
                assets = load_card(Path(record["card_path"])).manifest.assets
            except (ValueError, OSError):
                pass  # Failed installations still need a manageable registry entry.
            result.append(
                {key: record[key] for key in ("id", "name", "status", "error")}
                | {
                    "assets": {
                        key: f"/api/characters/{record['id']}/assets/{key}"
                        for key in assets
                    },
                    "sync_error": sync_errors.get(record["id"]),
                }
            )
        return {
            "characters": result,
            "current_id": host.current_id,
            "selection_revision": host.revision,
            "limit": 4,
        }

    @fast.put("/api/characters/current")
    async def select(payload: _SelectionPayload) -> dict[str, Any]:
        try:
            revision = await host.select(payload.character_id)
        except KeyError as error:
            raise HTTPException(404, "角色不存在") from error
        except ValueError as error:
            raise HTTPException(503, str(error)) from error
        return {"current_id": host.current_id, "selection_revision": revision}

    def spawn_start(identity: str) -> None:
        task = asyncio.create_task(host.start(identity))
        host.background.add(task)
        task.add_done_callback(host.background.discard)

    @fast.post("/api/characters", status_code=202)
    async def register(file: UploadFile = File(...)) -> dict[str, Any]:
        if not file.filename or Path(file.filename).suffix.lower() not in (
            ".nyxcard",
            ".zip",
        ):
            raise HTTPException(422, "请上传 .nyxcard 或 .zip 卡包")
        try:
            record = await host.register(await file.read(MAX_CARD_BYTES + 1))
        except ValueError as error:
            raise HTTPException(
                409 if "四个" in str(error) or "已存在" in str(error) else 422,
                str(error),
            ) from error
        if record["status"] == "STARTING":
            spawn_start(record["id"])
        return {key: record[key] for key in ("id", "name", "status", "error")}

    def known(identity: str) -> None:
        if identity not in host.records:
            raise HTTPException(404, "角色不存在")

    @fast.delete("/api/characters/{identity}", status_code=202)
    async def unload(identity: str) -> dict[str, str]:
        known(identity)
        try:
            await host.unload(identity)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return {"status": host.records[identity]["status"]}

    @fast.post("/api/characters/{identity}/restore", status_code=202)
    async def restore(identity: str) -> dict[str, str]:
        known(identity)
        try:
            await host.restore(identity, start=False)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        spawn_start(identity)
        return {"status": "STARTING"}

    @fast.post("/api/characters/{identity}/retry-start", status_code=202)
    async def retry(identity: str) -> dict[str, str]:
        known(identity)
        try:
            await host.retry_start(identity)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        spawn_start(identity)
        return {"status": "STARTING"}

    @fast.get("/api/characters/{identity}/assets/{key}")
    async def asset(identity: str, key: str) -> FileResponse:
        known(identity)
        directory = Path(host.records[identity]["card_path"])
        try:
            card = load_card(directory)
        except (ValueError, OSError) as error:
            raise HTTPException(503, "角色图片不可用") from error
        if key not in card.manifest.assets:
            raise HTTPException(404, "角色没有该图片")
        return FileResponse(directory / card.manifest.assets[key])

    @fast.post("/api/observe")
    async def observe(payload: _HostObservePayload) -> dict[str, Any]:
        if payload.sampled_at > time.time():
            raise HTTPException(422, "sampled_at is in the future")
        try:
            sequence = await host.observe(payload.model_dump())
        except Exception as error:
            raise HTTPException(503, "在线状态未受理") from error
        return {"sequence": sequence, "event_id": f"presence:{sequence}"}

    async def dispatch(scope: Scope, receive: Receive, send: Send) -> None:
        path = scope["path"]
        if path.startswith("/api/characters/"):
            parts = path[len("/api/characters/") :].split("/", 1)
            identity = parts[0]
            relative = "/api/" + (parts[1] if len(parts) == 2 else "")
        else:
            identity, relative = "nyx", path
        if relative == "/api/observe":
            await JSONResponse({"detail": "观察须通过宿主入口提交"}, status_code=404)(
                scope, receive, send
            )
            return
        record = host.records.get(identity)
        app = host.apps.get(identity)
        response: Response | None = None
        if record is None:
            response = JSONResponse({"detail": "角色不存在"}, status_code=404)
        elif record["status"] != "RUNNING" or app is None:
            response = JSONResponse({"detail": "角色不可运行"}, status_code=503)
        elif scope["method"] in ("POST", "PUT", "DELETE"):
            headers = dict(scope["headers"])
            expected = str(host.revision).encode()
            # Legacy writes retain compatibility when Nyx is selected.
            stale = identity != host.current_id or (
                path.startswith("/api/characters/")
                and headers.get(b"x-nyx-selection-revision") != expected
            )
            if stale:
                response = JSONResponse({"detail": "角色选择已变化"}, status_code=409)
        if response is not None:
            await response(scope, receive, send)
            return
        assert app is not None
        if identity not in bound or bound[identity][0] is not app:
            from nyx.runtime import root_event

            bound[identity] = (
                app,
                build_app(
                    app,
                    root_event=root_event,
                    max_epub_bytes=50 * 1024 * 1024,
                    sse_queue_size=100,
                    launch_nonce=launch_nonce,
                ),
            )
        task = asyncio.current_task()
        if task is not None:
            host.requests[identity].add(task)
        try:
            await bound[identity][1](
                {
                    **scope,
                    "path": relative,
                    "raw_path": relative.encode(),
                    "root_path": "",
                },
                receive,
                send,
            )
        finally:
            if task is not None:
                host.requests[identity].discard(task)

    fast.mount("/api/characters", dispatch)
    fast.mount("/api", dispatch)
    install_local_guard(fast)
    return fast
