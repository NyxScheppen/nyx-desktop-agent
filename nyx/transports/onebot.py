"""OneBot v11 正向 WebSocket，仅连接本地的指定机器人与用户私聊。"""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import asyncio
import json
import logging
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from functools import partial
from typing import TYPE_CHECKING, Any, TypeVar, cast

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from nyx.config import ConfigError
from nyx.db import DatabaseLockTimeout
from nyx.enums import EventType, Source
from nyx.policy import policy
from nyx.transports.outbox import TransportOutbox
from nyx.types import Event

if TYPE_CHECKING:
    from nyx.app_context import _App

_LOGGER = logging.getLogger(__name__)
_CQ_REPLY = re.compile(r"^\[CQ:reply,id=(-?[0-9]+)\]")
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
            raise ConfigError(
                policy("onebot", "onebot.templates.init_text", str).format(
                    p0=self._config.access_token_env
                )
            )
        self._token = token
        self.name = f"onebot:{self._config.self_id}:{self._config.owner_id}"
        assert app.database is not None
        self.outbox = TransportOutbox(app.database, self.name)
        self._connection: ClientConnection | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._task: asyncio.Task[None] | None = None
        self._accepting = True
        self._confirmed_event_id: str | None = None
        self._confirmed_message_id: str | None = None
        self._confirmed_attempt_id: str | None = None

    @staticmethod
    def _message_id(value: object) -> str | None:
        if type(value) is int:
            return str(value)
        if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value):
            return value
        return None

    @classmethod
    def _text_message(cls, message: object) -> tuple[str, str | None] | None:
        quote_id = None
        if isinstance(message, str):
            quoted = _CQ_REPLY.match(message)
            if quoted is not None:
                quote_id = quoted[1]
                message = message[quoted.end():]
            if "[CQ:" in message:
                return None
            text = message.replace("&#91;", "[").replace("&#93;", "]")
            return text.replace("&amp;", "&"), quote_id
        if not isinstance(message, list):
            return None
        parts: list[str] = []
        for index, segment in enumerate(cast(list[object], message)):
            if not isinstance(segment, dict):
                return None
            values = cast(dict[str, Any], segment)
            data = values.get("data")
            if not isinstance(data, dict):
                return None
            fields = cast(dict[str, Any], data)
            if index == 0 and values.get("type") == "reply":
                quote_id = cls._message_id(fields.get("id"))
                if quote_id is None:
                    return None
            elif values.get("type") == "text" and isinstance(fields.get("text"), str):
                parts.append(fields["text"])
            else:
                return None
        return "".join(parts), quote_id

    async def _quoted_attempt(self, quote_id: str) -> str | None:
        if quote_id == self._confirmed_message_id:
            return self._confirmed_attempt_id
        attempt = await self.outbox.quoted_initiative(quote_id)
        if attempt is not None or self._connection is None:
            return attempt
        try:
            response = await self._call("get_msg", {"message_id": int(quote_id)})
        except OneBotError as error:
            _LOGGER.info(policy("onebot", "quote.lookup_error", str), error)
            return None
        data = response.get("data")
        if not isinstance(data, dict):
            return None
        data = cast(dict[str, Any], data)
        sender = data.get("sender")
        if (
            data.get("message_type") != "private"
            or self._message_id(data.get("message_id")) != quote_id
            or self._message_id(data.get("target_id", self._config.owner_id))
            != str(self._config.owner_id)
            or not isinstance(sender, dict)
            or self._message_id(cast(dict[str, Any], sender).get("user_id"))
            != str(self._config.self_id)
            or type(data.get("time")) is not int
        ):
            return None
        parsed = self._text_message(data.get("message"))
        if parsed is None or parsed[1] is not None:
            return None
        # OneBot timestamps have second precision, local events have fractions.
        return await self.outbox.quoted_initiative(
            quote_id, legacy_text=parsed[0], sent_before=float(data["time"]) + 1
        )

    async def on_external(self, payload: dict[str, Any]) -> None:
        """Admit private text and resolve a leading QQ reply segment."""
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
        parsed = self._text_message(payload.get("message"))
        if parsed is None:
            return
        text, quote_id = parsed
        message_id, timestamp = payload.get("message_id"), payload.get("time")
        if (
            not text.strip()
            or type(message_id) is not int
            or type(timestamp) is not int
        ):
            return
        identity = f"onebot:{self._config.self_id}:{message_id}"
        quote: dict[str, str] = {}
        if quote_id is not None:
            quote["quote_message_id"] = quote_id
            attempt_id = await self._quoted_attempt(quote_id)
            if attempt_id is not None:
                quote["reply_to"] = attempt_id
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
                    **quote,
                },
            )
        )

    async def _call(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        connection = self._connection
        if connection is None:
            raise OneBotError(policy("onebot", "onebot.copy.call_text", str))
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
            response = await asyncio.wait_for(
                future, timeout=policy("onebot", "onebot.parameters.call_timeout", int)
            )
            if response.get("status") != "ok" or response.get("retcode") != 0:
                raise OneBotError(policy("onebot", "onebot.copy.call_text_2", str))
            return response
        except (OSError, TimeoutError, WebSocketException) as error:
            raise OneBotError(type(error).__name__) from None
        finally:
            self._pending.pop(echo, None)

    async def emit(self, event: Event) -> str | None:
        """Send text literally; only a matching successful echo confirms it."""
        text = event.content["content"]
        if event.type is EventType.THINK:
            text = policy("onebot", "onebot.templates.emit_text", str).format(p0=text)
        response = await self._call(
            "send_private_msg",
            {
                "user_id": self._config.owner_id,
                "message": [
                    {"type": "text", "data": {"text": text}}
                ],
            },
        )
        data = response.get("data")
        if isinstance(data, dict):
            return self._message_id(cast(dict[str, Any], data).get("message_id"))
        return None

    async def _receive(self, inbox: asyncio.Queue[dict[str, Any]]) -> None:
        assert self._connection is not None
        while True:
            try:
                raw = await self._connection.recv()
            except (OSError, WebSocketException) as error:
                raise OneBotError(type(error).__name__) from None
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                _LOGGER.warning(policy("onebot", "onebot.copy.receive_text", str))
                continue
            if not isinstance(payload, dict):
                continue
            payload = cast(dict[str, Any], payload)
            echo = payload.get("echo")
            future = self._pending.get(echo) if isinstance(echo, str) else None
            if future is not None and not future.done():
                future.set_result(payload)
            elif "post_type" in payload:
                if payload.get("post_type") == "message":
                    try:
                        inbox.put_nowait(payload)
                    except asyncio.QueueFull:
                        raise OneBotError(
                            policy("onebot", "quote.queue_full", str)
                        ) from None

    async def _admit_inbox(self, inbox: asyncio.Queue[dict[str, Any]]) -> None:
        while True:
            await self.on_external(await inbox.get())

    async def _retry_lock(
        self, phase: str, operation: Callable[[], Awaitable[_T]]
    ) -> _T:
        attempt = 0
        while True:
            try:
                return await operation()
            except DatabaseLockTimeout:
                delay = min(
                    2
                    ** min(
                        attempt, policy("onebot", "delivery.backoff_exponent_max", int)
                    ),
                    policy("onebot", "delivery.backoff_max_seconds", int),
                )
                attempt += 1
                _LOGGER.warning(
                    "OneBot DB lock retry: phase=%s attempt=%d wait=%ds",
                    phase, attempt, delay,
                )
                await asyncio.sleep(delay)

    async def _confirm_sent(self) -> None:
        event_id = self._confirmed_event_id
        if event_id is not None:
            message_id = self._confirmed_message_id
            if message_id is None:
                await self._retry_lock("sent", partial(self.outbox.sent, event_id))
            else:
                await self._retry_lock(
                    "sent", partial(self.outbox.sent, event_id, message_id)
                )
            self._confirmed_event_id = None
            self._confirmed_message_id = None
            self._confirmed_attempt_id = None

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
                await asyncio.sleep(
                    policy("onebot", "onebot.parameters.send_value_factor", float)
                )
                continue
            try:
                self._confirmed_message_id = await self.emit(event)
            except OneBotError as error:
                await self._retry_lock(
                    "retry", partial(self.outbox.retry, event.id, type(error).__name__)
                )
                raise
            self._confirmed_event_id = event.id
            attempt_id = event.content.get("attempt_id")
            if event.type is EventType.INITIATE_CHAT and isinstance(attempt_id, str):
                self._confirmed_attempt_id = attempt_id
            await self._confirm_sent()

    async def _session(self) -> None:
        inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=policy("onebot", "quote.receive_queue_size", int)
        )
        receiver = asyncio.create_task(self._receive(inbox))
        admitter = asyncio.create_task(self._admit_inbox(inbox))

        async def send_after_login() -> None:
            response = await self._call("get_login_info", {})
            data = response.get("data")
            if (
                not isinstance(data, dict)
                or cast(dict[str, Any], data).get("user_id") != self._config.self_id
            ):
                raise OneBotError(
                    policy("onebot", "onebot.copy.send_after_login_text", str)
                )
            await self._send()

        sender = asyncio.create_task(send_after_login())
        tasks = (receiver, sender, admitter)
        try:
            await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in tasks:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
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
                        open_timeout=policy(
                            "onebot", "onebot.parameters.run_open_timeout", int
                        ),
                        close_timeout=policy(
                            "onebot", "delivery.close_timeout_seconds", int
                        ),
                        proxy=None,
                    )
                except (
                    OSError,
                    TimeoutError,
                    WebSocketException,
                ) as error:
                    _LOGGER.warning(
                        policy("onebot", "onebot.copy.run_text", str),
                        type(error).__name__,
                    )
                else:
                    self._connection = connection
                    try:
                        await self._session()
                    except OneBotError as error:
                        _LOGGER.warning(
                            policy("onebot", "onebot.copy.run_text_2", str), error
                        )
                    finally:
                        self._connection = None
                        await connection.close()
                await asyncio.sleep(delay)
                delay = min(
                    delay * 2, policy("onebot", "delivery.backoff_max_seconds", int)
                )
        finally:
            self._accepting = False
            if self._confirmed_event_id is not None:
                try:
                    await self.outbox.sent(
                        self._confirmed_event_id, self._confirmed_message_id
                    )
                    self._confirmed_event_id = None
                    self._confirmed_message_id = None
                    self._confirmed_attempt_id = None
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
