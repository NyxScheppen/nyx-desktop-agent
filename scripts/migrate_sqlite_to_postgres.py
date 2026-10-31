"""Read-only SQLite source inspection for the hosted PostgreSQL migration."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, cast


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
                    'SELECT COUNT(*) FROM "'
                    + str(row["name"]).replace('"', '""')
                    + '"'
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--database-url-env", default="NYX_DATABASE_URL")
    args = parser.parse_args()
    if not os.environ.get(args.database_url_env):
        parser.error(f"{args.database_url_env} is not set")
    print(json.dumps(inspect_source(args.source), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
