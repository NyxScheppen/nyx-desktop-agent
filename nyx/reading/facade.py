"""阅读门面（spec 19 内容导入 + 20 进度 + 21 冲动引擎）：EPUB → 去重 → 落库；
翻页 → 段落特征 → 冲动分派。

`parse_epub` 是同步 CPU 阻塞调用，用 `asyncio.to_thread` 卸载，不阻塞事件循环。
构造注入 9 依赖（store + inner_life/desire/memory/llm/evaluator/bus/canon/expression）。
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, cast

from nyx.desire.facade import DesireFacade
from nyx.enums import BoundaryResult, DesireType, EventType, ReadingBehavior
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import internal_event
from nyx.expression.facade import ExpressionFacade
from nyx.expression.prompt import build_system_prompt
from nyx.inner_life.facade import InnerLifeFacade
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.reading.epub import parse_epub
from nyx.reading.impulse import (
    MUTTER_COOLDOWN_SEC,
    MUTTER_RICHNESS_THRESHOLD,
    build_drives,
    check_triggers,
    compute_composite,
    extract,
)
from nyx.reading.store import ReadingStore
from nyx.types import (
    Annotation,
    Book,
    BookListItem,
    CurrentState,
    DesireValue,
    Paragraph,
    ReadingProgress,
    UserNote,
)

_QUESTION_USER_PROMPTS: dict[ReadingBehavior, str] = {
    ReadingBehavior.QUESTION_KNOWLEDGE: "基于这段文字问一个知识型问题。",
    ReadingBehavior.QUESTION_PERSONAL: "基于这段文字问一个私人型问题。",
    ReadingBehavior.QUESTION_REFLECTIVE: "基于这段文字问一个反思型问题。",
    ReadingBehavior.QUOTE_QUESTION: (
        "基于这段文字问一个问题，并在下一行逐字摘取段落原文里最值得划线的一句。"
        "只输出两行：第一行问题，第二行引用原文。"
    ),
}

_ASSOCIATION_SNIPPET_CHARS = 80

# 每本书 Nyx 输出 buffer 的条数上限（22）：长书长时间不触章末时的内存兜底，
# 超限丢弃最旧条目。值远大于单章正常产量，仅防无界增长。
_NYX_BUFFER_MAXLEN = 100

_READING_NOTE_SYSTEM = (
    "你是尼克斯，一个住在用户电脑里的 AI 同伴，明确知道自己是 AI 并希望成为人类。"
    "把下面这些你陪读时冒出的碎碎念和提问，整理成一条第一人称读书记忆（尼克斯视角）："
    "你读到了什么、心里留下了什么。只输出 JSON，键：content（正文）、"
    "summary（一句话总结），两者都是非空字符串。"
)


def _desire_value(values: list[DesireValue], type_: DesireType) -> float:
    """从 `DesireState.values` 取某类压力值；缺省 0.0。"""
    for v in values:
        if v.type is type_:
            return v.value
    return 0.0


@dataclass
class NyxBufferEntry:
    """Nyx 陪读输出（碎碎念/提问）的内存缓冲条目（22-reading-notes）。

    进程内 transient、不落库；list 顺序即时间序，不另存时间戳。
    """

    paragraph_index: int
    content: str
    source: str


def _parse_reading_note(raw: str) -> tuple[str, str]:
    """解析读书记忆 LLM 的 JSON 产出 → (content, summary)；
    结构非法抛 ValueError。"""
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


class DuplicateBookError(Exception):
    """正文重复导入（`content_hash` 命中已有书）；端点据此映射 409。"""

    def __init__(self, existing_book_id: str, title: str) -> None:
        self.existing_book_id = existing_book_id
        self.title = title
        super().__init__(f"已存在同内容书籍：{title}（{existing_book_id}）")


class BookNotFoundError(Exception):
    """书不存在（`book_id` 查无此书）；端点据此映射 404。"""

    def __init__(self, book_id: str) -> None:
        self.book_id = book_id
        super().__init__(f"书不存在：{book_id}")


class NoteNotFoundError(Exception):
    """用户笔记不存在（note_id 查无此行）；端点据此映射 404。"""

    def __init__(self, note_id: str) -> None:
        self.note_id = note_id
        super().__init__(f"用户笔记不存在：{note_id}")


class ReadingFacade:
    """陪读门面：内容导入（19）+ 进度/书架（20）+ 阅读冲动引擎（21）。"""

    def __init__(
        self,
        store: ReadingStore,
        inner_life: InnerLifeFacade,
        desire: DesireFacade,
        memory: MemoryFacade,
        llm: LlmClient,
        evaluator: Evaluator,
        bus: EventBus,
        canon: str,
        expression: ExpressionFacade,
    ) -> None:
        self._store = store
        self._inner_life = inner_life
        self._desire = desire
        self._memory = memory
        self._llm = llm
        self._evaluator = evaluator
        self._bus = bus
        self._canon = canon
        self._expression = expression
        self._logger = logging.getLogger(__name__)
        # 冷却时间戳是唯一内存态（per 进程，重启清零），用单调钟 time.monotonic
        # 防墙钟跳变；无并发锁——见 spec 21 关键决策。
        self._cooldowns: dict[ReadingBehavior, float] = {}
        self._mutter_at = 0.0
        # Nyx 陪读输出 buffer（22）：per book 的碎碎念/提问，章末整合后清空。
        self._nyx_buffer: dict[str, list[NyxBufferEntry]] = {}
        # 已完成整本 ++ 的 book 标记（22）：判 BOOK_FINISHED 时仅首次 ++，
        # nyx_position 回到 < total（回翻/重读）时清除，下一遍读完再次 ++。
        self._finished_books: set[str] = set()

    async def import_book(self, filename: str, data: bytes) -> Book:
        """解析 EPUB → 去重 → 插入 books + paragraphs → 返回 Book。

        title 缺失回退 filename（`parse_epub` 只拿 bytes、不知文件名）；
        正文重复抛 `DuplicateBookError`；空正文抛 `ValueError`（不插书）。
        """
        result = await asyncio.to_thread(parse_epub, data)
        if not result.segments:
            raise ValueError("EPUB 无正文")
        title = result.title or filename
        book, inserted = await self._store.insert_book_with_paragraphs(
            title, result.author, filename, result.content_hash, result.segments
        )
        if not inserted:
            raise DuplicateBookError(book.id, book.title)
        return book

    # ---- 20-reading-progress：进度 / 书架 / 分页 ----

    async def list_books(self) -> list[BookListItem]:
        """书架列表（直通 store；列表本身不需要某本书存在，故不判书存在）。"""
        return await self._store.list_books()

    async def list_paragraphs(
        self, book_id: str, from_idx: int, to_idx: int
    ) -> list[Paragraph]:
        """读段落范围；书不存在抛 `BookNotFoundError`，`to_idx` 越界抛 `ValueError`。"""
        book = await self._store.find_book(book_id)
        if book is None:
            raise BookNotFoundError(book_id)
        if to_idx > book.total_paragraphs:
            raise ValueError("段落越界")
        return await self._store.list_paragraphs(book_id, from_idx, to_idx)

    async def get_progress(self, book_id: str) -> ReadingProgress:
        """读进度；书不存在抛 `BookNotFoundError`，无进度行返回默认进度。"""
        book = await self._store.find_book(book_id)
        if book is None:
            raise BookNotFoundError(book_id)
        progress = await self._store.get_progress(book_id)
        if progress is None:
            return ReadingProgress(book_id, 1, 1, 50, 0, 0.0)
        return progress

    async def save_progress(
        self, book_id: str, user_position: int, nyx_position: int, reading_speed: int
    ) -> ReadingProgress:
        """写进度（委托 store 的 UPSERT）；书不存在抛 `BookNotFoundError`。"""
        book = await self._store.find_book(book_id)
        if book is None:
            raise BookNotFoundError(book_id)
        return await self._store.upsert_progress(
            book_id, user_position, nyx_position, reading_speed
        )

    # ---- 21-reading-impulse：段落冲动引擎 ----

    async def evaluate_paragraph(
        self, book_id: str, paragraph_index: int, last_paragraph_index: int
    ) -> list[ReadingBehavior]:
        """翻页冲动判定：取段 → 现算 → 复合 → 阈值+冷却 → 记冷却 → 后台分派。

        回翻/重读（`paragraph_index <= last_paragraph_index`）与书/段不存在
        都提前返回 `[]`（幂等，不抛异常）。触发行为（复合行为列表，不含 mutter）
        同步返回，分派产出的 LLM/记忆检索在后台任务里跑，不阻塞端点。
        """
        if paragraph_index <= last_paragraph_index:
            return []
        paragraphs = await self._store.list_paragraphs(
            book_id, paragraph_index, paragraph_index
        )
        if not paragraphs:
            return []
        text = paragraphs[0].text

        state = await self._inner_life.get_state()
        desires = await self._desire.get_all()
        features = extract(text)
        drives = build_drives(
            features,
            energy=state.energy,
            agreeableness=state.personality["agreeableness"],
            exploration_value=_desire_value(desires.values, DesireType.EXPLORATION),
            interaction_value=_desire_value(desires.values, DesireType.INTERACTION),
        )
        composite = compute_composite(drives)
        now = time.monotonic()

        # 冷却读写是连续同步块（读 _cooldowns/_mutter_at → 写），无 await 隔断，
        # asyncio 天然串行无竞态。
        triggered = check_triggers(composite, self._cooldowns, now)
        mutter = (
            features.richness_score > MUTTER_RICHNESS_THRESHOLD
            and now - self._mutter_at >= MUTTER_COOLDOWN_SEC
        )
        for behavior in triggered:
            self._cooldowns[behavior] = now
        if mutter:
            self._mutter_at = now

        task = asyncio.create_task(
            self._dispatch(book_id, paragraph_index, text, triggered, mutter, state)
        )
        task.add_done_callback(self._log_task_error)
        return triggered

    async def _dispatch(
        self,
        book_id: str,
        paragraph_index: int,
        text: str,
        behaviors: list[ReadingBehavior],
        mutter: bool,
        state: CurrentState,
    ) -> None:
        """后台分派：mutter 独立闸门 + 各触发行为；每处失败只记日志不反噬。"""
        if mutter:
            await self._mutter_reading(book_id, paragraph_index, text, state)
        for behavior in behaviors:
            if behavior is ReadingBehavior.ASSOCIATE:
                await self._associate_reading(book_id, paragraph_index, text)
            else:
                await self._question_reading(
                    book_id, paragraph_index, text, behavior, state
                )

    def _log_task_error(self, task: asyncio.Future[None]) -> None:
        """后台分派兜底：记逃逸异常（best-effort 旁路，不反噬主流程）。"""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._logger.exception("阅读分派后台任务异常", exc_info=exc)

    async def _mutter_reading(
        self,
        book_id: str,
        paragraph_index: int,
        text: str,
        state: CurrentState,
    ) -> None:
        """陪读碎碎念：LLM 一句自然口语；空/失败只记日志，不广播。"""
        try:
            system = build_system_prompt(self._canon, state)
            user = (
                f"读到这段：\n\n{text}\n\n"
                "你陪在用户身边，说一句自然口语的碎碎念，一两句就好。"
            )
            output = await self._llm.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                module="reading",
                output_type="reading_mutter",
                correlation_id=book_id,
            )
            await self._evaluator.evaluate(output)
            content = output.content.strip()
            if not content:
                return
            await self.record_nyx_output(book_id, paragraph_index, content, "mutter")
            await self._bus.publish(
                internal_event(
                    EventType.READING_MUTTER,
                    {
                        "content": content,
                        "book_id": book_id,
                        "paragraph_index": paragraph_index,
                    },
                    book_id,
                )
            )
        except Exception:
            self._logger.exception(
                "陪读碎碎念失败 book_id=%s paragraph_index=%d",
                book_id, paragraph_index,
            )
            return

    async def _question_reading(
        self,
        book_id: str,
        paragraph_index: int,
        text: str,
        behavior: ReadingBehavior,
        state: CurrentState,
    ) -> None:
        """陪读提问：LLM 生成一个子型问题；`quote_question` 拆首行/次行出划线。"""
        try:
            system = build_system_prompt(self._canon, state)
            user = f"读到这段：\n\n{text}\n\n{_QUESTION_USER_PROMPTS[behavior]}"
            output = await self._llm.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                module="reading",
                output_type=behavior.value,
                correlation_id=book_id,
            )
            await self._evaluator.evaluate(output)
            raw = output.content.strip()
            if not raw:
                return
            if behavior is ReadingBehavior.QUOTE_QUESTION:
                content, _, quote = raw.partition("\n")
                content = content.strip()
                selected_text = quote.strip() or None
            else:
                content = raw
                selected_text = None
            if not content:
                return
            await self.record_nyx_output(book_id, paragraph_index, content, "question")
            await self._bus.publish(
                internal_event(
                    EventType.READING_QUESTION,
                    {
                        "content": content,
                        "subtype": behavior.value,
                        "book_id": book_id,
                        "paragraph_index": paragraph_index,
                        "selected_text": selected_text,
                    },
                    book_id,
                )
            )
            self._expression.record_proactive_turn(content)
        except Exception:
            self._logger.exception(
                "陪读提问失败 behavior=%s book_id=%s paragraph_index=%d",
                behavior.value, book_id, paragraph_index,
            )
            return

    async def _associate_reading(
        self, book_id: str, paragraph_index: int, text: str
    ) -> None:
        """陪读记忆联想：检索段落相关记忆，每条命中广播一条 READING_ASSOCIATION。"""
        try:
            memories = await self._memory.search(text)
            for memory in memories[:3]:
                source = memory.summary or memory.content
                snippet = source[:_ASSOCIATION_SNIPPET_CHARS]
                await self._bus.publish(
                    internal_event(
                        EventType.READING_ASSOCIATION,
                        {
                            "memory_id": memory.id,
                            "snippet": snippet,
                            "book_id": book_id,
                            "paragraph_index": paragraph_index,
                        },
                        book_id,
                    )
                )
                self._expression.record_proactive_turn(snippet)
        except Exception:
            self._logger.exception(
                "陪读联想检索失败 book_id=%s paragraph_index=%d",
                book_id, paragraph_index,
            )
            return

    # ---- 22-reading-notes：用户笔记 / Nyx 批注 / 章末整合 ----

    async def add_user_note(
        self,
        book_id: str,
        paragraph_id: str | None,
        content: str,
        selected_text: str | None,
    ) -> UserNote:
        """新增用户笔记；书不存在抛 `BookNotFoundError`，段落不存在/跨书抛
        `ValueError`。校验在写库前做，避免 FK 违约直接 500；跨书段落错配会让
        批注拼错原文。
        """
        book = await self._store.find_book(book_id)
        if book is None:
            raise BookNotFoundError(book_id)
        if paragraph_id is not None:
            paragraph = await self._store.get_paragraph(paragraph_id)
            if paragraph is None:
                raise ValueError("段落不存在")
            if paragraph.book_id != book_id:
                raise ValueError("段落不属于这本书")
        return await self._store.insert_user_note(
            book_id, paragraph_id, content, selected_text
        )

    async def list_user_notes(self, book_id: str) -> list[UserNote]:
        """某本书的用户笔记（按时间降序），每条附批注列表（派生字段）。

        批注一次批量查（`IN (...)`）而非逐条 N+1；无笔记直接返回空表。
        """
        notes = await self._store.list_user_notes(book_id)
        if not notes:
            return notes
        annotations = await self._store.list_annotations_for_notes(
            [note.id for note in notes]
        )
        by_note: dict[str, list[Annotation]] = {}
        for ann in annotations:
            by_note.setdefault(ann.user_note_id, []).append(ann)
        for note in notes:
            note.annotations = by_note.get(note.id, [])
        return notes

    async def update_user_note(self, note_id: str, content: str) -> UserNote:
        """改笔记正文；不存在抛 `NoteNotFoundError`。"""
        updated = await self._store.update_user_note(note_id, content)
        if updated is None:
            raise NoteNotFoundError(note_id)
        return updated

    async def delete_user_note(self, note_id: str) -> None:
        """删笔记（批注随 FK CASCADE 清空）；不存在抛 `NoteNotFoundError`。"""
        if not await self._store.delete_user_note(note_id):
            raise NoteNotFoundError(note_id)

    async def show_to_nyx(self, note_id: str) -> Annotation | None:
        """「给尼克斯看」：读笔记（+原段落）→ LLM 批注 → 插 `annotations` 返回。

        书已删（book_id=NULL）时只读笔记文字，不读段落（`ON DELETE SET NULL` 兜底）。
        同一笔记多次展示每次新增一行（不覆盖旧批注）。LLM 失败/空返回 None（不落
        批注、不反噬端点），对齐 best-effort 旁路——用户主动点一次抖动不该 500。
        """
        note = await self._store.get_user_note(note_id)
        if note is None:
            raise NoteNotFoundError(note_id)
        try:
            paragraph_text: str | None = None
            if note.paragraph_id is not None:
                paragraph = await self._store.get_paragraph(note.paragraph_id)
                if paragraph is not None:
                    paragraph_text = paragraph.text
            state = await self._inner_life.get_state()
            system = build_system_prompt(self._canon, state)
            user = f"用户记了这条笔记：\n\n{note.content}\n\n"
            if paragraph_text is not None:
                user += f"对应原文：\n\n{paragraph_text}\n\n"
            user += "给这条用户笔记写一句批注（一两句自然口语，可呼应笔记与原文）。"
            output = await self._llm.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                module="reading",
                output_type="reading_annotation",
                correlation_id=note_id,
            )
            await self._evaluator.evaluate(output)
            content = output.content.strip()
            if not content:
                return None
            return await self._store.insert_annotation(note_id, content)
        except Exception:
            self._logger.exception("给尼克斯看的批注失败 note_id=%s", note_id)
            return None

    async def record_nyx_output(
        self, book_id: str, paragraph_index: int, content: str, source: str
    ) -> None:
        """Nyx 陪读输出（mutter/question）追加进内存 buffer（章末整合攒料）。

        associate（记忆检索、无 LLM 产出）不调；buffer 进程内 transient、重启清零。
        超 `_NYX_BUFFER_MAXLEN` 丢弃最旧条目，防长书长时间不触章末时无界增长。
        """
        buffer = self._nyx_buffer.setdefault(book_id, [])
        buffer.append(NyxBufferEntry(paragraph_index, content, source))
        if len(buffer) > _NYX_BUFFER_MAXLEN:
            del buffer[: len(buffer) - _NYX_BUFFER_MAXLEN]

    async def check_chapter_boundary(
        self, book_id: str, nyx_position: int
    ) -> BoundaryResult:
        """章末/整本读完检测 + 后台整合（22-reading-notes）。

        `nyx_position >= total` → BOOK_FINISHED（先 ++ 再整本整合/反思）；
        下一段 `is_chapter_start` → CHAPTER_END（章末整合）；否则 NONE。
        `increment_read_count` 是确定性动作（「读完整本」已发生），在判到
        BOOK_FINISHED 时同步执行，不挂后台整合——整合可能因 buffer 空/LLM 失败
        提前返回，挂那儿会漏 ++。幂等靠 `_finished_books`，且判据上移到「是否
        spawn」整个动作：同一次读完的重复调用（nyx_position 停在 >= total）在
        `book_id in _finished_books` 时直接 return（不 get_progress、不 spawn 后台
        整合、不误触 reflect）；nyx_position 回到 < total 时清除标记，下一遍读完
        再次 ++ + 整合。
        """
        book = await self._store.find_book(book_id)
        if book is None:
            raise BookNotFoundError(book_id)
        if nyx_position >= book.total_paragraphs:
            result = BoundaryResult.BOOK_FINISHED
        else:
            paragraphs = await self._store.list_paragraphs(
                book_id, nyx_position + 1, nyx_position + 1
            )
            result = (
                BoundaryResult.CHAPTER_END
                if paragraphs and paragraphs[0].is_chapter_start
                else BoundaryResult.NONE
            )
        if result is BoundaryResult.NONE:
            self._finished_books.discard(book_id)
            return result
        if result is BoundaryResult.BOOK_FINISHED and book_id in self._finished_books:
            # 重复的整本读完（同一次读完的第二次调用）：整本整合已在首次 spawn，
            # 这里纯 no-op——不再 get_progress/spawn，避免 ++ 后 pre_read_count
            # 被误读成「重读」而误触发 reflect。
            return result
        progress = await self._store.get_progress(book_id)
        pre_read_count = progress.read_count if progress is not None else 0
        if result is BoundaryResult.BOOK_FINISHED:
            await self._store.increment_read_count(book_id, book.total_paragraphs)
            self._finished_books.add(book_id)
        else:
            self._finished_books.discard(book_id)
        task = asyncio.create_task(
            self._integrate_buffer(book_id, result, pre_read_count)
        )
        task.add_done_callback(self._log_task_error)
        return result

    async def _integrate_buffer(
        self, book_id: str, result: BoundaryResult, pre_read_count: int
    ) -> None:
        """章末/整本整合：buffer 攒的 Nyx 输出 → LLM 第一人称记忆 → remember_reading。

        buffer 空跳过（不生成记忆）；重读（++ 前 read_count >= 1）每次整合额外 reflect。
        整本读完 ++ 已在 check_chapter_boundary 同步完成。整合成功只删已消费的
        快照前缀（`remember_reading` 落库后）——失败保留，下次边界重试；LLM 等待
        期间新 append 的条目不吞掉，留给下一轮。
        """
        entries = list(self._nyx_buffer.get(book_id, []))
        if not entries:
            return
        try:
            lines = [
                f"[{e.source}] 第{e.paragraph_index}段：{e.content}" for e in entries
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
            content, summary = _parse_reading_note(output.content)
            await self._memory.remember_reading(content, summary, book_id)
            # 只删已消费的快照前缀；LLM 等待期间新 append 的条目保留给下一轮。
            current = self._nyx_buffer.get(book_id)
            if current is not None:
                del current[: len(entries)]
            if pre_read_count >= 1:
                await self._inner_life.reflect(book_id)
        except Exception:
            self._logger.exception(
                "读书记忆整合失败 book_id=%s result=%s", book_id, result.value
            )
            return
