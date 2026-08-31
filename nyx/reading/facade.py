"""阅读门面（spec 19 内容导入 + 20 进度 + 21 冲动引擎）：EPUB → 去重 → 落库；
翻页 → 段落特征 → 冲动分派。

`parse_epub` 是同步 CPU 阻塞调用，用 `asyncio.to_thread` 卸载，不阻塞事件循环。
构造注入 8 依赖（store + inner_life/desire/memory/llm/evaluator/bus/canon）。
"""

import asyncio
import logging
import time

from nyx.desire.facade import DesireFacade
from nyx.enums import DesireType, EventType, ReadingBehavior
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import internal_event
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
    Book,
    BookListItem,
    CurrentState,
    DesireValue,
    Paragraph,
    ReadingProgress,
)

# 4 个提问子型（associate 之外的全部复合行为）；mutter 是独立闸门，不在此列。
_QUESTION_BEHAVIORS = frozenset({
    ReadingBehavior.QUESTION_KNOWLEDGE,
    ReadingBehavior.QUESTION_PERSONAL,
    ReadingBehavior.QUESTION_REFLECTIVE,
    ReadingBehavior.QUOTE_QUESTION,
})

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


def _desire_value(values: list[DesireValue], type_: DesireType) -> float:
    """从 `DesireState.values` 取某类压力值；缺省 0.0。"""
    for v in values:
        if v.type is type_:
            return v.value
    return 0.0


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
    ) -> None:
        self._store = store
        self._inner_life = inner_life
        self._desire = desire
        self._memory = memory
        self._llm = llm
        self._evaluator = evaluator
        self._bus = bus
        self._canon = canon
        self._logger = logging.getLogger(__name__)
        # 冷却时间戳是唯一内存态（per 进程，重启清零）；无并发锁——见 spec 21 关键决策。
        self._cooldowns: dict[ReadingBehavior, float] = {}
        self._mutter_at = 0.0

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
        now = time.time()

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

        asyncio.create_task(
            self._dispatch(book_id, paragraph_index, text, triggered, mutter, state)
        )
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
        except Exception:
            self._logger.exception(
                "陪读碎碎念失败 book_id=%s paragraph_index=%d",
                book_id, paragraph_index,
            )
            return
        content = output.content.strip()
        if not content:
            return
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
        except Exception:
            self._logger.exception(
                "陪读提问失败 behavior=%s book_id=%s paragraph_index=%d",
                behavior.value, book_id, paragraph_index,
            )
            return
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

    async def _associate_reading(
        self, book_id: str, paragraph_index: int, text: str
    ) -> None:
        """陪读记忆联想：检索段落相关记忆，每条命中广播一条 READING_ASSOCIATION。"""
        try:
            memories = await self._memory.search(text)
        except Exception:
            self._logger.exception(
                "陪读联想检索失败 book_id=%s paragraph_index=%d",
                book_id, paragraph_index,
            )
            return
        for memory in memories[:3]:
            snippet = (memory.summary or memory.content)[:_ASSOCIATION_SNIPPET_CHARS]
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
