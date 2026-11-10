"""Resumable per-character backfill on the existing memory maintenance loop."""

import asyncio
import logging
import math
import time
from dataclasses import replace

from nyx.memory.conversation_chunks import embedding_version, index_version
from nyx.memory.embedding import EmbedFn
from nyx.memory.store import MemoryStore

_LOGGER = logging.getLogger(__name__)


async def maintain_indexes(
    store: MemoryStore, embed: EmbedFn | None, *, max_chunks: int = 16,
) -> None:
    """Publish bounded progress after each vector, never hold DB locks in inference.

    Cancellation leaves the unfinished revision pending. Errors retain completed
    chunks and retry with capped backoff; changed/deleted sources fail publication.
    """
    version = index_version(embed)
    remaining = max_chunks
    deadline = time.monotonic() + 10
    for job in await store.pending_conversation_indexes(version, time.time()):
        if remaining <= 0 or time.monotonic() >= deadline:
            break
        try:
            if not await store.prepare_conversation_index(job, version):
                continue
            chunks = await store.list_conversation_chunks(job.segment.id)
            pending = [chunk for chunk in chunks if embed is not None and (
                chunk.embedding is None
                or chunk.embedding_version != embedding_version(embed)
            )]
            published = 0
            for chunk in pending:
                if remaining <= 0 or time.monotonic() >= deadline:
                    break
                assert embed is not None
                vector = await asyncio.wait_for(embed(chunk.content), timeout=30)
                if not vector or not all(math.isfinite(value) for value in vector):
                    raise ValueError("Invalid conversation embedding")
                remaining -= 1
                if not await store.publish_conversation_vector(
                    job, replace(chunk, embedding=vector,
                                 embedding_version=embedding_version(embed)),
                    version,
                ):
                    break
                published += 1
            await store.finish_conversation_index(
                job, version, complete=published == len(pending),
            )
        except Exception as error:
            # Store a class name only: provider exceptions may contain credentials
            # or source text. Cancellation deliberately bypasses this boundary.
            _LOGGER.warning("Conversation index retry segment=%s error=%s",
                            job.segment.id, type(error).__name__)
            await store.finish_conversation_index(
                job, version, complete=False, error=type(error).__name__,
            )
