"""陪读输出缓冲与章末记忆整合。"""
import json
import logging
from dataclasses import dataclass
from typing import Any, cast

from nyx.enums import BoundaryResult, EventType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import internal_event
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade

NYX_BUFFER_MAXLEN = 100

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
    ) -> None:
        self._llm = llm
        self._evaluator = evaluator
        self._memory = memory
        self._bus = bus
        self._logger = logging.getLogger(__name__)
        self.buffer: dict[str, list[NyxBufferEntry]] = {}

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
                + "\n".join(lines)
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
