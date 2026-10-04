"""Codex usage sensors, polled directly by Home Assistant."""

from homeassistant.const import Platform
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import CodexCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry[CodexCoordinator]
) -> bool:
    coordinator = CodexCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR])
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: ConfigEntry[CodexCoordinator]
) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, [Platform.SENSOR])
