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
    _Host,
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
from nyx.policy import policy
from nyx.runtime import (
    check_reflect,
    host_presence_loop,
    host_vision_loop,
    root_event,
    supervise_bus,
    tick_loop,
    vision_loop,
)
from nyx.subscriptions import subscribe
from nyx.tools.registry import ToolRegistry
from nyx.types import Event

_LOCAL_HOST = policy("runtime", "main._LOCAL_HOST", str)
_LOCAL_PORT = policy("runtime", "main._LOCAL_PORT", int)
_TICK_INTERVAL = policy("runtime", "main._TICK_INTERVAL", float)
_MUTTER_CHECK_INTERVAL = policy("runtime", "main._MUTTER_CHECK_INTERVAL", float)
_INITIATE_CHAT_INTERVAL = policy("runtime", "main._INITIATE_CHAT_INTERVAL", float)
_REFLECT_CHECK_INTERVAL = policy("runtime", "main._REFLECT_CHECK_INTERVAL", float)
_REFLECT_MIN_INTERVAL = policy("runtime", "main._REFLECT_MIN_INTERVAL", float)
_REFLECT_MIN_NEW_MEMORIES = policy("runtime", "main._REFLECT_MIN_NEW_MEMORIES", int)
_BUS_BACKOFF_BASE = policy("runtime", "main._BUS_BACKOFF_BASE", float)
_BUS_BACKOFF_MAX = policy("runtime", "main._BUS_BACKOFF_MAX", float)
_BUS_MAX_FAILURES = policy("runtime", "main._BUS_MAX_FAILURES", int)
_BUS_RECOVERY_STREAK = policy("runtime", "main._BUS_RECOVERY_STREAK", int)
_SSE_QUEUE_SIZE = policy("runtime", "main._SSE_QUEUE_SIZE", int)
_CANON_FILES = policy("runtime", "main._CANON_FILES", tuple[str, str])
_ASK_FILES = policy("runtime", "main._ASK_FILES", tuple[str, ...])
_MAX_EPUB_BYTES = policy("runtime", "main._MAX_EPUB_BYTES", int)
_LAUNCH_NONCE_ENV = policy("runtime", "main._LAUNCH_NONCE_ENV", str)
_TIME_MODULE = time  # compatibility patch target for existing runtime tests


def _server_bind() -> tuple[str, int]:
    """Use Railway's injected port only when running in a hosted container."""
    raw_port = os.environ.get("PORT")
    if not raw_port:
        return _LOCAL_HOST, _LOCAL_PORT
    try:
        port = int(raw_port)
    except ValueError as error:
        raise ValueError("PORT must be an integer between 1 and 65535") from error
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be an integer between 1 and 65535")
    return "0.0.0.0", port


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
        max_epub_bytes=_MAX_EPUB_BYTES,
        sse_queue_size=_SSE_QUEUE_SIZE,
        launch_nonce=os.environ.get(_LAUNCH_NONCE_ENV) or None,
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
        # Initialize NumPy's native DLLs before starting the parent-pipe watcher.
        # In the Windows bundle, first importing them in the inference worker
        # after that watcher starts can hang in extension-module initialization.
        __import__("numpy")

        resources = Path(str(getattr(sys, "_MEIPASS")))
        os.environ.setdefault("NYX_CANON_DIR", str(resources / "nyx" / "prompts"))
        config = load_config(
            os.environ.get("NYX_CONFIG") or str(resources / "config.yaml")
        )
        if config.embedding.model == "BAAI/bge-small-zh-v1.5":
            config.embedding.model = str(resources / "embedding-model")
    else:
        config = load_config()
    from nyx.api.routes import build_host_app

    server_host, server_port = _server_bind()
    host = await _Host.open(config, Path.cwd())
    server = uvicorn.Server(
        uvicorn.Config(
            build_host_app(
                host, launch_nonce=os.environ.get(_LAUNCH_NONCE_ENV) or None
            ),
            host=server_host,
            port=server_port,
        )
    )

    def watch_parent() -> None:
        # The owned pipe reaches EOF even if the desktop process crashes.
        sys.stdin.buffer.read(1)
        server.should_exit = True

    if frozen:
        threading.Thread(target=watch_parent, daemon=True).start()
    for identity, record in host.records.items():
        if record["status"] == "RUNNING":
            host.activate(identity)
    tasks: set[asyncio.Task[Any]] = {
        asyncio.create_task(server.serve()),
        asyncio.create_task(host_presence_loop(host)),
    }
    if config.vision.enabled:
        tasks.add(asyncio.create_task(host_vision_loop(host)))
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await host.close()


def _run_with_reload() -> None:
    import subprocess

    from watchfiles import DefaultFilter, watch

    paths: list[str] = ["nyx"]
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
