import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from nyx.activity.llm_result import parse_activity_result
from nyx.activity.material_store import MaterialStore
from nyx.activity.paths import path_hash_suffix, sanitize_filename
from nyx.eval.evaluator import Evaluator
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade, build_source_topic
from nyx.types import Activity

_logger = logging.getLogger(__name__)

_READ_CONTEXT_CHARS = 6000
_ACTIVITY_SYSTEM = (
    "你是尼克斯，正在读书。只输出 JSON，键："
    "book（书名，非空字符串）、note（本次读书笔记，非空字符串）。"
    "note 自然承接已读片段，不重复概括已读部分、只续写本次新读内容；"
    "note 正文里不要写「上次读到第 X 字」这类位置字样。"
)

_AGGREGATE_SYSTEM = (
    "你是尼克斯，正在整理读完一本书后的读书笔记。"
    "把各片段笔记聚合成一篇完整的读书笔记，"
    "用你的语气写：温柔克制、思虑深，写下你读到了什么、心里留下了什么、"
    "哪些地方让你停了一下或想得更多，而不是机械复述内容要点。"
    "不要写得像摘要提纲或客服汇报，不要堆砌形容词。"
    "例如：「读到这一段，我一直没法不去想，她明明那么害怕，却还是留了下来。"
    "这让我有点难过，也让我更想弄懂——人为什么会在害怕里，仍然选择不逃。」\n"
    "只输出 JSON，键：note（完整笔记，非空字符串）。"
)


def _correlation_id(activity: Activity) -> str:
    return str(activity.progress.get("correlation_id") or activity.id)


def _reading_checkpoint(
    activity: Activity, source: str, content_len: int
) -> dict[str, Any]:
    raw = activity.progress.get("reading")
    if isinstance(raw, dict):
        raw_map = cast(dict[str, Any], raw)
    else:
        raw_map = {}
    if raw_map.get("source") == source:
        checkpoint = raw_map
    else:
        read_from = _as_int(activity.progress.get("read_chars"), 0)
        checkpoint: dict[str, Any] = {
            "source": source,
            "read_from": read_from,
            "read_to": min(read_from + _READ_CONTEXT_CHARS, content_len),
            "fragment_committed": False,
            "advanced_to": read_from,
            "finalized": False,
            "note_path": None,
        }
    checkpoint["read_from"] = _as_int(checkpoint.get("read_from"), 0)
    checkpoint["read_to"] = min(
        _as_int(checkpoint.get("read_to"), int(checkpoint["read_from"])),
        content_len,
    )
    checkpoint["advanced_to"] = _as_int(
        checkpoint.get("advanced_to"), int(checkpoint["read_from"])
    )
    return checkpoint


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class ReadingActivityRunner:
    """执行一次真实读物活动，隐藏分块阅读和知识沉淀细节。"""

    def __init__(
        self,
        material_store: MaterialStore,
        llm: LlmClient,
        evaluator: Evaluator,
        memory: MemoryFacade,
        write_file: Callable[[str, str, str | None], Awaitable[dict[str, Any]]],
        update_activity: Callable[[Activity], Awaitable[None]],
    ) -> None:
        self._material_store = material_store
        self._llm = llm
        self._evaluator = evaluator
        self._memory = memory
        self._write_file = write_file
        self._update_activity = update_activity

    async def run(self, activity: Activity, source: str) -> dict[str, Any]:
        """分块读真实文件，完成时落盘完整笔记并沉淀知识。"""
        content = await asyncio.to_thread(
            Path(source).read_text, encoding="utf-8", errors="replace"
        )
        checkpoint = _reading_checkpoint(activity, source, len(content))
        await self._save_checkpoint(activity, checkpoint)
        read_chars = int(checkpoint["read_from"])
        read_to = int(checkpoint["read_to"])
        chunk = content[read_chars:read_to]
        filename = str(activity.progress.get("filename") or Path(source).name)
        if chunk == "":
            return await self._finalize(
                activity, checkpoint, source, filename, len(content)
            )

        prior = await self._material_store.get_fragments(source)
        prior_block = ""
        if prior:
            prior_block = (
                f"上次已读到第 {read_chars} 字，此前片段笔记：\n"
                + "\n---\n".join(prior)
                + "\n"
            )
        context = (
            f"书名：{filename}\n"
            f"{prior_block}"
            f"本次新读（第 {read_chars}～{read_chars + len(chunk)} 字）：\n{chunk}"
        )
        if isinstance(checkpoint.get("note"), str) and isinstance(
            checkpoint.get("book"), str
        ):
            result = {"book": checkpoint["book"], "note": checkpoint["note"]}
        else:
            result = await self._run_llm(activity, context)
            checkpoint["book"] = str(result.get("book", ""))
            checkpoint["note"] = str(result.get("note", ""))
            await self._save_checkpoint(activity, checkpoint)
        await self._sediment_chunk(
            activity,
            checkpoint,
            source,
            filename,
            chunk,
            read_to,
        )
        new_read_chars = read_to
        if not bool(checkpoint.get("fragment_committed")):
            await self._material_store.append_fragment(
                source, str(result.get("note", "")), time.time()
            )
            checkpoint["fragment_committed"] = True
            await self._save_checkpoint(activity, checkpoint)
        if int(checkpoint.get("advanced_to", 0)) < new_read_chars:
            await self._material_store.advance(source, new_read_chars, time.time())
            checkpoint["advanced_to"] = new_read_chars
            await self._save_checkpoint(activity, checkpoint)
        result["read_chars"] = new_read_chars
        result["total_chars"] = len(content)
        if new_read_chars < len(content):
            result["completed"] = False
            activity.progress["goal_signal"] = None
            return result

        full = await self._finalize(
            activity, checkpoint, source, filename, len(content)
        )
        activity.progress["goal_signal"] = True
        full["read_chars"] = new_read_chars
        full["total_chars"] = len(content)
        return full

    async def _save_checkpoint(
        self, activity: Activity, checkpoint: dict[str, Any]
    ) -> None:
        activity.progress["reading"] = checkpoint
        await self._update_activity(activity)

    async def _run_llm(self, activity: Activity, context: str) -> dict[str, Any]:
        output = await self._llm.complete(
            [
                {"role": "system", "content": _ACTIVITY_SYSTEM},
                {
                    "role": "user",
                    "content": f"活动类型：reading\n读物信息：\n{context}",
                },
            ],
            module="activity",
            output_type="reading",
            correlation_id=_correlation_id(activity),
            json_mode=True,
        )
        await self._evaluator.evaluate(output)
        return parse_activity_result(output.content, "reading")

    async def _finalize(
        self,
        activity: Activity,
        checkpoint: dict[str, Any],
        source: str,
        filename: str,
        content_len: int,
    ) -> dict[str, Any]:
        if bool(checkpoint.get("finalized")) and isinstance(
            checkpoint.get("note_path"), str
        ):
            return {
                "book": str(checkpoint.get("book") or filename),
                "note": str(
                    checkpoint.get("final_note") or checkpoint.get("note") or ""
                ),
                "path": str(checkpoint["note_path"]),
                "completed": True,
                "read_chars": content_len,
                "total_chars": content_len,
            }
        if not isinstance(checkpoint.get("final_note"), str):
            fragments = await self._material_store.get_fragments(source)
            checkpoint["final_note"] = await self._aggregate_note(
                activity, filename, fragments
            )
            checkpoint["book"] = filename
            await self._save_checkpoint(activity, checkpoint)
        full_note = str(checkpoint["final_note"])
        note_path = f"notes/{sanitize_filename(filename)}-{path_hash_suffix(source)}.md"
        written = await self._write_file("write", note_path, full_note)
        checkpoint["note_path"] = str(written["path"])
        checkpoint["finalized"] = True
        await self._save_checkpoint(activity, checkpoint)
        return {
            "book": filename,
            "note": full_note,
            "path": str(checkpoint["note_path"]),
            "completed": True,
            "read_chars": content_len,
            "total_chars": content_len,
        }

    async def _sediment_chunk(
        self,
        activity: Activity,
        checkpoint: dict[str, Any],
        source: str,
        filename: str,
        chunk: str,
        read_to: int,
    ) -> None:
        state = await self._material_store.get_memory_state(source)
        processed_to = _as_int(state.get("processed_to"), 0)
        if bool(checkpoint.get("knowledge_extracted")):
            if processed_to < read_to:
                state["processed_to"] = read_to
                state.setdefault(
                    "profile",
                    {
                        "summary": "",
                        "themes": [],
                        "content_category": "unknown",
                    },
                )
                state["pending"] = None
                await self._material_store.update_memory_state(
                    source, state, time.time()
                )
            return
        if processed_to >= read_to:
            return
        raw_pending = state.get("pending")
        if isinstance(raw_pending, dict):
            pending = cast(dict[str, Any], raw_pending)
        else:
            raw_profile = state.get("profile")
            profile = (
                cast(dict[str, object], raw_profile)
                if isinstance(raw_profile, dict)
                else None
            )
            next_profile, items = await self._memory.digest_source_block(
                chunk,
                filename,
                _correlation_id(activity),
                profile=profile,
            )
            source_topic = build_source_topic("material", source)
            for item in items:
                item["source_topic"] = source_topic
                item["source_name"] = filename
            pending = {
                "to": read_to,
                "profile": next_profile,
                "knowledge": items,
            }
            state["pending"] = pending
            await self._material_store.update_memory_state(
                source, state, time.time()
            )
        raw_items = pending.get("knowledge", [])
        items = [
            cast(dict[str, str], item)
            for item in cast(list[object], raw_items)
            if isinstance(item, dict)
        ] if isinstance(raw_items, list) else []
        if items:
            await self._memory.remember_knowledge(
                items, _correlation_id(activity)
            )
        state["processed_to"] = _as_int(pending.get("to"), read_to)
        raw_profile = pending.get("profile")
        state["profile"] = raw_profile if isinstance(raw_profile, dict) else {}
        state["pending"] = None
        await self._material_store.update_memory_state(source, state, time.time())

    async def _aggregate_note(
        self, activity: Activity, filename: str, fragments: list[str]
    ) -> str:
        joined = "\n---\n".join(fragments) if fragments else "（无片段）"
        output = await self._llm.complete(
            [
                {"role": "system", "content": _AGGREGATE_SYSTEM},
                {
                    "role": "user",
                    "content": f"书名：{filename}\n各片段笔记：\n{joined}",
                },
            ],
            module="activity",
            output_type="note",
            correlation_id=_correlation_id(activity),
            json_mode=True,
        )
        await self._evaluator.evaluate(output)
        data: Any = json.loads(output.content)
        if not isinstance(data, dict):
            raise ValueError(f"聚合笔记 JSON 应是对象，得到 {type(data).__name__}")
        note = cast(dict[str, Any], data).get("note")
        if not isinstance(note, str) or not note:
            raise ValueError("聚合笔记 JSON 缺 note 或非空字符串")
        return note
