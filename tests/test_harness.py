from collections.abc import Mapping
from dataclasses import dataclass, field

import pytest
from nyx_harness import PluginContext, build_plugins, start_plugins


@dataclass
class Component:
    context: PluginContext = field(init=False)
    name: str
    provides: tuple[str, ...]
    requires: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    started: bool = False

    async def factory(self, ctx: PluginContext) -> Mapping[str, object]:
        return {self.name: self}

    def tools(self) -> list[object]:
        return []

    async def start(self, ctx: PluginContext) -> None:
        self.started = True


async def test_independent_components_bind_before_start_without_nyx() -> None:
    consumer = Component("consumer", ("consumer",), optional=("provider",))
    provider = Component("provider", ("provider",), requires=("consumer",))
    capabilities: dict[str, object] = {}
    await build_plugins((consumer, provider), capabilities)
    await start_plugins((consumer, provider), capabilities)
    assert consumer.context.get("provider") is provider
    assert consumer.started and provider.started


async def test_invalid_dependencies_do_not_construct_any_component() -> None:
    first = Component("first", ("first",))
    broken = Component("broken", ("broken",), requires=("missing",))
    capabilities: dict[str, object] = {}
    with pytest.raises(RuntimeError, match="missing"):
        await build_plugins((first, broken), capabilities)
    assert capabilities == {}


async def test_missing_binding_prevents_all_starts() -> None:
    first = Component("first", ("first",))
    broken = Component("broken", ("broken",), optional=("unbound",))
    capabilities: dict[str, object] = {}
    await build_plugins((first, broken), capabilities)
    with pytest.raises(RuntimeError, match="unbound"):
        await start_plugins((first, broken), capabilities)
    assert not first.started and not broken.started


async def test_out_of_order_dependencies_do_not_compare_unbound_contexts() -> None:
    consumer = Component("consumer", ("consumer",), requires=("provider",))
    provider = Component("provider", ("provider",))
    capabilities: dict[str, object] = {}
    await build_plugins((consumer, provider), capabilities)
    assert list(capabilities) == ["provider", "consumer"]
