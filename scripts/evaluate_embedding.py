"""Compare FP32 ONNX to PyTorch and an existing Chinese vector database.

The source SQLite database is opened read-only. Retrieval and persistence run
against temporary copies; source texts/vectors never leave this machine.
Requires the embedding-export extra for the PyTorch reference only.
"""

import argparse
import asyncio
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from nyx.config import MemoryConfig
from nyx.db import connect
from nyx.enums import MemoryKind
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.llm.client import LlmClient
from nyx.memory.ann import AnnIndex
from nyx.memory.embedding import OnnxEncoder
from nyx.memory.facade import MemoryFacade
from nyx.memory.retrieval import EmbedFn, MemoryRetrieval
from nyx.memory.store import MemoryStore
from nyx.types import Memory


async def evaluate(database: Path, model_name: str) -> dict[str, Any]:
    """Check embedding parity, real recall and actual semantic dedup persistence."""
    if os.environ.get("NYX_DATABASE_URL", "").strip():
        raise ValueError("Unset NYX_DATABASE_URL before this SQLite-only evaluation")
    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(2)
    reference = SentenceTransformer(model_name, device="cpu", local_files_only=True)
    encode = getattr(reference, "encode")
    encoder = OnnxEncoder(model_name)
    with tempfile.TemporaryDirectory(prefix="nyx-embedding-eval-") as temp:
        snapshot = Path(temp) / "snapshot.db"
        with (
            closing(
                sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
            ) as src,
            closing(sqlite3.connect(snapshot)) as dest,
        ):
            src.backup(dest)
        db = await connect(str(snapshot))
        try:
            store = MemoryStore(db)
            memories = [m for m in await store.list_memories() if m.embedding]
            if not memories:
                raise ValueError("No existing vectors to evaluate")
            print(f"Evaluating {len(memories)} stored memories", flush=True)
            # Every real summary is a query; paraphrases additionally exercise
            # semantic recall beyond the source text's exact wording.
            queries = list(dict.fromkeys(m.summary for m in memories if m.summary))
            paraphrases = [
                ("汉朝皇帝和宰相分别负责什么？", "职权"),
                ("汉朝如何选拔官员？", "选举"),
                ("汉朝怎样征税和服兵役？", "兵役"),
                ("研究古代制度时应该怎样看待当时人的评价？", "历史意见"),
                ("Steam 账号忘记密码以后如何恢复？", "安全"),
                ("Steam 如何添加好友和聊天？", "好友系统"),
                ("词表里面有哪些特殊标记？", "词表"),
                ("中国公民可以自由选择宗教吗？", "宗教"),
                ("信仰这首歌由谁演唱？", "歌曲"),
            ]
            queries.extend(query for query, _ in paraphrases)
            texts = list(
                dict.fromkeys(
                    [m.content for m in memories]
                    + queries
                    + ["", "  Python 和中文记忆，SQLite！  ", "中文长文本。" * 200]
                )
            )
            old = np.asarray(encode(texts, show_progress_bar=False, batch_size=1))
            new = np.asarray([encoder(text) for text in texts])
            print(f"Compared {len(texts)} real/boundary texts", flush=True)
            old_by_text = {text: vector.tolist() for text, vector in zip(texts, old)}
            new_by_text = {text: vector.tolist() for text, vector in zip(texts, new)}

            def embedder(vectors: dict[str, list[float]]) -> EmbedFn:
                async def embed(text: str) -> list[float]:
                    return vectors[text]

                return embed

            old_embed = embedder(old_by_text)
            new_embed = embedder(new_by_text)
            old_retrieval = MemoryRetrieval(store, old_embed)
            new_retrieval = MemoryRetrieval(store, new_embed)
            index = AnnIndex.build(memories)
            ann_changes = 0
            recall_changes = 0
            max_score_error = 0.0
            threshold_flips = 0
            stored = np.asarray([m.embedding for m in memories])
            stored = stored / np.sqrt(np.sum(stored * stored, axis=1, keepdims=True))
            paraphrase_results: list[dict[str, Any]] = []
            for query in queries:
                a = old_by_text[query]
                b = new_by_text[query]
                old_hits = index.query(a, 80)
                new_hits = index.query(b, 80)
                ann_changes += [h.memory_id for h in old_hits] != [
                    h.memory_id for h in new_hits
                ]
                old_result = await old_retrieval.search(query)
                new_result = await new_retrieval.search(query)
                recall_changes += [m.id for m in old_result] != [
                    m.id for m in new_result
                ]
                sa = stored @ (np.asarray(a) / np.sqrt(np.sum(np.asarray(a) ** 2)))
                sb = stored @ (np.asarray(b) / np.sqrt(np.sum(np.asarray(b) ** 2)))
                max_score_error = max(max_score_error, float(np.max(np.abs(sa - sb))))
                scores_a = cast(list[float], sa.tolist())
                scores_b = cast(list[float], sb.tolist())
                for threshold in (0.45, 0.9, 0.95, 0.985):
                    threshold_flips += sum(
                        (a_score >= threshold) != (b_score >= threshold)
                        for a_score, b_score in zip(scores_a, scores_b)
                    )
                for text, expected in paraphrases:
                    if query == text:
                        found = any(expected in m.summary for m in new_result[:5])
                        reference_found = any(
                            expected in m.summary for m in old_result[:5]
                        )
                        paraphrase_results.append(
                            {
                                "query": query,
                                "reference_expected_in_top5": reference_found,
                                "expected_in_top5": found,
                            }
                        )
            print(
                f"Compared {len(queries)} queries against unchanged vectors", flush=True
            )

            dedup_cases: list[tuple[Memory, str]] = []
            for memory in memories:
                if memory.kind not in {MemoryKind.LESSON, MemoryKind.REFLECTION}:
                    # Different content hash, same facts; do not take the exact
                    # hash dedup shortcut when exercising semantic persistence.
                    dedup_cases.append((memory, memory.content + "。"))
            # Include factual changes near the dedup boundary, not only positives.
            for memory in memories[:12]:
                dedup_cases.extend(
                    [
                        (memory, memory.content + "但上述说法不成立。"),
                        (memory, "2027年：" + memory.content),
                    ]
                )
            dedup_texts = [text for _, text in dedup_cases]
            if dedup_texts:
                dedup_old = encode(dedup_texts, show_progress_bar=False, batch_size=1)
                old_by_text.update(
                    {t: v.tolist() for t, v in zip(dedup_texts, dedup_old)}
                )
                new_by_text.update({t: encoder(t) for t in dedup_texts})
            dedup_changes = 0
            dedup_merged = 0

            async def persist(
                memory: Memory, text: str, embed: EmbedFn
            ) -> tuple[bool, str]:
                isolated = await connect(":memory:")
                try:
                    isolated_store = MemoryStore(isolated)
                    for existing in memories:
                        await isolated_store.add(replace(existing))
                    facade = MemoryFacade(
                        isolated_store,
                        MemoryRetrieval(isolated_store, embed),
                        EventBus(isolated),
                        cast(LlmClient, None),
                        cast(Evaluator, None),
                        MemoryConfig(),
                        embed,
                    )
                    candidate = replace(
                        memory, id=str(uuid4()), content=text, embedding=None
                    )
                    result = await facade._persist_memory(  # pyright: ignore[reportPrivateUsage]
                        candidate, "onnx-eval", defer_best_effort=True
                    )
                    return result.created, result.memory.id
                finally:
                    await isolated.close()

            for case_number, (memory, text) in enumerate(dedup_cases, 1):
                before = await persist(memory, text, old_embed)
                after = await persist(memory, text, new_embed)
                # New IDs are random, compare target IDs only for merged rows.
                dedup_changes += before[0] != after[0] or (
                    not before[0] and before[1] != after[1]
                )
                dedup_merged += not after[0]
                if case_number % 32 == 0:
                    print(f"Persisted dedup comparisons: {case_number}", flush=True)
            report: dict[str, Any] = {
                "stored_memories": len(memories),
                "texts_compared": len(texts),
                "max_component_error": float(np.max(np.abs(old - new))),
                "min_vector_cosine": float(
                    np.min(
                        np.sum(old * new, axis=1)
                        / (
                            np.sqrt(np.sum(old * old, axis=1))
                            * np.sqrt(np.sum(new * new, axis=1))
                        )
                    )
                ),
                "old_database_queries": len(queries),
                "ann_order_changes": ann_changes,
                "final_recall_changes": recall_changes,
                "max_old_database_score_error": max_score_error,
                "threshold_decision_changes": threshold_flips,
                "semantic_dedup_cases": len(dedup_cases),
                "semantic_dedup_merged": dedup_merged,
                "semantic_dedup_changes": dedup_changes,
                "paraphrases": paraphrase_results,
            }
            report["passed"] = (
                report["max_component_error"] <= 1e-5
                and report["min_vector_cosine"] >= 0.999999
                and recall_changes == 0
                and ann_changes == 0
                and threshold_flips == 0
                and dedup_changes == 0
                and all(
                    not item["reference_expected_in_top5"] or item["expected_in_top5"]
                    for item in paraphrase_results
                )
            )
            return report
        finally:
            await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--model", default="BAAI/bge-small-zh-v1.5")
    args = parser.parse_args()
    report = asyncio.run(evaluate(args.database, args.model))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
