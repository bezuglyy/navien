"""Navien number entities (setpoints)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _device_name(hub, channel) -> str:
    dev = hub.device_info.get("deviceInfo", {}) if getattr(hub, "device_info", None) else {}
    raw = dev.get("deviceName") or dev.get("name") or "Navien"
    # Channel label usually corresponds to "Basment", "Main", etc.
    ch_name = channel.channel_info.get("channelName") or channel.channel_info.get("name") or f"Channel {channel.channel_number}"
    return f"{raw} {ch_name}"


def _mac(hub) -> str:
    dev = hub.device_info.get("deviceInfo", {}) if getattr(hub, "device_info", None) else {}
    return (dev.get("macAddress") or dev.get("mac") or "unknown").replace(":", "").lower()


def _temp_unit(hass: HomeAssistant) -> str:
    # e.g. "°C" / "°F"
    return hass.config.units.temperature_unit


def _is_celsius(channel) -> bool:
    # 1 = Celsius, 2 = Fahrenheit (per TemperatureType enum)
    return int(channel.channel_info.get("temperatureType", 2)) == 1


def _encode_setpoint(channel, value: float) -> int:
    """Convert displayed setpoint (°C/°F) to raw API units."""
    if _is_celsius(channel):
        # API raw uses 0.5°C steps encoded as integer (value * 2)
        return int(round(value * 2))
    return int(round(value))


def _safe_float(v) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _pick(status: dict, keys: list[str]) -> Optional[float]:
    for k in keys:
        if k in status:
            return _safe_float(status.get(k))
    return None


@dataclass
class _Spec:
    key_candidates: list[str]
    name_ru: str
    icon: str
    setter: Callable[[object, int], object]  # async method on channel
    min_info_keys: list[str]
    max_info_keys: list[str]
    fallback_min: float
    fallback_max: float


DHW_SPEC = _Spec(
    key_candidates=["DHWSettingTemp", "dhwSettingTemp", "DHWSetTemp", "DomesticOutletSetTemp"],
    name_ru="Уставка температуры ГВС",
    icon="mdi:thermometer-water",
    setter=lambda ch, raw: ch.set_temperature(raw),
    min_info_keys=["setupDHWTempMin", "dhwTempMin", "setupDHWMin"],
    max_info_keys=["setupDHWTempMax", "dhwTempMax", "setupDHWMax"],
    fallback_min=30.0,
    fallback_max=60.0,
)

HEAT_SPEC = _Spec(
    key_candidates=[
        "CHSettingTemp",
        "chSettingTemp",
        "HeatingSettingTemp",
        "heatingSettingTemp",
        "HeatingSetTemp",
        "heatingSetTemp",
        "HeatingTargetTemp",
        "heatingTargetTemp",
    ],
    name_ru="Уставка температуры отопления",
    icon="mdi:radiator",
    setter=lambda ch, raw: ch.set_heating_temperature(raw),
    min_info_keys=["setupHeatingTempMin", "setupCHTempMin", "heatingTempMin", "chTempMin"],
    max_info_keys=["setupHeatingTempMax", "setupCHTempMax", "heatingTempMax", "chTempMax"],
    fallback_min=20.0,
    fallback_max=80.0,
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = []

    for channel in hub.channels.values():
        # Always create both; availability will reflect actual device support.
        entities.append(NavienSetpointNumber(hass, hub, channel, DHW_SPEC, suffix="dhw_setpoint"))
        entities.append(NavienSetpointNumber(hass, hub, channel, HEAT_SPEC, suffix="heat_setpoint"))

    async_add_entities(entities)


class NavienSetpointNumber(NumberEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, hub, channel, spec: _Spec, suffix: str) -> None:
        self.hass = hass
        self.hub = hub
        self.channel = channel
        self.spec = spec
        self._suffix = suffix

        mac = _mac(hub)
        self._attr_unique_id = f"{mac}_{channel.channel_number}_{suffix}"

        self._attr_name = f"{channel.channel_info.get('channelName','') or channel.channel_number} {spec.name_ru}".strip()
        self._attr_icon = spec.icon
        self._attr_native_unit_of_measurement = _temp_unit(hass)
        self._attr_native_step = 0.5 if _is_celsius(channel) else 1.0

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{mac}_{channel.channel_number}")},
            name=_device_name(hub, channel),
            manufacturer="Navien",
            model=channel.channel_info.get("modelName") or channel.channel_info.get("unitType") or "NaviLink",
            sw_version=str(hub.user_info.get("token", {}).get("version", "")) if getattr(hub, "user_info", None) else None,
        )

        channel.register_callback(self.async_write_ha_state)

    @property
    def available(self) -> bool:
        return bool(self.hub.connected and self.channel.channel_status)

    @property
    def native_value(self) -> Optional[float]:
        status = self.channel.channel_status or {}
        return _pick(status, self.spec.key_candidates)

    @property
    def native_min_value(self) -> float:
        info = self.channel.channel_info or {}
        v = _pick(info, self.spec.min_info_keys)
        return v if v is not None else self.spec.fallback_min

    @property
    def native_max_value(self) -> float:
        info = self.channel.channel_info or {}
        v = _pick(info, self.spec.max_info_keys)
        return v if v is not None else self.spec.fallback_max

    async def async_set_native_value(self, value: float) -> None:
        raw = _encode_setpoint(self.channel, float(value))
        try:
            await self.spec.setter(self.channel, raw)
        except Exception as e:
            _LOGGER.error("Failed to set %s for channel %s: %s", self._suffix, self.channel.channel_number, e)
            raise

    async def async_will_remove_from_hass(self) -> None:
        try:
            self.channel.deregister_callback(self.async_write_ha_state)
        except Exception:
            pass
