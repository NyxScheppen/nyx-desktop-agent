import json
from typing import Any

from nyx.db import Database


class ToolCallStore:
    """工具观测与执行回执；回执失败必须传播，观测仍为旁路。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def start_execution(
        self, log_id: str, correlation_id: str,
    ) -> dict[str, Any] | None:
        """Claim an attempt before effects; return an existing receipt on replay."""
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "INSERT OR IGNORE INTO tool_execution (id, correlation_id, status) "
                "VALUES (?, ?, 'running')", (log_id, correlation_id),
            )
            if cursor.rowcount == 1:
                return None
            row = await (await conn.execute(
                "SELECT * FROM tool_execution WHERE id = ?", (log_id,),
            )).fetchone()
            return dict(row) if row is not None else None

    async def finish_execution(
        self, log_id: str, result: Any, error: str | None,
    ) -> None:
        """Save the full outcome before acknowledging the graph node."""
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE tool_execution SET status = ?, result_json = ?, error = ? "
                "WHERE id = ?",
                ("success" if error is None else "error",
                 json.dumps(result, ensure_ascii=False) if error is None else None,
                 error, log_id),
            )

    async def clear_executions(self, correlation_id: str) -> None:
        """Drop temporary full results only after the reply has committed."""
        async with self._db.transaction() as conn:
            await conn.execute(
                "DELETE FROM tool_execution WHERE correlation_id = ?",
                (correlation_id,),
            )

    async def insert(self, record: dict[str, Any]) -> None:
        """Persist one bounded execution snapshot, without replacing a replay."""
        async with self._db.transaction() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO tool_call_log "
                "(id, created_at, correlation_id, call_id, tool_name, args_json, "
                "status, duration_ms, result_json, error) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record["id"],
                    record["created_at"],
                    record["correlation_id"],
                    record["call_id"],
                    record["tool_name"],
                    record["args_json"],
                    record["status"],
                    record["duration_ms"],
                    record["result_json"],
                    record["error"],
                ),
            )

    async def mark_adopted(self, log_id: str) -> None:
        """Only move adoption forward after the caller accepts the result."""
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE tool_call_log SET adopted = 1 WHERE id = ?", (log_id,)
            )
