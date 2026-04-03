"""Support for Navien water heaters."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
    STATE_GAS,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, STATE_OFF, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .navien_api import TemperatureType

_LOGGER = logging.getLogger(__name__)

SUPPORT_FLAGS = (
    WaterHeaterEntityFeature.AWAY_MODE
    | WaterHeaterEntityFeature.TARGET_TEMPERATURE
    | WaterHeaterEntityFeature.OPERATION_MODE
    | WaterHeaterEntityFeature.ON_OFF
)

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
    entities: list[WaterHeaterEntity] = []

    for hub in hubs:
        try:
            await asyncio.wait_for(hub.ready_event.wait(), timeout=60)
        except Exception:  # noqa: BLE001
            continue

        for channel in hub.channels.values():
            entities.append(NavienWaterHeater(hass, hub, channel))

    async_add_entities(entities)

class NavienWaterHeater(WaterHeaterEntity):
    _attr_supported_features = SUPPORT_FLAGS
    _attr_translation_key = "water_heater"
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, hub, channel) -> None:
        self.hass = hass
        self.hub = hub
        self.channel = channel

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

    @property
    def unique_id(self) -> str:
        return _device_ident(self.hub, self.channel)

    @property
    def temperature_unit(self):
        temp_unit = UnitOfTemperature.CELSIUS
        if self.channel.channel_info.get("temperatureType") == TemperatureType.FAHRENHEIT.value:
            temp_unit = UnitOfTemperature.FAHRENHEIT
        return temp_unit

    @property
    def is_away_mode_on(self):
        return not self.channel.channel_status.get("powerStatus", False)

    @property
    def current_operation(self):
        return STATE_GAS if self.channel.channel_status.get("powerStatus", False) else STATE_OFF

    @property
    def operation_list(self):
        return [STATE_OFF, STATE_GAS]

    @property
    def current_temperature(self):
        unit_list = self.channel.channel_status.get("unitInfo", {}).get("unitStatusList", [])
        if unit_list:
            return round(sum([ui.get("currentOutletTemp") for ui in unit_list]) / len(unit_list))
        _LOGGER.debug("No channel status information available for %s", _device_name(self.hub, self.channel))
        return None

    @property
    def target_temperature(self):
        return self.channel.channel_status.get("DHWSettingTemp")

    @property
    def min_temp(self):
        return self.channel.channel_info.get("setupDHWTempMin", 0)

    @property
    def max_temp(self):
        return self.channel.channel_info.get("setupDHWTempMax", 0)

    async def async_set_temperature(self, **kwargs):
        hass_units = "us_customary" if self.hass.config.units.temperature_unit == UnitOfTemperature.FAHRENHEIT else "metric"
        navien_units = "us_customary" if self.channel.channel_info.get("temperatureType", 2) == TemperatureType.FAHRENHEIT.value else "metric"
        target_temp = kwargs.get(ATTR_TEMPERATURE)
        if target_temp is None:
            return

        if hass_units == navien_units:
            if self.temperature_unit == UnitOfTemperature.CELSIUS:
                target_temp = round(2 * float(target_temp))
        else:
            if hass_units == "metric":
                target_temp = round((float(target_temp) * 9 / 5) + 32)
            else:
                target_temp = round((float(target_temp) - 32) * 10 / 9)

        await self.channel.set_temperature(target_temp)

    async def async_turn_away_mode_on(self):
        await self.channel.set_power_state(False)

    async def async_turn_away_mode_off(self):
        await self.channel.set_power_state(True)

    async def async_set_operation_mode(self, operation_mode):
        await self.channel.set_power_state(operation_mode == STATE_GAS)


    async def async_turn_on(self):
        await self.channel.set_power_state(True)

    async def async_turn_off(self):
        await self.channel.set_power_state(False)
