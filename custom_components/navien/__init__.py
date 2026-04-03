"""The Navien integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import certifi
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_PASSWORD,
    CONF_POLLING_INTERVAL,
    CONF_USERNAME,
    DEFAULT_POLLING_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .navien_api import NavilinkConnect

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Navien component."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries."""
    _LOGGER.debug("Migrating Navien entry %s from version %s", entry.entry_id, entry.version)

    data = dict(entry.data)
    options = dict(entry.options)

    if entry.version == 1:
        if CONF_POLLING_INTERVAL not in options:
            polling = int(data.pop(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL))
            options[CONF_POLLING_INTERVAL] = polling

        hass.config_entries.async_update_entry(entry, data=data, options=options, version=2)
        _LOGGER.info("Migrated Navien entry %s to version 2", entry.entry_id)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Navien from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    username = entry.data.get(CONF_USERNAME, "")
    password = entry.data.get(CONF_PASSWORD, "")
    polling_interval = int(
        entry.options.get(
            CONF_POLLING_INTERVAL,
            entry.data.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL),
        )
    )

    if not username or not password:
        raise ConfigEntryNotReady("Missing username or password")

    aws_ca_path = certifi.where()

    discover = NavilinkConnect(
        userId=username,
        passwd=password,
        polling_interval=0,
        aws_cert_path=aws_ca_path,
    )

    try:
        device_list = await discover.login()
    except Exception as err:
        raise ConfigEntryNotReady(f"Unable to login to Navien service: {err}") from err

    if not device_list:
        raise ConfigEntryNotReady("No Navien devices found")

    hubs: list[NavilinkConnect] = []
    startup_tasks: list[asyncio.Task] = []

    for idx, _device in enumerate(device_list):
        hub = NavilinkConnect(
            userId=username,
            passwd=password,
            device_index=idx,
            polling_interval=polling_interval,
            aws_cert_path=aws_ca_path,
        )
        task = hass.async_create_task(hub.start())
        startup_tasks.append(task)
        hubs.append(hub)

    hass.data[DOMAIN][entry.entry_id] = {
        "hubs": hubs,
        "username": username,
        "startup_tasks": startup_tasks,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    data: dict[str, Any] = hass.data[DOMAIN].get(entry.entry_id, {})
    hubs: list[NavilinkConnect] = data.get("hubs", [])
    startup_tasks: list[asyncio.Task] = data.get("startup_tasks", [])

    for task in startup_tasks:
        if not task.done():
            task.cancel()

    for hub in hubs:
        try:
            await hub.disconnect()
        except Exception:
            _LOGGER.debug("Error disconnecting hub", exc_info=True)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
