from pathlib import Path

from nyx import db
from nyx.eval.evaluator import Evaluator
from nyx.types import LLMOutput


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


def _voice_output() -> LLMOutput:
    return LLMOutput(
        id="out-2",
        module="expression",
        type="speak",
        model="fake",
        content="x",
        token_usage={"input": 1, "output": 1},
        correlation_id="corr-2",
    )


async def test_evaluate_persists(tmp_path: Path) -> None:
    path = tmp_path / "e.db"
    database = await db.connect(str(path))
    try:
        await Evaluator(database).evaluate(_output())
    finally:
        await database.conn.close()

    reopened = await db.connect(str(path))
    try:
        ev = Evaluator(reopened)
        assert len(await ev.list_reports()) == 1
        assert len(await ev.list_token_usage()) == 1
    finally:
        await reopened.conn.close()


async def test_list_reports_roundtrip() -> None:
    database = await db.connect(":memory:")
    try:
        ev = Evaluator(database)
        await ev.evaluate(_output())
        await ev.evaluate(_output())
        reports = await ev.list_reports()
        assert len(reports) == 2
        assert reports[0].token_usage == {"input": 1, "output": 1}
        assert reports[0].scores == {"ooc": 1.0}
    finally:
        await database.conn.close()


async def test_evaluate_ooc_embed_combine() -> None:
    database = await db.connect(":memory:")
    try:
        async def _orthogonal_embed(text: str) -> list[float]:
            # content "x" → [1,0]；语料行 → [0,1]，二者正交（sim=0）
            return [1.0, 0.0] if text == "x" else [0.0, 1.0]

        ev = Evaluator(database, embed=_orthogonal_embed)
        report = await ev.evaluate(_voice_output())
        # voice 类型：min(keyword=1.0, embed=0.0) = 0.0
        assert report.scores["ooc"] == 0.0
    finally:
        await database.conn.close()


async def test_evaluate_ooc_non_voice_skips_embed() -> None:
    database = await db.connect(":memory:")
    try:
        calls: list[str] = []

        async def _embed(text: str) -> list[float]:
            calls.append(text)
            return [0.0, 1.0]

        ev = Evaluator(database, embed=_embed)
        report = await ev.evaluate(_output())   # type scene_memory，非 voice
        assert report.scores["ooc"] == 1.0      # 仅关键词，embed 未触发
        assert calls == []
    finally:
        await database.conn.close()


async def test_list_token_usage_since() -> None:
    database = await db.connect(":memory:")
    try:
        ev = Evaluator(database)
        await ev.evaluate(_output())
        [usage] = await ev.list_token_usage()
        assert len(await ev.list_token_usage(since=usage.created_at)) == 1
        assert len(await ev.list_token_usage(since=usage.created_at + 1)) == 0
    finally:
        await database.conn.close()
