"""Dependency assembly for small, in-process agent plugin sets."""

from __future__ import annotations

from collections.abc import Awaitable, Mapping, Sequence
from types import MappingProxyType
from typing import Protocol, TypeVar


class PluginContext:
    """Read-only capability view shared by plugin factories and lifecycle hooks."""

    def __init__(
        self,
        capabilities: Mapping[str, object],
        declared: tuple[str, ...],
    ) -> None:
        self._capabilities = (
            MappingProxyType(capabilities)
            if isinstance(capabilities, dict)
            else capabilities
        )
        self._declared = frozenset(declared)

    def get(self, capability: str) -> object | None:
        """Reject undeclared access and return None for an unbound optional value."""
        if capability not in self._declared:
            raise RuntimeError(f"undeclared capability: {capability}")
        return self._capabilities.get(capability)


class Plugin(Protocol):
    name: str
    provides: tuple[str, ...]
    requires: tuple[str, ...]
    optional: tuple[str, ...]
    context: PluginContext

    def factory(self, ctx: PluginContext, /) -> Awaitable[Mapping[str, object]]: ...
    def tools(self) -> Sequence[object]: ...
    async def start(self, ctx: PluginContext) -> None: ...


PluginT = TypeVar("PluginT", bound=Plugin)


def sort_plugins(plugins: Sequence[PluginT]) -> tuple[PluginT, ...]:
    """Resolve strong dependencies, breaking ties by declaration order."""
    providers = {capability for plugin in plugins for capability in plugin.provides}
    for plugin in plugins:
        missing = set(plugin.requires) - providers
        if missing:
            raise RuntimeError(f"{plugin.name}: missing requires {sorted(missing)}")
    pending = list(plugins)
    ordered: list[PluginT] = []
    available: set[str] = set()
    while pending:
        ready_index = next(
            (
                i for i, plugin in enumerate(pending)
                if set(plugin.requires) <= available
            ),
            None,
        )
        if ready_index is None:
            raise RuntimeError(f"requires cycle: {[plugin.name for plugin in pending]}")
        ready = pending.pop(ready_index)
        ordered.append(ready)
        available.update(ready.provides)
    return tuple(ordered)


def validate_optional(
    plugins: Sequence[Plugin],
    capabilities: Mapping[str, object],
    *,
    degradable: tuple[str, ...] = (),
) -> None:
    """Check required weak edges before invoking any start hook."""
    for plugin in plugins:
        missing = [
            capability
            for capability in plugin.optional
            if capability not in degradable and capabilities.get(capability) is None
        ]
        if missing:
            raise RuntimeError(f"{plugin.name}: unbound optional {missing}")


async def build_plugins(
    plugins: Sequence[Plugin],
    capabilities: dict[str, object],
) -> None:
    """Construct plugins after validating their complete strong dependency graph."""
    foundation = tuple(capabilities)
    for plugin in sort_plugins(plugins):
        plugin.context = PluginContext(
            capabilities,
            foundation + plugin.provides + plugin.requires + plugin.optional,
        )
        capabilities.update(await plugin.factory(plugin.context))
        tool_capability = f"{plugin.name}_tools"
        if tool_capability in plugin.provides:
            capabilities[tool_capability] = plugin.tools()


async def start_plugins(
    plugins: Sequence[Plugin],
    capabilities: Mapping[str, object],
    *,
    degradable: tuple[str, ...] = (),
) -> None:
    """Validate weak edges before running lifecycle hooks in dependency order."""
    validate_optional(plugins, capabilities, degradable=degradable)
    for plugin in sort_plugins(plugins):
        await plugin.start(plugin.context)
