"""Support for Navien switches."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN

def _device_name(hub, channel) -> str:
    dev = hub.device_info.get("deviceInfo", {}) if getattr(hub, "device_info", None) else {}
    name = dev.get("deviceName", "Navien")
    return f"{name} CH{channel.channel_number}"

def _device_ident(hub, channel) -> str:
    """Return a stable unique identifier for a channel.

    IMPORTANT: Some accounts can contain multiple devices that share the same gateway/homeSeq/macAddress
    and even channel numbers. Include deviceId/deviceSeq (when available) to avoid unique_id collisions.
    """
    dev = hub.device_info.get("deviceInfo", {}) if getattr(hub, "device_info", None) else {}
    mac = dev.get("macAddress", "unknown")
    home = str(dev.get("homeSeq", ""))
    device_id = str(
        dev.get("deviceId")
        or dev.get("deviceSeq")
        or dev.get("deviceNo")
        or dev.get("deviceUid")
        or dev.get("additionalValue")
        or ""
    )
    device_type = str(dev.get("deviceType", ""))
    # channel_number is still useful to keep entities grouped per channel
    return f"{home}_{mac}_{device_type}_{device_id}_{channel.channel_number}"

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    hubs = data.get("hubs", [])
    entities: list[SwitchEntity] = []

    for hub in hubs:
        try:
            await asyncio.wait_for(hub.ready_event.wait(), timeout=60)
        except Exception:  # noqa: BLE001
            continue

        for channel in hub.channels.values():
            entities.append(NavienPowerSwitch(hub, channel))
            if channel.channel_info.get("onDemandUse", 2) == 1:
                entities.append(NavienHotButtonSwitch(hub, channel))

    async_add_entities(entities)

import asyncio

class _BaseNavienSwitch(SwitchEntity):
    def __init__(self, hub, channel) -> None:
        self.hub = hub
        self.channel = channel
        self._attr_has_entity_name = True

    async def async_added_to_hass(self) -> None:
        self.channel.register_callback(self.async_write_ha_state)

    async def async_will_remove_from_hass(self) -> None:
        self.channel.deregister_callback(self.async_write_ha_state)

    @property
    def available(self) -> bool:
        return self.channel.is_available()

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, _device_ident(self.hub, self.channel))},
            manufacturer="Navien",
            name=_device_name(self.hub, self.channel),
        )

class NavienHotButtonSwitch(_BaseNavienSwitch):
    _attr_translation_key = "hot_button"

    @property
    def unique_id(self) -> str:
        return _device_ident(self.hub, self.channel) + "_hot_button"

    @property
    def is_on(self) -> bool:
        return self.channel.channel_status.get("onDemandUseFlag", False)

    async def async_turn_on(self) -> None:
        await self.channel.set_hot_button_state(True)

    async def async_turn_off(self) -> None:
        await self.channel.set_hot_button_state(False)

class NavienPowerSwitch(_BaseNavienSwitch):
    _attr_translation_key = "power"

    @property
    def unique_id(self) -> str:
        return _device_ident(self.hub, self.channel) + "_power"

    @property
    def is_on(self) -> bool:
        return self.channel.channel_status.get("powerStatus", False)

    async def async_turn_on(self) -> None:
        await self.channel.set_power_state(True)

    async def async_turn_off(self) -> None:
        await self.channel.set_power_state(False)
