from typing import cast

from nyx.eval.judge import judge_relevance, should_judge
from nyx.llm.client import LlmClient, LlmMessage
from nyx.types import LLMOutput


class _FakeLlm:
    """complete 返回固定 JSON 的 fake，不触网。"""

    def __init__(self, content: str) -> None:
        self._content = content

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
    ) -> LLMOutput:
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


def test_should_judge() -> None:
    assert should_judge("judge", 1.0, 0.0) is False   # judge 输出不递归 judge
    assert should_judge("reply", 0.1, 0.05) is True
    assert should_judge("reply", 0.1, 0.5) is False


async def test_judge_relevance_returns_score() -> None:
    fake = _FakeLlm('{"score": 4}')
    score, judge_output = await judge_relevance(cast(LlmClient, fake), _output())
    assert score == 4.0
    assert judge_output is not None
    assert judge_output.type == "judge"
    assert judge_output.module == "eval"
    assert judge_output.correlation_id == "corr-1"


async def test_judge_relevance_tolerates_bad_json() -> None:
    for bad in ("[", "[]", '{"score":"abc"}'):
        fake = _FakeLlm(bad)
        score, _ = await judge_relevance(cast(LlmClient, fake), _output())
        assert score == 0.0


async def test_judge_relevance_transport_failure() -> None:
    score, judge_output = await judge_relevance(cast(LlmClient, _BoomLlm()), _output())
    assert score == 0.0
    assert judge_output is None   # 无产出不记账


async def test_judge_relevance_rejects_bool_score() -> None:
    fake = _FakeLlm('{"score": true}')
    score, judge_output = await judge_relevance(cast(LlmClient, fake), _output())
    assert score == 0.0          # float(True)==1.0 的坑：布尔不算数字
    assert judge_output is not None   # 产出仍在，token 照记


async def test_judge_relevance_clamps() -> None:
    cases = [
        ('{"score":100}', 5.0),
        ('{"score":0.5}', 1.0),
        ('{"score":4}', 4.0),
    ]
    for raw, expected in cases:
        fake = _FakeLlm(raw)
        score, _ = await judge_relevance(cast(LlmClient, fake), _output())
        assert score == expected
