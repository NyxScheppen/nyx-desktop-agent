import json
import sqlite3
from pathlib import Path

import pytest

from scripts.migrate_sqlite_to_postgres import foreign_key_table_order, inspect_source


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


def test_inspect_source_finds_host_and_registered_character_databases_read_only(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    host_path = data / "characters" / "host.db"
    character_path = data / "characters" / "nyx" / "nyx.db"
    _database(character_path, 52, rows=2)
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
    assert manifest["characters"][0]["database"]["version"] == 52
    assert manifest["characters"][0]["database"]["tables"]["sample"] == 2
    assert {path: path.read_bytes() for path in before} == before


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
