"""Base tool class for XBloom LLM tools."""

from __future__ import annotations

from typing import Any, ClassVar

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from custom_components.xbloom.const import DOMAIN
from custom_components.xbloom.machine import Capability


class XBloomBaseTool(llm.Tool):
    """Base class for XBloom LLM tools.

    Holds a reference to one machine (any model — see ``machine.py``) so
    tools can read state and act through the same code path the entities
    use. ``required`` lists the capabilities the machine must have for the
    catalog to offer the tool at all.
    """

    integration = DOMAIN
    required: ClassVar[frozenset[Capability]] = frozenset()

    def __init__(self, coordinator: Any, hass: HomeAssistant) -> None:
        super().__init__()
        self.coordinator = coordinator
        self.hass = hass

    @property
    def _arms_on_machine(self) -> bool:
        """Whether arming opens a page on the machine (the Studio) or only
        records the request locally until it is confirmed."""
        return bool(getattr(self.coordinator, "arming_buttons", False))

    def _armed_text(self, page: str) -> str:
        """What the user should be told an arm did."""
        if self._arms_on_machine:
            return f"The machine is now showing its {page} page with these settings. "
        return "Nothing has been sent to the machine yet. "

    async def _unavailable(self) -> llm.ToolResult | None:
        """None when the machine is reachable, else the tool's error result."""
        if await self.coordinator.async_ensure_available():
            return None
        return llm.ToolResult(
            data={
                "success": False,
                "error": "connect_failed",
                "instruction": self.coordinator.unreachable_hint,
            },
            error=True,
        )
