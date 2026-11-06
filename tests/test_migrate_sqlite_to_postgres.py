import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.migrate_sqlite_to_postgres import (
    _conversation_archive,  # pyright: ignore[reportPrivateUsage]
    _copy_character_files,  # pyright: ignore[reportPrivateUsage]
    _rewrite_character_record,  # pyright: ignore[reportPrivateUsage]
    foreign_key_table_order,
    inspect_source,
)


def _database(path: Path, version: int, rows: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    connection.execute("INSERT INTO schema_version VALUES (?)", (version,))
    connection.execute("CREATE TABLE sample (id TEXT PRIMARY KEY)")
    connection.executemany(
        "INSERT INTO sample VALUES (?)", [(str(i),) for i in range(rows)]
    )
    connection.commit()
    connection.close()


@pytest.mark.parametrize("version", [51, 52, 53])
def test_inspect_source_finds_host_and_registered_character_databases_read_only(
    tmp_path: Path, version: int,
) -> None:
    data = tmp_path / "data"
    host_path = data / "characters" / "host.db"
    character_path = data / "characters" / "nyx" / "nyx.db"
    _database(character_path, version, rows=2)
    _database(host_path, 2)
    host = sqlite3.connect(host_path)
    host.execute("CREATE TABLE characters (id TEXT PRIMARY KEY, record TEXT NOT NULL)")
    host.execute(
        "INSERT INTO characters VALUES (?, ?)",
        (
            "nyx",
            json.dumps(
                {
                    "id": "nyx",
                    "db_path": str(character_path),
                    "card_path": str(data / "characters" / "nyx" / "card"),
                    "workspace": str(data / "characters" / "nyx" / "workspace"),
                }
            ),
        ),
    )
    host.commit()
    host.close()
    before = {path: path.read_bytes() for path in (host_path, character_path)}

    manifest = inspect_source(tmp_path)

    assert manifest["host"]["version"] == 2
    assert manifest["characters"][0]["id"] == "nyx"
    assert manifest["characters"][0]["database"]["version"] == version
    assert manifest["characters"][0]["database"]["tables"]["sample"] == 2
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("backfill", [True, False])
def test_import_archive_validates_old_and_new_source_mapping(backfill: bool) -> None:
    with (
        sqlite3.connect(":memory:") as source,
        sqlite3.connect(":memory:") as target_db,
    ):
        source.execute(
            "CREATE TABLE conversation_segment "
            "(id TEXT PRIMARY KEY, raw_event_ids TEXT, active INTEGER)"
        )
        source.executemany(
            "INSERT INTO conversation_segment VALUES (?, ?, ?)",
            [("z", '["u:user","u:nyx"]', 1), ("a", '["u:user"]', 0)],
        )
        target_db.execute("ATTACH DATABASE ':memory:' AS role")
        target_db.execute(
            "CREATE TABLE role.conversation_turn_archive "
            "(turn_id TEXT PRIMARY KEY, segment_id TEXT)"
        )
        expected = [("u:user", "a"), ("u:nyx", "z")]
        if not backfill:
            target_db.executemany(
                "INSERT INTO role.conversation_turn_archive VALUES (?, ?)", expected
            )
        target = Mock()
        target.execute.side_effect = target_db.execute

        def insert(sql: str, rows: list[tuple[str, str]]) -> sqlite3.Cursor:
            return target_db.executemany(sql.replace("%s", "?"), rows)

        target.cursor.return_value.executemany.side_effect = insert
        _conversation_archive(source, target, "role", backfill=backfill)
        assert dict(target_db.execute(
            "SELECT turn_id, segment_id FROM role.conversation_turn_archive"
        ).fetchall()) == dict(expected)
        target_db.execute(
            "UPDATE role.conversation_turn_archive SET segment_id='wrong'"
        )
        with pytest.raises(ValueError, match="archive mismatch"):
            _conversation_archive(source, target, "role", backfill=False)


@pytest.mark.parametrize("raw", ["{}", "[1]", '[""]', "invalid"])
def test_import_archive_rejects_invalid_turn_ids(raw: str) -> None:
    with sqlite3.connect(":memory:") as source:
        source.execute(
            "CREATE TABLE conversation_segment (id TEXT, raw_event_ids TEXT)"
        )
        source.execute("INSERT INTO conversation_segment VALUES ('s', ?)", (raw,))
        target = Mock()
        with pytest.raises(ValueError):
            _conversation_archive(source, target, "role", backfill=True)
        target.cursor.assert_not_called()


def test_inspect_source_rejects_missing_registered_character_database(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    host_path = data / "characters" / "host.db"
    _database(host_path, 2)
    host = sqlite3.connect(host_path)
    host.execute("CREATE TABLE characters (id TEXT PRIMARY KEY, record TEXT NOT NULL)")
    host.execute(
        "INSERT INTO characters VALUES (?, ?)",
        ("alice", json.dumps({"id": "alice", "db_path": str(data / "missing.db")})),
    )
    host.commit()
    host.close()

    with pytest.raises(ValueError, match="alice"):
        inspect_source(data)


def test_migration_keeps_builtin_nyx_card_in_the_image(tmp_path: Path) -> None:
    builtin_card = Path(__file__).parents[1] / "nyx" / "cards" / "nyx"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target_root = tmp_path / "target"
    record = {
        "id": "nyx",
        "card_path": str(builtin_card),
        "workspace": str(workspace),
    }

    rewritten = _rewrite_character_record(record, target_root)
    copied: list[Path] = []
    _copy_character_files(record, target_root, copied)

    assert rewritten["card_path"] == str(builtin_card)
    assert not (target_root / "characters" / "nyx" / "card").exists()
    assert (target_root / "characters" / "nyx" / "workspace").is_dir()


def test_foreign_key_table_order_imports_parent_before_child(tmp_path: Path) -> None:
    path = tmp_path / "dependencies.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE parent (id TEXT PRIMARY KEY);"
        "CREATE TABLE child (id TEXT PRIMARY KEY, parent_id TEXT "
        "REFERENCES parent(id));"
        "CREATE TABLE child_note (id TEXT PRIMARY KEY, child_id TEXT "
        "REFERENCES child(id));"
    )

    order = foreign_key_table_order(connection)

    assert order.index("parent") < order.index("child")
    assert order.index("child") < order.index("child_note")
    connection.close()


def test_foreign_key_table_order_rejects_a_dependency_cycle(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "cycle.db")
    connection.executescript(
        "CREATE TABLE first (id TEXT PRIMARY KEY, second_id TEXT "
        "REFERENCES second(id));"
        "CREATE TABLE second (id TEXT PRIMARY KEY, first_id TEXT "
        "REFERENCES first(id));"
    )

    with pytest.raises(ValueError, match="foreign key cycle"):
        foreign_key_table_order(connection)
    connection.close()


def test_foreign_key_table_order_allows_self_referential_rows(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "self-reference.db")
    connection.execute(
        "CREATE TABLE segment (id TEXT PRIMARY KEY, previous_id TEXT "
        "REFERENCES segment(id))"
    )

    assert foreign_key_table_order(connection) == ["segment"]
    connection.close()
