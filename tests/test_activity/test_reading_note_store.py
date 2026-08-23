from nyx import db
from nyx.activity.reading_note_store import ReadingNoteStore
from nyx.types import Annotation, ReadingNote


def _note(
    id: str, book: str = "骑士团史.md", created_at: float = 1000.0
) -> ReadingNote:
    return ReadingNote(
        id=id, book=book, content=f"{id} 的完整笔记", created_at=created_at
    )


def _annotation(
    id: str,
    target_id: str,
    content: str = "批注",
    created_at: float = 1000.0,
) -> Annotation:
    return Annotation(
        id=id, target_id=target_id, author="user", content=content,
        created_at=created_at,
    )


async def _new_store() -> tuple[ReadingNoteStore, db.Database]:
    database = await db.connect(":memory:")
    return ReadingNoteStore(database), database


async def test_insert_and_list_notes_ordered_desc() -> None:
    store, database = await _new_store()
    try:
        await store.insert(_note("n1", created_at=1000.0))
        await store.insert(_note("n2", created_at=2000.0))
        notes = await store.list_notes()
        assert [n.id for n in notes] == ["n2", "n1"]   # created_at 倒序
        assert notes[0].annotation_count == 0
    finally:
        await database.conn.close()


async def test_list_notes_counts_annotations() -> None:
    store, database = await _new_store()
    try:
        await store.insert(_note("n1"))
        await store.add_annotation(_annotation("a1", "n1"))
        await store.add_annotation(_annotation("a2", "n1"))
        notes = await store.list_notes()
        assert len(notes) == 1
        assert notes[0].annotation_count == 2
    finally:
        await database.conn.close()


async def test_delete_cascades_annotations() -> None:
    store, database = await _new_store()
    try:
        await store.insert(_note("n1"))
        await store.add_annotation(_annotation("a1", "n1"))
        await store.delete("n1")
        assert await store.list_notes() == []
        assert await store.list_annotations("n1") == []
    finally:
        await database.conn.close()


async def test_list_annotations_ordered_asc() -> None:
    store, database = await _new_store()
    try:
        await store.insert(_note("n1"))
        await store.add_annotation(_annotation("a1", "n1", created_at=1000.0))
        await store.add_annotation(_annotation("a2", "n1", created_at=2000.0))
        anns = await store.list_annotations("n1")
        assert [a.id for a in anns] == ["a1", "a2"]   # 升序
    finally:
        await database.conn.close()


async def test_delete_annotation() -> None:
    store, database = await _new_store()
    try:
        await store.insert(_note("n1"))
        await store.add_annotation(_annotation("a1", "n1"))
        await store.add_annotation(_annotation("a2", "n1"))
        await store.delete_annotation("a1")
        anns = await store.list_annotations("n1")
        assert [a.id for a in anns] == ["a2"]
    finally:
        await database.conn.close()
