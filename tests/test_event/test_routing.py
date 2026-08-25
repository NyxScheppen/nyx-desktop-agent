from nyx.enums import EventType, TickType
from nyx.events.routing import ROUTING, TICK_ROUTING

_VALID_MODULES = {
    "expression", "inner_life", "desire", "activity", "memory", "encounter"
}


def test_routing_keys_are_all_event_types_except_clock_tick() -> None:
    assert set(ROUTING) == set(EventType) - {EventType.CLOCK_TICK}


def test_tick_routing_keys_are_all_tick_types() -> None:
    assert set(TICK_ROUTING) == set(TickType)


def test_routing_values_are_known_modules() -> None:
    for modules in list(ROUTING.values()) + list(TICK_ROUTING.values()):
        assert set(modules) <= _VALID_MODULES
