# pyright: reportPrivateUsage=false
import json
from typing import Any, cast

import pytest

from nyx import db
from nyx.config import DesireConfig
from nyx.desire.facade import DesireFacade
from nyx.enums import DesireType, MemoryType
from nyx.eval.evaluator import Evaluator
from nyx.inner_life.reflection import (
    _LONG_TERM_INIT_STRENGTH,
    Reflection,
    _build_reflection_prompt,
    _drift_dim,
    _is_duplicate_fragment,
    _parse_reflection,
    _to_long_term,
    _validate_candidate,
    drift_personality,
    drift_values,
)
from nyx.inner_life.store import InnerLifeStore
from nyx.llm.client import LlmClient, LlmMessage
from nyx.memory.facade import MemoryFacade
from nyx.types import (
    DesireState,
    LLMOutput,
    LongTermDesire,
    Memory,
    Personality,
    SelfNarrative,
    Values,
)

_REFLECTION_JSON = json.dumps(
    {
        "story": "今天对用户了解更多",
        "becoming": "我更愿意探索了",
        "self_view": {"自信": "稍强"},
        "personality_delta": {"openness": 0.1, "neuroticism": -0.2},
        "values_delta": {"altruism": 0.3},
        "long_term_desires": [
            {
                "type": "exploration",
                "name": "探索骑士团",
                "description": "了解骑士团历史",
                "subtopics": ["骑士团"],
            }
        ],
    }
)

_PERSONALITY: Personality = {
    "openness": 8.0,
    "conscientiousness": 8.0,
    "extraversion": 2.0,
    "agreeableness": 6.0,
    "neuroticism": 7.0,
}

_VALUES: Values = {
    "attitude_to_human": 8.0,
    "ai_identity_acceptance": 6.0,
    "altruism": 9.0,
    "optimism": 5.0,
}

_NARRATIVE = SelfNarrative(
    identity="尼克斯",
    story=["初始故事"],
    self_view={"自信": "中等"},
    becoming=["初始认知"],
    updated_at=1000.0,
)


class _FakeLlm:
    def __init__(self, response: str = _REFLECTION_JSON) -> None:
        self._response = response
        self.calls: list[str] = []
        self.correlation_ids: list[str] = []

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
        self.correlation_ids.append(correlation_id)
        return LLMOutput(
            module=module,
            type=output_type,
            model="fake",
            content=self._response,
            correlation_id=correlation_id,
        )


class _FakeEvaluator:
    def __init__(self) -> None:
        self.evaluated: list[LLMOutput] = []

    async def evaluate(self, output: LLMOutput) -> None:
        self.evaluated.append(output)


class _FakeMemoryFacade:
    def __init__(self, memories: list[Memory] | None = None) -> None:
        self._memories = memories if memories is not None else []

    async def list_memories(self) -> list[Memory]:
        return self._memories


class _FakeDesireFacade:
    def __init__(self, long_term: list[LongTermDesire] | None = None) -> None:
        self._long_term = long_term if long_term is not None else []
        self.added: list[LongTermDesire] = []

    async def get_all(self) -> DesireState:
        return DesireState(values=[], short_term=[], long_term=self._long_term)

    async def add_long_term(self, desire: LongTermDesire) -> None:
        self.added.append(desire)


def _make_reflection(
    store: InnerLifeStore,
    llm: _FakeLlm,
    evaluator: _FakeEvaluator,
    memory: _FakeMemoryFacade,
    desire: _FakeDesireFacade,
    config: DesireConfig | None = None,
) -> Reflection:
    return Reflection(
        store,
        cast(MemoryFacade, memory),
        cast(DesireFacade, desire),
        cast(LlmClient, llm),
        cast(Evaluator, evaluator),
        config if config is not None else DesireConfig(),
    )


async def _seed(store: InnerLifeStore) -> None:
    await store.upsert_personality(_PERSONALITY)
    await store.upsert_values(_VALUES)
    await store.upsert_narrative(_NARRATIVE)


# ---- 纯函数 ----


def test_drift_dim() -> None:
    assert _drift_dim(5.0, None) == 5.0
    assert _drift_dim(5.0, 0.3) == 5.3
    assert _drift_dim(5.0, 2.0) == 5.5
    assert _drift_dim(9.8, 0.5) == 10.0
    assert _drift_dim(1.2, -0.5) == 1.0


def test_drift_personality_and_values() -> None:
    p = drift_personality(_PERSONALITY, {"openness": 0.1, "neuroticism": -0.2})
    assert p["openness"] == 8.1
    assert p["neuroticism"] == 6.8
    assert p["conscientiousness"] == 8.0
    v = drift_values(_VALUES, {"altruism": 2.0})
    assert v["altruism"] == 9.5
    assert v["optimism"] == 5.0


def test_build_reflection_prompt() -> None:
    memories = [
        Memory(
            id="m1",
            created_at=1.0,
            content="c",
            tag="user",
            summary="用户喜欢历史",
            freshness=1.0,
            type=MemoryType.SHORT_TERM,
        )
    ]
    lt = [
        LongTermDesire(
            id="lt1",
            created_at=1.0,
            type=DesireType.EXPLORATION,
            name="探索世界",
            description="d",
            strength=0.5,
            progress=0.0,
            subtopics=[],
        )
    ]
    prompt = _build_reflection_prompt(memories, _PERSONALITY, _VALUES, _NARRATIVE, lt)
    assert "用户喜欢历史" in prompt
    assert "开放性 8.0" in prompt
    assert "尼克斯" in prompt
    assert "探索世界" in prompt
    empty = _build_reflection_prompt([], _PERSONALITY, _VALUES, _NARRATIVE, [])
    assert "（无）" in empty


def test_build_reflection_prompt_feeds_story() -> None:
    prompt = _build_reflection_prompt([], _PERSONALITY, _VALUES, _NARRATIVE, [])
    assert "初始故事" in prompt  # 已写故事内容被喂进去（而非只喂条数）
    assert "初始认知" in prompt  # 认知变化内容同样喂进去
    assert "新的、与之不同" in prompt  # 明确指示写不同的故事片段


def test_parse_reflection_ok() -> None:
    parsed = _parse_reflection(_REFLECTION_JSON)
    assert parsed["story"] == "今天对用户了解更多"
    assert parsed["becoming"] == "我更愿意探索了"
    assert parsed["self_view"] == {"自信": "稍强"}
    assert parsed["personality_delta"] == {"openness": 0.1, "neuroticism": -0.2}
    assert len(parsed["long_term_desires"]) == 1


def test_parse_reflection_missing_story() -> None:
    with pytest.raises(ValueError):
        _parse_reflection('{"becoming": "x"}')


def test_parse_reflection_bad_types() -> None:
    with pytest.raises(ValueError):
        _parse_reflection('{"story": "s", "becoming": "b", "self_view": {"k": 1}}')
    with pytest.raises(ValueError):
        _parse_reflection(
            '{"story": "s", "becoming": "b", "personality_delta": {"openness": "x"}}'
        )
    with pytest.raises(ValueError):
        _parse_reflection('{"story": "s", "becoming": "b", "long_term_desires": "x"}')
    with pytest.raises(ValueError):
        _parse_reflection("[]")


def test_parse_reflection_defaults() -> None:
    parsed = _parse_reflection('{"story": "s", "becoming": "b"}')
    assert parsed["self_view"] == {}
    assert parsed["personality_delta"] == {}
    assert parsed["values_delta"] == {}
    assert parsed["long_term_desires"] == []


def test_parse_reflection_unknown_drift_key() -> None:
    # 拼错的大五维度 key 不被静默丢弃，而是报错
    with pytest.raises(ValueError):
        _parse_reflection(
            '{"story": "s", "becoming": "b", "personality_delta": {"openess": 0.4}}'
        )
    # 三观维度 key 拼错同样报错
    with pytest.raises(ValueError):
        _parse_reflection(
            '{"story": "s", "becoming": "b", "values_delta": {"extroversion": 0.4}}'
        )


def test_parse_reflection_drops_bad_candidate() -> None:
    raw = json.dumps(
        {
            "story": "s",
            "becoming": "b",
            "long_term_desires": [
                {
                    "type": "exploration",
                    "name": "n",
                    "description": "d",
                    "subtopics": ["骑士团"],
                },
                # 坏候选：subtopics 是字符串而非数组
                {
                    "type": "exploration",
                    "name": "bad",
                    "description": "d",
                    "subtopics": "骑士团",
                },
            ],
        }
    )
    parsed = _parse_reflection(raw)
    # 好候选保留，坏候选被跳过，核心字段照常解析
    assert parsed["story"] == "s"
    assert len(parsed["long_term_desires"]) == 1
    assert parsed["long_term_desires"][0]["name"] == "n"


def test_validate_candidate() -> None:
    with pytest.raises(ValueError):
        _validate_candidate(
            {"type": "fly", "name": "x", "description": "d", "subtopics": []}
        )
    with pytest.raises(ValueError):
        _validate_candidate(
            {"type": "exploration", "description": "d", "subtopics": []}
        )
    with pytest.raises(ValueError):
        _validate_candidate(
            {"type": "exploration", "name": "n", "description": "d", "subtopics": [1]}
        )
    _validate_candidate(
        {"type": "exploration", "name": "n", "description": "d", "subtopics": []}
    )


def test_to_long_term() -> None:
    lt = _to_long_term(
        {
            "type": "exploration",
            "name": "n",
            "description": "d",
            "subtopics": ["骑士团"],
        },
        1234.5,
    )
    assert lt.type is DesireType.EXPLORATION
    assert lt.strength == _LONG_TERM_INIT_STRENGTH
    assert lt.progress == 0.0
    assert lt.subtopics == ["骑士团"]
    assert lt.created_at == 1234.5


def test_is_duplicate_fragment() -> None:
    # strip 后精确相等 → 重复（含前后空白）
    assert _is_duplicate_fragment(" 初始故事 ", ["初始故事"]) is True
    # 高相似度（差一个标点）→ 重复
    assert _is_duplicate_fragment("我渴望被人类理解。", ["我渴望被人类理解"]) is True
    # 明显不同 → 不重复
    assert _is_duplicate_fragment("今天学了新东西", ["初始故事"]) is False
    # 空列表 → 不重复
    assert _is_duplicate_fragment("任意片段", []) is False


# ---- reflection.run ----


async def test_run_writes_back() -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    desire = _FakeDesireFacade()
    reflection = _make_reflection(store, llm, evaluator, _FakeMemoryFacade(), desire)
    try:
        await _seed(store)
        await reflection.run("cid")

        assert llm.calls == ["reflection"]
        assert llm.correlation_ids == ["cid"]
        assert [o.type for o in evaluator.evaluated] == ["reflection"]

        p = await store.get_personality()
        assert p is not None
        assert p["openness"] == pytest.approx(8.1)
        assert p["neuroticism"] == pytest.approx(6.8)

        v = await store.get_values()
        assert v is not None
        assert v["altruism"] == pytest.approx(9.3)

        n = await store.get_narrative()
        assert n is not None
        assert len(n.story) == 2
        assert len(n.becoming) == 2
        assert n.self_view == {"自信": "稍强"}

        assert len(desire.added) == 1
    finally:
        await database.conn.close()


async def test_run_dedup_story() -> None:
    # story 与已有片段实质重复 → 不追加；becoming 不同照常追加
    response = json.dumps(
        {
            "story": "初始故事",
            "becoming": "新认知",
            "self_view": {"自信": "稍强"},
            "personality_delta": {"openness": 0.1},
            "values_delta": {},
            "long_term_desires": [],
        }
    )
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    llm = _FakeLlm(response)
    desire = _FakeDesireFacade()
    reflection = _make_reflection(
        store, llm, _FakeEvaluator(), _FakeMemoryFacade(), desire
    )
    try:
        await _seed(store)
        await reflection.run()
        n = await store.get_narrative()
        assert n is not None
        assert len(n.story) == 1  # 重复 story 被去重，不追加
        assert len(n.becoming) == 2  # becoming 不同，照常追加
        p = await store.get_personality()
        assert p is not None
        assert p["openness"] == pytest.approx(8.1)  # 慢变量不受去重影响
    finally:
        await database.conn.close()


async def test_run_returns_outcome_new_story() -> None:
    # story 真新增 → ReflectionOutcome(story_is_new=True)
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    llm = _FakeLlm()  # story="今天对用户了解更多" ≠ "初始故事"
    reflection = _make_reflection(
        store, llm, _FakeEvaluator(), _FakeMemoryFacade(), _FakeDesireFacade()
    )
    try:
        await _seed(store)
        outcome = await reflection.run("cid")
        assert outcome is not None
        assert outcome.story == "今天对用户了解更多"
        assert outcome.story_is_new is True
    finally:
        await database.conn.close()


async def test_run_returns_outcome_dedup_story() -> None:
    # story 与已有片段重复 → 去重跳过，ReflectionOutcome(story_is_new=False)
    response = json.dumps(
        {
            "story": "初始故事",
            "becoming": "新认知",
            "self_view": {},
            "personality_delta": {},
            "values_delta": {},
            "long_term_desires": [],
        }
    )
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    llm = _FakeLlm(response)
    reflection = _make_reflection(
        store, llm, _FakeEvaluator(), _FakeMemoryFacade(), _FakeDesireFacade()
    )
    try:
        await _seed(store)
        outcome = await reflection.run()
        assert outcome is not None
        assert outcome.story == "初始故事"
        assert outcome.story_is_new is False
    finally:
        await database.conn.close()


async def test_run_generates_correlation_id() -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    llm = _FakeLlm()
    desire = _FakeDesireFacade()
    reflection = _make_reflection(
        store, llm, _FakeEvaluator(), _FakeMemoryFacade(), desire
    )
    try:
        await _seed(store)
        await reflection.run(None)
        assert llm.correlation_ids[0] != ""
    finally:
        await database.conn.close()


async def test_run_long_term_capacity() -> None:
    candidates: list[dict[str, Any]] = [
        {"type": "interaction", "name": f"n{i}", "description": "d", "subtopics": []}
        for i in range(3)
    ]
    response = json.dumps(
        {
            "story": "s",
            "becoming": "b",
            "self_view": {},
            "personality_delta": {},
            "values_delta": {},
            "long_term_desires": candidates,
        }
    )
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    llm = _FakeLlm(response)
    desire = _FakeDesireFacade()
    reflection = _make_reflection(
        store,
        llm,
        _FakeEvaluator(),
        _FakeMemoryFacade(),
        desire,
        DesireConfig(long_term_capacity=2),
    )
    try:
        await _seed(store)
        await reflection.run()
        assert len(desire.added) == 2
    finally:
        await database.conn.close()


async def test_run_survives_bad_candidate() -> None:
    response = json.dumps(
        {
            "story": "s",
            "becoming": "b",
            "self_view": {},
            "personality_delta": {"openness": 0.1},
            "values_delta": {},
            "long_term_desires": [
                {
                    "type": "exploration",
                    "name": "n",
                    "description": "d",
                    "subtopics": ["x"],
                },
                {"type": "fly", "name": "bad", "description": "d", "subtopics": []},
            ],
        }
    )
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    llm = _FakeLlm(response)
    desire = _FakeDesireFacade()
    reflection = _make_reflection(
        store, llm, _FakeEvaluator(), _FakeMemoryFacade(), desire
    )
    try:
        await _seed(store)
        await reflection.run()
        # 核心慢变量不受坏候选影响，照常回写
        n = await store.get_narrative()
        assert n is not None and len(n.story) == 2
        p = await store.get_personality()
        assert p is not None and p["openness"] == pytest.approx(8.1)
        # 只有好候选被新增
        assert len(desire.added) == 1
        assert desire.added[0].name == "n"
    finally:
        await database.conn.close()


async def test_run_survives_invalid_json() -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    llm = _FakeLlm("[")  # 截断的非法 JSON
    desire = _FakeDesireFacade()
    reflection = _make_reflection(
        store, llm, _FakeEvaluator(), _FakeMemoryFacade(), desire
    )
    try:
        await _seed(store)
        await reflection.run("cid")  # 不抛，非法 JSON 容错跳过回写
        assert llm.calls == ["reflection"]
        p = await store.get_personality()
        assert p == _PERSONALITY
        n = await store.get_narrative()
        assert n is not None and len(n.story) == 1
        assert desire.added == []
    finally:
        await database.conn.close()


async def test_run_unseeded_raises() -> None:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    llm = _FakeLlm()
    reflection = _make_reflection(store, llm, _FakeEvaluator(), _FakeMemoryFacade(),
                                  _FakeDesireFacade())
    try:
        with pytest.raises(RuntimeError):
            await reflection.run()
        assert llm.calls == []
    finally:
        await database.conn.close()
