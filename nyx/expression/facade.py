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
from nyx.tools.registry import ToolRegistry
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
        tools: ToolRegistry,
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
        # 待用户回应状态：问句（wait_user）与搭话（被忽略回灌）各一组。
        self._waiting_user = False
        self._ask_text = ""
        self._ask_at = 0.0
        self._ask_cid: str | None = None
        self._pending_chat_desire_id: str | None = None
        self._chat_at = 0.0
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
                tools=tools,
            )
        )

    async def reply(self, msg: str, correlation_id: str) -> None:
        """完整回复流程：跑 LangGraph 图，内部发布 think/speak/ask。"""
        # 用户说话 = 回应了之前的问句/搭话；清等待状态（不做「是否真在答」判断）。
        self._waiting_user = False
        self._ask_cid = None
        self._pending_chat_desire_id = None
        state = await self._inner_life.get_state()
        initial: ReplyState = {
            "message": msg,
            "mode": ContextMode.FAST,
            # 朴素回溯最近 max_context_len 条（快通道用，不含当前消息）；
            # 慢通道在 assemble 重截断。
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
            "tool_outputs": [],
        }
        result = await self._graph.ainvoke(initial)
        if result["mode"] is ContextMode.SLOW:
            self._last_slow_at = time.time()
        if result["ask"] is not None:
            self._waiting_user = True
            self._ask_text = result["ask"]
            self._ask_at = time.time()
            self._ask_cid = correlation_id

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
        # 开场白落历史：用户随后回复时能回溯到这句搭话（记忆互通）。
        self._history.append(
            Message(role="nyx", content=output.content, timestamp=time.time())
        )
        # 记「待回应」：用户没回 → check_timeouts 淘汰该互动欲（值回灌）。
        self._pending_chat_desire_id = desire.id
        self._chat_at = time.time()
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

    async def check_timeouts(self, now: float) -> None:
        """超时收尾（tick 心跳直呼）：问句无人答 → 记「用户未回答」；
        搭话被忽略 → 淘汰该互动欲（expire 内值回灌 +0.3）。"""
        if self._waiting_user and now - self._ask_at >= self._config.ask_timeout:
            await self._memory.record_no_answer(self._ask_text, self._ask_cid or "")
            self._waiting_user = False
            self._ask_cid = None
        if (
            self._pending_chat_desire_id is not None
            and now - self._chat_at >= self._config.chat_ignore_timeout
        ):
            await self._desire.expire(self._pending_chat_desire_id)
            self._pending_chat_desire_id = None
