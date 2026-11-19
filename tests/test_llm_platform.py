"""The llm/ platform package: api_id gating, catalog, lazy-loading invariants.

Covers SPEC §4.2/§5-2 (tasks/2026-07-llm-platform-migration-spec.md):
- our tools never ride along in Assist or any foreign API (None gating),
- the catalog builds the full 13-tool set bound to one machine,
- the platform entry module stays import-light (AST) so a foreign API's
  tool collection never loads the tool implementations,
- the setup path never imports the llm/ package (AST, active from T3).

Gating/catalog/AST tests run on any HA version; only the success path of
``async_get_tools`` needs ``homeassistant.components.llm`` (HA ≥ 2026.8,
the devcontainer image) and skips elsewhere.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.xbloom.const import (
    DATA_COORDINATOR,
    DOMAIN,
    XBLOOM_LLM_API_ID,
    XBLOOM_LLM_PROMPT,
)
from tests.machine_fakes import FakeStudio
from tests.test_llm_prompt import REGISTERED_TOOLS

COMPONENT_ROOT = (
    Path(__file__).resolve().parent.parent / "custom_components" / "xbloom"
)

# Modules that hold tool implementations (plus the catalog that aggregates
# them), all under llm/. None of these may be imported at module level
# by the platform hook llm/__init__.py.
TOOL_MODULES = {
    "base",
    "catalog",
    "cloud_recipe",
    "local_recipe",
    "pour",
    "recipe",
    "slot",
    "status",
    "tare",
}


def _fake_hass(domain_data: dict) -> SimpleNamespace:
    return SimpleNamespace(data={DOMAIN: domain_data})


def _api_id(entry_id: str) -> str:
    return f"{XBLOOM_LLM_API_ID}_{entry_id}"


def _entry(coordinator: SimpleNamespace) -> dict:
    return {DATA_COORDINATOR: coordinator}


# --- api_id gating (None paths — no HA 2026.8 dependency) -----------------


def test_assist_api_gets_none():
    from custom_components.xbloom import llm as llm_platform

    hass = _fake_hass({"entry1": _entry(SimpleNamespace())})
    assert llm_platform.async_get_tools(hass, None, "assist") is None


def test_foreign_api_gets_none():
    from custom_components.xbloom import llm as llm_platform

    hass = _fake_hass({"entry1": _entry(SimpleNamespace())})
    assert llm_platform.async_get_tools(hass, None, "some_other_api") is None


def test_unknown_entry_gets_none():
    from custom_components.xbloom import llm as llm_platform

    hass = _fake_hass({"entry1": _entry(SimpleNamespace())})
    assert llm_platform.async_get_tools(hass, None, _api_id("nope")) is None


def test_no_domain_data_gets_none():
    from custom_components.xbloom import llm as llm_platform

    hass = SimpleNamespace(data={})
    assert llm_platform.async_get_tools(hass, None, _api_id("entry1")) is None


def test_non_entry_domain_key_gets_none():
    """hass.data[DOMAIN] holds non-entry keys (e.g. yaml_recipes) — never a match."""
    from custom_components.xbloom import llm as llm_platform

    hass = _fake_hass({"yaml_recipes": {"Recipe": {}}})
    assert llm_platform.async_get_tools(hass, None, _api_id("yaml_recipes")) is None


# --- catalog (builds on any HA version) ------------------------------------


def test_catalog_builds_the_full_tool_set():
    from custom_components.xbloom.llm.catalog import build_tools

    coordinator, hass = FakeStudio(), SimpleNamespace()
    tools = build_tools(coordinator, hass)
    assert len(tools) == len(REGISTERED_TOOLS)
    assert {tool.name for tool in tools} == set(REGISTERED_TOOLS)
    assert all(tool.coordinator is coordinator for tool in tools)
    assert all(tool.hass is hass for tool in tools)


def test_catalog_offers_only_what_the_machine_can_do():
    """A model without recipes (the Original) gets no recipe/slot/tare tools,
    and its own ranges in the grind/pour schemas."""
    from custom_components.xbloom.llm.catalog import build_tools
    from custom_components.xbloom.models.j20.coordinator import J20Coordinator

    original = SimpleNamespace(
        capabilities=J20Coordinator.capabilities, limits=J20Coordinator.limits
    )
    tools = {tool.name: tool for tool in build_tools(original, SimpleNamespace())}
    assert set(tools) == {
        "xbloom__get_status",
        "xbloom__grind",
        "xbloom__pour",
        "xbloom__cancel",
        "xbloom__calibrate_grinder",
    }
    grind_schema = tools["xbloom__grind"].parameters
    assert grind_schema({"grind_size": 30})["grind_size"] == 30
    with pytest.raises(Exception):  # noqa: B017 — voluptuous Invalid
        grind_schema({"grind_size": 31})
    assert "rpm" not in {str(key) for key in grind_schema.schema}
    assert "flow_rate" not in {str(key) for key in tools["xbloom__pour"].parameters.schema}
    # No arm page on the machine and no recipes: the tool text says so.
    assert "nothing is sent" in tools["xbloom__grind"].description
    assert "execute_recipe" not in tools["xbloom__grind"].description
    assert "GRIND SIZE REFERENCE" not in str(grind_schema.schema)


def test_prompt_only_describes_what_the_machine_can_do():
    from custom_components.xbloom.llm.catalog import build_prompt
    from custom_components.xbloom.models.j20.coordinator import J20Coordinator

    assert build_prompt(FakeStudio()) == XBLOOM_LLM_PROMPT
    original = SimpleNamespace(
        capabilities=J20Coordinator.capabilities, limits=J20Coordinator.limits
    )
    prompt = build_prompt(original)
    for absent in (
        "xbloom__execute_recipe", "xbloom__list_recipes", "xbloom__tare_scale",
        "xbloom__write_easy_slot", "Bluetooth", "RPM", "80=coarsest",
    ):
        assert absent not in prompt, absent
    assert "1=finest to 30=coarsest" in prompt
    assert "30–500 ml at 85–95 °C" in prompt
    for tool in ("get_status", "grind", "pour", "cancel", "calibrate_grinder"):
        assert f"xbloom__{tool}" in prompt


def test_arm_reply_matches_where_the_arm_lives():
    from custom_components.xbloom.llm.pour import XBloomPourTool
    from custom_components.xbloom.models.j20.coordinator import J20Coordinator

    studio = XBloomPourTool(FakeStudio(), None)
    assert "showing its pour page" in studio._armed_text("pour")
    original = XBloomPourTool(
        SimpleNamespace(capabilities=J20Coordinator.capabilities, limits=J20Coordinator.limits),
        None,
    )
    assert original._armed_text("pour") == "Nothing has been sent to the machine yet. "


def test_catalog_builds_fresh_instances_per_call():
    from custom_components.xbloom.llm.catalog import build_tools

    coord_a, coord_b = FakeStudio(), FakeStudio()
    hass = SimpleNamespace()
    tools_a = build_tools(coord_a, hass)
    tools_b = build_tools(coord_b, hass)
    assert all(tool.coordinator is coord_a for tool in tools_a)
    assert all(tool.coordinator is coord_b for tool in tools_b)
    assert not {id(t) for t in tools_a} & {id(t) for t in tools_b}


# --- success path (needs homeassistant.components.llm — HA ≥ 2026.8) ------


def test_own_api_returns_tools_and_prompt():
    pytest.importorskip(
        "homeassistant.components.llm", reason="HA ≥ 2026.8 (devcontainer image)"
    )
    from custom_components.xbloom import llm as llm_platform

    coordinator = FakeStudio()
    hass = _fake_hass({"entry1": _entry(coordinator)})
    result = llm_platform.async_get_tools(hass, None, _api_id("entry1"))
    assert result is not None
    assert {tool.name for tool in result.tools} == set(REGISTERED_TOOLS)
    assert result.prompt == XBLOOM_LLM_PROMPT
    assert all(tool.coordinator is coordinator for tool in result.tools)


def test_two_entries_each_bind_their_own_coordinator():
    pytest.importorskip(
        "homeassistant.components.llm", reason="HA ≥ 2026.8 (devcontainer image)"
    )
    from custom_components.xbloom import llm as llm_platform

    coord_a, coord_b = FakeStudio(), FakeStudio()
    hass = _fake_hass({"entry_a": _entry(coord_a), "entry_b": _entry(coord_b)})
    result_a = llm_platform.async_get_tools(hass, None, _api_id("entry_a"))
    result_b = llm_platform.async_get_tools(hass, None, _api_id("entry_b"))
    assert all(tool.coordinator is coord_a for tool in result_a.tools)
    assert all(tool.coordinator is coord_b for tool in result_b.tools)


# --- API shell (llm_api.py) delegates to the platform ----------------------


class _ShellHass(SimpleNamespace):
    """Minimal hass satisfying async_import_module + the API shell."""

    def __init__(self, domain_data: dict) -> None:
        super().__init__(data={DOMAIN: domain_data})

    @property
    def loop(self):
        return asyncio.get_running_loop()

    async def async_add_import_executor_job(self, func, *args):
        return func(*args)


def test_api_shell_keeps_id_and_name_format():
    from custom_components.xbloom.const import XBLOOM_LLM_API_NAME
    from custom_components.xbloom.llm_api import XBloomCoffeeAPI

    api = XBloomCoffeeAPI(_ShellHass({}), "entry1", "AA:BB:CC:DD:EE:FF")
    assert api.id == f"{XBLOOM_LLM_API_ID}_entry1"
    assert api.name == f"{XBLOOM_LLM_API_NAME} (AA:BB:CC:DD:EE:FF)"


def test_api_shell_builds_instance_via_platform():
    pytest.importorskip(
        "homeassistant.components.llm", reason="HA ≥ 2026.8 (devcontainer image)"
    )
    from custom_components.xbloom.llm_api import XBloomCoffeeAPI

    coordinator = FakeStudio()
    hass = _ShellHass({"entry1": _entry(coordinator)})
    api = XBloomCoffeeAPI(hass, "entry1", "AA:BB:CC:DD:EE:FF")
    instance = asyncio.run(api.async_get_api_instance(None))
    assert {tool.name for tool in instance.tools} == set(REGISTERED_TOOLS)
    assert instance.api_prompt == XBLOOM_LLM_PROMPT
    assert all(tool.coordinator is coordinator for tool in instance.tools)


def test_api_shell_raises_when_entry_is_gone():
    """Platform returns None (entry unloaded) → the shell must raise, not
    hand the agent an empty APIInstance."""
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.xbloom.llm_api import XBloomCoffeeAPI

    hass = _ShellHass({})
    api = XBloomCoffeeAPI(hass, "gone", "AA:BB:CC:DD:EE:FF")
    with pytest.raises(HomeAssistantError):
        asyncio.run(api.async_get_api_instance(None))


# --- lazy-loading invariants (AST — SPEC §8) --------------------------------


def _module_level_imports(path: Path) -> tuple[set[str], set[str]]:
    """Return (relative modules, absolute modules) imported at module level.

    Only top-level statements count — imports inside functions (the lazy
    path) and under ``if TYPE_CHECKING:`` are intentionally allowed.
    """
    tree = ast.parse(path.read_text())
    relative: set[str] = set()
    absolute: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            if node.level >= 1:
                # ``from . import x`` has module=None — record the names.
                if node.module is None:
                    relative.update(alias.name for alias in node.names)
                else:
                    relative.add(node.module)
            else:
                absolute.add(node.module or "")
        elif isinstance(node, ast.Import):
            absolute.update(alias.name for alias in node.names)
    return relative, absolute


def test_platform_entry_module_stays_light():
    """llm/__init__.py must not pull tool/catalog modules (or components.llm)
    at module level — a foreign API's collection would load them otherwise."""
    relative, absolute = _module_level_imports(COMPONENT_ROOT / "llm" / "__init__.py")
    heavy = {mod for mod in relative if mod.split(".")[0] in TOOL_MODULES}
    # Reaching a machine model at module level would load the tools' deps.
    heavy |= {mod for mod in absolute if ".models" in mod}
    heavy |= {mod for mod in relative if mod.split(".")[0] == "models"}
    assert not heavy, f"llm/__init__.py imports tool modules at module level: {heavy}"
    assert "homeassistant.components.llm" not in absolute, (
        "llm/__init__.py must import homeassistant.components.llm inside "
        "async_get_tools (success path only) so the module also loads on "
        "pre-2026.8 test hosts"
    )


def test_setup_path_never_imports_the_platform_package():
    """The setup path (__init__.py, the model packages' __init__, llm_api.py)
    must not import the llm package or any llm.* submodule at module level —
    a submodule import executes llm/__init__.py (and the catalog pulls every
    tool), defeating lazy loading."""
    platform = "custom_components.xbloom.llm"
    for fname in (
        "__init__.py",
        "models/j15/__init__.py",
        "models/j20/__init__.py",
        "llm_api.py",
    ):
        relative, absolute = _module_level_imports(COMPONENT_ROOT / fname)
        offending = {mod for mod in relative if mod == "llm" or mod.startswith("llm.")}
        offending |= {
            mod for mod in absolute if mod == platform or mod.startswith(f"{platform}.")
        }
        assert not offending, f"{fname} imports the llm/ package: {offending}"
