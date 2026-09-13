from typing import cast

from nyx.enums import BoundaryResult
from nyx.eval.evaluator import Evaluator
from nyx.inner_life.facade import InnerLifeFacade
from nyx.llm.client import LlmClient, LlmMessage
from nyx.memory.facade import MemoryFacade
from nyx.reading.integration import ReadingIntegration
from nyx.types import LLMOutput


class _Llm:
    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
        tools: list[dict[str, object]] | None = None,
    ) -> LLMOutput:
        return LLMOutput(
            module=module,
            type=output_type,
            model="fake",
            content='{"content":"记住了","summary":"章末"}',
            correlation_id=correlation_id,
        )


class _Evaluator:
    async def evaluate(self, output: LLMOutput) -> None:
        return None


class _Memory:
    def __init__(self) -> None:
        self.remembered: list[tuple[str, str, str]] = []

    async def remember_reading(
        self, content: str, summary: str, correlation_id: str
    ) -> None:
        self.remembered.append((content, summary, correlation_id))


class _InnerLife:
    async def reflect(self, correlation_id: str | None = None) -> None:
        return None


async def test_integrate_consumes_buffer_after_memory_is_saved() -> None:
    memory = _Memory()
    integration = ReadingIntegration(
        cast(LlmClient, _Llm()),
        cast(Evaluator, _Evaluator()),
        cast(MemoryFacade, memory),
        cast(InnerLifeFacade, _InnerLife()),
    )
    await integration.record("book-1", 2, "这里让我停了一下", "mutter")

    await integration.integrate("book-1", BoundaryResult.CHAPTER_END, 0)

    assert memory.remembered == [("记住了", "章末", "book-1")]
    assert integration.buffer.get("book-1") == []
