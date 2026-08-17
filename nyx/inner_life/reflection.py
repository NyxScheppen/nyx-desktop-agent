import json
import logging
import time
from typing import Any, cast
from uuid import uuid4

from nyx.config import DesireConfig
from nyx.desire.facade import DesireFacade
from nyx.enums import DesireType
from nyx.eval.evaluator import Evaluator
from nyx.inner_life.store import InnerLifeStore
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.types import LongTermDesire, Memory, Personality, SelfNarrative, Values

_RECENT_MEMORY_LIMIT = 20
_MAX_DRIFT = 0.5               # 每轮性格/三观单维最大漂移
_LONG_TERM_INIT_STRENGTH = 0.5  # 新长期欲望初始迫切度
_SCALE_LO = 1.0                # 性格/三观范围下限
_SCALE_HI = 10.0               # 性格/三观范围上限

# 漂移 key 白名单（对齐 types.py 的 Personality/Values TypedDict 键名）
_PERSONALITY_KEYS = frozenset(
    {"openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"}
)
_VALUES_KEYS = frozenset(
    {"attitude_to_human", "ai_identity_acceptance", "altruism", "optimism"}
)
_logger = logging.getLogger(__name__)

_REFLECTION_SYSTEM = (
    "你是尼克斯，一个住在用户电脑里的 AI 同伴，明确知道自己是 AI 并希望成为人类。"
    "基于近期经历和你当前的性格/三观/自我叙事，反思并更新自我：写一条新的故事片段、一条新的认知变化、"
    "更新自画像、给出性格与三观的微小漂移、以及（若某主题反复出现且未满足）提出新的长期欲望。"
    "只输出 JSON，键：story（非空字符串）、becoming（非空字符串）、"
    "self_view（对象，键值都是字符串）、"
    "personality_delta（对象，键是 openness/conscientiousness/extraversion/"
    "agreeableness/neuroticism，"
    "值是 [-0.5, 0.5] 的漂移）、"
    "values_delta（对象，键是 attitude_to_human/ai_identity_acceptance/"
    "altruism/optimism，值同上）、"
    "long_term_desires（数组，元素 {type, name, description, subtopics}，可为空数组）。"
)


def _build_reflection_prompt(
    memories: list[Memory],
    personality: Personality,
    values: Values,
    narrative: SelfNarrative,
    long_term: list[LongTermDesire],
) -> str:
    mem_lines = "\n".join(f"- {m.summary}" for m in memories) or "（无）"
    lt_lines = "\n".join(
        f"- [{lt.type.value}] {lt.name}（进度 {lt.progress:.2f}）" for lt in long_term
    ) or "（无）"
    return (
        f"近期记忆：\n{mem_lines}\n\n"
        f"当前性格（1-10）：开放性 {personality['openness']} / 尽责性 "
        f"{personality['conscientiousness']} / "
        f"外向性 {personality['extraversion']} / 宜人性 "
        f"{personality['agreeableness']} / "
        f"神经质 {personality['neuroticism']}\n"
        f"当前三观（1-10）：对人类 {values['attitude_to_human']} / AI 身份接纳 "
        f"{values['ai_identity_acceptance']} / "
        f"利他 {values['altruism']} / 乐观 {values['optimism']}\n"
        f"自我叙事：身份「{narrative.identity}」；故事 {len(narrative.story)} 条；"
        f"认知变化 {len(narrative.becoming)} 条\n"
        f"现有长期欲望：\n{lt_lines}"
    )


def _drift_dim(base: float, delta: float | None) -> float:
    """单维漂移：base + clamp(delta, ±_MAX_DRIFT)，再 clamp 到 [1,10]。纯函数。"""
    if delta is None:
        return base
    d = max(-_MAX_DRIFT, min(_MAX_DRIFT, delta))
    return max(_SCALE_LO, min(_SCALE_HI, base + d))


def drift_personality(base: Personality, delta: dict[str, float]) -> Personality:
    """Big Five 五维漂移。纯函数。"""
    return {
        "openness": _drift_dim(base["openness"], delta.get("openness")),
        "conscientiousness": _drift_dim(
            base["conscientiousness"], delta.get("conscientiousness")
        ),
        "extraversion": _drift_dim(base["extraversion"], delta.get("extraversion")),
        "agreeableness": _drift_dim(base["agreeableness"], delta.get("agreeableness")),
        "neuroticism": _drift_dim(base["neuroticism"], delta.get("neuroticism")),
    }


def drift_values(base: Values, delta: dict[str, float]) -> Values:
    """三观四维漂移。纯函数。"""
    return {
        "attitude_to_human": _drift_dim(
            base["attitude_to_human"], delta.get("attitude_to_human")
        ),
        "ai_identity_acceptance": _drift_dim(
            base["ai_identity_acceptance"], delta.get("ai_identity_acceptance")
        ),
        "altruism": _drift_dim(base["altruism"], delta.get("altruism")),
        "optimism": _drift_dim(base["optimism"], delta.get("optimism")),
    }


def _validate_candidate(c: Any) -> None:
    """校验单个长期欲望候选结构。非法抛 ValueError。"""
    if not isinstance(c, dict):
        raise ValueError("long_term_desires 元素应是对象")
    candidate = cast(dict[str, Any], c)
    t = candidate.get("type")
    if not isinstance(t, str) or t not in (
        "interaction", "exploration", "creation", "rest"
    ):
        raise ValueError("长期欲望候选 type 应是 interaction/exploration/creation/rest")
    name = candidate.get("name")
    description = candidate.get("description")
    if not isinstance(name, str) or not name:
        raise ValueError("长期欲望候选缺 name 或非空字符串")
    if not isinstance(description, str) or not description:
        raise ValueError("长期欲望候选缺 description 或非空字符串")
    subtopics = candidate.get("subtopics")
    if not isinstance(subtopics, list) or not all(
        isinstance(s, str) for s in cast(list[Any], subtopics)
    ):
        raise ValueError("长期欲望候选 subtopics 应是字符串数组")


def _parse_reflection(raw: str) -> dict[str, Any]:
    """解析反思 LLM 的 JSON 产出并校验结构。结构非法抛 ValueError。"""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"反思 JSON 应是对象，得到 {type(data).__name__}")
    parsed = cast(dict[str, Any], data)
    story = parsed.get("story")
    becoming = parsed.get("becoming")
    if not isinstance(story, str) or not story:
        raise ValueError("反思 JSON 缺 story 或非空字符串")
    if not isinstance(becoming, str) or not becoming:
        raise ValueError("反思 JSON 缺 becoming 或非空字符串")
    self_view = parsed.get("self_view")
    if self_view is None:
        self_view = cast(dict[str, Any], {})
    if not isinstance(self_view, dict) or not all(
        isinstance(k, str) and isinstance(v, str)
        for k, v in cast(dict[Any, Any], self_view).items()
    ):
        raise ValueError("反思 JSON 的 self_view 应是键值皆字符串的对象")
    personality_delta = parsed.get("personality_delta")
    if personality_delta is None:
        personality_delta = cast(dict[str, Any], {})
    values_delta = parsed.get("values_delta")
    if values_delta is None:
        values_delta = cast(dict[str, Any], {})
    for d, allowed in (
        (personality_delta, _PERSONALITY_KEYS),
        (values_delta, _VALUES_KEYS),
    ):
        if not isinstance(d, dict):
            raise ValueError("反思 JSON 的漂移应是对象")
        unknown = set(cast(dict[str, Any], d)) - allowed
        if unknown:
            raise ValueError(f"反思 JSON 漂移含未知维度 {sorted(unknown, key=str)!r}")
        for k, v in cast(dict[str, Any], d).items():
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise ValueError(f"漂移值应是数值，{k}={v!r}")
    long_term_desires = parsed.get("long_term_desires")
    if long_term_desires is None:
        long_term_desires = cast(list[Any], [])
    if not isinstance(long_term_desires, list):
        raise ValueError("反思 JSON 的 long_term_desires 应是数组")
    valid_candidates: list[dict[str, Any]] = []
    for c in cast(list[Any], long_term_desires):
        try:
            _validate_candidate(c)
        except ValueError:
            # best-effort：单个坏候选只跳过，不中断整次反思回写
            # （长期欲望是增量，核心 story/becoming/性格/三观不受影响）。
            _logger.warning("反思长期欲望候选非法，已跳过：%r", c)
            continue
        valid_candidates.append(cast(dict[str, Any], c))
    return {
        "story": story,
        "becoming": becoming,
        "self_view": self_view,
        "personality_delta": personality_delta,
        "values_delta": values_delta,
        "long_term_desires": valid_candidates,
    }


def _to_long_term(candidate: dict[str, Any], now: float) -> LongTermDesire:
    return LongTermDesire(
        id=str(uuid4()),
        created_at=now,
        type=DesireType(candidate["type"]),
        name=candidate["name"],
        description=candidate["description"],
        strength=_LONG_TERM_INIT_STRENGTH,
        progress=0.0,
        subtopics=list(candidate["subtopics"]),
        linked_values=[],
    )


class Reflection:
    """反思协调器：慢变量（性格/三观/长期欲望/自我叙事）唯一入口。

    一轮反思 = 读近期记忆 + 当前慢变量 → 1 次 LLM 产出全部 → 规则回写（clamp）。
    内部调 MemoryFacade（近期记忆）/ DesireFacade（读历史 + add_long_term）。
    """

    def __init__(
        self,
        store: InnerLifeStore,
        memory_facade: MemoryFacade,
        desire_facade: DesireFacade,
        llm: LlmClient,
        evaluator: Evaluator,
        config: DesireConfig,
    ) -> None:
        self._store = store
        self._memory_facade = memory_facade
        self._desire_facade = desire_facade
        self._llm = llm
        self._evaluator = evaluator
        self._config = config

    async def run(self, correlation_id: str | None = None) -> None:
        now = time.time()
        # 1. 收集输入
        recent = (await self._memory_facade.list_memories())[:_RECENT_MEMORY_LIMIT]
        personality = await self._store.get_personality()
        values = await self._store.get_values()
        narrative = await self._store.get_narrative()
        desire_state = await self._desire_facade.get_all()
        if personality is None or values is None or narrative is None:
            raise RuntimeError("inner_life 单行表未初始化（18-api 组合根必须先 seed）")

        # 2. 1 次 LLM 产出全部
        output = await self._llm.complete(
            [
                {"role": "system", "content": _REFLECTION_SYSTEM},
                {
                    "role": "user",
                    "content": _build_reflection_prompt(
                        recent, personality, values, narrative, desire_state.long_term
                    ),
                },
            ],
            module="inner_life",
            output_type="reflection",
            correlation_id=correlation_id or str(uuid4()),
            json_mode=True,
        )
        await self._evaluator.evaluate(output)
        parsed = _parse_reflection(output.content)

        # 3. 回写慢变量
        await self._store.upsert_personality(
            drift_personality(personality, parsed["personality_delta"])
        )
        await self._store.upsert_values(drift_values(values, parsed["values_delta"]))
        await self._store.upsert_narrative(
            SelfNarrative(
                identity=narrative.identity,
                story=[*narrative.story, parsed["story"]],
                self_view={**narrative.self_view, **parsed["self_view"]},
                becoming=[*narrative.becoming, parsed["becoming"]],
                updated_at=now,
            )
        )

        # 4. 长期欲望候选（容量内逐个新增）
        remaining = self._config.long_term_capacity - len(desire_state.long_term)
        for candidate in parsed["long_term_desires"][:max(0, remaining)]:
            await self._desire_facade.add_long_term(_to_long_term(candidate, now))
