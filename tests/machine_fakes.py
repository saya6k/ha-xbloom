"""Duck-typed machine stand-ins for tests of the shared features.

``FakeStudio`` is a ``SimpleNamespace`` that carries the Studio's real
``machine.Machine`` implementation (capabilities, limits, connect gate,
status summary …) bound to whatever attributes a test sets, so tests of
the LLM tools / panel / services keep setting the same ``client``,
``async_connect``, ``_armed_operation``, ``data`` they always did.
"""
from __future__ import annotations

from types import SimpleNamespace

from custom_components.xb.models.j15.coordinator import XBloomCoordinator

_STUDIO = XBloomCoordinator


class FakeStudio(SimpleNamespace):
    capabilities = _STUDIO.capabilities
    limits = _STUDIO.limits
    unreachable_hint = _STUDIO.unreachable_hint
    arming_buttons = _STUDIO.arming_buttons
    async_ensure_available = _STUDIO.async_ensure_available
    status_summary = _STUDIO.status_summary
    water_shortage = _STUDIO.water_shortage
    detected_color = _STUDIO.detected_color
    photo_version = _STUDIO.photo_version
    async_read_photo = _STUDIO.async_read_photo
    armed_operation = property(lambda self: getattr(self, "_armed_operation", None))
    machine_label = property(lambda self: getattr(self, "mac_address", "AA:BB:CC:DD:EE:FF"))
    # The real one falls back to the device registry; tests use data only.
    machine_serial = property(lambda self: (getattr(self, "data", None) or {}).get("serial_number"))
