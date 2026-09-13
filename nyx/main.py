"""Nyx 服务入口与兼容导出。"""
# pyright: reportPrivateUsage=false, reportUnusedFunction=false
import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI

from nyx.api.routes import build_app as build_api
from nyx.api.routes import sanitize_filename
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
    load_prompt_files,
    seed_desire,
    seed_inner_life,
    seed_long_term,
)
from nyx.config import Config, load_config
from nyx.desire.store import DesireStore
from nyx.enums import ActivityStatus, DesireType, EventType, Source, TickType
from nyx.expression.mutter import should_initiate_chat
from nyx.inner_life.store import InnerLifeStore
from nyx.runtime import root_event, supervise_bus, tick_loop, vision_loop
from nyx.subscriptions import subscribe
from nyx.tools.file_io import file_io
from nyx.tools.registry import ToolRegistry
from nyx.types import Event, LongTermDesire

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


def _root_event(
    type_: EventType,
    content: dict[str, Any],
    source: Source = Source.EXTERNAL,
) -> Event:
    return root_event(type_, content, source)


def _sanitize_filename(name: str) -> str:
    return sanitize_filename(name)


def _load_prompt_files(canon_dir: Path, names: tuple[str, ...]) -> str:
    return load_prompt_files(canon_dir, names)


def _load_canon(canon_dir: Path) -> str:
    return load_canon(canon_dir, _CANON_FILES)


def _load_ask(canon_dir: Path) -> str:
    return load_ask(canon_dir, _ASK_FILES)


async def _seed_inner_life(store: InnerLifeStore) -> None:
    await seed_inner_life(store)


async def _seed_desire(store: DesireStore) -> None:
    await seed_desire(store)


def _seed_long_term(now: float) -> list[LongTermDesire]:
    return seed_long_term(now)


async def _interrupt_running(app: _App, by: EventType) -> None:
    current = await app.activity.get_current()
    if current is not None and current.status is ActivityStatus.RUNNING:
        await app.activity.interrupt(current.id, by)


async def _on_user_message(app: _App, event: Event) -> None:
    await _interrupt_running(app, EventType.USER_MESSAGE)
    await app.expression.reply(event.content["message"], event.correlation_id)


async def _on_clock_tick(app: _App, event: Event) -> None:
    tick_type = TickType(event.content["tick_type"])
    if tick_type is TickType.SCHEDULE_BLOCK_START:
        await app.activity.on_tick(tick_type)
    elif tick_type is TickType.DESIRE_EVAL:
        state = await app.inner_life.get_state()
        await app.desire.evaluate(state.energy)
    elif tick_type is TickType.MUTTER_CHECK:
        await app.expression.mutter(
            await app.inner_life.get_state(), event.correlation_id
        )
    elif tick_type is TickType.INITIATE_CHAT_CHECK:
        await _check_initiate_chat(app)
    elif tick_type is TickType.REFLECTION_CHECK:
        await _check_reflect(app, event.correlation_id)


async def _check_initiate_chat(app: _App) -> None:
    desires = await app.desire.get_pending()
    interaction = next(
        (desire for desire in desires if desire.type is DesireType.INTERACTION), None
    )
    if interaction is None:
        return
    state = await app.inner_life.get_state()
    online = app.last_presence in ("online", "busy")
    busy = app.last_presence == "busy"
    if should_initiate_chat(
        desires, online, busy, state.energy, time.time() - app.last_chat_at
    ):
        await _interrupt_running(app, EventType.INITIATE_CHAT)
        if await app.expression.initiate_chat(interaction, state):
            app.last_chat_at = time.time()


async def _check_reflect(app: _App, correlation_id: str) -> None:
    narrative = await app.inner_life.get_narrative()
    if time.time() - narrative.updated_at < _REFLECT_MIN_INTERVAL:
        return
    new_count = await app.memory.count_new(None, narrative.updated_at)
    if new_count >= _REFLECT_MIN_NEW_MEMORIES:
        await app.inner_life.reflect(correlation_id)


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
    return await assemble_app_context(
        config, canon_files=_CANON_FILES, ask_files=_ASK_FILES
    )


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
    config = load_config()
    app = await build_app_context(config)
    server = uvicorn.Server(uvicorn.Config(build_app(app), host=_HOST, port=_PORT))
    tasks: set[asyncio.Task[Any]] = {
        asyncio.create_task(server.serve()),
        asyncio.create_task(_supervise_bus(app)),
        asyncio.create_task(_tick_loop(app)),
    }
    if app.screen_observer is not None:
        tasks.add(asyncio.create_task(_vision_loop(app)))
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


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
