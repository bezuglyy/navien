"""Constants for Navien integration."""

from __future__ import annotations

DOMAIN = "navien"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_POLLING_INTERVAL = "polling_interval"

DEFAULT_POLLING_INTERVAL = 15
MIN_POLLING_INTERVAL = 10
MAX_POLLING_INTERVAL = 120

PLATFORMS: list[str] = ["water_heater", "sensor", "switch", "number"]
