from typing import cast

from nyx.eval.evaluator import Evaluator
from nyx.eval.store import EvalStore
from nyx.types import EvalRecord, LlmMessage, LLMOutput


class _FakeStore:
    def __init__(self) -> None:
        self.records: list[EvalRecord] = []
        self.raise_on_insert = False
        self.prompt_messages: list[LlmMessage] | None = None

    async def insert(
        self,
        record: EvalRecord,
        prompt_messages: list[LlmMessage] | None = None,
    ) -> None:
        if self.raise_on_insert:
            raise RuntimeError("boom")
        self.records.append(record)
        self.prompt_messages = prompt_messages


def _out(call_id: str = "call-1") -> LLMOutput:
    return LLMOutput(
        module="expression",
        type="speak",
        model="m",
        content="普通输出",
        correlation_id="c",
        prompt_tokens=7,
        completion_tokens=3,
        call_id=call_id,
        prompt_messages=[{"role": "user", "content": "问题"}],
    )


async def test_evaluate_records() -> None:
    store = _FakeStore()
    ev = Evaluator(embed=None, store=cast(EvalStore, store))
    await ev.evaluate(_out())
    assert len(store.records) == 1
    r = store.records[0]
    assert r.call_id == "call-1"
    assert r.module == "expression"
    assert r.output_type == "speak"
    assert r.ooc_keyword == 1.0
    assert r.ooc_embed is None                 # embed=None 关 embedding 档
    assert r.prompt_tokens == 7
    assert r.completion_tokens == 3
    assert store.prompt_messages == [{"role": "user", "content": "问题"}]


async def test_evaluate_store_none_no_crash() -> None:
    ev = Evaluator(embed=None, store=None)
    await ev.evaluate(_out())                    # 无 store 不落库、不抛


async def test_evaluate_insert_error_swallowed() -> None:
    store = _FakeStore()
    store.raise_on_insert = True
    ev = Evaluator(embed=None, store=cast(EvalStore, store))
    await ev.evaluate(_out())                    # 落库失败 best-effort，不重抛
    assert store.records == []
