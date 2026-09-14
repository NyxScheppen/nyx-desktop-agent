from pathlib import Path
from typing import Any, cast

from nyx.activity.reading_runner import ReadingActivityRunner
from nyx.enums import ActivityStatus, ActivityType
from nyx.eval.evaluator import Evaluator
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.types import Activity, LLMOutput, Material


def test_reading_runner_has_single_activity_entrypoint() -> None:
    """读书执行细节通过 runner 的单一入口暴露给 facade。"""
    assert hasattr(ReadingActivityRunner, "run")


class _FakeMaterialStore:
    def __init__(self) -> None:
        self.fragments: list[str] = []
        self.append_calls = 0
        self.advance_calls = 0
        self.read_chars = 0

    async def get_fragments(self, path: str) -> list[str]:
        return self.fragments

    async def append_fragment(self, path: str, note: str, now: float) -> None:
        self.append_calls += 1
        self.fragments.append(note)

    async def advance(self, path: str, read_chars: int, now: float) -> None:
        self.advance_calls += 1
        self.read_chars = read_chars

    async def get_by_path(self, path: str) -> Material | None:
        return None


class _FakeLlm:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
    ) -> LLMOutput:
        self.calls.append(output_type)
        return LLMOutput(
            module=module,
            type=output_type,
            model="fake",
            content='{"book":"b","note":"n"}',
            correlation_id=correlation_id,
        )


class _FakeEvaluator:
    async def evaluate(self, output: LLMOutput) -> None:
        return None


class _FakeMemory:
    def __init__(self) -> None:
        self.calls = 0

    async def remember_knowledge(
        self, items: list[dict[str, str]], correlation_id: str
    ) -> None:
        self.calls += 1
        return None


class _WriteSpy:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(
        self, action: str, path: str, content: str | None = None
    ) -> dict[str, Any]:
        self.calls += 1
        return {"path": path}


def _activity(source: str, checkpoint: dict[str, Any]) -> Activity:
    return Activity(
        id="a1",
        type=ActivityType.READING,
        schedule_block_id="09:00",
        status=ActivityStatus.RUNNING,
        progress={
            "source": source,
            "filename": "book.txt",
            "read_chars": 0,
            "total_chars": 10,
            "correlation_id": "c1",
            "reading": checkpoint,
        },
        started_at=1.0,
    )


async def _noop_write(
    action: str, path: str, content: str | None = None
) -> dict[str, Any]:
    return {"path": path}


async def test_resume_skips_committed_fragment(tmp_path: Path) -> None:
    source = tmp_path / "book.txt"
    source.write_text("0123456789", encoding="utf-8")
    material = _FakeMaterialStore()
    updates: list[dict[str, Any]] = []

    async def update(activity: Activity) -> None:
        updates.append(dict(activity.progress["reading"]))

    runner = ReadingActivityRunner(
        cast(Any, material),
        cast(LlmClient, _FakeLlm()),
        cast(Evaluator, _FakeEvaluator()),
        cast(MemoryFacade, _FakeMemory()),
        _noop_write,
        update,
    )
    activity = _activity(
        str(source),
        {
            "source": str(source),
            "read_from": 0,
            "read_to": 5,
            "fragment_committed": True,
            "advanced_to": 0,
            "finalized": False,
            "note_path": None,
            "knowledge_extracted": False,
            "book": "b",
            "note": "n",
        },
    )

    result = await runner.run(activity, str(source))

    assert result["read_chars"] == 5
    assert material.append_calls == 0
    assert material.advance_calls == 1
    assert updates[-1]["advanced_to"] == 5


async def test_resume_skips_completed_advance(tmp_path: Path) -> None:
    source = tmp_path / "book.txt"
    source.write_text("0123456789", encoding="utf-8")
    material = _FakeMaterialStore()

    async def update(activity: Activity) -> None:
        return None

    runner = ReadingActivityRunner(
        cast(Any, material),
        cast(LlmClient, _FakeLlm()),
        cast(Evaluator, _FakeEvaluator()),
        cast(MemoryFacade, _FakeMemory()),
        _noop_write,
        update,
    )
    activity = _activity(
        str(source),
        {
            "source": str(source),
            "read_from": 0,
            "read_to": 5,
            "fragment_committed": True,
            "advanced_to": 5,
            "finalized": False,
            "note_path": None,
            "knowledge_extracted": False,
            "book": "b",
            "note": "n",
        },
    )

    result = await runner.run(activity, str(source))

    assert result["read_chars"] == 5
    assert material.append_calls == 0
    assert material.advance_calls == 0


async def test_resume_skips_finalized_note_and_knowledge(tmp_path: Path) -> None:
    source = tmp_path / "book.txt"
    source.write_text("0123456789", encoding="utf-8")
    material = _FakeMaterialStore()
    llm = _FakeLlm()
    memory = _FakeMemory()
    write_file = _WriteSpy()

    async def update(activity: Activity) -> None:
        return None

    runner = ReadingActivityRunner(
        cast(Any, material),
        cast(LlmClient, llm),
        cast(Evaluator, _FakeEvaluator()),
        cast(MemoryFacade, memory),
        write_file,
        update,
    )
    activity = _activity(
        str(source),
        {
            "source": str(source),
            "read_from": 0,
            "read_to": 10,
            "fragment_committed": True,
            "advanced_to": 10,
            "finalized": True,
            "note_path": "workspace/notes/book.txt-abcd.md",
            "knowledge_extracted": True,
            "book": "book.txt",
            "note": "完整笔记",
        },
    )

    result = await runner.run(activity, str(source))

    assert result["note"] == "完整笔记"
    assert result["path"] == "workspace/notes/book.txt-abcd.md"
    assert llm.calls == []
    assert write_file.calls == 0
    assert memory.calls == 0
