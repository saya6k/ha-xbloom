"""execute_recipe / execute_tea_recipe fail the call when nothing brews.

Both handlers used to log a warning and return success for an unknown
recipe and for a brew the machine refused (low water, firmware without
tea), so an automation could not tell that nothing was brewed.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant.core")

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.xb.const import (
    DATA_COORDINATOR,
    DOMAIN,
    SERVICE_EXECUTE_RECIPE,
    SERVICE_EXECUTE_TEA_RECIPE,
)
from custom_components.xb.services import async_register_services
from tests.machine_fakes import FakeStudio


class _Services:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}

    def has_service(self, _domain: str, _service: str) -> bool:
        return False

    def async_register(self, _domain, service, handler, **_kwargs) -> None:
        self.handlers[service] = handler


def _setup(error: Exception | None = None):
    calls: list[str] = []

    async def execute(**_kwargs):
        calls.append(studio.selected_recipe)
        if error is not None:
            raise error

    studio = FakeStudio(
        recipes={"Hot": {"cup_type": "Omni"}, "Green": {"cup_type": "Tea"}},
        selected_recipe=None,
        grind_size=50,
        rpm=100,
        async_execute_recipe=execute,
        async_update_listeners=lambda: None,
    )
    studio.select_recipe = lambda name: setattr(studio, "selected_recipe", name)
    services = _Services()
    hass = SimpleNamespace(data={DOMAIN: {"e": {DATA_COORDINATOR: studio}}}, services=services)
    async_register_services(hass)
    return services.handlers, calls


def _call(handler, **data):
    return asyncio.run(handler(SimpleNamespace(data=data)))


def test_execute_recipe_brews_the_named_recipe():
    handlers, calls = _setup()
    _call(handlers[SERVICE_EXECUTE_RECIPE], recipe="Hot")
    assert calls == ["Hot"]


def test_unknown_recipe_fails_the_call():
    handlers, calls = _setup()
    with pytest.raises(ServiceValidationError):
        _call(handlers[SERVICE_EXECUTE_RECIPE], recipe="Nope")
    with pytest.raises(ServiceValidationError):
        _call(handlers[SERVICE_EXECUTE_TEA_RECIPE], recipe="Nope")
    assert calls == []


def test_no_selected_recipe_fails_the_call():
    handlers, _ = _setup()
    with pytest.raises(ServiceValidationError):
        _call(handlers[SERVICE_EXECUTE_RECIPE])


def test_refused_brew_fails_the_call():
    handlers, _ = _setup(HomeAssistantError("xBloom water level is too low"))
    with pytest.raises(HomeAssistantError, match="water level"):
        _call(handlers[SERVICE_EXECUTE_RECIPE], recipe="Hot")
    with pytest.raises(HomeAssistantError, match="water level"):
        _call(handlers[SERVICE_EXECUTE_TEA_RECIPE], recipe="Green")


def test_execute_tea_recipe_refuses_a_coffee_recipe():
    handlers, calls = _setup()
    with pytest.raises(ServiceValidationError):
        _call(handlers[SERVICE_EXECUTE_TEA_RECIPE], recipe="Hot")
    assert calls == []
