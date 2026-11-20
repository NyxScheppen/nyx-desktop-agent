"""Evidence-backed, transactional revisions of the single inner document."""

import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any, cast
from uuid import uuid4

from nyx.config import DesireConfig
from nyx.desire.facade import DesireFacade, LongTermSnapshot
from nyx.enums import DesireType
from nyx.eval.evaluator import Evaluator
from nyx.inner_life.document import SECTIONS, render_document, revise_document
from nyx.inner_life.store import InnerLifeStore
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.policy import policy
from nyx.types import InnerDocument, LongTermDesire, Memory, ReflectionOutcome

_RECENT_MEMORY_LIMIT = policy("inner_life", "reflection._RECENT_MEMORY_LIMIT", int)
_CREATION_REFLECTION_DELTA = policy(
    "inner_life", "reflection._CREATION_REFLECTION_DELTA", float
)
_TRIGGER_EVIDENCE_LIMIT = policy(
    "inner_life", "reflection._TRIGGER_EVIDENCE_LIMIT", int
)
_TRIGGER_ITEM_LIMIT = policy("inner_life", "reflection._TRIGGER_ITEM_LIMIT", int)
_TRIGGER_REASON_LABELS = policy(
    "inner_life", "reflection._TRIGGER_REASON_LABELS", dict[str, str]
)
_REFLECTION_SYSTEM = policy("inner_life", "reflection._REFLECTION_SYSTEM", str)


def _build_trigger_context(trigger: dict[str, Any] | None) -> str:
    """Render bounded trigger evidence for the reflection prompt."""
    content = trigger or {}
    reason = content.get("reason", "manual")
    if not isinstance(reason, str) or reason not in _TRIGGER_REASON_LABELS:
        raise ValueError(
            policy(
                "inner_life", "reflection.templates.build_trigger_context_text_2", str
            ).format(p0=reason)
        )
    evidence = content.get("evidence")
    lines: list[str] = []
    if isinstance(evidence, str):
        text = evidence.strip()
        if text:
            lines.append(text[:_TRIGGER_EVIDENCE_LIMIT])
    elif isinstance(evidence, list):
        for item in cast(list[Any], evidence)[:_TRIGGER_ITEM_LIMIT]:
            if not isinstance(item, dict):
                continue
            item_dict = cast(dict[str, Any], item)
            summary = item_dict.get("summary")
            if not isinstance(summary, str) or not summary.strip():
                continue
            source_id = item_dict.get("memory_id") or item_dict.get("source_id")
            prefix = f"[{source_id}] " if isinstance(source_id, str) else ""
            lines.append(f"{prefix}{summary.strip()}"[:_TRIGGER_EVIDENCE_LIMIT])
    elif evidence is not None:
        raise ValueError(
            policy("inner_life", "reflection.copy.build_trigger_context_text_2", str)
        )
    evidence_text = "\n".join(f"- {line}" for line in lines) or policy(
        "inner_life", "reflection.copy.build_trigger_context_text", str
    )
    return policy(
        "inner_life", "reflection.templates.build_trigger_context_text", str
    ).format(p0=_TRIGGER_REASON_LABELS[reason], p1=evidence_text)


def _parse_reflection(raw: str) -> dict[str, Any]:
    def reject(value: str) -> None:
        raise ValueError(
            policy("inner_life", "reflection.templates.reject_text", str).format(
                p0=value
            )
        )

    data: Any = json.loads(raw, parse_constant=reject)
    if not isinstance(data, dict):
        raise ValueError(
            policy("inner_life", "reflection.copy.parse_reflection_text", str)
        )
    result = cast(dict[str, Any], data)
    summary = result.get("summary")
    if (
        not isinstance(summary, str)
        or not summary.strip()
        or len(summary)
        > policy(
            "inner_life", "reflection.parameters.parse_reflection_value_limit", int
        )
    ):
        raise ValueError(
            policy("inner_life", "reflection.copy.parse_reflection_text_2", str)
        )
    if not isinstance(result.get("patches"), list):
        raise ValueError(
            policy("inner_life", "reflection.copy.parse_reflection_text_3", str)
        )
    candidates = result.get("long_term_desires", [])
    if not isinstance(candidates, list) or len(cast(list[Any], candidates)) > policy(
        "inner_life", "reflection.parameters.parse_reflection_value_limit_2", int
    ):
        raise ValueError(
            policy("inner_life", "reflection.copy.parse_reflection_text_4", str)
        )
    accepted: list[dict[str, Any]] = []
    for raw_candidate in cast(list[Any], candidates):
        if _valid_candidate(raw_candidate):
            accepted.append(cast(dict[str, Any], raw_candidate))
        else:
            logging.getLogger(__name__).warning(
                policy("inner_life", "reflection.copy.parse_reflection_text_6", str)
            )
    consumed = result.get("consumed_reading_ids", [])
    if not isinstance(consumed, list) or any(
        not isinstance(v, str) for v in cast(list[Any], consumed)
    ):
        raise ValueError(
            policy("inner_life", "reflection.copy.parse_reflection_text_5", str)
        )
    result["long_term_desires"] = accepted
    result["consumed_reading_ids"] = consumed
    return result


def _valid_candidate(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    candidate = cast(dict[str, Any], value)
    if candidate.get("type") not in ("exploration", "interaction"):
        return False
    for key, limit in policy(
        "inner_life", "reflection.desire_field_limits", tuple[tuple[str, int], ...]
    ):
        text = candidate.get(key)
        if not isinstance(text, str) or not text.strip() or len(text) > limit:
            return False
    subtopics = candidate.get("subtopics")
    return (
        isinstance(subtopics, list)
        and len(cast(list[Any], subtopics))
        <= policy(
            "inner_life", "reflection.parameters.valid_candidate_result_limit", int
        )
        and all(
            isinstance(v, str)
            and v.strip()
            and len(v)
            <= policy(
                "inner_life",
                "reflection.parameters.valid_candidate_result_limit_2",
                int,
            )
            for v in cast(list[Any], subtopics)
        )
    )


@dataclass(frozen=True)
class ReflectionPlan:
    document: InnerDocument
    long_term_desires: tuple[LongTermDesire, ...]
    long_term_snapshot: LongTermSnapshot
    summary: str
    document_changed: bool
    base_document_updated_at: float
    base_revision: int
    reading_evidence_ids: tuple[str, ...] = ()
    record: Memory | None = None
    correlation_id: str = ""


class Reflection:
    """Prepare revisions outside a transaction and commit against their base."""

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

    async def prepare(
        self, correlation_id: str | None = None, trigger: dict[str, Any] | None = None
    ) -> ReflectionPlan:
        current = await self._store.get_document()
        if current is None:
            raise RuntimeError(
                policy("inner_life", "reflection.copy.prepare_text", str)
            )
        memories = sorted(
            await self._memory_facade.inner_reflection_memories(),
            key=lambda m: (m.created_at, m.id),
            reverse=True,
        )[:_RECENT_MEMORY_LIMIT]
        segments = await self._memory_facade.browse_conversations(
            limit=policy("inner_life", "reflection.parameters.prepare_limit", int)
        )
        facts = await self._memory_facade.recent_facts()
        reading = await self._memory_facade.pending_reading_evidence(
            policy("inner_life", "reflection.reading_evidence_limit", int)
        )
        desires = await self._desire_facade.get_all()
        evidence: dict[str, str] = {}
        for memory in memories:
            evidence[f"memory:{memory.id}"] = memory.content[
                : policy(
                    "inner_life",
                    "reflection.parameters.prepare_evidence_f_memory_memory_id_chars_or_items",
                    int,
                )
            ]
        for segment in segments:
            evidence[f"conversation:{segment.id}"] = segment.raw_content[
                : policy(
                    "inner_life",
                    "reflection.parameters.prepare_evidence_f_conversation_segment_id_chars_or_items",
                    int,
                )
            ]
        for fact in facts:
            evidence[f"fact:{fact.id}"] = (
                f"{fact.subject}｜{fact.predicate}｜{fact.object_value}"
            )
        reading_ids: dict[str, str] = {}
        for item in reading:
            source = f"reading:{item.id}"
            reading_ids[source] = item.id
            evidence[source] = item.content[
                : policy(
                    "inner_life",
                    "reflection.parameters.prepare_evidence_source_chars_or_items",
                    int,
                )
            ]
        prompt = (
            policy("inner_life", "reflection.templates.prepare_text", str).format(
                p0=_build_trigger_context(trigger),
                p1=json.dumps(SECTIONS, ensure_ascii=False),
                p2=current.revision,
                p3=render_document(current),
            )
            + json.dumps([asdict(d) for d in desires.long_term], ensure_ascii=False)
            + policy("inner_life", "reflection.copy.prepare_text_2", str)
            + "\n\n".join(f"[{source}]\n{text}" for source, text in evidence.items())
        )
        cid = correlation_id or str(uuid4())
        output = await self._llm.complete(
            [
                {"role": "system", "content": _REFLECTION_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            module="inner_life",
            output_type="reflection",
            correlation_id=cid,
            json_mode=True,
        )
        await self._evaluator.evaluate(output)
        parsed = _parse_reflection(output.content)
        now = max(
            time.time(),
            current.updated_at
            + policy("inner_life", "reflection.parameters.prepare_now_factor", float),
        )
        revised = revise_document(current, parsed["patches"], evidence, now)
        consumed = set(parsed["consumed_reading_ids"])
        if not consumed <= reading_ids.keys():
            raise ValueError(
                policy("inner_life", "reflection.copy.prepare_text_3", str)
            )
        consumed.update(
            ref["id"]
            for patch in parsed["patches"]
            for ref in patch["evidence"]
            if ref["id"] in reading_ids
        )
        candidates = tuple(
            LongTermDesire(
                id=str(uuid4()),
                created_at=now,
                type=DesireType(c["type"]),
                name=c["name"].strip(),
                description=c["description"].strip(),
                strength=policy(
                    "inner_life", "reflection.parameters.prepare_strength_factor", float
                ),
                progress=0.0,
                subtopics=list(dict.fromkeys(c["subtopics"])),
            )
            for c in parsed["long_term_desires"]
        )
        accepted, snapshot = await self._desire_facade.prepare_long_term_candidates(
            candidates
        )
        summary = parsed["summary"].strip()
        changed = revised.revision != current.revision
        record = await self._memory_facade.prepare_reflection_record(
            json.dumps(
                {
                    "type": "inner_state_reflection",
                    "correlation_id": cid,
                    "trigger": trigger or {"reason": "manual"},
                    "input": {"system": _REFLECTION_SYSTEM, "prompt": prompt},
                    "output": output.content,
                    "call_id": output.call_id,
                    "before": {"document": asdict(current)},
                    "after": {
                        "document": asdict(revised),
                        "accepted_long_term_desires": [asdict(d) for d in accepted],
                    },
                    "patches": parsed["patches"],
                    "changed": changed,
                },
                ensure_ascii=False,
            ),
            policy("inner_life", "reflection.templates.prepare_text_2", str).format(
                p0=summary
            ),
        )
        return ReflectionPlan(
            revised,
            accepted,
            snapshot,
            summary,
            changed,
            current.updated_at,
            current.revision,
            tuple(reading_ids[key] for key in sorted(consumed)),
            record,
            cid,
        )

    async def apply(self, plan: ReflectionPlan) -> ReflectionOutcome:
        await self._store.assert_document_updated_at(plan.base_document_updated_at)
        current = await self._store.get_document()
        if current is None or current.revision != plan.base_revision:
            raise RuntimeError(policy("inner_life", "reflection.copy.apply_text", str))
        if plan.reading_evidence_ids:
            await self._memory_facade.consume_reading_evidence(
                list(plan.reading_evidence_ids)
            )
        await self._store.upsert_document(plan.document)
        await self._desire_facade.add_prepared_long_terms_in_transaction(
            plan.long_term_desires,
            plan.long_term_snapshot,
        )
        await self._desire_facade.pressure_creation(_CREATION_REFLECTION_DELTA)
        if plan.record is not None:
            await self._memory_facade.commit_reflection_record(
                plan.record, plan.correlation_id
            )
        return ReflectionOutcome(
            summary=plan.summary, document_changed=plan.document_changed
        )

    async def run(self, correlation_id: str | None = None) -> ReflectionOutcome:
        plan = await self.prepare(correlation_id)
        if self._store.db.in_transaction:
            return await self.apply(plan)
        async with self._store.db.transaction():
            return await self.apply(plan)
