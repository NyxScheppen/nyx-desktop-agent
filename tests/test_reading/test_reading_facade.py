# pyright: reportPrivateUsage=false
"""ReadingFacade 集成测试：19 内容导入 + 20 进度 + 21 冲动引擎，:memory: + 真 store。

`parse_epub` 用 monkeypatch 注入固定 `EpubResult`（不碰真实 EPUB 字节）。
21 的依赖（inner_life/desire/memory/llm/evaluator/bus）用 duck-typed fake
`cast` 注入；后台分派用 `asyncio.sleep(0)` 跑完（fake 无真 I/O，不悬挂）。
"""

import asyncio
from typing import cast

import pytest

from nyx import db
from nyx.desire.facade import DesireFacade
from nyx.enums import (
    BoundaryResult,
    DesireType,
    EmotionCategory,
    EnergyState,
    EventType,
    MemoryType,
    ReadingBehavior,
)
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.inner_life.facade import InnerLifeFacade
from nyx.llm.client import LlmClient, LlmMessage
from nyx.memory.facade import MemoryFacade
from nyx.reading import facade as facade_mod
from nyx.reading.epub import EpubResult
from nyx.reading.facade import (
    BookNotFoundError,
    DuplicateBookError,
    NoteNotFoundError,
    ReadingFacade,
)
from nyx.reading.segmenter import Segment
from nyx.reading.store import ReadingStore
from nyx.types import (
    CurrentState,
    DesireState,
    DesireValue,
    Event,
    LLMOutput,
    Memory,
    Personality,
    Values,
)

_RICH_TEXT = (
    "他绝望地哭，问生命的意义是什么？为什么自由如此虚无！"
    "她说……命运与灵魂在黑暗中挣扎。"
)
_FLAT_TEXT = "今天天气不错。"


def _mk_state(energy: float = 100.0, agreeableness: float = 10.0) -> CurrentState:
    personality: Personality = {
        "openness": 5.0,
        "conscientiousness": 5.0,
        "extraversion": 5.0,
        "agreeableness": agreeableness,
        "neuroticism": 5.0,
    }
    values: Values = {
        "attitude_to_human": 5.0,
        "ai_identity_acceptance": 5.0,
        "altruism": 5.0,
        "optimism": 5.0,
    }
    return CurrentState(
        valence=0.0,
        arousal=0.0,
        emotion=EmotionCategory.NEUTRAL,
        personality=personality,
        values=values,
        energy=energy,
        energy_state=EnergyState.OKAY,
        current_activity=None,
        active_desires=[],
    )


def _desire_values() -> list[DesireValue]:
    return [
        DesireValue(DesireType.EXPLORATION, 1.0, 1.0, 1.0, 0.0),
        DesireValue(DesireType.INTERACTION, 1.0, 1.0, 1.0, 0.0),
    ]


def _memory(mid: str = "m1", content: str = "生命的意义在于寻找") -> Memory:
    return Memory(
        id=mid, created_at=0.0, content=content, tag="knowledge",
        summary="", freshness=1.0, type=MemoryType.SHORT_TERM,
    )


class _FakeInnerLife:
    def __init__(self, state: CurrentState) -> None:
        self.state = state
        self.reflect_calls: list[str | None] = []

    async def get_state(self) -> CurrentState:
        return self.state

    async def reflect(self, correlation_id: str | None = None) -> None:
        self.reflect_calls.append(correlation_id)


class _FakeDesire:
    def __init__(self, values: list[DesireValue]) -> None:
        self._values = values

    async def get_all(self) -> DesireState:
        return DesireState(values=self._values, short_term=[], long_term=[])


class _FakeMemory:
    def __init__(self, results: list[Memory]) -> None:
        self._results = results
        self.search_calls = 0
        self.remembered: list[tuple[str, str, str]] = []

    async def search(self, query: str) -> list[Memory]:
        self.search_calls += 1
        return list(self._results)

    async def remember_reading(
        self, content: str, summary: str, correlation_id: str
    ) -> None:
        self.remembered.append((content, summary, correlation_id))


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.published.append(event)


class _FakeLlm:
    def __init__(
        self, contents: dict[str, str] | None = None, default: str = "好问题。"
    ) -> None:
        self._contents = contents or {}
        self._default = default
        self.calls: list[tuple[str, str]] = []
        self.messages: list[list[LlmMessage]] = []
        self.json_modes: list[tuple[str, bool]] = []

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
        tools: list[dict[str, object]] | None = None,
    ) -> LLMOutput:
        self.calls.append((output_type, correlation_id))
        self.messages.append(messages)
        self.json_modes.append((output_type, json_mode))
        return LLMOutput(
            module=module,
            type=output_type,
            model="fake",
            content=self._contents.get(output_type, self._default),
            correlation_id=correlation_id,
        )


class _FakeEvaluator:
    def __init__(self) -> None:
        self.evaluated: list[LLMOutput] = []

    async def evaluate(self, output: LLMOutput) -> None:
        self.evaluated.append(output)


class _NoneContentLlm(_FakeLlm):
    """LLM 返回 content=None（契约违约），模拟客户端异常输出。"""

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
        tools: list[dict[str, object]] | None = None,
    ) -> LLMOutput:
        return LLMOutput(
            module=module,
            type=output_type,
            model="fake",
            content=cast(str, None),
            correlation_id=correlation_id,
        )


class _NoneSearchMemory(_FakeMemory):
    """memory.search 返回 None（契约违约），模拟检索异常输出。"""

    async def search(self, query: str) -> list[Memory]:
        return cast(list[Memory], None)


def _build_impulse_facade(
    database: db.Database,
    bus: _FakeBus,
    llm: _FakeLlm,
    memory: _FakeMemory,
    inner_life: _FakeInnerLife,
    desire: _FakeDesire,
) -> ReadingFacade:
    return ReadingFacade(
        ReadingStore(database),
        cast(InnerLifeFacade, inner_life),
        cast(DesireFacade, desire),
        cast(MemoryFacade, memory),
        cast(LlmClient, llm),
        cast(Evaluator, _FakeEvaluator()),
        cast(EventBus, bus),
        "canon",
    )


async def _facade(
    monkeypatch: pytest.MonkeyPatch,
    segments: list[Segment],
    *,
    title: str = "测试书",
    author: str = "测试作者",
    content_hash: str = "c" * 64,
) -> tuple[ReadingFacade, db.Database]:
    database = await db.connect(":memory:")

    def fake_parse(data: bytes) -> EpubResult:
        return EpubResult(
            title=title, author=author, segments=segments, content_hash=content_hash
        )

    monkeypatch.setattr(facade_mod, "parse_epub", fake_parse)
    facade = _build_impulse_facade(
        database, _FakeBus(), _FakeLlm(), _FakeMemory([]),
        _FakeInnerLife(_mk_state()), _FakeDesire(_desire_values()),
    )
    return facade, database


async def _impulse_facade(
    monkeypatch: pytest.MonkeyPatch,
    segments: list[Segment],
    *,
    state: CurrentState | None = None,
    desire_values: list[DesireValue] | None = None,
    search_results: list[Memory] | None = None,
    llm: _FakeLlm | None = None,
) -> tuple[ReadingFacade, db.Database, _FakeBus, _FakeLlm, _FakeMemory]:
    database = await db.connect(":memory:")

    def fake_parse(data: bytes) -> EpubResult:
        return EpubResult(
            title="测试书", author="测试作者", segments=segments,
            content_hash="c" * 64,
        )

    monkeypatch.setattr(facade_mod, "parse_epub", fake_parse)
    bus = _FakeBus()
    fake_llm = llm if llm is not None else _FakeLlm()
    memory = _FakeMemory(search_results or [])
    facade = _build_impulse_facade(
        database, bus, fake_llm, memory,
        _FakeInnerLife(state or _mk_state()),
        _FakeDesire(desire_values or _desire_values()),
    )
    return facade, database, bus, fake_llm, memory


async def test_import_book_inserts_book_and_paragraphs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(
        monkeypatch,
        [
            Segment(text="第一章\n开头", is_chapter_start=True),
            Segment(text="正文", is_chapter_start=False),
            Segment(text="结尾", is_chapter_start=False),
        ],
    )
    try:
        book = await facade.import_book("nwsdl.epub", b"fake")
        cursor = await database.conn.execute(
            'SELECT "index" FROM paragraphs WHERE book_id = ? ORDER BY "index"',
            (book.id,),
        )
        indexes = [r["index"] for r in await cursor.fetchall()]
    finally:
        await database.conn.close()
    assert book.total_paragraphs == 3
    assert book.title == "测试书"
    assert indexes == [1, 2, 3]


async def test_import_book_duplicate_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(
        monkeypatch, [Segment(text="正文", is_chapter_start=False)],
        content_hash="d" * 64,
    )
    try:
        first = await facade.import_book("a.epub", b"x")
        with pytest.raises(DuplicateBookError) as exc:
            await facade.import_book("b.epub", b"x")
    finally:
        await database.conn.close()
    assert exc.value.existing_book_id == first.id
    assert exc.value.title == "测试书"


async def test_import_book_empty_segments_raises_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(monkeypatch, [])
    try:
        with pytest.raises(ValueError):
            await facade.import_book("a.epub", b"x")
        cursor = await database.conn.execute("SELECT COUNT(*) AS n FROM books")
        row = await cursor.fetchone()
    finally:
        await database.conn.close()
    assert row is not None and row["n"] == 0


async def test_import_book_title_falls_back_to_filename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(
        monkeypatch, [Segment(text="正文", is_chapter_start=False)], title="",
    )
    try:
        book = await facade.import_book("我的书.epub", b"x")
    finally:
        await database.conn.close()
    assert book.title == "我的书.epub"


async def test_delete_book_cascades_paragraphs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(
        monkeypatch,
        [
            Segment(text="正文", is_chapter_start=False),
            Segment(text="续", is_chapter_start=False),
        ],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        await database.conn.execute("DELETE FROM books WHERE id = ?", (book.id,))
        await database.conn.commit()
        cursor = await database.conn.execute(
            "SELECT COUNT(*) AS n FROM paragraphs WHERE book_id = ?", (book.id,),
        )
        row = await cursor.fetchone()
    finally:
        await database.conn.close()
    assert row is not None and row["n"] == 0


# ---- 20-reading-progress：进度 / 书架 / 分页 ----

async def test_list_books_lists_imported_book(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(
        monkeypatch, [Segment(text="正文", is_chapter_start=False)],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        items = await facade.list_books()
    finally:
        await database.conn.close()
    assert len(items) == 1
    assert items[0].id == book.id
    assert items[0].user_position == 0  # 未读哨兵
    assert items[0].last_read_at is None


async def test_get_progress_default_when_no_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(
        monkeypatch, [Segment(text="正文", is_chapter_start=False)],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        progress = await facade.get_progress(book.id)
    finally:
        await database.conn.close()
    assert progress.book_id == book.id
    assert progress.user_position == 1
    assert progress.nyx_position == 1
    assert progress.reading_speed == 50
    assert progress.read_count == 0
    assert progress.updated_at == 0.0  # 从未保存哨兵


async def test_save_progress_insert_then_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(
        monkeypatch, [Segment(text="正文", is_chapter_start=False)],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        first = await facade.save_progress(book.id, 2, 2, 50)
        second = await facade.save_progress(book.id, 5, 4, 80)
        cursor = await database.conn.execute(
            "SELECT COUNT(*) AS n FROM reading_progress WHERE book_id = ?", (book.id,),
        )
        row = await cursor.fetchone()
    finally:
        await database.conn.close()
    assert first.user_position == 2
    assert second.user_position == 5
    assert second.reading_speed == 80
    assert second.read_count == 0  # save 不写 read_count
    assert row is not None and row["n"] == 1


async def test_list_paragraphs_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(
        monkeypatch,
        [Segment(text=f"第{i}段", is_chapter_start=(i == 1)) for i in range(1, 6)],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        paras = await facade.list_paragraphs(book.id, 2, 4)
    finally:
        await database.conn.close()
    assert [p.index for p in paras] == [2, 3, 4]
    assert paras[0].is_chapter_start is False


async def test_list_paragraphs_to_idx_exceeds_total_raises_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(
        monkeypatch, [Segment(text="正文", is_chapter_start=False)],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        with pytest.raises(ValueError):
            await facade.list_paragraphs(book.id, 1, 99)
    finally:
        await database.conn.close()


async def test_book_not_found_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(
        monkeypatch, [Segment(text="正文", is_chapter_start=False)],
    )
    try:
        with pytest.raises(BookNotFoundError):
            await facade.get_progress("missing")
        with pytest.raises(BookNotFoundError):
            await facade.save_progress("missing", 1, 1, 50)
        with pytest.raises(BookNotFoundError):
            await facade.list_paragraphs("missing", 1, 2)
    finally:
        await database.conn.close()


# ---- 21-reading-impulse：段落冲动引擎 ----

async def test_evaluate_paragraph_forward_dispatches_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database, bus, _, _ = await _impulse_facade(
        monkeypatch,
        [Segment(text=_RICH_TEXT, is_chapter_start=False)],
        search_results=[_memory()],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        triggered = await facade.evaluate_paragraph(book.id, 1, 0)
        await asyncio.sleep(0)  # 后台分派跑完
    finally:
        await database.conn.close()
    assert ReadingBehavior.ASSOCIATE in triggered
    types = [e.type for e in bus.published]
    assert EventType.READING_MUTTER in types
    assert EventType.READING_QUESTION in types
    assert EventType.READING_ASSOCIATION in types


async def test_evaluate_paragraph_backtrack_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database, bus, _, _ = await _impulse_facade(
        monkeypatch, [Segment(text=_RICH_TEXT, is_chapter_start=False)],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        triggered = await facade.evaluate_paragraph(book.id, 1, 1)
        await asyncio.sleep(0)
    finally:
        await database.conn.close()
    assert triggered == []
    assert bus.published == []


async def test_evaluate_paragraph_missing_book_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database, bus, _, _ = await _impulse_facade(
        monkeypatch, [Segment(text=_RICH_TEXT, is_chapter_start=False)],
    )
    try:
        triggered = await facade.evaluate_paragraph("missing", 1, 0)
        await asyncio.sleep(0)
    finally:
        await database.conn.close()
    assert triggered == []
    assert bus.published == []


async def test_evaluate_paragraph_cooldown_suppresses_repeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database, _, _, _ = await _impulse_facade(
        monkeypatch, [Segment(text=_RICH_TEXT, is_chapter_start=False)],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        first = await facade.evaluate_paragraph(book.id, 1, 0)
        second = await facade.evaluate_paragraph(book.id, 2, 1)
    finally:
        await database.conn.close()
    assert ReadingBehavior.ASSOCIATE in first
    assert second == []  # 同一批行为全部在冷却中


async def test_evaluate_paragraph_flat_paragraph_no_mutter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database, bus, _, _ = await _impulse_facade(
        monkeypatch, [Segment(text=_FLAT_TEXT, is_chapter_start=False)],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        await facade.evaluate_paragraph(book.id, 1, 0)
        await asyncio.sleep(0)
    finally:
        await database.conn.close()
    assert not any(e.type is EventType.READING_MUTTER for e in bus.published)


async def test_evaluate_paragraph_associate_searches_and_broadcasts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database, bus, _, memory = await _impulse_facade(
        monkeypatch,
        [Segment(text=_RICH_TEXT, is_chapter_start=False)],
        search_results=[_memory("m1", "第一条记忆"), _memory("m2", "第二条记忆")],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        await facade.evaluate_paragraph(book.id, 1, 0)
        await asyncio.sleep(0)
    finally:
        await database.conn.close()
    assert memory.search_calls == 1
    assoc = [e for e in bus.published if e.type is EventType.READING_ASSOCIATION]
    assert [a.content["memory_id"] for a in assoc] == ["m1", "m2"]
    assert assoc[0].content["snippet"] == "第一条记忆"


async def test_evaluate_paragraph_quote_question_splits_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _FakeLlm({"quote_question": "这段为什么重要？\n因为生命的意义。"})
    facade, database, bus, _, _ = await _impulse_facade(
        monkeypatch,
        [Segment(text=_RICH_TEXT, is_chapter_start=False)],
        llm=llm,
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        await facade.evaluate_paragraph(book.id, 1, 0)
        await asyncio.sleep(0)
    finally:
        await database.conn.close()
    quote = [
        e for e in bus.published
        if e.type is EventType.READING_QUESTION
        and e.content["subtype"] == "quote_question"
    ]
    assert len(quote) == 1
    assert quote[0].content["content"] == "这段为什么重要？"
    assert quote[0].content["selected_text"] == "因为生命的意义。"


async def test_evaluate_paragraph_quote_question_single_line_null_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _FakeLlm({"quote_question": "这段为什么重要？"})
    facade, database, bus, _, _ = await _impulse_facade(
        monkeypatch,
        [Segment(text=_RICH_TEXT, is_chapter_start=False)],
        llm=llm,
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        await facade.evaluate_paragraph(book.id, 1, 0)
        await asyncio.sleep(0)
    finally:
        await database.conn.close()
    quote = [
        e for e in bus.published
        if e.type is EventType.READING_QUESTION
        and e.content["subtype"] == "quote_question"
    ]
    assert len(quote) == 1
    assert quote[0].content["selected_text"] is None


async def test_mutter_reading_none_content_skips_without_raise() -> None:
    """LLM 返回 None 内容（契约违约）→ 后处理不炸、不广播（try 兜住 .strip()）。"""
    database = await db.connect(":memory:")
    bus = _FakeBus()
    facade = _build_impulse_facade(
        database, bus, _NoneContentLlm(), _FakeMemory([]),
        _FakeInnerLife(_mk_state()), _FakeDesire(_desire_values()),
    )
    try:
        await facade._mutter_reading("b1", 1, _RICH_TEXT, _mk_state())
    finally:
        await database.conn.close()
    assert bus.published == []


async def test_associate_reading_none_search_skips_without_raise() -> None:
    """memory.search 返回 None（契约违约）→ 切片不炸、不广播（try 兜住 [:3]）。"""
    database = await db.connect(":memory:")
    bus = _FakeBus()
    facade = _build_impulse_facade(
        database, bus, _FakeLlm(), _NoneSearchMemory([]),
        _FakeInnerLife(_mk_state()), _FakeDesire(_desire_values()),
    )
    try:
        await facade._associate_reading("b1", 1, _RICH_TEXT)
    finally:
        await database.conn.close()
    assert bus.published == []


# ---- 22-reading-notes：用户笔记 / Nyx 批注 / 章末整合 ----

async def _note_facade(
    monkeypatch: pytest.MonkeyPatch,
    segments: list[Segment],
    *,
    llm: _FakeLlm | None = None,
    inner_life: _FakeInnerLife | None = None,
    memory: _FakeMemory | None = None,
    evaluator: _FakeEvaluator | None = None,
) -> tuple[
    ReadingFacade, db.Database, _FakeBus, _FakeLlm, _FakeMemory,
    _FakeInnerLife, _FakeEvaluator,
]:
    """22 笔记测试栈：真 ReadingStore + 暴露全部 fake（断言批注/整合/反思）。"""
    database = await db.connect(":memory:")

    def fake_parse(data: bytes) -> EpubResult:
        return EpubResult(
            title="测试书", author="测试作者", segments=segments,
            content_hash="c" * 64,
        )

    monkeypatch.setattr(facade_mod, "parse_epub", fake_parse)
    bus = _FakeBus()
    fake_llm = llm if llm is not None else _FakeLlm()
    fake_memory = memory if memory is not None else _FakeMemory([])
    fake_inner = inner_life if inner_life is not None else _FakeInnerLife(_mk_state())
    fake_eval = evaluator if evaluator is not None else _FakeEvaluator()
    facade = ReadingFacade(
        ReadingStore(database),
        cast(InnerLifeFacade, fake_inner),
        cast(DesireFacade, _FakeDesire(_desire_values())),
        cast(MemoryFacade, fake_memory),
        cast(LlmClient, fake_llm),
        cast(Evaluator, fake_eval),
        cast(EventBus, bus),
        "canon",
    )
    return facade, database, bus, fake_llm, fake_memory, fake_inner, fake_eval


async def _check_and_drain(
    facade: ReadingFacade, book_id: str, nyx_position: int
) -> BoundaryResult:
    """check_chapter_boundary 后确定性等后台整合任务跑完（非 sleep 计时）。

    后台 `_integrate_buffer` 走真 aiosqlite I/O（`get_progress` 等），单次
    `asyncio.sleep(0)` 不够；捕获新创建的 task 直接 await 它。
    """
    before = asyncio.all_tasks()
    result = await facade.check_chapter_boundary(book_id, nyx_position)
    created = asyncio.all_tasks() - before
    if created:
        await asyncio.gather(*created)
    return result


def test_parse_reading_note_valid() -> None:
    content, summary = facade_mod._parse_reading_note(
        '{"content": "记住了这句话", "summary": "读某章"}'
    )
    assert (content, summary) == ("记住了这句话", "读某章")


def test_parse_reading_note_non_json_raises() -> None:
    with pytest.raises(ValueError):
        facade_mod._parse_reading_note("不是 JSON")


def test_parse_reading_note_missing_key_raises() -> None:
    with pytest.raises(ValueError):
        facade_mod._parse_reading_note('{"content": "只有正文"}')


def test_parse_reading_note_wrong_type_raises() -> None:
    with pytest.raises(ValueError):
        facade_mod._parse_reading_note('{"content": 123, "summary": "x"}')


async def test_add_and_list_user_notes_with_annotations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database, *_ = await _note_facade(
        monkeypatch, [Segment(text="正文", is_chapter_start=False)],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        pid = (await facade.list_paragraphs(book.id, 1, 1))[0].id
        note = await facade.add_user_note(book.id, pid, "笔记一", "划线")
        await facade._store.insert_annotation(note.id, "批注")
        notes = await facade.list_user_notes(book.id)
    finally:
        await database.conn.close()
    assert len(notes) == 1
    assert notes[0].id == note.id
    assert notes[0].paragraph_id == pid
    assert [a.content for a in notes[0].annotations] == ["批注"]


async def test_add_user_note_without_paragraph_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database, *_ = await _note_facade(
        monkeypatch, [Segment(text="正文", is_chapter_start=False)],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        note = await facade.add_user_note(book.id, None, "自由记", None)
    finally:
        await database.conn.close()
    assert note.paragraph_id is None
    assert note.selected_text is None


async def test_update_user_note_hit_and_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database, *_ = await _note_facade(
        monkeypatch, [Segment(text="正文", is_chapter_start=False)],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        note = await facade.add_user_note(book.id, None, "旧", None)
        updated = await facade.update_user_note(note.id, "新")
        with pytest.raises(NoteNotFoundError):
            await facade.update_user_note("nope", "x")
    finally:
        await database.conn.close()
    assert updated.id == note.id
    assert updated.content == "新"


async def test_delete_user_note_hit_and_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database, *_ = await _note_facade(
        monkeypatch, [Segment(text="正文", is_chapter_start=False)],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        note = await facade.add_user_note(book.id, None, "笔记", None)
        await facade.delete_user_note(note.id)
        with pytest.raises(NoteNotFoundError):
            await facade.delete_user_note(note.id)
    finally:
        await database.conn.close()


async def test_show_to_nyx_writes_annotation_with_paragraph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _FakeLlm({"reading_annotation": "这里写得好。"})
    facade, database, _, fake_llm, _, _, evaluator = await _note_facade(
        monkeypatch,
        [Segment(text="独特原文段落", is_chapter_start=False)],
        llm=llm,
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        pid = (await facade.list_paragraphs(book.id, 1, 1))[0].id
        note = await facade.add_user_note(book.id, pid, "笔记正文", "划线")
        annotation = await facade.show_to_nyx(note.id)
    finally:
        await database.conn.close()
    assert annotation.user_note_id == note.id
    assert annotation.content == "这里写得好。"
    assert ("reading_annotation", note.id) in fake_llm.calls
    assert len(evaluator.evaluated) == 1
    assert "独特原文段落" in fake_llm.messages[0][1]["content"]


async def test_show_to_nyx_book_deleted_reads_note_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _FakeLlm({"reading_annotation": "批注。"})
    facade, database, _, fake_llm, *_ = await _note_facade(
        monkeypatch, [Segment(text="独特原文段落", is_chapter_start=False)], llm=llm,
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        pid = (await facade.list_paragraphs(book.id, 1, 1))[0].id
        note = await facade.add_user_note(book.id, pid, "笔记正文", "划线")
        await database.conn.execute("DELETE FROM books WHERE id = ?", (book.id,))
        await database.conn.commit()
        annotation = await facade.show_to_nyx(note.id)
    finally:
        await database.conn.close()
    assert annotation.user_note_id == note.id
    # 书已删 → prompt 只含笔记文字，不含原段落
    assert "笔记正文" in fake_llm.messages[0][1]["content"]
    assert "独特原文段落" not in fake_llm.messages[0][1]["content"]


async def test_check_chapter_boundary_chapter_end_integrates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _FakeLlm({"reading_note": '{"content": "这章记住了", "summary": "读某章"}'})
    facade, database, _, fake_llm, memory, _, evaluator = await _note_facade(
        monkeypatch,
        [
            Segment(text="第一章", is_chapter_start=True),
            Segment(text="正文", is_chapter_start=False),
            Segment(text="第二章", is_chapter_start=True),
        ],
        llm=llm,
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        await facade.record_nyx_output(book.id, 1, "这句话不错", "mutter")
        result = await _check_and_drain(facade, book.id, 2)
    finally:
        await database.conn.close()
    assert result is BoundaryResult.CHAPTER_END
    assert memory.remembered == [("这章记住了", "读某章", book.id)]
    assert ("reading_note", book.id) in fake_llm.calls
    assert ("reading_note", True) in fake_llm.json_modes
    assert len(evaluator.evaluated) == 1


async def test_check_chapter_boundary_none_when_next_not_chapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database, _, _, memory, _, _ = await _note_facade(
        monkeypatch,
        [
            Segment(text="第一章", is_chapter_start=True),
            Segment(text="正文", is_chapter_start=False),
            Segment(text="正文续", is_chapter_start=False),
        ],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        await facade.record_nyx_output(book.id, 1, "这句话", "mutter")
        result = await _check_and_drain(facade, book.id, 1)
    finally:
        await database.conn.close()
    assert result is BoundaryResult.NONE
    assert memory.remembered == []  # 非边界不整合


async def test_check_chapter_boundary_book_finished_integrates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _FakeLlm({"reading_note": '{"content": "整本读完了", "summary": "全书"}'})
    facade, database, _, _, memory, _, _ = await _note_facade(
        monkeypatch,
        [Segment(text="第一章", is_chapter_start=True)],
        llm=llm,
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        await facade.record_nyx_output(book.id, 1, "最后一句话", "mutter")
        result = await _check_and_drain(facade, book.id, 1)  # total=1
        progress = await facade._store.get_progress(book.id)
    finally:
        await database.conn.close()
    assert result is BoundaryResult.BOOK_FINISHED
    assert memory.remembered == [("整本读完了", "全书", book.id)]
    assert progress is not None and progress.read_count == 1  # 0→1


async def test_check_chapter_boundary_reread_reflects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _FakeLlm({"reading_note": '{"content": "又读一遍", "summary": "重读"}'})
    inner = _FakeInnerLife(_mk_state())
    facade, database, _, _, memory, inner, _ = await _note_facade(
        monkeypatch,
        [
            Segment(text="第一章", is_chapter_start=True),
            Segment(text="正文", is_chapter_start=False),
            Segment(text="第二章", is_chapter_start=True),
        ],
        llm=llm,
        inner_life=inner,
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        await facade._store.increment_read_count(book.id)  # read_count=1（重读）
        await facade.record_nyx_output(book.id, 1, "这句话", "mutter")
        result = await _check_and_drain(facade, book.id, 2)
    finally:
        await database.conn.close()
    assert result is BoundaryResult.CHAPTER_END
    assert memory.remembered == [("又读一遍", "重读", book.id)]
    assert inner.reflect_calls == [book.id]


async def test_check_chapter_boundary_first_read_no_reflect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _FakeLlm({"reading_note": '{"content": "首读", "summary": "首读"}'})
    inner = _FakeInnerLife(_mk_state())
    facade, database, _, _, memory, inner, _ = await _note_facade(
        monkeypatch,
        [
            Segment(text="第一章", is_chapter_start=True),
            Segment(text="正文", is_chapter_start=False),
            Segment(text="第二章", is_chapter_start=True),
        ],
        llm=llm,
        inner_life=inner,
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        await facade.record_nyx_output(book.id, 1, "这句话", "mutter")
        result = await _check_and_drain(facade, book.id, 2)
    finally:
        await database.conn.close()
    assert result is BoundaryResult.CHAPTER_END
    assert memory.remembered == [("首读", "首读", book.id)]
    assert inner.reflect_calls == []  # 首读不反思


async def test_integrate_buffer_empty_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database, _, _, memory, _, _ = await _note_facade(
        monkeypatch,
        [
            Segment(text="第一章", is_chapter_start=True),
            Segment(text="正文", is_chapter_start=False),
            Segment(text="第二章", is_chapter_start=True),
        ],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        result = await _check_and_drain(facade, book.id, 2)  # buffer 空
    finally:
        await database.conn.close()
    assert result is BoundaryResult.CHAPTER_END
    assert memory.remembered == []  # buffer 空不生成记忆


async def test_mutter_and_question_record_nyx_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database, *_ = await _note_facade(
        monkeypatch, [Segment(text=_RICH_TEXT, is_chapter_start=False)],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        await facade.evaluate_paragraph(book.id, 1, 0)
        await asyncio.sleep(0)
    finally:
        await database.conn.close()
    entries = facade._nyx_buffer.get(book.id, [])
    assert {e.source for e in entries} == {"mutter", "question"}
