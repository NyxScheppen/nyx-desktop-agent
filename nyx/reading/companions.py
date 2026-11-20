"""陪读行为执行：碎碎念、提问与记忆联想。"""

import logging
from collections.abc import Awaitable, Callable

from nyx.enums import EventType, ReadingBehavior
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import internal_event
from nyx.expression.classifier import is_question
from nyx.expression.facade import ExpressionFacade
from nyx.expression.prompt import build_system_prompt
from nyx.llm.client import InitiationSkipped, LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.policy import policy
from nyx.types import CurrentState

_QUESTION_USER_PROMPTS: dict[ReadingBehavior, str] = policy(
    "reading", "companions._QUESTION_USER_PROMPTS", dict[ReadingBehavior, str]
)

_ASSOCIATION_SNIPPET_CHARS = policy(
    "reading", "companions._ASSOCIATION_SNIPPET_CHARS", int
)
_READING_PROMPT_MAX_CHARS = policy(
    "reading", "companions._READING_PROMPT_MAX_CHARS", int
)
RecordOutput = Callable[[str, int, str, str], Awaitable[None]]


class ReadingCompanion:
    """执行陪读 LLM 行为，并广播对应事件。"""

    def __init__(
        self,
        llm: LlmClient,
        evaluator: Evaluator,
        bus: EventBus,
        memory: MemoryFacade,
        expression: ExpressionFacade,
        canon: str,
        record_output: RecordOutput,
    ) -> None:
        self._llm = llm
        self._evaluator = evaluator
        self._bus = bus
        self._memory = memory
        self._expression = expression
        self._canon = canon
        self._record_output = record_output
        self._logger = logging.getLogger(__name__)

    async def dispatch(
        self,
        book_id: str,
        paragraph_index: int,
        text: str,
        behaviors: list[ReadingBehavior],
        mutter: bool,
        state: CurrentState,
    ) -> bool:
        """Run each selected behavior without changing trigger semantics."""
        question_succeeded = False
        if mutter:
            await self.mutter(book_id, paragraph_index, text, state)
        for behavior in behaviors:
            if behavior is ReadingBehavior.ASSOCIATE:
                await self.associate(book_id, paragraph_index, text)
            else:
                question_succeeded = (
                    await self.question(book_id, paragraph_index, text, behavior, state)
                    or question_succeeded
                )
        return question_succeeded

    async def mutter(
        self,
        book_id: str,
        paragraph_index: int,
        text: str,
        state: CurrentState,
    ) -> None:
        """Generate and publish one lightweight reading aside."""
        try:
            system = build_system_prompt(self._canon, state)
            user = policy("reading", "companions.templates.mutter_text", str).format(
                p0=text[:_READING_PROMPT_MAX_CHARS]
            )
            output = await self._llm.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                module="reading",
                output_type="reading_mutter",
                correlation_id=book_id,
            )
            await self._evaluator.evaluate(output)
            if not self._llm.can_initiate():
                raise InitiationSkipped
            content = output.content.strip()
            if not content:
                return
            await self._bus.publish(
                internal_event(
                    EventType.READING_MUTTER,
                    {
                        "content": content,
                        "book_id": book_id,
                        "paragraph_index": paragraph_index,
                    },
                    book_id,
                )
            )
            await self._record_output(book_id, paragraph_index, content, "mutter")
        except InitiationSkipped:
            return
        except Exception:
            self._logger.exception(
                policy("reading", "companions.copy.mutter_text", str),
                book_id,
                paragraph_index,
            )

    async def question(
        self,
        book_id: str,
        paragraph_index: int,
        text: str,
        behavior: ReadingBehavior,
        state: CurrentState,
    ) -> bool:
        """Generate and publish one question behavior."""
        committed = False
        try:
            system = build_system_prompt(self._canon, state)
            user = policy("reading", "companions.templates.question_text", str).format(
                p0=text[:_READING_PROMPT_MAX_CHARS], p1=_QUESTION_USER_PROMPTS[behavior]
            )
            output = await self._llm.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                module="reading",
                output_type=behavior.value,
                correlation_id=book_id,
            )
            await self._evaluator.evaluate(output)
            raw = output.content.strip()
            if not raw:
                return False
            if behavior is ReadingBehavior.QUOTE_QUESTION:
                lines = raw.splitlines()
                if len(lines) != 2:
                    return False
                content = lines[0].strip()
                selected_text = lines[1].strip()
                if (
                    not selected_text
                    or selected_text
                    not in text[
                        : policy(
                            "reading",
                            "companions.parameters.question_value_chars_or_items",
                            int,
                        )
                    ]
                ):
                    return False
            else:
                content = raw
                selected_text = None
            if not content or not is_question(content):
                return False
            correlation_id = f"{book_id}:{paragraph_index}"
            await self._expression.commit_reading_question(
                content,
                f"{book_id}:{paragraph_index}",
                correlation_id,
                {
                    "content": content,
                    "subtype": behavior.value,
                    "book_id": book_id,
                    "paragraph_index": paragraph_index,
                    "selected_text": selected_text,
                },
            )
            committed = True
            await self._record_output(book_id, paragraph_index, content, "question")
            return committed
        except InitiationSkipped:
            return False
        except Exception:
            self._logger.exception(
                policy("reading", "companions.parameters.question_value_text", str),
                behavior.value,
                book_id,
                paragraph_index,
            )
            return committed

    async def associate(self, book_id: str, paragraph_index: int, text: str) -> None:
        """Publish up to three memories associated with the paragraph."""
        try:
            memories = await self._memory.search(text)
            for memory in memories[
                : policy(
                    "reading",
                    "companions.parameters.associate_value_chars_or_items",
                    int,
                )
            ]:
                source = memory.summary or memory.content
                snippet = source[:_ASSOCIATION_SNIPPET_CHARS]
                event = internal_event(
                    EventType.READING_ASSOCIATION,
                    {
                        "memory_id": memory.id,
                        "snippet": snippet,
                        "book_id": book_id,
                        "paragraph_index": paragraph_index,
                    },
                    book_id,
                )
                if not self._llm.can_initiate():
                    return
                await self._bus.publish(event)
                self._expression.record_proactive_turn(snippet, event=event)
        except Exception:
            self._logger.exception(
                policy("reading", "companions.copy.associate_text", str),
                book_id,
                paragraph_index,
            )
