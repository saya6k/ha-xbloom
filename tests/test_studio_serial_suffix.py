"""Studios are told apart by the serial suffix they advertise ("XBLOOM 4CV030").

Live 2026-11-19: Bluetooth discovery listed the machine as "XBLOOM 4CV030"
(the serial number's last six characters) while the device was named after
the MAC ("xBloom Studio F64F"). Both now use the serial suffix.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant.core")

from custom_components.xb.models.j15.coordinator import (
    XBloomCoordinator,
    serial_suffix_from_name,
)


@pytest.mark.parametrize(
    ("name", "suffix"),
    [
        ("XBLOOM 4CV030", "4CV030"),
        ("xBloom 4cv030", "4CV030"),
        (" XBLOOM 4CV030 ", "4CV030"),
        ("XBLOOM", None),
        ("XBLOOM 4CV03", None),
        ("Other 4CV030", None),
        ("", None),
        (None, None),
    ],
)
def test_serial_suffix_from_advertised_name(name, suffix):
    assert serial_suffix_from_name(name) == suffix


def _name(serial_suffix):
    fake = SimpleNamespace(mac_address="68:79:C4:0F:F6:4F", serial_suffix=serial_suffix)
    return XBloomCoordinator.machine_name.fget(fake)


def test_machine_name_uses_the_serial_suffix():
    assert _name("4CV030") == "xBloom Studio 4CV030"


def test_machine_name_falls_back_to_the_mac():
    assert _name(None) == "xBloom Studio F64F"
