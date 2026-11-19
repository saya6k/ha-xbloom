"""Studio (j15) and Original (j20) never reach into each other.

Studio's services, sidebar panel and LLM platform iterate
``hass.data[DOMAIN]`` and treat every coordinator found there as an
``XBloomCoordinator``. When the Original kept its ``J20Coordinator`` there
too, a mixed install crashed the panel's machine list and sent untargeted
service calls to the Original. The Original's coordinator now lives in
``entry.runtime_data``, and neither package imports the other.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "xbloom"


def _imported_modules(path: Path) -> set[str]:
    """Every module a file imports, resolved to an absolute dotted name."""
    package = ".".join(
        ("custom_components", "xbloom", *path.relative_to(COMPONENT).parent.parts)
    )
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.rsplit(".", node.level - 1)[0]
                module = f"{base}.{node.module}" if node.module else base
                found.add(module)
                if node.module is None:
                    found.update(f"{base}.{alias.name}" for alias in node.names)
            else:
                found.add(node.module or "")
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


@pytest.mark.parametrize(("family", "other"), [("j15", "j20"), ("j20", "j15")])
def test_families_do_not_import_each_other(family, other):
    forbidden = f"custom_components.xbloom.{other}"
    for path in sorted((COMPONENT / family).rglob("*.py")):
        leaks = {m for m in _imported_modules(path) if m.startswith(forbidden)}
        assert not leaks, f"{path.relative_to(COMPONENT)} imports {leaks}"


def test_original_never_stores_state_in_domain_data():
    for path in sorted((COMPONENT / "j20").rglob("*.py")):
        uses = [
            node.lineno
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, ast.Attribute)
            and node.attr == "data"
            and isinstance(node.value, ast.Name)
            and node.value.id == "hass"
        ]
        assert not uses, f"{path.relative_to(COMPONENT)} reads hass.data at {uses}"


@pytest.mark.parametrize(
    "module",
    [
        "custom_components.xbloom",
        "custom_components.xbloom.binary_sensor",
        "custom_components.xbloom.button",
        "custom_components.xbloom.event",
        "custom_components.xbloom.number",
        "custom_components.xbloom.select",
        "custom_components.xbloom.sensor",
        "custom_components.xbloom.switch",
        "custom_components.xbloom.update",
        "custom_components.xbloom.config_flow",
    ],
)
def test_entry_points_import(module):
    pytest.importorskip("homeassistant.components.event")
    mod = importlib.import_module(module)
    assert callable(getattr(mod, "async_setup_entry", None) or mod.XBloomConfigFlow)
