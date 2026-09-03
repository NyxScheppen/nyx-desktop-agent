"""eval 记账 store：`eval_log` 表（15-eval）。每次 `evaluate()` 写一条，
供查询「最近 N 条 + 总 token（按 call_id 去重）」。
"""
import aiosqlite

from nyx.db import Database
from nyx.types import EvalRecord, EvalStats

_COLS = (
    "id, created_at, call_id, module, output_type, model, correlation_id, "
    "ooc_keyword, ooc_embed, prompt_tokens, completion_tokens"
)


class EvalStore:
    """`eval_log` 记账。db 由组合根注入（同所有 store 共享一个 conn+lock）。

    每个方法一个 `async with db.lock` 的 SQL 块，不跨方法嵌套
    （asyncio.Lock 不可重入，见 store-lock-scope）。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert(self, record: EvalRecord) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                f"INSERT INTO eval_log ({_COLS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _row(record),
            )
            await self._db.conn.commit()

    async def list_recent(self, limit: int = 5) -> list[EvalRecord]:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM eval_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
        return [_row_to_record(r) for r in rows]

    async def total_tokens(self) -> EvalStats:
        # 同 call_id 的 think/speak 只计一次：先按 call_id 分组取单值（MAX，
        # 因共享同一调用故取值相同），再对分组结果求和。
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT COALESCE(SUM(prompt_tokens), 0), "
                "COALESCE(SUM(completion_tokens), 0) "
                "FROM (SELECT call_id, MAX(prompt_tokens) AS prompt_tokens, "
                "MAX(completion_tokens) AS completion_tokens "
                "FROM eval_log GROUP BY call_id)"
            )
            row = await cursor.fetchone()
        prompt = int(row[0]) if row is not None else 0
        completion = int(row[1]) if row is not None else 0
        return EvalStats(
            total_tokens=prompt + completion,
            prompt_tokens=prompt,
            completion_tokens=completion,
        )


def _row(
    r: EvalRecord,
) -> tuple[str, float, str, str, str, str, str, float, float | None, int, int]:
    return (
        r.id, r.created_at, r.call_id, r.module, r.output_type, r.model,
        r.correlation_id, r.ooc_keyword, r.ooc_embed, r.prompt_tokens,
        r.completion_tokens,
    )


def _row_to_record(row: aiosqlite.Row) -> EvalRecord:
    return EvalRecord(
        id=row["id"],
        created_at=row["created_at"],
        call_id=row["call_id"],
        module=row["module"],
        output_type=row["output_type"],
        model=row["model"],
        correlation_id=row["correlation_id"],
        ooc_keyword=row["ooc_keyword"],
        ooc_embed=row["ooc_embed"],
        prompt_tokens=row["prompt_tokens"],
        completion_tokens=row["completion_tokens"],
    )
