"""外部感官的入向与出向契约。"""

from typing import Any, Protocol

from nyx.types import Event


class TransportAdapter(Protocol):
    name: str

    async def on_external(self, payload: dict[str, Any]) -> None: ...
    async def emit(self, event: Event) -> None: ...
    async def run(self) -> None: ...
    async def quiesce(self) -> None: ...
