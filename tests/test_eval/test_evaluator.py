from pathlib import Path
from typing import cast

from nyx import db
from nyx.config import EvalConfig
from nyx.db import Database
from nyx.eval.evaluator import Evaluator
from nyx.llm.client import LlmClient, LlmMessage
from nyx.types import LLMOutput


class _FakeLlm:
    """complete 返回固定 JSON 的 fake，记录 output_type。"""

    def __init__(self, content: str = '{"score": 4}') -> None:
        self._content = content
        self.calls: list[str] = []

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
    ) -> LLMOutput:
        self.calls.append(output_type)
        return LLMOutput(
            id="fake-id",
            module=module,
            type=output_type,
            model="fake",
            content=self._content,
            token_usage={"input": 1, "output": 1},
            correlation_id=correlation_id,
        )


class _BoomLlm:
    """complete 抛异常，模拟 judge 传输失败（超时/5xx）。"""

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
    ) -> LLMOutput:
        raise RuntimeError("transport down")


def _output() -> LLMOutput:
    return LLMOutput(
        id="out-1",
        module="memory",
        type="scene_memory",
        model="fake",
        content="x",
        token_usage={"input": 1, "output": 1},
        correlation_id="corr-1",
    )


def _ev(database: Database, rate: float) -> Evaluator:
    return Evaluator(
        database, cast(LlmClient, _FakeLlm()), EvalConfig(judge_sample_rate=rate)
    )


async def test_evaluate_sampled() -> None:
    database = await db.connect(":memory:")
    try:
        ev = _ev(database, 1.0)
        report = await ev.evaluate(_output())
        assert report.scores["relevance"] == 4.0
        assert report.output_id == "out-1"
        assert len(await ev.list_reports()) == 1
        usages = await ev.list_token_usage()
        assert sorted(u.purpose for u in usages) == ["judge", "scene_memory"]
    finally:
        await database.conn.close()


async def test_evaluate_judge_transport_failure() -> None:
    database = await db.connect(":memory:")
    try:
        ev = Evaluator(
            database, cast(LlmClient, _BoomLlm()), EvalConfig(judge_sample_rate=1.0)
        )
        report = await ev.evaluate(_output())
        assert report.scores["relevance"] == 0.0   # judge 传输失败容错 0.0
        [usage] = await ev.list_token_usage()
        assert usage.purpose == "scene_memory"     # 仅主产出，judge 无产出不记账
    finally:
        await database.conn.close()


async def test_evaluate_not_sampled() -> None:
    database = await db.connect(":memory:")
    try:
        ev = _ev(database, 0.0)
        report = await ev.evaluate(_output())
        assert report.scores["relevance"] == 0.0
        [usage] = await ev.list_token_usage()
        assert usage.purpose == "scene_memory"
    finally:
        await database.conn.close()


async def test_evaluate_persists(tmp_path: Path) -> None:
    path = tmp_path / "e.db"
    database = await db.connect(str(path))
    try:
        ev = _ev(database, 0.0)
        await ev.evaluate(_output())
    finally:
        await database.conn.close()

    reopened = await db.connect(str(path))
    try:
        ev2 = _ev(reopened, 0.0)
        assert len(await ev2.list_reports()) == 1
        assert len(await ev2.list_token_usage()) == 1
    finally:
        await reopened.conn.close()


async def test_list_reports_roundtrip() -> None:
    database = await db.connect(":memory:")
    try:
        ev = _ev(database, 0.0)
        await ev.evaluate(_output())
        await ev.evaluate(_output())
        reports = await ev.list_reports()
        assert len(reports) == 2
        assert reports[0].token_usage == {"input": 1, "output": 1}
        assert reports[0].scores["format"] in (0.0, 1.0)
    finally:
        await database.conn.close()


async def test_list_token_usage_since() -> None:
    database = await db.connect(":memory:")
    try:
        ev = _ev(database, 0.0)
        await ev.evaluate(_output())
        [usage] = await ev.list_token_usage()
        assert len(await ev.list_token_usage(since=usage.created_at)) == 1
        assert len(await ev.list_token_usage(since=usage.created_at + 1)) == 0
    finally:
        await database.conn.close()
