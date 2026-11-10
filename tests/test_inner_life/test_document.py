# pyright: reportPrivateUsage=false
"""Critical patch and legacy-save recovery paths for the inner document."""

import json
from dataclasses import asdict
from typing import Any

import aiosqlite
import pytest

from nyx import db
from nyx.inner_life.document import MAX_DOCUMENT_CHARS, revise_document
from nyx.inner_life.store import InnerLifeStore
from nyx.types import InnerDocument, InnerTopic


def _document() -> InnerDocument:
    return InnerDocument(
        [
            InnerTopic("worldview", "view-of-humans", "对人类的看法", "我想理解人类。"),
            InnerTopic("relationships", "trust", "信任", "我还在试着靠近。"),
        ],
        3,
        100.0,
    )


def _patch(**overrides: Any) -> dict[str, Any]:
    return {
        "op": "set",
        "section": "relationships",
        "id": "trust",
        "title": "慢慢信任",
        "content": "我愿意把不同意见也告诉他。",
        "reason": "他明确邀请我表达不同意见",
        "evidence": [{"id": "conversation:1", "quote": "我愿意听你的不同意见"}],
        **overrides,
    }


def test_revision_preserves_unrelated_topics_and_original_snapshot() -> None:
    original = _document()
    revised = revise_document(
        original, [_patch()], {"conversation:1": "我愿意听你的不同意见"}, 200.0
    )
    assert revised.topics[0] == original.topics[0]
    assert revised.topics[1].id == "trust"
    assert revised.topics[1].title == "慢慢信任"
    assert original.topics[1].content == "我还在试着靠近。"
    assert revised.revision == 4
    same = revise_document(revised, [], {}, 300.0)
    assert same.revision == 4 and same.updated_at == 300.0


@pytest.mark.parametrize(
    "patches",
    [
        [_patch(section="worldview")],
        [_patch(id="missing", op="remove")],
        [_patch(), _patch()],
        [_patch(content="")],
        [_patch(evidence=[{"id": "conversation:1", "quote": "他承诺永远爱我"}])],
        [_patch(evidence=[])],
        [_patch(section="new-section")],
        [_patch(content="长" * 6001)],
    ],
)
def test_invalid_patch_rejects_entire_revision(patches: list[dict[str, Any]]) -> None:
    original = _document()
    with pytest.raises(ValueError):
        revise_document(
            original, patches, {"conversation:1": "我愿意听你的不同意见"}, 200.0
        )
    assert original == _document()


async def _legacy_connection(identity: str = "我是旧角色") -> aiosqlite.Connection:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await db.migrate(conn, migrations=[m for m in db._MIGRATIONS if m[0] < 55])
    await conn.execute(
        "INSERT INTO self_narrative (id, identity, story, self_view, becoming, "
        "updated_at) VALUES ('self', ?, ?, ?, ?, 123)",
        (
            identity,
            json.dumps(["成长片段" + str(i) for i in range(100)]),
            json.dumps({"边界": "我愿意说不"}),
            json.dumps(["我学会表达不同意见"]),
        ),
    )
    await conn.execute("INSERT INTO personality VALUES ('self', 8, 8, 2, 6, 7)")
    await conn.execute("INSERT INTO value_system VALUES ('self', 9, 8, 7, 6)")
    await conn.execute("INSERT INTO aesthetic VALUES ('self', 8, 8, 7, 9)")
    await conn.commit()
    return conn


async def test_legacy_migration_preserves_growth_and_retries_idempotently() -> None:
    conn = await _legacy_connection()
    try:
        await db.migrate(conn)
        row = await (await conn.execute("SELECT * FROM inner_document")).fetchone()
        assert row is not None
        text = row["topics"]
        assert "成长片段99" in text and "我愿意说不" in text
        assert "我学会表达不同意见" in text
        topics = json.loads(text)
        assert {topic["section"] for topic in topics} == {"identity", "narrative"}
        assert not any(topic["id"].startswith((
            "legacy-personality", "legacy-worldview", "legacy-aesthetic"
        )) for topic in topics)
        assert row["revision"] == 1 and row["updated_at"] == 123
        tables = {
            r[0]
            for r in await (
                await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }
        assert (
            not {"personality", "value_system", "aesthetic", "self_narrative"} & tables
        )
        columns = {
            r[1]
            for r in await (
                await conn.execute("PRAGMA table_info(long_term_desire)")
            ).fetchall()
        }
        assert "linked_values" not in columns
        await db.migrate(conn)
        again = await (
            await conn.execute("SELECT topics FROM inner_document")
        ).fetchone()
        assert again is not None and again["topics"] == text
    finally:
        await conn.close()


async def test_failed_migration_retains_legacy_save_for_recovery() -> None:
    conn = await _legacy_connection("长" * (MAX_DOCUMENT_CHARS + 1))
    try:
        with pytest.raises(ValueError):
            await db.migrate(conn)
        version = await (
            await conn.execute("SELECT version FROM schema_version")
        ).fetchone()
        assert version is not None and version[0] == 54
        assert await (await conn.execute("SELECT * FROM personality")).fetchone()
        # Correcting the invalid legacy value makes a retry possible.
        await conn.execute("UPDATE self_narrative SET identity='旧身份'")
        await conn.commit()
        await db.migrate(conn)
        assert await (await conn.execute("SELECT * FROM inner_document")).fetchone()
    finally:
        await conn.close()


async def test_document_survives_restart_without_reseeding(tmp_path: Any) -> None:
    path = str(tmp_path / "character.db")
    database = await db.connect(path)
    original = _document()
    try:
        await InnerLifeStore(database).upsert_document(original)
    finally:
        await database.close()
    database = await db.connect(path)
    try:
        restored = await InnerLifeStore(database).get_document()
        assert restored is not None and asdict(restored) == asdict(original)
    finally:
        await database.close()
