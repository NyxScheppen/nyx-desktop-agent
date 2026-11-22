"""Retry an invalid model output at the producing step, with concrete feedback."""

from collections.abc import Awaitable, Callable
from typing import TypeVar

from nyx.policy import policy

_T = TypeVar("_T")


async def validated(produce: Callable[[str | None], Awaitable[_T]]) -> _T:
    error: str | None = None
    for attempt in range(policy("llm", "validation.attempts", int)):
        try:
            return await produce(error)
        except ValueError as exc:
            if attempt + 1 == policy("llm", "validation.attempts", int):
                raise
            error = policy("llm", "validation.retry", str).format(errors=str(exc))
    raise AssertionError("unreachable validation loop")
