"""Account allowance and its reset time."""

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CodexCoordinator

SENSORS = (
    SensorEntityDescription(
        key="remaining",
        name="Usage remaining",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:percent",
    ),
    SensorEntityDescription(
        key="reset",
        name="Usage reset",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[CodexCoordinator],
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(CodexUsageSensor(entry, description) for description in SENSORS)


class CodexUsageSensor(CoordinatorEntity[CodexCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        entry: ConfigEntry[CodexCoordinator],
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(entry.runtime_data)
        self.entity_description = description
        self._attr_unique_id = f"{entry.unique_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            name="Codex",
            manufacturer="OpenAI",
            model="Account allowance",
        )

    @property
    def native_value(self) -> float | datetime | None:
        if self.entity_description.key == "remaining":
            return self.coordinator.data.remaining_percent
        return self.coordinator.data.resets_at

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key == "remaining":
            return {
                "window_minutes": self.coordinator.data.window_minutes,
                "plan": self.coordinator.data.plan,
            }
        return None
