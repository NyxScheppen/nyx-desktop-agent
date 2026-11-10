# pyright: reportPrivateUsage=false
import asyncio
import threading

import pytest

from nyx.memory import embedding


async def test_cancelled_inference_keeps_slot_and_queued_cancellation_skips_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    class Encoder:
        def __init__(self, model_name: str) -> None:
            pass

        def __call__(self, text: str) -> list[float]:
            calls.append(text)
            if text == "running":
                started.set()
                assert release.wait(5)
            return [1.0]

    monkeypatch.setattr(embedding, "OnnxEncoder", Encoder)
    embed = embedding.build_embed("unused")
    running = asyncio.ensure_future(embed("running"))
    tasks = [running]
    try:
        assert await asyncio.to_thread(started.wait, 5)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        queued = asyncio.ensure_future(embed("cancelled"))
        tasks.append(queued)
        await asyncio.sleep(0)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        next_call = asyncio.ensure_future(embed("next"))
        tasks.append(next_call)
        await asyncio.sleep(0)
        assert calls == ["running"]
        release.set()
        assert await asyncio.wait_for(next_call, 5) == [1.0]
        assert calls == ["running", "next"]
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_queue_bound_and_failed_load_can_recover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    attempts = 0

    class Encoder:
        def __init__(self, model_name: str) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("missing model")

        def __call__(self, text: str) -> list[float]:
            started.set()
            assert release.wait(5)
            return [1.0]

    monkeypatch.setattr(embedding, "OnnxEncoder", Encoder)
    monkeypatch.setattr(embedding, "_MAX_PENDING", 2)
    embed = embedding.build_embed("unused")
    with pytest.raises(OSError, match="missing model"):
        await embed("fail")
    running = asyncio.ensure_future(embed("recover"))
    tasks = [running]
    try:
        assert await asyncio.to_thread(started.wait, 5)
        queued = asyncio.ensure_future(embed("queued"))
        tasks.append(queued)
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="queue is full"):
            await embed("overflow")
        queued.cancel()
        await asyncio.gather(queued, return_exceptions=True)
        replacement = asyncio.ensure_future(embed("replacement"))
        tasks.append(replacement)
        release.set()
        assert await asyncio.wait_for(replacement, 5) == [1.0]
        assert attempts == 2
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
