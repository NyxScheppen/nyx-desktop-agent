"""OneBot v11 正向 WebSocket，仅连接本地的指定机器人与用户私聊。"""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from functools import partial
from typing import TYPE_CHECKING, Any, TypeVar, cast

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from nyx.config import ConfigError
from nyx.db import DatabaseLockTimeout
from nyx.enums import EventType, Source
from nyx.transports.outbox import TransportOutbox
from nyx.types import Event

if TYPE_CHECKING:
    from nyx.app_context import _App

_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")


class OneBotError(RuntimeError):
    """An API refusal contains no remote text or credentials."""


class OneBotAdapter:
    """Bind one private conversation to a character and its durable outbox."""

    def __init__(self, app: _App) -> None:
        self._app = app
        self._config = app.config.onebot
        token = os.environ.get(self._config.access_token_env)
        if not token:
            raise ConfigError(f"OneBot 环境变量 {self._config.access_token_env} 未设置")
        self._token = token
        self.name = f"onebot:{self._config.self_id}:{self._config.owner_id}"
        assert app.database is not None
        self.outbox = TransportOutbox(app.database, self.name)
        self._connection: ClientConnection | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._task: asyncio.Task[None] | None = None
        self._accepting = True
        self._confirmed_event_id: str | None = None

    async def on_external(self, payload: dict[str, Any]) -> None:
        """Admit only the configured user's private plain text, with stable IDs."""
        if not self._accepting or (
            self._app.host is not None
            and (
                self._app.host.closing
                or self._app.host.records[self._app.character_id]["status"] != "RUNNING"
            )
        ):
            return
        if (
            payload.get("post_type") != "message"
            or payload.get("message_type") != "private"
            or payload.get("self_id") != self._config.self_id
            or payload.get("user_id") != self._config.owner_id
        ):
            return
        message = payload.get("message")
        if isinstance(message, str):
            if "[CQ:" in message:
                return
            text = message.replace("&#91;", "[").replace("&#93;", "]")
            text = text.replace("&amp;", "&")
        elif isinstance(message, list):
            parts: list[str] = []
            for segment in cast(list[Any], message):
                if not isinstance(segment, dict):
                    return
                segment = cast(dict[str, Any], segment)
                if segment.get("type") != "text":
                    return
                data: Any = segment.get("data")
                if not isinstance(data, dict):
                    return
                text_part = cast(dict[str, Any], data).get("text")
                if not isinstance(text_part, str):
                    return
                parts.append(text_part)
            text = "".join(parts)
        else:
            return
        message_id, timestamp = payload.get("message_id"), payload.get("time")
        if (
            not text.strip()
            or type(message_id) is not int
            or type(timestamp) is not int
        ):
            return
        identity = f"onebot:{self._config.self_id}:{message_id}"
        await self._app.bus.publish(
            Event(
                id=identity,
                timestamp=float(timestamp),
                source=Source.EXTERNAL,
                type=EventType.USER_MESSAGE,
                correlation_id=identity,
                content={
                    "message": text,
                    "transport": self.name,
                    "self_id": self._config.self_id,
                    "user_id": self._config.owner_id,
                    "message_id": message_id,
                },
            )
        )

    async def _call(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        connection = self._connection
        if connection is None:
            raise OneBotError("OneBot 尚未连接")
        echo = str(uuid.uuid4())
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[echo] = future
        try:
            await connection.send(
                json.dumps(
                    {
                        "action": action,
                        "params": params,
                        "echo": echo,
                    },
                    ensure_ascii=False,
                )
            )
            response = await asyncio.wait_for(future, timeout=15)
            if response.get("status") != "ok" or response.get("retcode") != 0:
                raise OneBotError("OneBot API 拒绝请求")
            return response
        except (OSError, TimeoutError, WebSocketException) as error:
            raise OneBotError(type(error).__name__) from None
        finally:
            self._pending.pop(echo, None)

    async def emit(self, event: Event) -> None:
        """Send text literally; only a matching successful echo confirms it."""
        text = event.content["content"]
        if event.type is EventType.THINK:
            text = f"💭 内心话\n{text}"
        await self._call(
            "send_private_msg",
            {
                "user_id": self._config.owner_id,
                "message": [
                    {"type": "text", "data": {"text": text}}
                ],
            },
        )

    async def _receive(self) -> None:
        assert self._connection is not None
        while True:
            try:
                raw = await self._connection.recv()
            except (OSError, WebSocketException) as error:
                raise OneBotError(type(error).__name__) from None
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                _LOGGER.warning("OneBot 忽略非法 JSON")
                continue
            if not isinstance(payload, dict):
                continue
            payload = cast(dict[str, Any], payload)
            echo = payload.get("echo")
            future = self._pending.get(echo) if isinstance(echo, str) else None
            if future is not None and not future.done():
                future.set_result(payload)
            elif "post_type" in payload:
                await self.on_external(payload)

    async def _retry_lock(
        self, phase: str, operation: Callable[[], Awaitable[_T]]
    ) -> _T:
        attempt = 0
        while True:
            try:
                return await operation()
            except DatabaseLockTimeout:
                delay = min(2 ** min(attempt, 5), 30)
                attempt += 1
                _LOGGER.warning(
                    "OneBot DB lock retry: phase=%s attempt=%d wait=%ds",
                    phase, attempt, delay,
                )
                await asyncio.sleep(delay)

    async def _confirm_sent(self) -> None:
        event_id = self._confirmed_event_id
        if event_id is not None:
            await self._retry_lock("sent", lambda: self.outbox.sent(event_id))
            self._confirmed_event_id = None

    async def _release(self) -> None:
        # Shutdown must be bounded. An unreleased row recovers through its lease.
        try:
            await self.outbox.release(exclude_event_id=self._confirmed_event_id)
        except DatabaseLockTimeout:
            _LOGGER.warning("OneBot lease cleanup deferred to expiry: DB lock timeout")

    async def _send(self) -> None:
        while True:
            # Survives receiver failure/reconnect while confirmation was blocked.
            await self._confirm_sent()
            await self._retry_lock("scan", self.outbox.scan)
            event = await self._retry_lock("claim", self.outbox.claim)
            if event is None:
                await asyncio.sleep(0.25)
                continue
            try:
                await self.emit(event)
            except OneBotError as error:
                await self._retry_lock(
                    "retry", partial(self.outbox.retry, event.id, type(error).__name__)
                )
                raise
            self._confirmed_event_id = event.id
            await self._confirm_sent()

    async def _session(self) -> None:
        receiver = asyncio.create_task(self._receive())

        async def send_after_login() -> None:
            response = await self._call("get_login_info", {})
            data = response.get("data")
            if (
                not isinstance(data, dict)
                or cast(dict[str, Any], data).get("user_id") != self._config.self_id
            ):
                raise OneBotError("OneBot 登录身份不匹配")
            await self._send()

        sender = asyncio.create_task(send_after_login())
        try:
            await asyncio.wait(
                (receiver, sender),
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            receiver.cancel()
            sender.cancel()
            results = await asyncio.gather(receiver, sender, return_exceptions=True)
            for future in self._pending.values():
                future.cancel()
            self._pending.clear()
            await self._release()
        errors = [result for result in results if isinstance(result, Exception)]
        for error in errors:
            if not isinstance(error, OneBotError):
                raise error
        if errors:
            raise errors[0]

    async def run(self) -> None:
        """Reconnect locally without converting normal outages into role failure."""
        self._task = asyncio.current_task()
        delay = 1
        try:
            while self._accepting:
                try:
                    connection = await connect(
                        self._config.ws_url,
                        additional_headers={
                            "Authorization": f"Bearer {self._token}",
                        },
                        open_timeout=5,
                        close_timeout=1,
                        proxy=None,
                    )
                except (
                    OSError,
                    TimeoutError,
                    WebSocketException,
                ) as error:
                    _LOGGER.warning("OneBot 连接重试：%s", type(error).__name__)
                else:
                    self._connection = connection
                    try:
                        await self._session()
                    except OneBotError as error:
                        _LOGGER.warning("OneBot 会话重试：%s", error)
                    finally:
                        self._connection = None
                        await connection.close()
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
        finally:
            self._accepting = False
            if self._confirmed_event_id is not None:
                try:
                    await self.outbox.sent(self._confirmed_event_id)
                    self._confirmed_event_id = None
                except DatabaseLockTimeout:
                    _LOGGER.warning(
                        "OneBot sent confirmation deferred: DB lock timeout"
                    )
            await self._release()

    async def quiesce(self) -> None:
        """Close admission; runtime awaits the run task before database closure."""
        self._accepting = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
