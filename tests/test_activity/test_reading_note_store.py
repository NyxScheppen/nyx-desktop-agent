from nyx import db
from nyx.activity.reading_note_store import ReadingNoteStore
from nyx.types import Annotation, ReadingNote


def _note(
    id: str,
    book: str = "骑士团史.md",
    created_at: float = 1000.0,
    path: str = "",
) -> ReadingNote:
    return ReadingNote(
        id=id, book=book, content=f"{id} 的完整笔记", created_at=created_at,
        path=path,
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


async def test_list_notes_counts_annotations() -> None:
    store, database = await _new_store()
    try:
        await store.upsert_by_path(_note("n1"))
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
        await store.upsert_by_path(_note("n1"))
        await store.add_annotation(_annotation("a1", "n1"))
        await store.delete("n1")
        assert await store.list_notes() == []
        assert await store.list_annotations("n1") == []
    finally:
        await database.conn.close()


async def test_upsert_by_path_insert_then_update() -> None:
    store, database = await _new_store()
    try:
        await store.upsert_by_path(_note("n1", path="/books/a.md"))
        await store.upsert_by_path(
            _note("n2", book="a.md", created_at=2000.0, path="/books/a.md")
        )
        notes = await store.list_notes()
        assert len(notes) == 1                        # 同 path 不重复
        assert notes[0].id == "n1"                    # 原地更新，id 不变
        assert notes[0].content == "n2 的完整笔记"
        assert notes[0].path == "/books/a.md"
    finally:
        await database.conn.close()


async def test_upsert_by_path_distinct_paths_same_filename() -> None:
    store, database = await _new_store()
    try:
        await store.upsert_by_path(
            _note("n1", book="a.md", created_at=1000.0, path="/x/a.md")
        )
        await store.upsert_by_path(
            _note("n2", book="a.md", created_at=2000.0, path="/y/a.md")
        )
        notes = await store.list_notes()
        assert [n.id for n in notes] == ["n2", "n1"]   # 不同 path 同名书互不删
    finally:
        await database.conn.close()


async def test_list_annotations_ordered_asc() -> None:
    store, database = await _new_store()
    try:
        await store.upsert_by_path(_note("n1"))
        await store.add_annotation(_annotation("a1", "n1", created_at=1000.0))
        await store.add_annotation(_annotation("a2", "n1", created_at=2000.0))
        anns = await store.list_annotations("n1")
        assert [a.id for a in anns] == ["a1", "a2"]   # 升序
    finally:
        await database.conn.close()


async def test_delete_annotation() -> None:
    store, database = await _new_store()
    try:
        await store.upsert_by_path(_note("n1"))
        await store.add_annotation(_annotation("a1", "n1"))
        await store.add_annotation(_annotation("a2", "n1"))
        await store.delete_annotation("a1")
        anns = await store.list_annotations("n1")
        assert [a.id for a in anns] == ["a2"]
    finally:
        await database.conn.close()
