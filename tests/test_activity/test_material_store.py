from nyx import db
from nyx.activity.material_store import MaterialStore


async def _new_store() -> tuple[MaterialStore, db.Database]:
    database = await db.connect(":memory:")
    return MaterialStore(database), database


async def test_next_readable_picks_latest_unread() -> None:
    store, database = await _new_store()
    try:
        await store.upsert("/a.txt", "a.txt", 100, 1000.0)
        await store.upsert("/b.txt", "b.txt", 100, 2000.0)
        mat = await store.next_readable()
        assert mat is not None and mat.path == "/b.txt"
    finally:
        await database.conn.close()


async def test_next_readable_skips_completed() -> None:
    store, database = await _new_store()
    try:
        await store.upsert("/a.txt", "a.txt", 100, 1000.0)
        await store.upsert("/b.txt", "b.txt", 100, 2000.0)
        await store.advance("/b.txt", 100, 3000.0)  # b 读完
        mat = await store.next_readable()
        assert mat is not None and mat.path == "/a.txt"
    finally:
        await database.conn.close()


async def test_next_readable_none_when_all_read() -> None:
    store, database = await _new_store()
    try:
        await store.upsert("/a.txt", "a.txt", 100, 1000.0)
        await store.advance("/a.txt", 100, 2000.0)
        assert await store.next_readable() is None
    finally:
        await database.conn.close()


async def test_upsert_resets_progress_on_reupload() -> None:
    store, database = await _new_store()
    try:
        await store.upsert("/a.txt", "a.txt", 100, 1000.0)
        await store.advance("/a.txt", 50, 2000.0)
        await store.upsert("/a.txt", "a.txt", 200, 3000.0)  # 重传：进度归零、总字数更新
        mat = await store.next_readable()
        assert mat is not None and mat.read_chars == 0 and mat.total_chars == 200
    finally:
        await database.conn.close()


async def test_find_by_topic_matches_unread() -> None:
    store, database = await _new_store()
    try:
        await store.upsert("/a.txt", "a.txt", 100, 1000.0)
        await store.upsert("/骑士团史.md", "骑士团史.md", 100, 2000.0)
        mat = await store.find_by_topic("骑士团")
        assert mat is not None and mat.path == "/骑士团史.md"
    finally:
        await database.conn.close()


async def test_find_by_topic_skips_completed() -> None:
    store, database = await _new_store()
    try:
        await store.upsert("/骑士团史.md", "骑士团史.md", 100, 1000.0)
        await store.advance("/骑士团史.md", 100, 2000.0)  # 读完
        assert await store.find_by_topic("骑士团") is None
    finally:
        await database.conn.close()


async def test_find_by_topic_no_match_returns_none() -> None:
    store, database = await _new_store()
    try:
        await store.upsert("/a.txt", "a.txt", 100, 1000.0)
        assert await store.find_by_topic("骑士团") is None
    finally:
        await database.conn.close()


async def test_get_by_path_returns_latest_progress() -> None:
    """get_by_path 按路径取书，含 advance 后的最新 read_chars。"""
    store, database = await _new_store()
    try:
        await store.upsert("/a.txt", "a.txt", 100, 1000.0)
        await store.advance("/a.txt", 60, 2000.0)
        mat = await store.get_by_path("/a.txt")
        assert mat is not None and mat.read_chars == 60
    finally:
        await database.conn.close()


async def test_get_by_path_missing_returns_none() -> None:
    store, database = await _new_store()
    try:
        assert await store.get_by_path("/nope.txt") is None
    finally:
        await database.conn.close()


async def test_list_all_returns_ordered_by_created_desc() -> None:
    """list_all：全量读物按 created_at 倒序（最近上传在前），进度随 advance。"""
    store, database = await _new_store()
    try:
        await store.upsert("/a.txt", "a.txt", 100, 1000.0)
        await store.upsert("/b.txt", "b.txt", 200, 2000.0)
        await store.advance("/b.txt", 50, 3000.0)
        mats = await store.list_all()
        assert [m.path for m in mats] == ["/b.txt", "/a.txt"]
        assert mats[0].read_chars == 50
    finally:
        await database.conn.close()


async def test_list_all_empty() -> None:
    store, database = await _new_store()
    try:
        assert await store.list_all() == []
    finally:
        await database.conn.close()
