# pyright: reportPrivateUsage=false
from collections.abc import Mapping
from dataclasses import replace

import pytest


def test_topological_order_is_stable_and_resolves_capabilities() -> None:
    from nyx.harness import sort_plugins
    from nyx.plugins import _Plugin

    plugins = (
        _Plugin("consumer", _unused, ("consumer",), ("state_reader",)),
        _Plugin("independent", _unused, ("independent",)),
        _Plugin("provider", _unused, ("provider", "state_reader")),
    )
    assert [plugin.name for plugin in sort_plugins(plugins)] == [
        "independent",
        "provider",
        "consumer",
    ]


@pytest.mark.parametrize("requires", [("missing",), ("consumer",)])
def test_invalid_strong_dependencies_fail_before_construction(
    requires: tuple[str, ...],
) -> None:
    from nyx.harness import sort_plugins
    from nyx.plugins import _Plugin

    with pytest.raises(RuntimeError, match="consumer"):
        sort_plugins((_Plugin("consumer", _unused, ("consumer",), requires),))


def test_strong_cycle_is_rejected_but_optional_cycle_is_allowed() -> None:
    from nyx.harness import sort_plugins
    from nyx.plugins import _Plugin

    first = _Plugin("first", _unused, ("first",), ("second",))
    second = _Plugin("second", _unused, ("second",), ("first",))
    with pytest.raises(RuntimeError, match="first.*second"):
        sort_plugins((first, second))
    second.requires = ()
    second.optional = ("first",)
    assert [plugin.name for plugin in sort_plugins((first, second))] == [
        "second",
        "first",
    ]


def test_optional_binding_validation_distinguishes_degradable_edges() -> None:
    from nyx.plugins import _Plugin, validate_optional

    plugin = _Plugin(
        "consumer",
        _unused,
        ("consumer",),
        optional=("state_reader", "reading_context", "on_failure"),
    )
    with pytest.raises(RuntimeError, match="state_reader"):
        validate_optional((plugin,), {})
    validate_optional((plugin,), {"state_reader": object()})


async def test_unbound_optional_prevents_all_start_hooks() -> None:
    from nyx.plugins import PluginContext, _Plugin, start_plugins

    started: list[str] = []

    async def start(ctx: PluginContext) -> None:
        started.append("started")

    first = _Plugin("first", _unused, ("first",), starter=start)
    second = _Plugin("second", _unused, ("second",), optional=("state_reader",))
    with pytest.raises(RuntimeError, match="state_reader"):
        await start_plugins((first, second), {})
    assert started == []


def test_context_only_exposes_declared_capabilities_and_observes_injection() -> None:
    from nyx.harness import PluginContext

    capabilities: dict[str, object] = {}
    ctx = PluginContext(capabilities, ("reading_context",))
    assert ctx.get("reading_context") is None
    capabilities["reading_context"] = reader = object()
    assert ctx.get("reading_context") is reader
    with pytest.raises(RuntimeError, match="undeclared"):
        ctx.get("undeclared")


def test_plugin_routes_equal_all_seventeen_runtime_routes() -> None:
    from nyx.events.routing import ROUTE_SPECS
    from nyx.harness import sort_plugins
    from nyx.plugins import default_plugins, validate_routes

    plugins = default_plugins()
    validate_routes(plugins)
    routes = [route for plugin in plugins for route in plugin.routes()]
    assert len(routes) == 17
    assert set(routes) == set(ROUTE_SPECS)
    assert [plugin.name for plugin in sort_plugins(plugins)] == [
        "eval",
        "memory",
        "tools",
        "desire",
        "activity",
        "inner_life",
        "expression",
        "reading",
    ]


@pytest.mark.parametrize("remove", [True, False])
def test_route_drift_is_rejected_even_with_matching_consumer_id(remove: bool) -> None:
    from nyx.plugins import default_plugins, validate_routes

    plugins = default_plugins()
    spec, handler = plugins[1].route_bindings[0]
    plugins[1].route_bindings = (
        () if remove else ((replace(spec, max_attempts=1), handler),)
    ) + plugins[1].route_bindings[1:]
    with pytest.raises(RuntimeError, match="route"):
        validate_routes(plugins)


async def _unused(ctx: object) -> Mapping[str, object]:
    return {}


