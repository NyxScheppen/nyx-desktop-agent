"""Read-only SQLite source inspection for the hosted PostgreSQL migration."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any, cast

import psycopg

from nyx.app_context import _HOST_MIGRATIONS  # pyright: ignore[reportPrivateUsage]
from nyx.db import (
    _MIGRATIONS,  # pyright: ignore[reportPrivateUsage]
    postgres_migrations,
)

_SKIPPED_TABLES = {"checkpoints", "writes"}


def _data_root(source: Path) -> Path:
    source = source.resolve()
    for candidate in (source, source / "data"):
        if (candidate / "characters" / "host.db").is_file():
            return candidate
    raise ValueError(f"host.db not found under {source}")


def _inspect_database(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"SQLite database not found: {path}")
    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        version_row = connection.execute(
            "SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        tables = {
            str(row["name"]): int(
                connection.execute(
                    'SELECT COUNT(*) FROM "' + str(row["name"]).replace('"', '""') + '"'
                ).fetchone()[0]
            )
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        }
    except sqlite3.Error as error:
        raise ValueError(f"cannot inspect SQLite database {path}: {error}") from error
    finally:
        if connection is not None:
            connection.close()
    return {
        "path": str(path),
        "version": int(version_row[0]) if version_row else 0,
        "tables": tables,
    }


def inspect_source(source: Path) -> dict[str, Any]:
    """Return a source manifest without writing to SQLite or the filesystem."""
    data_root = _data_root(source)
    host_path = data_root / "characters" / "host.db"
    host = _inspect_database(host_path)
    uri = f"{host_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT id, record FROM characters ORDER BY id"
        ).fetchall()
    except sqlite3.Error as error:
        raise ValueError(f"host characters table is unavailable: {error}") from error
    finally:
        connection.close()

    characters: list[dict[str, Any]] = []
    for row in rows:
        decoded = json.loads(str(row["record"]))
        if not isinstance(decoded, dict):
            raise ValueError(f"invalid character record: {row['id']}")
        record = cast(dict[str, Any], decoded)
        if not isinstance(record.get("db_path"), str):
            raise ValueError(f"invalid character record: {row['id']}")
        database_path = Path(record["db_path"])
        if not database_path.is_absolute():
            database_path = (data_root.parent / database_path).resolve()
        try:
            database = _inspect_database(database_path)
        except ValueError as error:
            raise ValueError(f"character {row['id']}: {error}") from error
        characters.append(
            {
                "id": str(row["id"]),
                "database": database,
                "card_path": record.get("card_path"),
                "card_exists": Path(record["card_path"]).is_dir()
                if isinstance(record.get("card_path"), str)
                else False,
                "workspace": record.get("workspace"),
                "workspace_exists": Path(record["workspace"]).is_dir()
                if isinstance(record.get("workspace"), str)
                else False,
            }
        )
    return {"data_root": str(data_root), "host": host, "characters": characters}


def foreign_key_table_order(connection: sqlite3.Connection) -> list[str]:
    tables = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        if str(row[0]) not in _SKIPPED_TABLES
    ]
    dependencies = {
        table: {
            str(row[2])
            for row in connection.execute(
                f'PRAGMA foreign_key_list("{table.replace(chr(34), chr(34) * 2)}")'
            ).fetchall()
            if str(row[2]) in tables and str(row[2]) != table
        }
        for table in tables
    }
    ordered: list[str] = []
    remaining = set(tables)
    while remaining:
        ready = sorted(
            table for table in remaining if not dependencies[table] & remaining
        )
        if not ready:
            raise ValueError("foreign key cycle in SQLite schema")
        ordered.extend(ready)
        remaining.difference_update(ready)
    return ordered


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sqlite_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [
        str(row[1])
        for row in connection.execute(
            f'PRAGMA table_info("{table.replace(chr(34), chr(34) * 2)}")'
        ).fetchall()
    ]


def _sqlite_primary_key(connection: sqlite3.Connection, table: str) -> list[str]:
    return [
        str(row[1])
        for row in sorted(
            connection.execute(
                f'PRAGMA table_info("{table.replace(chr(34), chr(34) * 2)}")'
            ).fetchall(),
            key=lambda row: int(row[5]),
        )
        if int(row[5]) > 0
    ]


def _target_table_columns(connection: Any, schema: str, table: str) -> list[str]:
    rows = connection.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
        (schema, table),
    ).fetchall()
    return [str(row[0]) for row in rows]


def _target_table_names(connection: Any, schema: str) -> list[str]:
    rows = connection.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema=%s AND table_type='BASE TABLE' ORDER BY table_name",
        (schema,),
    ).fetchall()
    return [str(row[0]) for row in rows]


def _target_schema_is_empty(connection: Any, schema: str) -> bool:
    row = connection.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n "
        "ON n.oid=c.relnamespace WHERE n.nspname=%s) OR EXISTS "
        "(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname=%s) OR EXISTS (SELECT 1 FROM pg_type t "
        "JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname=%s)",
        (schema, schema, schema),
    ).fetchone()
    return not bool(row[0])


def _target_schema_exists(connection: Any, schema: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name=%s",
        (schema,),
    ).fetchone()
    return row is not None


def _schema_name(character_id: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", character_id):
        raise ValueError(
            f"character id cannot become a PostgreSQL schema: {character_id}"
        )
    return f"character_{character_id}"


def _is_builtin_nyx_card(character_id: str, card_path: Path) -> bool:
    builtin_nyx_card = Path(__file__).resolve().parents[1] / "nyx" / "cards" / "nyx"
    return character_id == "nyx" and card_path.resolve() == builtin_nyx_card


def _rewrite_character_record(
    record: dict[str, Any], target_root: Path
) -> dict[str, Any]:
    character_id = str(record["id"])
    base = target_root / "characters" / character_id
    rewritten = dict(record)
    card_path = Path(str(record.get("card_path", "")))
    rewritten.update(
        {
            "db_path": str(base / f"{character_id}.db"),
            "card_path": str(card_path)
            if _is_builtin_nyx_card(character_id, card_path)
            else str(base / "card"),
            "workspace": str(base / "workspace"),
        }
    )
    return rewritten


def _copy_character_files(
    source_record: dict[str, Any], target_root: Path, copied: list[Path]
) -> None:
    character_id = str(source_record["id"])
    destination = target_root / "characters" / character_id
    if not destination.exists():
        destination.mkdir(parents=True, exist_ok=True)
        copied.append(destination)
    for field, child in (("card_path", "card"), ("workspace", "workspace")):
        source_value = source_record.get(field)
        if not isinstance(source_value, str):
            continue
        source_path = Path(source_value)
        if field == "card_path" and _is_builtin_nyx_card(
            character_id, source_path
        ):
            continue
        if not source_path.is_dir():
            raise ValueError(f"registered character {field} is missing: {source_path}")
        target_path = destination / child
        if target_path.exists():
            if source_path.resolve() == target_path.resolve():
                continue
            raise ValueError(f"migration target already contains {target_path}")
        copied.append(target_path)
        shutil.copytree(source_path, target_path)


def _create_schema(
    connection: Any, schema: str, migrations: list[tuple[int, list[str]]]
) -> None:
    connection.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote_identifier(schema)}")
    connection.execute(f"SET search_path TO {_quote_identifier(schema)}")
    for statement in migrations[0][1]:
        connection.execute(statement)
    source = _HOST_MIGRATIONS if schema == "host" else _MIGRATIONS
    version = max(version for version, _ in source)
    connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    connection.execute("INSERT INTO schema_version (version) VALUES (%s)", (version,))


def _conversation_archive(
    source: sqlite3.Connection,
    target: Any,
    schema: str,
    *,
    backfill: bool,
) -> None:
    expected: dict[str, str] = {}
    for segment, raw in source.execute(
        "SELECT id, raw_event_ids FROM conversation_segment ORDER BY id"
    ):
        ids: Any = json.loads(raw)
        if not isinstance(ids, list) or any(
            not isinstance(i, str) or not i for i in cast(list[Any], ids)
        ):
            raise ValueError("invalid conversation turn IDs")
        for identity in cast(list[str], ids):
            expected.setdefault(identity, segment)
    table = f"{_quote_identifier(schema)}.conversation_turn_archive"
    if backfill and expected:
        target.cursor().executemany(
            f"INSERT INTO {table} (turn_id, segment_id) VALUES (%s, %s)",
            list(expected.items()),
        )
    actual = dict(target.execute(f"SELECT turn_id, segment_id FROM {table}").fetchall())
    if actual != expected:
        raise ValueError(f"conversation archive mismatch in {schema}")


def _copy_table(
    source: sqlite3.Connection,
    target: Any,
    schema: str,
    table: str,
    *,
    event_sequence: bool = False,
) -> int:
    source_columns = _sqlite_columns(source, table)
    target_columns = _target_table_columns(target, schema, table)
    if not target_columns:
        raise ValueError(f"target schema is missing table {schema}.{table}")
    columns = [column for column in source_columns if column in target_columns]
    if event_sequence and "event_seq" not in columns:
        columns.append("event_seq")
    select_columns = ", ".join(_quote_identifier(column) for column in source_columns)
    cursor = source.execute(
        f'SELECT rowid, {select_columns} FROM "{table.replace(chr(34), chr(34) * 2)}" '
        "ORDER BY rowid"
    )
    positions = {column: index + 1 for index, column in enumerate(source_columns)}
    target_ref = f"{_quote_identifier(schema)}.{_quote_identifier(table)}"
    column_sql = ", ".join(_quote_identifier(column) for column in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    count = 0
    while rows := cursor.fetchmany(500):
        values = [
            tuple(
                row[positions[column]] if column in positions else row[0]
                for column in columns
            )
            for row in rows
        ]
        target.cursor().executemany(
            f"INSERT INTO {target_ref} ({column_sql}) VALUES ({placeholders})", values
        )
        count += len(values)
    sequence = (
        source.execute(
            "SELECT seq FROM sqlite_sequence WHERE name=?", (table,)
        ).fetchone()
        if source.execute(
            "SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'"
        ).fetchone()
        else None
    )
    if sequence is not None:
        primary_key = _sqlite_primary_key(source, table)
        if primary_key:
            sequence_name = target.execute(
                "SELECT pg_get_serial_sequence(%s, %s)",
                (f"{schema}.{table}", primary_key[0]),
            ).fetchone()[0]
            if sequence_name:
                target.execute(
                    "SELECT setval(%s::regclass, %s, true)",
                    (sequence_name, int(sequence[0])),
                )
    return count


def _validate_schema_version(target: Any, schema: str, expected: int) -> None:
    row = target.execute(
        f'SELECT version FROM "{schema}".schema_version LIMIT 1'
    ).fetchone()
    if row is None or int(row[0]) != expected:
        raise ValueError(f"schema version mismatch for {schema}")


def _validate_table(
    source: sqlite3.Connection,
    target: Any,
    schema: str,
    table: str,
    source_count: int,
) -> None:
    target_ref = f"{_quote_identifier(schema)}.{_quote_identifier(table)}"
    count = int(target.execute(f"SELECT COUNT(*) FROM {target_ref}").fetchone()[0])
    if count != source_count:
        raise ValueError(
            f"row count mismatch for {schema}.{table}: {source_count} != {count}"
        )
    primary_key = _sqlite_primary_key(source, table)
    if not primary_key:
        return
    columns = ", ".join(_quote_identifier(column) for column in primary_key)
    source_keys = {
        tuple(row)
        for row in source.execute(
            f'SELECT {columns} FROM "{table.replace(chr(34), chr(34) * 2)}"'
        ).fetchall()
    }
    target_keys = {
        tuple(row)
        for row in target.execute(f"SELECT {columns} FROM {target_ref}").fetchall()
    }
    if source_keys != target_keys:
        raise ValueError(f"primary key mismatch for {schema}.{table}")


def _validate_event_order(source: sqlite3.Connection, target: Any, schema: str) -> None:
    order_column = (
        "event_seq" if "event_seq" in _sqlite_columns(source, "event_log") else "rowid"
    )
    source_rows = source.execute(
        f"SELECT {order_column}, id FROM event_log ORDER BY {order_column}"
    ).fetchall()
    target_rows = target.execute(
        f"SELECT event_seq, id FROM {_quote_identifier(schema)}.event_log "
        "ORDER BY event_seq"
    ).fetchall()
    expected = [(int(row[0]), str(row[1])) for row in source_rows]
    actual = [(int(row[0]), str(row[1])) for row in target_rows]
    if expected != actual:
        raise ValueError(f"event_log order mismatch for {schema}")


def _validate_host_records(
    source: sqlite3.Connection, target: Any, target_root: Path
) -> None:
    rows = source.execute("SELECT id, record FROM characters ORDER BY id").fetchall()
    for row in rows:
        identity = str(row[0])
        expected = _rewrite_character_record(json.loads(row[1]), target_root)
        actual_row = target.execute(
            'SELECT record FROM "host".characters WHERE id=%s', (identity,)
        ).fetchone()
        if actual_row is None or json.loads(actual_row[0]) != expected:
            raise ValueError(f"host character path mapping mismatch: {identity}")
    target_tables = set(_target_table_names(target, "host"))
    source_tables = {
        str(row[0])
        for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for table, digest in (
        ("device_token", "token_hash"),
        ("pairing_code", "code_hash"),
    ):
        if table not in source_tables or table not in target_tables:
            continue
        source_rows = source.execute(
            f'SELECT id, "{digest}" FROM "{table}" ORDER BY id'
        ).fetchall()
        target_rows = target.execute(
            f'SELECT id, "{digest}" FROM "host"."{table}" ORDER BY id'
        ).fetchall()
        if [tuple(row) for row in source_rows] != [tuple(row) for row in target_rows]:
            raise ValueError(f"credential hash mismatch for host.{table}")


def import_source(source: Path, target_root: Path, database_url: str) -> dict[str, Any]:
    """Import a read-only SQLite snapshot into empty PostgreSQL schemas."""
    manifest = inspect_source(source)
    data_root = Path(manifest["data_root"])
    target_root = target_root.resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    target = psycopg.connect(database_url)
    copied: list[Path] = []
    row_counts: dict[str, dict[str, int]] = {}
    try:
        schemas = ["host"] + [
            _schema_name(str(item["id"])) for item in manifest["characters"]
        ]
        if int(manifest["host"]["version"]) != 2:
            raise ValueError("host SQLite schema must be version 2 before migration")
        for schema in schemas:
            if _target_schema_exists(target, schema) and not _target_schema_is_empty(
                target, schema
            ):
                raise ValueError(f"migration target schema is not empty: {schema}")
        for item in manifest["characters"]:
            if int(item["database"]["version"]) not in (51, 52, 53):
                raise ValueError(
                    f"character {item['id']} SQLite schema must be version 51, 52 or 53"
                )
        host_migrations = postgres_migrations(_HOST_MIGRATIONS)
        character_migrations = postgres_migrations(_MIGRATIONS)
        for schema, migrations in (
            ("host", host_migrations),
            *zip(schemas[1:], [character_migrations] * len(schemas[1:])),
        ):
            _create_schema(target, schema, migrations)

        host_path = data_root / "characters" / "host.db"
        host_source = sqlite3.connect(
            f"{host_path.resolve().as_uri()}?mode=ro", uri=True
        )
        try:
            for table in foreign_key_table_order(host_source):
                if table == "schema_version":
                    continue
                if table == "characters":
                    rows = host_source.execute(
                        "SELECT id, record FROM characters ORDER BY rowid"
                    ).fetchall()
                    values = [
                        (
                            str(row[0]),
                            json.dumps(
                                _rewrite_character_record(
                                    json.loads(row[1]), target_root
                                )
                            ),
                        )
                        for row in rows
                    ]
                    target.cursor().executemany(
                        'INSERT INTO "host"."characters" '
                        '("id", "record") VALUES (%s, %s)',
                        values,
                    )
                    count = len(values)
                else:
                    count = _copy_table(host_source, target, "host", table)
                _validate_table(host_source, target, "host", table, count)
                row_counts.setdefault("host", {})[table] = count
            _validate_host_records(host_source, target, target_root)
            _validate_schema_version(target, "host", 2)
        finally:
            host_source.close()

        for item in manifest["characters"]:
            character_id = str(item["id"])
            schema = _schema_name(character_id)
            source_path = Path(item["database"]["path"])
            character_source = sqlite3.connect(
                f"{source_path.resolve().as_uri()}?mode=ro", uri=True
            )
            try:
                for table in _SKIPPED_TABLES:
                    if table in item["database"]["tables"] and int(
                        character_source.execute(
                            f'SELECT COUNT(*) FROM "{table}"'
                        ).fetchone()[0]
                    ):
                        raise ValueError(
                            f"character {character_id} has non-empty {table}; "
                            "finish pending workflows before migration"
                        )
                for table in foreign_key_table_order(character_source):
                    if table == "schema_version":
                        continue
                    count = _copy_table(
                        character_source,
                        target,
                        schema,
                        table,
                        event_sequence=table == "event_log",
                    )
                    _validate_table(character_source, target, schema, table, count)
                    row_counts.setdefault(schema, {})[table] = count
                _validate_event_order(character_source, target, schema)
                _conversation_archive(
                    character_source,
                    target,
                    schema,
                    backfill=int(item["database"]["version"]) < 53,
                )
                _validate_schema_version(target, schema, max(v for v, _ in _MIGRATIONS))
            finally:
                character_source.close()
            _copy_character_files({**item, "id": character_id}, target_root, copied)
        target.commit()
    except BaseException:
        target.rollback()
        for path in reversed(copied):
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        raise
    finally:
        target.close()
    return {
        "target_data_root": str(target_root),
        "schemas": schemas,
        "rows": row_counts,
        "source": str(data_root),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--database-url-env", default="NYX_DATABASE_URL")
    parser.add_argument(
        "--inspect-only",
        action="store_true",
        help="print the source manifest without importing it",
    )
    parser.add_argument(
        "--import",
        dest="run_import",
        action="store_true",
        help="import the source snapshot into PostgreSQL",
    )
    parser.add_argument(
        "--target-data-root",
        type=Path,
        help="destination /app/data-compatible root for the import",
    )
    args = parser.parse_args()
    if not args.run_import or args.inspect_only:
        print(json.dumps(inspect_source(args.source), ensure_ascii=False, indent=2))
        return 0
    database_url = os.environ.get(args.database_url_env)
    if not database_url:
        parser.error(f"{args.database_url_env} is not set")
    if args.target_data_root is None:
        parser.error("--target-data-root is required with --import")
    target_root = args.target_data_root
    print(
        json.dumps(
            import_source(args.source, target_root, database_url),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
