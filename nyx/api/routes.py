"""FastAPI 请求模型与 REST/SSE 路由注册。"""
# pyright: reportPrivateUsage=false, reportUnusedFunction=false
import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.middleware.base import RequestResponseEndpoint
from starlette.routing import Match

from nyx.activity.observe import classify_presence
from nyx.app_context import _App
from nyx.browsing.facade import BrowsingFacade
from nyx.enums import BoundaryResult, EventType, MemoryKind, MemoryType
from nyx.events.bus import EventAdmissionError
from nyx.reading.facade import (
    BookNotFoundError,
    DuplicateBookError,
    NoteNotFoundError,
)
from nyx.reading.store import ProgressConflictError
from nyx.types import (
    Activity,
    Annotation,
    Book,
    BookListItem,
    BrowserPageSnapshot,
    CurrentState,
    DesireState,
    EvalRecord,
    EvalStats,
    Event,
    LlmMessage,
    Material,
    Memory,
    Paragraph,
    ReadingProgress,
    SelfNarrative,
    UserNote,
)

RootEvent = Callable[[EventType, dict[str, Any]], Event]
FileIo = Callable[..., Awaitable[dict[str, Any]]]


class _ChatPayload(BaseModel):
    message: str
    reply_to: str | None = None
    browsing_page_id: str | None = None


class _HostCaptureEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str
    navigation_id: str
    capture_seq: int = Field(ge=1)
    raw_url: str = Field(max_length=8192)
    canonical_candidate: str | None = Field(default=None, max_length=8192)
    title: str = Field(max_length=512)
    visible_text: str = Field(max_length=200000)
    selected_text: str | None = Field(default=None, max_length=4000)
    auth_tainted: bool
    truncated: bool = False


class _HostAuthorizationProbe(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str
    navigation_id: str
    sanitized_origin: str = Field(max_length=8192)
    auth_tainted: Literal[True]


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
    content: str = Field(..., min_length=1, max_length=4000)
    selected_text: str | None = Field(default=None, max_length=4000)


class _UpdateNotePayload(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)


class _BoundaryPayload(BaseModel):
    book_id: str
    nyx_position: int = Field(..., ge=1)


def sanitize_filename(name: str) -> str:
    """Remove path, control, and HTML-dangerous characters from an upload name."""
    cleaned = "".join(
        char
        for char in Path(name).name
        if char.isprintable() and char not in '<>"'
    )
    return cleaned[:255] or "book.epub"


def build_app(
    app: _App,
    *,
    root_event: RootEvent,
    file_io: FileIo,
    max_upload_bytes: int,
    max_epub_bytes: int,
    sse_queue_size: int,
) -> FastAPI:
    """Build the FastAPI application around an assembled app context."""
    fast = FastAPI(title="Nyx Agent")

    @fast.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        if (request.url.path != "/api/observe"
                and not request.url.path.startswith("/api/browsing/")):
            return await request_validation_exception_handler(request, error)
        # Rejected input may contain NaN/Infinity, which JSONResponse cannot encode.
        return JSONResponse(status_code=422, content={
            "detail": [
                {key: item[key] for key in ("loc", "msg", "type")}
                for item in error.errors()
            ],
        })

    @fast.get("/api/state")
    async def api_state() -> CurrentState:
        return await app.inner_life.get_state()

    @fast.post("/api/chat")
    async def api_chat(payload: _ChatPayload) -> dict[str, str]:
        if payload.browsing_page_id is not None:
            try:
                context = (
                    await app.browsing.get_prompt_context(payload.browsing_page_id)
                    if app.browsing is not None
                    else None
                )
            except ValueError:
                context = None
            if context is None:
                raise HTTPException(status_code=422, detail="invalid browsing_page_id")
        content: dict[str, Any] = {"message": payload.message}
        if payload.reply_to is not None:
            content["reply_to"] = payload.reply_to
        if payload.browsing_page_id is not None:
            content["browsing_page_id"] = payload.browsing_page_id
        event = root_event(EventType.USER_MESSAGE, content)
        try:
            await app.bus.publish(event)
        except EventAdmissionError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return {"event_id": event.id}

    @fast.get("/api/memories")
    async def api_memories(
        kind: MemoryKind | None = None, type: MemoryType | None = None
    ) -> list[Memory]:
        return await app.memory.list_memories(kind, type)

    @fast.get("/api/memories/search")
    async def api_memory_search(q: str) -> list[Memory]:
        return await app.memory.search(q)

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
    async def api_activity_results(limit: int = 100) -> list[Activity]:
        return await app.activity.get_results(limit)

    @fast.get("/api/events/log")
    async def api_events_log(
        limit: int = 100,
        event_type: EventType | None = None,
        correlation_id: str | None = None,
    ) -> list[Event]:
        return await app.bus.list_events(limit, event_type, correlation_id)

    @fast.get("/api/eval/recent")
    async def api_eval_recent(
        limit: int = Query(5, ge=1, le=100),
    ) -> list[EvalRecord]:
        return await app.eval_store.list_recent(limit)

    @fast.get("/api/eval/total_tokens")
    async def api_eval_total_tokens() -> EvalStats:
        return await app.eval_store.total_tokens()

    @fast.get("/api/eval/{record_id}/prompt")
    async def api_eval_prompt(
        record_id: str, response: Response,
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
        media_type = (
            "application/json" if payload.format == "json" else "text/markdown"
        )
        return Response(content=content, media_type=media_type)

    @fast.post("/api/upload")
    async def api_upload(file: UploadFile = File(...)) -> dict[str, str]:
        name = Path(file.filename or "upload.txt").name
        chunks: list[bytes] = []
        total = 0
        while chunk := await file.read(1 << 20):
            total += len(chunk)
            if total > max_upload_bytes:
                raise HTTPException(status_code=400, detail="文件过大")
            chunks.append(chunk)
        text = b"".join(chunks).decode("utf-8", errors="replace")
        result = await file_io("write", f"uploads/{name}", text)
        path = str(result["path"])
        await app.activity.register_material(path, name, len(text))
        return {"filename": name, "path": path}

    @fast.get("/api/materials")
    async def api_materials() -> dict[str, list[Material]]:
        return {"materials": await app.activity.list_materials()}

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
    async def api_events() -> StreamingResponse:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=sse_queue_size)
        app.bus.add_sse_sink(queue)

        async def generate():
            try:
                while True:
                    event = await queue.get()
                    data = {
                        **event.content,
                        "event_id": event.id,
                        "correlation_id": event.correlation_id,
                        "timestamp": event.timestamp,
                    }
                    payload = json.dumps(data, ensure_ascii=False, default=str)
                    yield f"event: {event.type.value}\ndata: {payload}\n\n"
            finally:
                app.bus.remove_sse_sink(queue)

        return StreamingResponse(generate(), media_type="text/event-stream")

    def browsing_error(code: str) -> HTTPException:
        status = {
            "invalid_bridge_token": 401,
            "session_mismatch": 403,
            "not_found": 404,
            "invalid_url": 422,
            "unsafe_url": 422,
            "invalid_payload": 422,
            "backend_unavailable": 503,
            "browsing_storage_limit": 507,
        }.get(code, 409)
        return HTTPException(
            status_code=status,
            detail={
                "code": code,
                "message": "Browsing request was rejected",
                "retryable": code == "backend_unavailable",
            },
        )

    def browsing_facade() -> BrowsingFacade:
        if app.browsing is None:
            raise browsing_error("backend_unavailable")
        return app.browsing

    def bearer(request: Request) -> str | None:
        value = request.headers.get("authorization", "")
        return value[7:] if value.startswith("Bearer ") else None

    def bridge(
        request: Request, session_id: str, closing: bool = False
    ) -> BrowsingFacade:
        browsing = browsing_facade()
        try:
            browsing.validate_bridge(bearer(request), session_id, closing=closing)
        except ValueError as error:
            raise browsing_error(str(error)) from error
        return browsing

    def exact_body(data: dict[str, Any], keys: set[str]) -> None:
        if set(data) != keys or any(
            not isinstance(value, str) for value in data.values()
        ):
            raise browsing_error("invalid_payload")

    @fast.post("/api/browsing/bridge/sessions")
    async def browser_bootstrap(
        request: Request, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if payload:
            raise browsing_error("invalid_payload")
        try:
            session, token = await browsing_facade().bootstrap(bearer(request))
            return {"session": session, "bridge_token": token}
        except ValueError as error:
            raise browsing_error(str(error)) from error

    @fast.post("/api/browsing/bridge/sessions/{session_id}/navigations")
    async def browser_navigation(
        session_id: str,
        request: Request,
        payload: dict[str, Any],
    ) -> dict[str, bool]:
        exact_body(payload, {"navigation_id"})
        try:
            await bridge(request, session_id).navigation_started(
                session_id, payload["navigation_id"]
            )
        except ValueError as error:
            raise browsing_error(str(error)) from error
        return {"accepted": True}

    @fast.post("/api/browsing/bridge/sessions/{session_id}/origins")
    async def browser_authorize(
        session_id: str,
        request: Request,
        payload: dict[str, Any],
    ) -> dict[str, bool]:
        exact_body(payload, {"navigation_id", "origin"})
        try:
            await bridge(request, session_id).allow_origin(
                session_id, payload["navigation_id"], payload["origin"]
            )
        except ValueError as error:
            raise browsing_error(str(error)) from error
        return {"granted": True}

    @fast.post("/api/browsing/bridge/sessions/{session_id}/origins/revoke")
    async def browser_revoke(
        session_id: str,
        request: Request,
        payload: dict[str, Any],
    ) -> dict[str, bool]:
        exact_body(payload, {"origin"})
        try:
            await bridge(request, session_id).revoke_origin(
                session_id, payload["origin"]
            )
        except ValueError as error:
            raise browsing_error(str(error)) from error
        return {"revoked": True}

    @fast.post("/api/browsing/bridge/sessions/{session_id}/pages")
    async def browser_capture(
        session_id: str,
        request: Request,
        response: Response,
        payload: _HostCaptureEnvelope | _HostAuthorizationProbe,
    ) -> dict[str, Any]:
        browsing = bridge(request, session_id)
        if payload.session_id != session_id:
            raise browsing_error("session_mismatch")
        try:
            if isinstance(payload, _HostAuthorizationProbe):
                await browsing.authorization_probe(
                    session_id, payload.navigation_id, payload.sanitized_origin
                )
                raise browsing_error("origin_authorization_required")
            page, created = await browsing.capture_checkpoint(
                session_id,
                BrowserPageSnapshot(
                    payload.navigation_id,
                    payload.capture_seq,
                    payload.raw_url,
                    payload.canonical_candidate,
                    payload.title,
                    payload.visible_text,
                    payload.selected_text,
                    payload.auth_tainted,
                    payload.truncated,
                ),
            )
            response.status_code = 201 if created else 200
            return {
                "page_id": page.id,
                "status": page.status,
                "revision": page.revision,
                "created": created,
            }
        except ValueError as error:
            raise browsing_error(str(error)) from error

    async def page_bridge(request: Request, page_id: str) -> BrowsingFacade:
        browsing = browsing_facade()
        try:
            browsing.validate_bridge(bearer(request), browsing._session_id or "")
            page = await browsing.get_page(page_id)
        except ValueError as error:
            raise browsing_error(str(error)) from error
        return bridge(request, page.session_id)

    @fast.post("/api/browsing/bridge/pages/{page_id}/focus")
    async def browser_focus(
        page_id: str,
        request: Request,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if (
            set(payload) - {"navigation_id", "revision", "focus_id", "selected_text"}
            or not isinstance(payload.get("navigation_id"), str)
            or type(payload.get("revision")) is not int
            or not isinstance(payload.get("focus_id"), str)
            or (
                payload.get("selected_text") is not None
                and not isinstance(payload["selected_text"], str)
            )
        ):
            raise browsing_error("invalid_payload")
        try:
            await (await page_bridge(request, page_id)).focus_page(
                page_id,
                payload["navigation_id"],
                payload["revision"],
                payload["focus_id"],
                payload.get("selected_text"),
            )
        except ValueError as error:
            raise browsing_error(str(error)) from error
        return {"accepted": True, "revision": payload["revision"]}

    @fast.post("/api/browsing/bridge/pages/{page_id}/leave")
    async def browser_leave(
        page_id: str,
        request: Request,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if (
            set(payload) != {"navigation_id", "revision"}
            or not isinstance(payload["navigation_id"], str)
            or type(payload["revision"]) is not int
        ):
            raise browsing_error("invalid_payload")
        try:
            browsing = await page_bridge(request, page_id)
            await browsing.leave_page(
                page_id, payload["navigation_id"], payload["revision"]
            )
            page = await browsing.get_page(page_id)
        except ValueError as error:
            raise browsing_error(str(error)) from error
        return {"frozen": True, "status": page.status}

    @fast.post("/api/browsing/bridge/sessions/{session_id}/close")
    async def browser_close(
        session_id: str,
        request: Request,
        payload: dict[str, Any],
    ) -> dict[str, bool]:
        if payload:
            raise browsing_error("invalid_payload")
        try:
            await bridge(request, session_id, closing=True).close_session(session_id)
        except ValueError as error:
            raise browsing_error(str(error)) from error
        return {"closed": True}

    @fast.get("/api/browsing/sessions/{session_id}")
    async def browser_session(
        session_id: str,
        limit: int = Query(50, ge=1, le=100),
        cursor: str | None = Query(None, min_length=1, max_length=128),
    ) -> dict[str, Any]:
        try:
            session, pages, next_cursor = await browsing_facade().get_session(
                session_id, limit, cursor
            )
            return {
                "session": session,
                "pages": pages,
                "next_cursor": next_cursor,
            }
        except ValueError as error:
            raise browsing_error(str(error)) from error

    @fast.post("/api/browsing/pages/{page_id}/retry")
    async def browser_retry(page_id: str, payload: dict[str, Any]) -> dict[str, str]:
        if payload:
            raise browsing_error("invalid_payload")
        try:
            browsing = browsing_facade()
            await browsing.retry_page(page_id)
            page = await browsing.get_page(page_id)
            return {"page_id": page.id, "status": page.status}
        except ValueError as error:
            raise browsing_error(str(error)) from error

    @fast.delete("/api/browsing/pages/{page_id}", status_code=204)
    async def browser_forget(page_id: str) -> None:
        try:
            await browsing_facade().forget_page(page_id)
        except ValueError as error:
            raise browsing_error(str(error)) from error

    @fast.delete("/api/browsing/history", status_code=204)
    async def browser_forget_all() -> None:
        try:
            await browsing_facade().forget_all_history()
        except ValueError as error:
            raise browsing_error(str(error)) from error

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
                if isinstance(item, APIRoute) and item.matches(scope)[0] is Match.FULL
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
                or headers - {"content-type"}
                or method not in {"GET", "POST", "PUT", "DELETE"}
            ):
                return reject(403, "origin_forbidden")
            response: Response | None = Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Methods": ", ".join(
                        sorted(route.methods | {"OPTIONS"})
                    ),
                    "Access-Control-Allow-Headers": "Content-Type",
                },
            )
            if request.headers.get("access-control-request-private-network") == "true":
                response.headers["Access-Control-Allow-Private-Network"] = "true"
        else:
            response = None
            if request.url.path.startswith("/api/browsing/bridge/"):
                length = request.headers.get("content-length", "")
                if length.isdecimal() and int(length) > 2097152:
                    response = reject(413, "request_too_large")
                else:
                    chunks = bytearray()
                    async for chunk in request.stream():
                        if len(chunks) + len(chunk) > 2097152:
                            response = reject(413, "request_too_large")
                            break
                        chunks.extend(chunk)
                    if response is None:
                        request._body = bytes(chunks)
            if (
                response is None
                and request.method in ("POST", "PUT", "DELETE")
                and route
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

    return fast
