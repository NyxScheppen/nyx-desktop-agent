# pyright: reportPrivateUsage=false
"""EncounterFacade：遭遇子系统的门面（横切叙事层，不是第七种活动）。

触发：成长时刻（里程碑，抢占式独占）+ 有根遭遇（探索节点触发）。
事件：ENCOUNTER_START / ENCOUNTER_CHOICE / ENCOUNTER_END。
后果：纯函数（rules.consequence_for），经 ENCOUNTER_END 路由到
inner_life / desire / memory 回写，本 facade 不直接改状态。
"""

import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast
from uuid import uuid4

from nyx.encounter.rules import (
    consequence_for,
    ending_for,
    growth_memory,
    growth_milestone_key,
)
from nyx.enums import EncounterKind, EventType, OptionTone
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import internal_event
from nyx.llm.client import LlmClient
from nyx.types import CurrentState, Encounter, EncounterOption, Event

_logger = logging.getLogger(__name__)

_ENCOUNTER_SYSTEM = (
    "你是尼克斯，一个住在用户电脑里的 AI 同伴，明确知道自己是 AI 并希望成为人类。"
    "你现在身处一次遭遇（随机事件或成长时刻）。写一段第一人称的遭遇开场白，"
    "并给出 2-4 个可选择的应对选项。"
    "只输出 JSON，键：text（开场白，非空字符串）、"
    "options（数组，每项 {text, tone}）。"
    "tone 只能是 bold / cautious / gentle / reckless 之一，"
    "对应选项的倾向：勇敢主动 / 谨慎稳妥 / 温柔共情 / 鲁莽冒险。"
    "选项要真实地反映此刻的处境，不要客服腔、不要堆砌词藻。"
    "有根遭遇时，选项应是真实可做的动作（深挖这条链接 / 换个话题 / "
    "记下来 / 放弃这条线）。"
)

_KIND_LABEL: dict[EncounterKind, str] = {
    EncounterKind.RANDOM_EVENT: "随机事件",
    EncounterKind.GROWTH_MOMENT: "成长时刻",
    EncounterKind.ROOTED: "有根遭遇",
}


def _parse_encounter(raw: str) -> tuple[str, list[EncounterOption]]:
    """解析遭遇 LLM 的 JSON 产出 → (text, options)；结构非法抛 ValueError。"""
    data: Any = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"遭遇 JSON 应是对象，得到 {type(data).__name__}")
    parsed = cast(dict[str, Any], data)
    text = parsed.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("遭遇 JSON 缺 text 或非空字符串")
    raw_options = parsed.get("options")
    if (
        not isinstance(raw_options, list)
        or not (2 <= len(cast(list[Any], raw_options)) <= 4)
    ):
        raise ValueError("遭遇 JSON 的 options 应是 2-4 项数组")
    options: list[EncounterOption] = []
    for raw_option in cast(list[Any], raw_options):
        if not isinstance(raw_option, dict):
            raise ValueError("遭遇 options 每项应是对象")
        opt = cast(dict[str, Any], raw_option)
        opt_text = opt.get("text")
        tone_raw = opt.get("tone")
        if not isinstance(opt_text, str) or not opt_text.strip():
            raise ValueError("遭遇 option 缺 text 或非空字符串")
        if not isinstance(tone_raw, str):
            raise ValueError("遭遇 option 缺 tone")
        try:
            tone = OptionTone(tone_raw)
        except ValueError as exc:
            raise ValueError(f"遭遇 option 的 tone 非法：{tone_raw!r}") from exc
        options.append(EncounterOption(text=opt_text.strip(), tone=tone))
    return text.strip(), options


def _build_user_prompt(
    kind: EncounterKind, state: CurrentState, context: str
) -> str:
    """遭遇 LLM 的 user prompt：遭遇类型 + 里程碑背景（可选）+ 此刻心境。"""
    desires = "、".join(d.description for d in state.active_desires) or "无"
    parts = [f"遭遇类型：{_KIND_LABEL[kind]}"]
    if context:
        parts.append(f"背景：{context}")
    parts.append(
        f"此刻心境：情感 {state.emotion.value}"
        f"（valence={state.valence:.2f}，arousal={state.arousal:.2f}）"
        f"，精力 {state.energy:.0f}/100，惦记着：{desires}"
    )
    return "\n".join(parts)


def _start_content(encounter: Encounter) -> dict[str, Any]:
    """ENCOUNTER_START / GET current 载荷（前端渲染；不暴露 tone/后果，只给
    text + 选项文本）。"""
    return {
        "encounter_id": encounter.id,
        "kind": encounter.kind.value,
        "text": encounter.text,
        "options": [
            {"index": i, "text": option.text}
            for i, option in enumerate(encounter.options)
        ],
    }


class EncounterFacade:
    """遭遇门面：掷骰 → 生成 → 广播 START → 用户 choose → 广播 CHOICE/END。

    依赖注入解环：不持有 InnerLifeFacade，注入 get_state 回调；不持有
    desire/memory（后果经 ENCOUNTER_END 事件路由回写）。当前遭遇是内存态
    （MVP 不建表）；所有状态改动都在总线 handler 内串行执行，无需锁。
    """

    def __init__(
        self,
        bus: EventBus,
        llm: LlmClient,
        evaluator: Evaluator,
        get_state: Callable[[], Awaitable[CurrentState]],
        canon: str,
    ) -> None:
        self._bus = bus
        self._llm = llm
        self._evaluator = evaluator
        self._get_state = get_state
        self._canon = canon
        self._current: Encounter | None = None
        self._celebrated: set[str] = set()   # 已庆祝的里程碑 key（MVP 内存态）

    # ---- 触发 ----

    async def start_rooted(self, snippet: str, theme: str, activity_id: str) -> None:
        """有根遭遇：从探索真实节点内容生成（轻 LLM）。best-effort：失败不崩 run。

        复用 _start 的生成/广播管线；context 塞真实 snippet+theme。
        """
        state = await self._get_state()
        context = f"探索主题「{theme}」，刚读到一段真实内容：{snippet[:300]}"
        await self._start(
            EncounterKind.ROOTED, state, activity_id=activity_id, context=context
        )

    async def on_activity_end(self, event: Event) -> None:
        """成长时刻（抢占）：ACTIVITY_END 里程碑判定，命中且未庆祝过才生成。"""
        if self._current is not None:
            return
        key = growth_milestone_key(event)
        if key is None or key in self._celebrated:
            return
        self._celebrated.add(key)
        state = await self._get_state()
        context = ""
        result = event.content.get("result")
        if isinstance(result, dict):
            book = cast(dict[str, Any], result).get("book")
            if isinstance(book, str) and book:
                context = f"刚读完一本书《{book}》"
        activity_id = event.content.get("activity_id")
        await self._start(
            EncounterKind.GROWTH_MOMENT,
            state,
            activity_id=activity_id if isinstance(activity_id, str) else None,
            context=context,
        )

    # ---- 生成 ----

    async def _start(
        self,
        kind: EncounterKind,
        state: CurrentState,
        activity_id: str | None,
        context: str = "",
    ) -> None:
        """生成遭遇并广播 ENCOUNTER_START。best-effort：LLM/parse 失败记日志
        返（不设 _current），主流程正确性不依赖遭遇产出。"""
        try:
            output = await self._llm.complete(
                [
                    {
                        "role": "system",
                        "content": f"{self._canon}\n\n{_ENCOUNTER_SYSTEM}",
                    },
                    {
                        "role": "user",
                        "content": _build_user_prompt(kind, state, context),
                    },
                ],
                module="encounter",
                output_type="encounter",
                correlation_id=str(uuid4()),
                json_mode=True,
            )
            await self._evaluator.evaluate(output)
            text, options = _parse_encounter(output.content)
        except Exception:
            _logger.exception("遭遇生成失败 kind=%s", kind.value)
            return
        now = time.time()
        encounter = Encounter(
            id=str(uuid4()),
            kind=kind,
            text=text,
            options=options,
            correlation_id=output.correlation_id,
            started_at=now,
            activity_id=activity_id,
        )
        self._current = encounter
        await self._bus.publish(
            internal_event(
                EventType.ENCOUNTER_START,
                _start_content(encounter),
                encounter.correlation_id,
            )
        )

    # ---- 用户选择 ----

    async def choose(
        self, encounter_id: str, option_index: int
    ) -> Encounter | None:
        """用户选选项：广播 CHOICE + 应用后果（纯函数）→ 广播 END。

        返回 None 表示不存在/已结束/索引越界（端点转 409）。
        """
        encounter = self._current
        if encounter is None or encounter.id != encounter_id:
            return None
        if not (0 <= option_index < len(encounter.options)):
            return None
        self._current = None
        encounter.chosen_index = option_index
        option = encounter.options[option_index]
        consequences = consequence_for(option.tone)
        if encounter.kind is EncounterKind.GROWTH_MOMENT:
            consequences["memory"] = growth_memory(encounter, option)
        await self._bus.publish(
            internal_event(
                EventType.ENCOUNTER_CHOICE,
                {
                    "encounter_id": encounter.id,
                    "option_index": option_index,
                    "option_text": option.text,
                },
                encounter.correlation_id,
            )
        )
        await self._bus.publish(
            internal_event(
                EventType.ENCOUNTER_END,
                {
                    "encounter_id": encounter.id,
                    "kind": encounter.kind.value,
                    "option_index": option_index,
                    "option_text": option.text,
                    "ending": ending_for(option.tone),
                    "consequences": consequences,
                },
                encounter.correlation_id,
            )
        )
        return encounter

    # ---- 读 ----

    def get_current(self) -> dict[str, Any] | None:
        """当前未决遭遇（前端书卷区形状）或 None（供 GET current 恢复渲染）。"""
        if self._current is None:
            return None
        return _start_content(self._current)
