"""Support for Navien sensors."""
from __future__ import annotations

import logging
import asyncio

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTemperature, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import DOMAIN
from .navien_api import TemperatureType

_LOGGER = logging.getLogger(__name__)

POWER_KCAL_PER_HOUR = "kcal/hr"
FLOW_GALLONS_PER_MIN = "gal/min"
FLOW_LITERS_PER_MIN = "L/min"

class GenericSensorDescription:
    def __init__(self, *, translation_key: str, state_class, native_unit_of_measurement, conversion_factor: float, device_class=None) -> None:
        self.translation_key = translation_key
        self.state_class = state_class
        self.native_unit_of_measurement = native_unit_of_measurement
        self.conversion_factor = conversion_factor
        self.device_class = device_class

    def convert(self, val: float) -> float:
        return round(val * self.conversion_factor, 1)

class TempSensorDescription(GenericSensorDescription):
    def __init__(self, *, translation_key: str, state_class, native_unit_of_measurement, convert_to, device_class=None) -> None:
        super().__init__(
            translation_key=translation_key,
            state_class=state_class,
            native_unit_of_measurement=native_unit_of_measurement,
            conversion_factor=1.0,
            device_class=device_class,
        )
        self.convert_to = convert_to

    def convert(self, temp: float) -> float:
        if self.convert_to == UnitOfTemperature.CELSIUS:
            return round((temp - 32) * 5 / 9, 1)
        if self.convert_to == UnitOfTemperature.FAHRENHEIT:
            return round((temp * 9 / 5) + 32, 1)
        return float(temp)

def _get_description(hass_units: str, navien_units: str, sensor_type: str):
    return {
        "gasInstantUsage": GenericSensorDescription(
            translation_key="gas_instant_usage",
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement=POWER_KCAL_PER_HOUR if hass_units == "metric" else UnitOfPower.BTU_PER_HOUR,
            conversion_factor=1 if hass_units == navien_units else (3.96567 if hass_units == "us_customary" else 0.2521646022),
        ),
        "accumulatedGasUsage": GenericSensorDescription(
            translation_key="gas_total_usage",
            state_class=SensorStateClass.TOTAL_INCREASING,
            native_unit_of_measurement=UnitOfVolume.CUBIC_METERS if hass_units == "metric" else UnitOfVolume.CUBIC_FEET,
            conversion_factor=1 if hass_units == navien_units else (35.3147 if hass_units == "us_customary" else 0.0283168732),
            device_class=SensorDeviceClass.GAS,
        ),
        "DHWFlowRate": GenericSensorDescription(
            translation_key="hot_water_flow",
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement=FLOW_LITERS_PER_MIN if hass_units == "metric" else FLOW_GALLONS_PER_MIN,
            conversion_factor=1 if hass_units == navien_units else (0.264172 if hass_units == "us_customary" else 3.78541),
        ),
        "currentInletTemp": TempSensorDescription(
            translation_key="inlet_temp",
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS if hass_units == "metric" else UnitOfTemperature.FAHRENHEIT,
            convert_to=None if hass_units == navien_units else (UnitOfTemperature.FAHRENHEIT if hass_units == "us_customary" else UnitOfTemperature.CELSIUS),
        ),
        "currentOutletTemp": TempSensorDescription(
            translation_key="outlet_temp",
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS if hass_units == "metric" else UnitOfTemperature.FAHRENHEIT,
            convert_to=None if hass_units == navien_units else (UnitOfTemperature.FAHRENHEIT if hass_units == "us_customary" else UnitOfTemperature.CELSIUS),
        ),
    }.get(sensor_type)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up Navien sensors."""
    data = hass.data[DOMAIN][entry.entry_id]
    hubs = data.get("hubs", [])
    entities: list[SensorEntity] = []

    for hub in hubs:
        # Wait briefly for channel info; don't block forever.
        try:
            await asyncio.wait_for(hub.ready_event.wait(), timeout=60)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Timed out waiting for Navien channel info (device_index=%s)", getattr(hub, "device_index", "?"))
            continue

        for channel in hub.channels.values():
            navien_units = "us_customary" if channel.channel_info.get("temperatureType", 2) == TemperatureType.FAHRENHEIT.value else "metric"
            hass_units = "us_customary" if hass.config.units.temperature_unit == UnitOfTemperature.FAHRENHEIT else "metric"

            entities.append(NavienHeatingPowerSensor(hub, channel))
            entities.append(NavienSetpointTempSensor(hass, hub, channel))
            entities.append(NavienAvgInletTempSensor(hass, hub, channel))
            entities.append(NavienAvgOutletTempSensor(hass, hub, channel))
            entities.append(NavienAvgHeatingPowerSensor(hass, hub, channel))

            unit_count = int(channel.channel_info.get("unitCount", 1) or 1)
            sensor_types = ["gasInstantUsage", "accumulatedGasUsage", "DHWFlowRate", "currentInletTemp", "currentOutletTemp"]

            # Create entities up-front even if we don't have unitStatusList yet.
            for unit_no in range(1, unit_count + 1):
                placeholder = {"unitNumber": unit_no}
                for sensor_type in sensor_types:
                    desc = _get_description(hass_units, navien_units, sensor_type)
                    if desc is None:
                        continue
                    entities.append(NavienUnitSensor(hass, hub, channel, placeholder, sensor_type, desc))


    async_add_entities(entities)

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

class NavienHeatingPowerSensor(SensorEntity):
    _attr_device_class = SensorDeviceClass.POWER_FACTOR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_should_poll = False

    def __init__(self, hub, channel) -> None:
        self.hub = hub
        self.channel = channel
        self._attr_has_entity_name = True
        self._attr_translation_key = "heating_power"

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
        return _device_ident(self.hub, self.channel) + "_heating_power_pct"

    @property
    def native_value(self) -> StateType:
        return self.channel.channel_status.get("avgCalorie", 0)


class _BaseChannelTempSensor(SensorEntity):
    """A temperature sensor derived from channel_status values."""
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, hub, channel, *, translation_key: str, status_key: str) -> None:
        self.hass = hass
        self.hub = hub
        self.channel = channel
        self.status_key = status_key
        self._attr_translation_key = translation_key

    async def async_added_to_hass(self) -> None:
        self.channel.register_callback(self._update)

    async def async_will_remove_from_hass(self) -> None:
        self.channel.deregister_callback(self._update)

    def _update(self) -> None:
        self.async_write_ha_state()

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
        return _device_ident(self.hub, self.channel) + f"_{self.status_key}"

    @property
    def native_unit_of_measurement(self) -> str:
        return UnitOfTemperature.CELSIUS if self.hass.config.units.temperature_unit == UnitOfTemperature.CELSIUS else UnitOfTemperature.FAHRENHEIT

    @property
    def native_value(self):
        raw = self.channel.channel_status.get(self.status_key)
        if raw is None:
            return None
        # Navien API already normalizes to C/F depending on device type; convert if HA differs.
        hass_units = "us_customary" if self.hass.config.units.temperature_unit == UnitOfTemperature.FAHRENHEIT else "metric"
        navien_units = "us_customary" if self.channel.channel_info.get("temperatureType", 2) == TemperatureType.FAHRENHEIT.value else "metric"
        desc = TempSensorDescription(
            translation_key=self._attr_translation_key or "temp",
            state_class=SensorStateClass.MEASUREMENT,
            native_unit_of_measurement=self.native_unit_of_measurement,
            convert_to=None if hass_units == navien_units else (UnitOfTemperature.FAHRENHEIT if hass_units == "us_customary" else UnitOfTemperature.CELSIUS),
            device_class=SensorDeviceClass.TEMPERATURE,
        )
        return desc.convert(float(raw))


class NavienSetpointTempSensor(_BaseChannelTempSensor):
    def __init__(self, hass, hub, channel) -> None:
        super().__init__(hass, hub, channel, translation_key="dhw_setting_temp", status_key="DHWSettingTemp")


class NavienAvgInletTempSensor(_BaseChannelTempSensor):
    def __init__(self, hass, hub, channel) -> None:
        super().__init__(hass, hub, channel, translation_key="avg_inlet_temp", status_key="avgInletTemp")


class NavienAvgOutletTempSensor(_BaseChannelTempSensor):
    def __init__(self, hass, hub, channel) -> None:
        super().__init__(hass, hub, channel, translation_key="avg_outlet_temp", status_key="avgOutletTemp")


class NavienAvgHeatingPowerSensor(SensorEntity):
    """Average heating power (avgCalorie) from channel status."""
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_should_poll = False

    def __init__(self, hass, hub, channel) -> None:
        self.hass = hass
        self.hub = hub
        self.channel = channel
        self._attr_translation_key = "avg_heating_power"

    async def async_added_to_hass(self) -> None:
        self.channel.register_callback(self._update)

    async def async_will_remove_from_hass(self) -> None:
        self.channel.deregister_callback(self._update)

    def _update(self) -> None:
        self.async_write_ha_state()

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
        return _device_ident(self.hub, self.channel) + "_avgCalorie"

    @property
    def native_unit_of_measurement(self) -> str:
        hass_units = "us_customary" if self.hass.config.units.temperature_unit == UnitOfTemperature.FAHRENHEIT else "metric"
        return POWER_KCAL_PER_HOUR if hass_units == "metric" else UnitOfPower.BTU_PER_HOUR

    @property
    def native_value(self):
        raw = self.channel.channel_status.get("avgCalorie")
        if raw is None:
            return None
        hass_units = "us_customary" if self.hass.config.units.temperature_unit == UnitOfTemperature.FAHRENHEIT else "metric"
        navien_units = "us_customary" if self.channel.channel_info.get("temperatureType", 2) == TemperatureType.FAHRENHEIT.value else "metric"
        factor = 1.0 if hass_units == navien_units else (3.96567 if hass_units == "us_customary" else 0.2521646022)
        return round(float(raw) * factor, 1)


class NavienUnitSensor(SensorEntity):
    _attr_should_poll = False

    def __init__(self, hass, hub, channel, unit_info: dict, sensor_type: str, desc: GenericSensorDescription) -> None:
        self.hass = hass
        self.hub = hub
        self.channel = channel
        self.unit_info = unit_info
        self.sensor_type = sensor_type
        self.desc = desc

        self._attr_has_entity_name = True
        self._attr_translation_key = desc.translation_key
        self._attr_device_class = desc.device_class
        self._attr_state_class = desc.state_class
        self._attr_native_unit_of_measurement = desc.native_unit_of_measurement

    async def async_added_to_hass(self) -> None:
        self.channel.register_callback(self._update)

    async def async_will_remove_from_hass(self) -> None:
        self.channel.deregister_callback(self._update)

    def _update(self) -> None:
        # refresh unit_info snapshot (unitNumber stable)
        unit_no = self.unit_info.get("unitNumber")
        for ui in self.channel.channel_status.get("unitInfo", {}).get("unitStatusList", []):
            if ui.get("unitNumber") == unit_no:
                self.unit_info = ui
                break

        hass_units = "us_customary" if self.hass.config.units.temperature_unit == UnitOfTemperature.FAHRENHEIT else "metric"
        navien_units = "us_customary" if self.channel.channel_info.get("temperatureType", 2) == TemperatureType.FAHRENHEIT.value else "metric"
        new_desc = _get_description(hass_units, navien_units, self.sensor_type)
        if new_desc is not None:
            self.desc = new_desc
            self._attr_native_unit_of_measurement = new_desc.native_unit_of_measurement
            self._attr_device_class = new_desc.device_class
            self._attr_state_class = new_desc.state_class
        self.async_write_ha_state()

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
        unit = str(self.unit_info.get("unitNumber", ""))
        return _device_ident(self.hub, self.channel) + f"_u{unit}_{self.sensor_type}"

    @property
    def native_value(self) -> StateType:
        return self.desc.convert(self.unit_info.get(self.sensor_type, 0))
