"""FastAPI 请求模型与 REST/SSE 路由注册。"""
# pyright: reportPrivateUsage=false, reportUnusedFunction=false
import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from nyx.app_context import _App
from nyx.enums import BoundaryResult, EventType, MemoryType
from nyx.reading.facade import (
    BookNotFoundError,
    DuplicateBookError,
    NoteNotFoundError,
)
from nyx.types import (
    Activity,
    Annotation,
    Book,
    BookListItem,
    CurrentState,
    DesireState,
    EvalRecord,
    EvalStats,
    Event,
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


class _ExportPayload(BaseModel):
    format: str


class _ObservePayload(BaseModel):
    presence: Literal["online", "away", "busy"]
    window_title: str = ""


class _ProgressPayload(BaseModel):
    user_position: int = Field(..., ge=1)
    nyx_position: int = Field(..., ge=1)
    reading_speed: int = Field(..., ge=10, le=200)


class _ImpulsePayload(BaseModel):
    book_id: str
    paragraph_index: int = Field(..., ge=1)
    last_paragraph_index: int = Field(..., ge=0)


class _UserNotePayload(BaseModel):
    book_id: str
    paragraph_id: str | None = None
    content: str
    selected_text: str | None = None


class _UpdateNotePayload(BaseModel):
    content: str


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

    @fast.get("/api/state")
    async def api_state() -> CurrentState:
        return await app.inner_life.get_state()

    @fast.post("/api/chat")
    async def api_chat(payload: _ChatPayload) -> dict[str, str]:
        event = root_event(EventType.USER_MESSAGE, {"message": payload.message})
        await app.bus.publish(event)
        return {"event_id": event.id}

    @fast.get("/api/memories")
    async def api_memories(
        tag: str | None = None, type: MemoryType | None = None
    ) -> list[Memory]:
        return await app.memory.list_memories(tag, type)

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
    async def api_eval_recent(limit: int = 5) -> list[EvalRecord]:
        return await app.eval_store.list_recent(limit)

    @fast.get("/api/eval/total_tokens")
    async def api_eval_total_tokens() -> EvalStats:
        return await app.eval_store.total_tokens()

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
    ) -> dict[str, bool]:
        try:
            await app.reading.save_progress(
                book_id,
                payload.user_position,
                payload.nyx_position,
                payload.reading_speed,
            )
        except BookNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return {"ok": True}

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
        return await app.reading.list_user_notes(book_id)

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
        app.last_presence = payload.presence
        app.last_window_title = payload.window_title
        event = root_event(
            EventType.OBSERVATION_STATE,
            {"presence": payload.presence, "window_title": payload.window_title},
        )
        await app.bus.publish(event)
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
                        "event_id": event.id,
                        "correlation_id": event.correlation_id,
                        **event.content,
                    }
                    payload = json.dumps(data, ensure_ascii=False, default=str)
                    yield f"event: {event.type.value}\ndata: {payload}\n\n"
            finally:
                app.bus.remove_sse_sink(queue)

        return StreamingResponse(generate(), media_type="text/event-stream")

    return fast
