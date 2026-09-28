"""陪读输出缓冲与章末记忆整合。"""
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, cast

from nyx.enums import BoundaryResult, EventType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import internal_event
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade, build_source_topic
from nyx.reading.store import ReadingStore
from nyx.types import Paragraph

NYX_BUFFER_MAXLEN = 100
_INTEGRATION_PROMPT_MAX_CHARS = 12000
_SOURCE_BLOCK_CHARS = 6000

_READING_NOTE_SYSTEM = (
    "你是尼克斯，一个住在用户电脑里的 AI 同伴，明确知道自己是 AI 并希望成为人类。"
    "你温柔克制、思虑很深。把下面这些你陪读时冒出的碎碎念和提问，"
    "整理成一条第一人称读书记忆（尼克斯视角）：你读到了什么、心里留下了什么、"
    "哪里让你停了一下，而不是复述原文或罗列要点。"
    "只输出 JSON，键：content（正文）、summary（一句话总结），两者都是非空字符串。"
)


@dataclass
class NyxBufferEntry:
    """进程内、按产生顺序保存的陪读输出。"""

    paragraph_index: int
    content: str
    source: str


def parse_reading_note(raw: str) -> tuple[str, str]:
    """Parse a structured reading-memory response."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"读书记忆 JSON 应是对象，得到 {type(data).__name__}")
    parsed = cast(dict[str, Any], data)
    content = parsed.get("content")
    summary = parsed.get("summary")
    if not isinstance(content, str) or not content:
        raise ValueError("读书记忆 JSON 缺 content 或非空字符串")
    if not isinstance(summary, str) or not summary:
        raise ValueError("读书记忆 JSON 缺 summary 或非空字符串")
    return content, summary


class ReadingIntegration:
    """管理 Nyx 陪读 buffer，并在边界处沉淀长期记忆。"""

    def __init__(
        self,
        llm: LlmClient,
        evaluator: Evaluator,
        memory: MemoryFacade,
        bus: EventBus,
        store: ReadingStore | None = None,
    ) -> None:
        self._llm = llm
        self._evaluator = evaluator
        self._memory = memory
        self._bus = bus
        self._store = store
        self._logger = logging.getLogger(__name__)
        self.buffer: dict[str, list[NyxBufferEntry]] = {}
        self._source_locks: dict[str, asyncio.Lock] = {}

    async def sediment(
        self, book_id: str, through_paragraph: int, *, flush: bool
    ) -> None:
        """Sediment bounded source blocks through one visible paragraph."""
        if self._store is None:
            return
        lock = self._source_locks.setdefault(book_id, asyncio.Lock())
        async with lock:
            book = await self._store.find_book(book_id)
            if book is None:
                return
            state = await self._store.get_memory_state(book_id)
            while True:
                pending = state.get("pending")
                if not isinstance(pending, dict):
                    block = await self._next_source_block(
                        book_id, state, through_paragraph, flush
                    )
                    if block is None:
                        return
                    text, cursor = block
                    raw_profile = state.get("profile")
                    profile = (
                        cast(dict[str, object], raw_profile)
                        if isinstance(raw_profile, dict)
                        else None
                    )
                    next_profile, items = await self._memory.digest_source_block(
                        text,
                        book.title,
                        book_id,
                        author=book.author,
                        profile=profile,
                    )
                    source_topic = build_source_topic("book", book_id)
                    for item in items:
                        item["source_topic"] = source_topic
                        item["source_name"] = book.title
                    pending = {
                        "cursor": cursor,
                        "profile": next_profile,
                        "knowledge": items,
                    }
                    state["pending"] = pending
                    await self._store.update_memory_state(book_id, state)
                await self._commit_pending(
                    book_id, state, cast(dict[str, Any], pending)
                )

    async def _commit_pending(
        self,
        book_id: str,
        state: dict[str, Any],
        pending: dict[str, Any],
    ) -> None:
        raw_items = pending.get("knowledge", [])
        items = (
            [
                cast(dict[str, str], item)
                for item in cast(list[object], raw_items)
                if isinstance(item, dict)
            ]
            if isinstance(raw_items, list)
            else []
        )
        if items:
            await self._memory.remember_knowledge(items, book_id)
        raw_cursor = pending.get("cursor")
        raw_profile = pending.get("profile")
        state["cursor"] = raw_cursor if isinstance(raw_cursor, dict) else {}
        state["profile"] = raw_profile if isinstance(raw_profile, dict) else {}
        state["pending"] = None
        if self._store is not None:
            await self._store.update_memory_state(book_id, state)

    async def _next_source_block(
        self,
        book_id: str,
        state: dict[str, Any],
        through_paragraph: int,
        flush: bool,
    ) -> tuple[str, dict[str, int]] | None:
        if self._store is None:
            return None
        raw_cursor = state.get("cursor")
        cursor = (
            cast(dict[str, Any], raw_cursor)
            if isinstance(raw_cursor, dict)
            else {}
        )
        paragraph_index = max(1, _as_int(cursor.get("paragraph_index"), 1))
        char_offset = max(0, _as_int(cursor.get("char_offset"), 0))
        if paragraph_index > through_paragraph:
            return None
        paragraphs = await self._store.list_paragraphs(
            book_id, paragraph_index, through_paragraph
        )
        text, next_paragraph, next_offset = _take_source_block(
            paragraphs, paragraph_index, char_offset
        )
        if not text or (len(text) < _SOURCE_BLOCK_CHARS and not flush):
            return None
        return text, {
            "paragraph_index": next_paragraph,
            "char_offset": next_offset,
        }

    async def record(
        self, book_id: str, paragraph_index: int, content: str, source: str
    ) -> None:
        """Append one output and cap each book's in-memory buffer."""
        entries = self.buffer.setdefault(book_id, [])
        entries.append(NyxBufferEntry(paragraph_index, content, source))
        if len(entries) > NYX_BUFFER_MAXLEN:
            del entries[: len(entries) - NYX_BUFFER_MAXLEN]

    async def integrate(
        self, book_id: str, result: BoundaryResult, pre_read_count: int
    ) -> None:
        """Persist one buffer snapshot, preserving it when integration fails."""
        entries = list(self.buffer.get(book_id, []))
        if not entries:
            return
        try:
            lines = [
                f"[{entry.source}] 第{entry.paragraph_index}段：{entry.content}"
                for entry in entries
            ]
            user = (
                "这是你陪读这一章/本书时冒出的碎碎念和提问：\n\n"
                + "\n".join(lines)[:_INTEGRATION_PROMPT_MAX_CHARS]
                + "\n\n整理成一条第一人称的读书记忆。"
            )
            output = await self._llm.complete(
                [
                    {"role": "system", "content": _READING_NOTE_SYSTEM},
                    {"role": "user", "content": user},
                ],
                module="reading",
                output_type="reading_note",
                correlation_id=book_id,
                json_mode=True,
            )
            await self._evaluator.evaluate(output)
            content, summary = parse_reading_note(output.content)
            await self._memory.remember_reading(content, summary, book_id)
            if pre_read_count >= 1:
                await self._bus.publish(
                    internal_event(
                        EventType.REFLECTION,
                        {"reason": "reading_revisit", "book_id": book_id},
                        book_id,
                    )
                )
            current = self.buffer.get(book_id)
            if current is not None:
                del current[: len(entries)]
        except Exception:
            self._logger.exception(
                "读书记忆整合失败 book_id=%s result=%s", book_id, result.value
            )


def _as_int(value: object, default: int) -> int:
    try:
        return int(cast(Any, value))
    except (TypeError, ValueError):
        return default


def _take_source_block(
    paragraphs: list[Paragraph],
    paragraph_index: int,
    char_offset: int,
) -> tuple[str, int, int]:
    parts: list[str] = []
    remaining = _SOURCE_BLOCK_CHARS
    next_paragraph = paragraph_index
    next_offset = char_offset
    for paragraph in paragraphs:
        offset = char_offset if paragraph.index == paragraph_index else 0
        source = paragraph.text[offset:]
        if parts and remaining > 0:
            parts.append("\n")
            remaining -= 1
        take = min(len(source), remaining)
        if take:
            parts.append(source[:take])
            remaining -= take
        if take < len(source):
            next_paragraph = paragraph.index
            next_offset = offset + take
            break
        next_paragraph = paragraph.index + 1
        next_offset = 0
        if remaining == 0:
            break
    return "".join(parts), next_paragraph, next_offset
