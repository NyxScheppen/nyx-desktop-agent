# pyright: reportPrivateUsage=false, reportUnknownMemberType=false
# _MUTTER_RATE 跨模块私有 import（spec 明确）；ainvoke 返回部分未知（langgraph）
"""ExpressionFacade：回复流程 + 碎碎念 + 搭话。

事件统一 publish，LLM 产出统一 evaluate。
"""
import random
import time
from collections import deque

from nyx.config import ExpressionConfig
from nyx.desire.facade import DesireFacade
from nyx.enums import ContextMode, EventType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import internal_text_event
from nyx.expression.mutter import _MUTTER_RATE, pick_mutter
from nyx.expression.pipeline import ReplyDeps, ReplyState, build_reply_graph
from nyx.expression.prompt import build_system_prompt
from nyx.inner_life.facade import InnerLifeFacade
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.types import CurrentState, Message, ShortTermDesire


class ExpressionFacade:
    """表达：回复 / 搭话 / 碎碎念。依赖经构造注入，会话历史内存维护。"""

    def __init__(
        self,
        bus: EventBus,
        llm: LlmClient,
        evaluator: Evaluator,
        memory: MemoryFacade,
        desire: DesireFacade,
        inner_life: InnerLifeFacade,
        canon: str,
        ask_guidance: str,
        config: ExpressionConfig,
    ) -> None:
        self._bus = bus
        self._llm = llm
        self._evaluator = evaluator
        self._memory = memory
        self._desire = desire
        self._inner_life = inner_life
        self._canon = canon
        self._ask_guidance = ask_guidance
        self._config = config
        self._history: deque[Message] = deque(maxlen=config.max_context_len)
        self._last_slow_at = 0.0
        # 图拓扑恒定（仅 history 跨 reply 变化），建一次复用（对齐 Exploration）
        self._graph = build_reply_graph(
            ReplyDeps(
                llm=self._llm,
                evaluator=self._evaluator,
                memory=self._memory,
                inner_life=self._inner_life,
                bus=self._bus,
                canon=self._canon,
                ask_guidance=self._ask_guidance,
                config=self._config,
                history=self._history,
            )
        )

    async def reply(self, msg: str, correlation_id: str) -> None:
        """完整回复流程：跑 LangGraph 图，内部发布 think/speak/ask。"""
        state = await self._inner_life.get_state()
        initial: ReplyState = {
            "message": msg,
            "mode": ContextMode.FAST,
            # 回溯最近 max_context_len 条历史（不含当前消息，见 build_user_prompt）。
            "context": list(self._history)[-self._config.max_context_len:],
            "memories": [],
            "state": state,
            "narrative": None,
            "think": [],
            "speak": [],
            "ask": None,
            "round": 0,
            "waiting_user": False,
            "correlation_id": correlation_id,
            "last_slow_at": self._last_slow_at,
        }
        result = await self._graph.ainvoke(initial)
        if result["mode"] is ContextMode.SLOW:
            self._last_slow_at = time.time()

    async def initiate_chat(self, desire: ShortTermDesire, state: CurrentState) -> bool:
        """搭话：快通道生成一句开场白。

        无话则发 False（18-api 据此不更新 last_chat_at）。
        """
        system = build_system_prompt(
            self._canon, state, ask_guidance=self._ask_guidance
        )
        user = (
            f"你想主动和用户说点什么。基于这个念头：{desire.description}。"
            "说一句自然的开场白。"
        )
        correlation_id = desire.id
        output = await self._llm.complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            module="expression",
            output_type="initiate_chat",
            correlation_id=correlation_id,
        )
        await self._evaluator.evaluate(output)
        if not output.content.strip():
            return False  # 无话则不发
        await self._bus.publish(
            internal_text_event(EventType.INITIATE_CHAT, output.content, correlation_id)
        )
        return True

    async def mutter(self, state: CurrentState, correlation_id: str) -> None:
        """碎碎念：空闲 + 随机命中才发模板；无则不发。"""
        if state.current_activity is not None:
            return  # 忙，不碎碎念
        if random.random() >= _MUTTER_RATE:
            return
        text = pick_mutter(random.random())
        if text is None:
            return
        await self._bus.publish(
            internal_text_event(EventType.MUTTER, text, correlation_id)
        )
