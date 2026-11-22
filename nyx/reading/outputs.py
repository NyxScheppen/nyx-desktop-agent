"""Strict reading outputs and retries scoped to their producing model call."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from nyx.eval.evaluator import Evaluator
from nyx.llm.client import LlmClient
from nyx.llm.validation import validated
from nyx.policy import policy
from nyx.types import LlmMessage


class Output(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", str_strip_whitespace=True)


class NeedEvidence(Exception):
    """A valid output needs actual reads, not another formatting attempt."""

    def __init__(self, indices: list[int], value: dict[str, Any]) -> None:
        self.indices, self.value = indices, value


class SummaryTooLong(ValueError):
    def __init__(self, actual: int, target: int) -> None:
        super().__init__(
            policy("reading", "study.prompts.summary_length", str).format(
                actual=actual, target=target
            )
        )


class Source(Output):
    paragraph_id: str = Field(min_length=1)


class MaterialNode(Output):
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    sources: list[Source] = Field(min_length=1)


class Material(Output):
    summary: str = Field(min_length=1)
    nodes: list[MaterialNode]


class Scope(Output):
    user_requirement: str
    scope: str
    start: int | None
    end: int | None
    feedback: bool


class Upsert(MaterialNode):
    op: Literal["upsert"]
    id: str = Field(min_length=1)
    state: Literal["unassessed", "clarify", "explaining", "connecting"]
    evidence: list[str]
    corrects: list[str]


class Delete(Output):
    op: Literal["delete"]
    id: str = Field(min_length=1)
    replacement: str | None


class Edge(Output):
    from_: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    label: str = Field(min_length=1)


class Graph(Output):
    action: Literal["patch"]
    reason: str = Field(min_length=1)
    operations: list[Upsert | Delete]
    deferred_evidence: list[str]
    edges: list[Edge]
    remove_edges: list[Edge]


class GraphRead(Output):
    action: Literal["read"]
    node_ids: list[str]
    evidence_ids: list[str]
    paragraph_ids: list[str]


class Summary(Output):
    summary: str = Field(min_length=1)
    turn_ids: list[str]


class Memory(Output):
    content: str = Field(min_length=1)
    summary: str = Field(min_length=1)


class Overview(Output):
    summary: str = Field(min_length=1)
    unit_ids: list[str]


class Understanding(Output):
    question: str = Field(min_length=1)
    requirements: list[str]
    carry_requirements: list[str]
    whole_book: bool
    subquestions: list[str] = Field(min_length=1)
    history_query: str | None
    understanding_query: str | None
    promotion_request: str = ""


class EvidenceCheck(Output):
    question: str = Field(min_length=1)
    status: Literal["supported", "missing"]
    sources: list[int]
    gap: str
    direct_sources: list[int]
    speaker: str
    statement_type: Literal[
        "narration", "confession", "accusation", "argument", "other"
    ]
    contradictions: list[str]
    context_gap: str


class EvidenceStep(Output):
    checks: list[EvidenceCheck]
    repair_question: str | None = None


class Search(EvidenceStep):
    action: Literal["search"]
    query: str = Field(min_length=1)
    start: int | None
    end: int | None


class Locate(EvidenceStep):
    action: Literal["locate"]
    query: str = Field(min_length=1)


class Excerpt(EvidenceStep):
    action: Literal["excerpt"]
    start: int = Field(ge=1)
    end: int = Field(ge=1)


class Delegate(EvidenceStep):
    action: Literal["delegate"]
    tasks: list[str] = Field(min_length=1)


class Claim(Output):
    text: str = Field(min_length=1)
    sources: list[int] = Field(min_length=1)
    limitation: str


class Evidence(EvidenceStep):
    action: Literal["finish"]
    summary: str = Field(min_length=1)
    claims: list[Claim]
    gaps: list[str]
    checks: list[EvidenceCheck]


class Reconsider(EvidenceStep):
    action: Literal["understand"]
    reason: str = Field(min_length=1)


EvidenceAction: TypeAlias = Annotated[
    Search | Locate | Excerpt | Delegate | Evidence | Reconsider,
    Field(discriminator="action"),
]


class QuoteSelection(Output):
    index: int = Field(ge=1)
    text: str = Field(min_length=1)


class Answer(Output):
    think: str
    speak: str = Field(min_length=1)
    claims: list[Claim]
    quote_selections: list[QuoteSelection] = Field(default_factory=list[QuoteSelection])


class ConclusionReview(Output):
    text: str
    supported: bool
    excess: str
    contrary: str


class QuestionReview(Output):
    question: str
    status: Literal["answered", "missing", "inaccurate"]


class RepairTask(Output):
    question: str = Field(min_length=1)
    user_requirement: str = Field(min_length=1)
    gap: str = Field(min_length=1)
    task: str = Field(min_length=1)


class InterpretationReview(Output):
    user_text: str = Field(min_length=1)
    verdict: Literal["acceptable", "correct"]
    dimension: Literal["fact", "scope", "cause", "speaker"] | None
    difference: str


class ReviewResult(Output):
    issues: list[str]
    tasks: list[RepairTask]
    interpretations: list[InterpretationReview]
    conclusions: list[ConclusionReview]
    questions: list[QuestionReview]


class Review(ReviewResult):
    next_step: Literal["accept", "answer", "evidence", "understand"]


class PartialReview(ReviewResult):
    next_step: Literal["accept", "answer"]


class NodeReview(Output):
    id: str
    user_supported: bool
    source_supported: bool
    reason: str


class GraphReview(Output):
    valid: bool
    issues: list[str]
    checks: list[NodeReview]


SCHEMAS: dict[str, Any] = {
    "material": Material,
    "scope": Scope,
    "graph": Annotated[Graph | GraphRead, Field(discriminator="action")],
    "summary": Summary,
    "memory": Memory,
    "overview": Overview,
    "understand": Understanding,
    "evidence": EvidenceAction,
    "evidence_finish": Evidence,
    "evidence_repair": Annotated[
        Search | Locate | Excerpt, Field(discriminator="action")
    ],
    "evidence_repair_search": Search,
    "answer": Answer,
    "review": Review,
    "review_partial": PartialReview,
    "graph_review": GraphReview,
}


async def complete(
    llm: LlmClient,
    evaluator: Evaluator,
    kind: str,
    system: str,
    payload: Any,
    correlation: str,
    validate: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    adapter: TypeAdapter[Any] = TypeAdapter(SCHEMAS[kind])
    schema = adapter.json_schema()
    if kind == "overview":
        schema["properties"]["summary"]["maxLength"] = payload["summary_chars"]
    messages: list[LlmMessage] = [
        {
            "role": "system",
            "content": system
            + "\n"
            + policy(
                "reading",
                "study.prompts.output_format",
                str,
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "input": payload,
                    "output_schema": schema,
                },
                ensure_ascii=False,
            ),
        },
    ]

    async def produce(error: str | None) -> dict[str, Any]:
        if error is not None:
            messages.append({"role": "user", "content": error})
        output = await llm.complete(
            messages,
            module="reading",
            output_type="reply"
            if kind == "answer"
            else "study_evidence"
            if kind in ("evidence_finish", "evidence_repair", "evidence_repair_search")
            else "study_review"
            if kind == "review_partial"
            else f"study_{kind}",
            correlation_id=correlation,
            json_mode=True,
        )
        await evaluator.evaluate(output)
        messages.append({"role": "assistant", "content": output.content})
        value = adapter.validate_json(output.content).model_dump(by_alias=True)
        if validate is not None:
            await validate(value)
        return value

    return await validated(produce)
