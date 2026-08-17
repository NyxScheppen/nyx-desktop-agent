"""eval Evaluator：三层评分 + token 记账 + 报告落库。基础设施（非 Facade），
直接持 db（对齐 EventBus）。
"""
import json
import random
import time
import uuid

import aiosqlite

from nyx.config import EvalConfig
from nyx.db import Database
from nyx.eval.judge import judge_relevance, should_judge
from nyx.eval.rules import ooc_score, validate_structure
from nyx.llm.client import LlmClient
from nyx.types import EvalReport, EvalScores, LLMOutput, TokenUsage


class Evaluator:
    """对所有 LLM 产出做三层评分 + token 记账（原则 4 + 原则 2）。"""

    def __init__(self, db: Database, llm: LlmClient, config: EvalConfig) -> None:
        self._db = db
        self._llm = llm
        self._sample_rate = config.judge_sample_rate

    async def evaluate(self, output: LLMOutput) -> EvalReport:
        """三层：结构 → 规则 → judge（抽样）。

        落 token_usage + eval_report，返回 EvalReport。
        """
        scores: EvalScores = {
            "format": validate_structure(output.content),
            "ooc": ooc_score(output.content),
            "relevance": 0.0,
        }
        judge_usage: TokenUsage | None = None
        if should_judge(output.type, self._sample_rate, random.random()):
            scores["relevance"], judge_output = await judge_relevance(self._llm, output)
            if judge_output is not None:
                judge_usage = self._to_token_usage(judge_output)
        report = EvalReport(
            id=str(uuid.uuid4()),
            output_id=output.id,
            module=output.module,
            type=output.type,
            scores=scores,
            token_usage=output.token_usage,
            correlation_id=output.correlation_id,
            created_at=time.time(),
        )
        output_usage = self._to_token_usage(output)   # 锁外：纯计算，不碰 db
        async with self._db.lock:
            await self._insert_report(report)
            await self._insert_token_usage(output_usage)
            if judge_usage is not None:
                await self._insert_token_usage(judge_usage)
            await self._db.conn.commit()   # 写后必 commit，三连 INSERT 原子提交
        return report

    async def list_reports(self, limit: int = 100) -> list[EvalReport]:
        async with self._db.lock:
            cur = await self._db.conn.execute(
                "SELECT * FROM eval_report ORDER BY created_at DESC LIMIT ?", (limit,)
            )
            rows = await cur.fetchall()
        return [_row_to_report(r) for r in rows]

    async def list_token_usage(self, since: float = 0) -> list[TokenUsage]:
        async with self._db.lock:
            cur = await self._db.conn.execute(
                "SELECT * FROM token_usage WHERE created_at >= ? "
                "ORDER BY created_at DESC",
                (since,),
            )
            rows = await cur.fetchall()
        return [_row_to_usage(r) for r in rows]

    # ---- 内部 ----

    @staticmethod
    def _to_token_usage(output: LLMOutput) -> TokenUsage:
        return TokenUsage(
            id=str(uuid.uuid4()),
            correlation_id=output.correlation_id,
            module=output.module,
            purpose=output.type,   # 03-llm：type → TokenUsage.purpose
            model=output.model,
            input_tokens=output.token_usage["input"],
            output_tokens=output.token_usage["output"],
            created_at=time.time(),
        )

    async def _insert_report(self, r: EvalReport) -> None:
        await self._db.conn.execute(
            "INSERT INTO eval_report "
            "(id, output_id, module, type, scores, token_usage, "
            "correlation_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                r.id, r.output_id, r.module, r.type,
                json.dumps(r.scores), json.dumps(r.token_usage),
                r.correlation_id, r.created_at,
            ),
        )

    async def _insert_token_usage(self, u: TokenUsage) -> None:
        await self._db.conn.execute(
            "INSERT INTO token_usage "
            "(id, correlation_id, module, purpose, model, "
            "input_tokens, output_tokens, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                u.id, u.correlation_id, u.module, u.purpose, u.model,
                u.input_tokens, u.output_tokens, u.created_at,
            ),
        )


def _row_to_report(row: aiosqlite.Row) -> EvalReport:
    return EvalReport(
        id=row["id"],
        output_id=row["output_id"],
        module=row["module"],
        type=row["type"],
        scores=json.loads(row["scores"]),
        token_usage=json.loads(row["token_usage"]),
        correlation_id=row["correlation_id"],
        created_at=row["created_at"],
    )


def _row_to_usage(row: aiosqlite.Row) -> TokenUsage:
    return TokenUsage(
        id=row["id"],
        correlation_id=row["correlation_id"],
        module=row["module"],
        purpose=row["purpose"],
        model=row["model"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        created_at=row["created_at"],
    )
