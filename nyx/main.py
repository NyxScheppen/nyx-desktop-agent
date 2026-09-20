"""Nyx 服务入口与兼容导出。"""
# pyright: reportPrivateUsage=false, reportUnusedFunction=false
import asyncio
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI

from nyx.api.routes import build_app as build_api
from nyx.app_context import (
    _App,
    build_tools,
)
from nyx.app_context import (
    build_app_context as assemble_app_context,
)
from nyx.bootstrap import (
    load_ask,
    load_canon,
    seed_desire,
    seed_inner_life,
)
from nyx.config import Config, load_config
from nyx.desire.store import DesireStore
from nyx.enums import EventType, Source
from nyx.inner_life.store import InnerLifeStore
from nyx.runtime import (
    check_reflect,
    root_event,
    supervise_bus,
    tick_loop,
    vision_loop,
)
from nyx.subscriptions import subscribe
from nyx.tools.file_io import file_io
from nyx.tools.registry import ToolRegistry
from nyx.types import Event

_HOST = "127.0.0.1"
_PORT = 8000
_TICK_INTERVAL = 60.0
_MUTTER_CHECK_INTERVAL = 150.0
_INITIATE_CHAT_INTERVAL = 300.0
_REFLECT_CHECK_INTERVAL = 3600.0
_REFLECT_MIN_INTERVAL = 21600.0
_REFLECT_MIN_NEW_MEMORIES = 3
_BUS_BACKOFF_BASE = 1.0
_BUS_BACKOFF_MAX = 30.0
_BUS_MAX_FAILURES = 8
_BUS_RECOVERY_STREAK = 3
_SSE_QUEUE_SIZE = 100
_CANON_FILES = ("canon.md",)
_ASK_FILES = ("ask.md",)
_MAX_UPLOAD_BYTES = 500_000
_MAX_EPUB_BYTES = 50 * 1024 * 1024
_TIME_MODULE = time  # compatibility patch target for existing runtime tests


def _root_event(
    type_: EventType,
    content: dict[str, Any],
    source: Source = Source.EXTERNAL,
) -> Event:
    return root_event(type_, content, source)


def _load_canon(canon_dir: Path) -> str:
    return load_canon(canon_dir, _CANON_FILES)


def _load_ask(canon_dir: Path) -> str:
    return load_ask(canon_dir, _ASK_FILES)


async def _seed_inner_life(store: InnerLifeStore) -> None:
    await seed_inner_life(store)


async def _seed_desire(store: DesireStore) -> None:
    await seed_desire(store)


async def _check_reflect(app: _App, correlation_id: str) -> None:
    await check_reflect(
        app,
        correlation_id,
        min_interval=_REFLECT_MIN_INTERVAL,
        min_new_memories=_REFLECT_MIN_NEW_MEMORIES,
    )


async def _tick_loop(app: _App) -> None:
    await tick_loop(
        app,
        tick_interval=_TICK_INTERVAL,
        mutter_check_interval=_MUTTER_CHECK_INTERVAL,
        initiate_chat_interval=_INITIATE_CHAT_INTERVAL,
        reflect_check_interval=_REFLECT_CHECK_INTERVAL,
        root_event_factory=_root_event,
    )


def _subscribe(app: _App) -> None:
    subscribe(app)


def build_app(app: _App) -> FastAPI:
    return build_api(
        app,
        root_event=_root_event,
        file_io=file_io,
        max_upload_bytes=_MAX_UPLOAD_BYTES,
        max_epub_bytes=_MAX_EPUB_BYTES,
        sse_queue_size=_SSE_QUEUE_SIZE,
    )


def _build_tools(config: Config) -> ToolRegistry:
    return build_tools(config)


async def build_app_context(config: Config) -> _App:
    app = await assemble_app_context(
        config, canon_files=_CANON_FILES, ask_files=_ASK_FILES
    )
    _subscribe(app)
    return app


async def _supervise_bus(app: _App) -> None:
    await supervise_bus(
        app,
        backoff_base=_BUS_BACKOFF_BASE,
        backoff_max=_BUS_BACKOFF_MAX,
        max_failures=_BUS_MAX_FAILURES,
        recovery_streak=_BUS_RECOVERY_STREAK,
    )


async def _vision_loop(app: _App) -> None:
    await vision_loop(app)


async def main() -> None:
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        resources = Path(str(getattr(sys, "_MEIPASS")))
        os.environ.setdefault("NYX_CANON_DIR", str(resources / "prompts"))
        config = load_config(
            os.environ.get("NYX_CONFIG") or str(resources / "config.yaml")
        )
        if config.embedding.model == "all-MiniLM-L6-v2":
            config.embedding.model = str(resources / "embedding-model")
    else:
        config = load_config()
    app = await build_app_context(config)
    server = uvicorn.Server(uvicorn.Config(build_app(app), host=_HOST, port=_PORT))
    def watch_parent() -> None:
        # The owned pipe reaches EOF even if the desktop process crashes.
        sys.stdin.buffer.read(1)
        server.should_exit = True

    if frozen:
        threading.Thread(target=watch_parent, daemon=True).start()
    bus_task = asyncio.create_task(_supervise_bus(app))
    tasks: set[asyncio.Task[Any]] = {
        asyncio.create_task(server.serve()),
        bus_task,
        asyncio.create_task(_tick_loop(app)),
    }
    if app.screen_observer is not None:
        tasks.add(asyncio.create_task(_vision_loop(app)))
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        reading_quiesce = getattr(app.reading, "quiesce", None)
        if reading_quiesce is not None:
            await reading_quiesce()
        for task in tasks:
            if task is not bus_task and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in tasks if task is not bus_task),
            return_exceptions=True,
        )
        reading_drain = getattr(app.reading, "drain", None)
        if reading_drain is not None:
            await reading_drain()
        close_bus = getattr(app.bus, "close", None)
        if close_bus is None:
            bus_task.cancel()
        else:
            await close_bus()
        await asyncio.gather(bus_task, return_exceptions=True)


def _run_with_reload() -> None:
    import subprocess

    from watchfiles import DefaultFilter, watch

    paths: list[str] = ["nyx", "prompts"]
    if Path("config.yaml").is_file():
        paths.append("config.yaml")
    process = subprocess.Popen([sys.executable, "-m", "nyx.main"])
    try:
        for _changes in watch(*paths, watch_filter=DefaultFilter()):
            process.terminate()
            process.wait()
            process = subprocess.Popen([sys.executable, "-m", "nyx.main"])
    except KeyboardInterrupt:
        pass
    finally:
        process.terminate()
        process.wait()


if __name__ == "__main__":
    if "--reload" in sys.argv:
        _run_with_reload()
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            pass
