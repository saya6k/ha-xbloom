"""The layering that keeps models and shared features apart.

* Model packages (``models/<model>``) never import each other.
* Shared features (services, LLM tools, panel, recipe store, cloud …)
  never import a model package — they reach machines only through
  ``machine.py``, filtered by capability. (When the shared code assumed
  every coordinator was a Studio, a mixed install crashed the panel's
  machine list and sent untargeted Studio service calls to the Original.)
* Every model's coordinator implements what its capabilities promise.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import textwrap
from pathlib import Path

import pytest

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "xb"


def _imported_modules(path: Path) -> set[str]:
    """Every module a file imports, resolved to an absolute dotted name."""
    package = ".".join(
        ("custom_components", "xb", *path.relative_to(COMPONENT).parent.parts)
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


MODELS = sorted(
    p.name for p in (COMPONENT / "models").iterdir()
    if p.is_dir() and not p.name.startswith("__")
)


@pytest.mark.parametrize("model", MODELS)
def test_models_do_not_import_each_other(model):
    forbidden = tuple(
        f"custom_components.xb.models.{other}" for other in MODELS if other != model
    )
    for path in sorted((COMPONENT / "models" / model).rglob("*.py")):
        leaks = {m for m in _imported_modules(path) if m.startswith(forbidden)}
        assert not leaks, f"{path.relative_to(COMPONENT)} imports {leaks}"


# Shared features: everything except the model packages and the entry
# points that route a config entry to its model (the integration module,
# the platform modules and the config flow).
_ROUTERS = {
    "__init__.py", "binary_sensor.py", "button.py", "event.py", "number.py",
    "select.py", "sensor.py", "switch.py", "update.py", "config_flow.py",
}
SHARED = sorted(
    path for path in COMPONENT.rglob("*.py")
    if "models" not in path.relative_to(COMPONENT).parts
    and str(path.relative_to(COMPONENT)) not in _ROUTERS
)


@pytest.mark.parametrize("path", SHARED, ids=lambda p: str(p.relative_to(COMPONENT)))
def test_shared_features_never_import_a_model(path):
    leaks = {
        m for m in _imported_modules(path)
        if m.startswith("custom_components.xb.models")
    }
    assert not leaks, f"{path.relative_to(COMPONENT)} imports {leaks}"


# The members each capability promises (machine.py's protocols).
_BASE = {"capabilities", "machine_label", "unreachable_hint",
         "async_ensure_available", "status_summary"}
_BY_CAPABILITY = {
    "grind": {"limits", "grind_size", "armed_operation", "async_arm_grind",
              "async_confirm_grind"},
    "grind_speed": {"rpm"},
    "pour_flow_rate": {"flow_rate"},
    "pour": {"limits", "temperature", "volume", "armed_operation",
             "async_arm_pour", "async_confirm_pour"},
    "cancel": {"async_cancel"},
    "calibrate_grinder": {"async_calibrate_grinder"},
    "tare": {"async_tare_scale"},
    "recipes": {"recipes", "selected_recipe", "list_local_recipes",
                "async_execute_recipe", "water_shortage", "cloud_login_configured"},
    "easy_slots": {"async_write_easy_slot", "easy_slot_contents"},
    "pods": {"pod_library", "_async_lookup_pod"},
    "advanced_settings": {"async_set_advanced_settings"},
    "photo": {"async_read_photo", "photo_version", "detected_color"},
}


def _coordinators():
    pytest.importorskip("homeassistant.components.event")
    from custom_components.xb.models.j15.coordinator import XBloomCoordinator
    from custom_components.xb.models.j20.coordinator import J20Coordinator

    return [XBloomCoordinator, J20Coordinator]


def _members(cls) -> set[str]:
    """Class attributes plus the instance attributes __init__ assigns."""
    names = set(dir(cls))
    for klass in cls.__mro__:
        init = klass.__dict__.get("__init__")
        if init is None or not hasattr(init, "__code__"):
            continue
        tree = ast.parse(textwrap.dedent(inspect.getsource(init)))
        names |= {
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name) and node.value.id == "self"
        }
    return names


def test_every_model_implements_its_capabilities():
    for cls in _coordinators():
        members = _members(cls)
        missing = _BASE - members
        for capability in cls.capabilities:
            missing |= _BY_CAPABILITY[capability.value] - members
        assert not missing, f"{cls.__name__} lacks {sorted(missing)}"


@pytest.mark.parametrize(
    "module",
    [
        "custom_components.xb",
        "custom_components.xb.binary_sensor",
        "custom_components.xb.button",
        "custom_components.xb.event",
        "custom_components.xb.number",
        "custom_components.xb.select",
        "custom_components.xb.sensor",
        "custom_components.xb.switch",
        "custom_components.xb.update",
        "custom_components.xb.config_flow",
    ],
)
def test_entry_points_import(module):
    pytest.importorskip("homeassistant.components.event")
    mod = importlib.import_module(module)
    assert callable(getattr(mod, "async_setup_entry", None) or mod.XBloomConfigFlow)
