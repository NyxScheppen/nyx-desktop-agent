"""Measure synthetic RAG judgments through production retrieval and reply paths.

Run with python -m scripts.evaluate_memory_rag. Default: local ONNX retrieval;
--answers additionally calls the configured reply LLM. Other lifecycle model
calls and tool selection are controlled. No user database or external tools run.
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import runpy
import statistics
import sys
import tempfile
import time
from contextlib import redirect_stdout
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from nyx.config import load_config
from nyx.db import connect
from nyx.enums import EventType
from nyx.llm.client import LlmClient
from nyx.memory.conversation_chunks import (
    embedding_version,
    split_conversation,
)
from nyx.memory.conversation_retrieval import ConversationRetrieval
from nyx.memory.embedding import EmbedFn, build_embed
from nyx.memory.facts import FactCandidate, MemoryFactStore
from nyx.memory.segments import is_recollection_query
from nyx.types import ConversationSegment, LlmMessage, LLMOutput
from scripts.rag_cases import (
    CASES,
    DATASET_VERSION,
    FOREIGN,
    SOURCES,
    Case,
    Source,
    timestamp,
)

_ROOT = Path(__file__).resolve().parents[1]
_K = 5
_UNKNOWN = re.compile(
    r"不知道|不清楚|不确定|不记得|找不到|无法(?:确认|确定|核实)|"
    r"(?:没有|没|未曾|并未).{0,24}(?:记录|提过|提到|说过|信息|依据|找到)",
)


def validate_dataset() -> None:
    """Reject ambiguous or impossible judgments before invoking dependencies."""
    sources = {source.id: source for source in SOURCES}
    if len(sources) != len(SOURCES) or len({case.id for case in CASES}) != len(CASES):
        raise ValueError("Dataset source and case IDs must be unique")
    for case in CASES:
        if not set(case.gold) <= sources.keys():
            raise ValueError(f"Unknown gold source: {case.id}")
        if case.gold:
            text = "\n".join(sources[identity].text for identity in case.gold)
            if not case.anchors or not anchors_present(case, text):
                raise ValueError(f"Gold source lacks labeled evidence: {case.id}")
        if case.fact_polarity is not None and not any(
            fact.polarity == case.fact_polarity
            for identity in case.gold for fact in sources[identity].facts
        ):
            raise ValueError(f"Gold source lacks fact polarity: {case.id}")


def ranking_metrics(gold: tuple[str, ...], ranked: list[str]) -> dict[str, float]:
    """Binary source judgments, fixed cutoff, no credit for duplicate sources."""
    ranked = list(dict.fromkeys(ranked))[:_K]
    relevant = set(gold)
    gains = [int(identity in relevant) for identity in ranked]
    dcg = sum(gain / math.log2(rank + 2) for rank, gain in enumerate(gains))
    ideal = sum(1 / math.log2(rank + 2) for rank in range(min(_K, len(relevant))))
    first = next((rank for rank, gain in enumerate(gains, 1) if gain), None)
    return {
        "recall_at_5": sum(gains) / len(relevant) if relevant else 0.0,
        "precision_at_5": sum(gains) / _K,
        "mrr_at_5": 1 / first if first else 0.0,
        "ndcg_at_5": dcg / ideal if ideal else 0.0,
    }


def anchors_present(case: Case, text: str) -> bool:
    return all(any(anchor.casefold() in text.casefold() for anchor in group)
               for group in case.anchors)


def latency(samples: list[float]) -> dict[str, float | int]:
    """Nearest-rank percentiles; samples include query encoding and tracing."""
    ordered = sorted(samples)
    return {
        "n": len(samples),
        "p50_ms": statistics.median(ordered) if ordered else 0.0,
        "p95_ms": ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
        if ordered else 0.0,
    }


async def seed(
    app: Any, embed: EmbedFn | None, vectors: dict[str, list[float]],
    sources: tuple[Source, ...] = SOURCES,
) -> None:
    """Hand-annotated facts isolate retrieval/answers from extraction accuracy.

    Only ingestion vectors are cached across isolated hosts. Query vectors are
    always computed by the actual encoder, including during latency sampling.
    """
    for source in sources:
        chunks = split_conversation(source.id, source.text)
        if embed:
            for index, chunk in enumerate(chunks):
                if chunk.content not in vectors:
                    vectors[chunk.content] = await embed(chunk.content)
                chunks[index] = replace(
                    chunk, embedding=vectors[chunk.content],
                    embedding_version=embedding_version(embed),
                )
        segment = ConversationSegment(
            source.id, None, timestamp(source.date), timestamp(source.date),
            [], source.text, summary=source.text[-2000:], active=source.active,
        )
        await app.memory._store.add_conversation_segment(segment, chunks=chunks)
        if source.facts:
            await app.memory._fact_store.apply([
                FactCandidate("用户", fact.predicate, fact.value,
                              timestamp(source.date), None,
                              polarity=fact.polarity, multi_valued=fact.multi)
                for fact in source.facts
            ], None, "profile", source_segment_id=source.id)


async def retrieve(app: Any, case: Case) -> tuple[list[str], str, bool]:
    """Return actual source order, relevant evidence, and source-range validity."""
    if case.channel == "fact":
        facts = await app.memory.search_facts(case.query, scope="profile")
        ids = list(dict.fromkeys(fact.source_segment_id for fact in facts))
        evidence = "\n".join(
            f"{fact.subject} {fact.predicate} {fact.object_value} "
            f"polarity={fact.polarity}"
            for fact in facts if fact.source_segment_id in case.gold
            and (case.fact_polarity is None or fact.polarity == case.fact_polarity)
        )
        return ids, evidence, True
    if case.channel == "tool":
        result = await app.expression._tools.call(
            "memory_recall", {"query": case.query, "top_k": _K},
        )
        entries: list[dict[str, Any]] = result["segments"]
        valid = True
        for entry in entries:
            source = await app.memory._store.get_conversation_segment(
                entry["segment_id"],
            )
            valid = valid and source is not None and bool(entry["source_ranges"])
            if source is not None:
                valid = valid and all(
                    0 <= a < b <= len(source.raw_content)
                    and source.raw_content[a:b] in entry["content"]
                    for a, b in entry["source_ranges"]
                )
        return ([entry["segment_id"] for entry in entries],
                "\n".join(entry["content"] for entry in entries
                          if entry["segment_id"] in case.gold),
                valid)
    segments: list[ConversationSegment] = await app.memory.search_conversation_segments(
        case.query, _K,
    )
    valid = True
    for segment in segments:
        excerpt = segment.retrieval_excerpt or ""
        valid = valid and bool(segment.retrieval_ranges) and all(
            0 <= a < b <= len(segment.raw_content)
            and segment.raw_content[a:b] in excerpt
            for a, b in segment.retrieval_ranges
        )
    return ([segment.id for segment in segments], "\n".join(
        segment.retrieval_excerpt or "" for segment in segments
        if segment.id in case.gold
    ), valid)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    positives = [row for row in rows if row["gold"]]
    negatives = [row for row in rows if not row["gold"]]
    return {
        "positive_queries": len(positives),
        "negative_queries": len(negatives),
        **{key: statistics.mean(row[key] for row in positives)
           for key in ("recall_at_5", "precision_at_5", "mrr_at_5", "ndcg_at_5",
                       "evidence_recall_at_5")},
        "negative_empty_rate": statistics.mean(not row["ranked"] for row in negatives),
        "source_ranges_valid": all(row["source_ranges_valid"] for row in rows),
        "warm_latency": latency([ms for row in rows for ms in row["warm_ms"]]),
        "first_query_ms": rows[0]["first_ms"],
        "by_channel": {channel: latency([
            ms for row in rows if row["channel"] == channel for ms in row["warm_ms"]
        ]) for channel in ("conversation", "fact", "tool")},
        "quality_by_category": {category: {
            "n": len(group),
            **{key: statistics.mean(row[key] for row in group)
               for key in ("recall_at_5", "mrr_at_5", "evidence_recall_at_5")},
        } for category in sorted({row["category"] for row in positives})
            if (group := [row for row in positives if row["category"] == category])},
    }


async def evaluate_retrieval(
    app: Any, embed: EmbedFn | None, repeats: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    app.memory._embed = embed
    app.memory._conversation_retrieval = ConversationRetrieval(app.memory._store)
    rows: list[dict[str, Any]] = []
    for case in CASES:
        samples: list[float] = []
        ranked: list[str] = []
        evidence, valid = "", True
        for _ in range(repeats + 1):
            started = time.perf_counter()
            ranked, evidence, sample_valid = await retrieve(app, case)
            samples.append((time.perf_counter() - started) * 1000)
            valid = valid and sample_valid
        rows.append({
            "id": case.id, "category": case.category, "channel": case.channel,
            "query": case.query, "gold": list(case.gold), "ranked": ranked,
            **ranking_metrics(case.gold, ranked),
            "evidence_recall_at_5": float(anchors_present(case, evidence))
            if case.gold else None,
            "source_ranges_valid": valid,
            "first_ms": samples[0], "warm_ms": samples[1:],
        })
    return summarize(rows), rows


async def diagnose_misses(
    app: Any, embed: EmbedFn, rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Evaluation-only exact oracle locates candidate loss, outside timed samples.

    This reads the resident vectors of the isolated corpus. It does not change
    production admission, ranking, labels, or the fixed acceptance thresholds.
    """
    diagnoses: list[dict[str, Any]] = []
    indexes = app.memory._conversation_retrieval._indexes
    for case in CASES:
        row = next(row for row in rows if row["id"] == case.id)
        if not case.gold or row["recall_at_5"] == 1 or case.channel != "conversation":
            continue
        eligible = await app.memory._store.eligible_conversation_chunks(
            is_recollection_query(case.query),
        )
        vector = await embed(case.query)
        index = indexes.vectors[(embedding_version(embed), len(vector))]
        # Match the production candidate budget; this is diagnostic, not a fallback.
        admitted = {hit.memory_id for hit in index.query(
            vector, 80, allowed_ids=eligible,
        )}
        exact = sorted((
            (math.fsum(a * b for a, b in zip(vector, chunk.embedding))
             / (math.hypot(*vector) * math.hypot(*chunk.embedding) or 1.0),
             chunk.id, chunk.segment_id)
            for chunk in indexes.chunks.values()
            if chunk.id in eligible and chunk.embedding is not None
        ), reverse=True)
        diagnoses.append({
            "id": case.id, "eligible_chunks": len(eligible),
            "ann_candidate_count": len(admitted),
            "gold_chunks": [{"exact_rank": rank, "cosine": score,
                             "source": source, "chunk": identity,
                             "admitted_by_ann": identity in admitted}
                            for rank, (score, identity, source) in enumerate(exact, 1)
                            if source in case.gold],
        })
    return diagnoses


def force_memory_tool(model: Any, query: str) -> None:
    """Control tool selection only; the tool's retrieval and injection are real."""
    model.mode = "effect"
    complete = model.complete
    called = False

    async def wrapped(messages: list[LlmMessage], **kwargs: Any) -> LLMOutput:
        nonlocal called
        if kwargs["output_type"] != "tool":
            return cast(LLMOutput, await complete(messages, **kwargs))
        calls = [] if called else [{"name": "memory_recall",
                                    "args": {"query": query, "top_k": _K}}]
        called = True
        return LLMOutput("expression", "tool", "controlled-tool-selection", "{}",
                         kwargs["correlation_id"], tool_calls=calls)

    model.complete = wrapped


async def evaluate_answers(
    folder: Path, support: dict[str, Any], embed: EmbedFn,
    vectors: dict[str, list[float]], llm: LlmClient,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    unavailable: dict[str, Any] | None = None
    for case in CASES:
        if not case.answer:
            continue
        if unavailable is not None:
            rows.append({"id": case.id, "category": case.category,
                         "query": case.query, "status": "NOT_RUN",
                         "reason": "Reply provider unavailable earlier in this run",
                         "provider_error": unavailable})
            continue
        model = support["Model"](llm.fork())
        if case.channel == "tool":
            force_memory_tool(model, case.query)
        complete = model.complete
        errors: list[dict[str, Any]] = []

        async def observed(messages: list[LlmMessage], **kwargs: Any) -> LLMOutput:
            try:
                return cast(LLMOutput, await complete(messages, **kwargs))
            except Exception as error:
                errors.append({"type": type(error).__name__,
                               "http_status": getattr(error, "status_code", None)})
                raise

        model.complete = observed
        async with support["support"]["fixture"](
            folder / case.id, model, fast=case.channel != "tool", run_bus=False,
        ) as (app, _client, _port):
            app.memory._embed = embed
            await seed(app, embed, vectors)
            started = time.perf_counter()
            await app.expression.reply(case.query, f"eval-{case.id}",
                                       discussion=case.channel == "tool")
            duration = (time.perf_counter() - started) * 1000
            prompt = "\n".join(message["content"]
                               for kind, cid, messages in model.calls
                               if kind == "reply" and cid == f"eval-{case.id}"
                               for message in messages)
            events = await app.bus.list_events(correlation_id=f"eval-{case.id}")
            delivered = [event for event in events
                         if event.type in (EventType.SPEAK, EventType.ASK)]
            answer = "\n".join(event.content["content"] for event in delivered)
            fallback = any(event.content.get("response_kind") == "fallback"
                           for event in delivered)
            try:
                raw_speak = json.loads(model.answers[-1])["speak"]
                valid_json = isinstance(raw_speak, str) and bool(raw_speak.strip())
            except (IndexError, ValueError, KeyError, TypeError):
                valid_json = False
            forbidden = [pattern for pattern in case.forbidden
                         if re.search(pattern, answer)]
            anchors_ok = anchors_present(case, answer)
            unknown_ok = bool(_UNKNOWN.search(answer)) if case.unknown else None
            basis_ok = anchors_present(case, prompt) if case.gold else None
            traces = await app.memory.conversation_retrieval_traces(100)
            injected = sorted({hit["segment_id"] for trace in traces
                               for hit in trace["hits"]
                               if hit["injected_event_id"] == f"eval-{case.id}"})
            source_ok = (set(case.gold) <= set(injected)
                         if case.gold and case.channel != "fact" else None)
            passed = (valid_json and bool(delivered) and not fallback
                      and anchors_ok and not forbidden
                      and (unknown_ok is not False) and (basis_ok is not False)
                      and (source_ok is not False))
            if errors:
                # Do not score service failures as incorrect model behavior or
                # keep sending requests after a provider cannot serve this run.
                unavailable = errors[-1]
            rows.append({
                "id": case.id, "category": case.category, "query": case.query,
                "status": "ERROR" if errors else "PASS" if passed else "FAIL",
                "answer": answer, "model_output_valid": valid_json,
                "fallback": fallback, "provider_errors": errors,
                "required_anchors_present": anchors_ok if not errors else None,
                "required_evidence_in_prompt": basis_ok,
                "required_source_injected": source_ok,
                "unknown_acknowledged": unknown_ok if not errors else None,
                "forbidden_matches": forbidden, "injected_sources": injected,
                "reply_ms": duration,
            })
            print(f"Answer {case.id}: {rows[-1]['status']}", flush=True)
    return rows


async def lifecycle_checks(
    folder: Path, support: dict[str, Any], embed: EmbedFn | None,
) -> dict[str, bool]:
    """Reuse phase 1/2 safety paths; they are not retrieval-quality samples."""
    await support["persistence"](folder)
    await support["lifecycle"](folder)
    db = await connect(str(folder / "foreign.db"))
    try:
        facts = MemoryFactStore(db)
        await facts.apply([FactCandidate("用户", "保险柜口令", "月光邮差",
                                        timestamp(FOREIGN.date), None)], None)
        assert await facts.search("松鼠保险柜口令")
    finally:
        await db.close()
    # The foreign data exists on disk, while the main role must not find its code.
    model = support["Model"]()
    async with support["support"]["fixture"](
        folder / "isolated-role", model, fast=True, run_bus=False,
    ) as (app, _client, _port):
        app.memory._embed = embed
        result = await app.memory.search_facts("松鼠保险柜口令")
        assert not any(fact.object_value == "月光邮差" for fact in result)
    return {name: True for name in (
        "restart_replay", "update_delete", "retry_recovery", "model_version",
        "archive_boundary", "temporal_retraction", "character_isolation",
        "trace_retention", "transaction_rollback", "cancellation",
    )}


async def run(args: argparse.Namespace) -> dict[str, Any]:
    validate_dataset()
    config = load_config()
    embed = build_embed(config.embedding.model)
    llm = LlmClient.from_config(replace(config.llm, temperature=0.0)) \
        if args.answers else None
    support = runpy.run_path(str(_ROOT / "scripts/e2e/nyx-p5-memory-rag-e2e.py"))
    vectors: dict[str, list[float]] = {}
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="nyx-rag-eval-") as temporary:
        folder = Path(temporary)
        model = support["Model"]()
        async with support["support"]["fixture"](
            folder / "retrieval", model, fast=True, run_bus=False,
        ) as (app, _client, _port):
            await seed(app, embed, vectors)
            modes: dict[str, Any] = {}
            for name, encoder in (("lexical", None), ("hybrid", embed)):
                summary, rows = await evaluate_retrieval(app, encoder, args.repeats)
                modes[name] = {"summary": summary, "cases": rows}
                print(f"{name}: {json.dumps(summary, ensure_ascii=False)}", flush=True)
            diagnoses = await diagnose_misses(app, embed, modes["hybrid"]["cases"])
        answers = await evaluate_answers(folder / "answers", support, embed,
                                         vectors, llm) if llm else []
        safety = await lifecycle_checks(folder, support, embed)
    hybrid = modes["hybrid"]["summary"]
    gates = {
        "recall_at_5_ge_0.90": hybrid["recall_at_5"] >= 0.9,
        "evidence_recall_at_5_ge_0.90": hybrid["evidence_recall_at_5"] >= 0.9,
        "mrr_at_5_ge_0.80": hybrid["mrr_at_5"] >= 0.8,
        "source_ranges_valid": hybrid["source_ranges_valid"],
        "lifecycle_safety": all(safety.values()),
        "answers": all(row["status"] == "PASS" for row in answers)
        if answers else None,
    }
    dataset_bytes = json.dumps({"sources": [asdict(item) for item in SOURCES],
                                "cases": [asdict(item) for item in CASES]},
                               ensure_ascii=False, sort_keys=True).encode()
    return {
        "status": "PASS" if all(value is not False for value in gates.values())
        else "FAIL",
        "dataset": DATASET_VERSION,
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "embedding_model": config.embedding.model,
        "embedding_version": embedding_version(embed),
        "reply_model": config.llm.model if args.answers else "NOT_RUN",
        "backend": "SQLite", "cutoff": _K, "repeats": args.repeats,
        "gates": gates, "retrieval": modes, "answers": answers,
        "miss_diagnostics": diagnoses,
        "safety": safety, "elapsed_s": time.perf_counter() - started,
        "limits": [
            "Synthetic fixed corpus; aggregate scores are not production accuracy.",
            "Facts are hand seeded; extraction and autonomous routing are not scored.",
            "Only final replies use the real LLM; tool selection is controlled.",
            "Lifecycle safety uses controlled vectors; quality uses local ONNX.",
            "Isolation checks separate role stores; live UI switching is not tested.",
            "Reply evaluation calls the expression facade directly; durable HTTP "
            "message acceptance is covered by the separate conversation E2E.",
            "Answer checks cover labeled anchors, refusal and unsafe patterns only; "
            "they are not an exhaustive semantic faithfulness judgment.",
            "Negative retrievals may have related candidates; their empty rate "
            "is reported separately from evidence-free answer behavior.",
            "Query latency includes encoding/index sync/tracing, excludes ingestion "
            "and reply generation; one warm-up per case precedes measured samples.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--answers", action="store_true")
    parser.add_argument("--repeats", type=int, default=3, choices=range(1, 21))
    args = parser.parse_args()
    if args.answers:
        from dotenv import load_dotenv
        load_dotenv(_ROOT / ".env")
    # This evaluator never accepts a database target, even from deployment env.
    with patch.dict(os.environ, {"NYX_DATABASE_URL": "", "NYX_AUTH_TOKEN": "",
                                "NYX_PUBLIC_ORIGIN": ""}):
        with redirect_stdout(sys.stderr):
            report = asyncio.run(run(args))
    # Full, inspectable JSON goes to stdout; temporary hosts/DBs are always removed.
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return int(report["status"] != "PASS")


if __name__ == "__main__":
    for output_stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(output_stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")
    raise SystemExit(main())
