"""The Tasmota integration."""

from __future__ import annotations

import logging

from hatasmota.const import (
    CONF_IP,
    CONF_MAC,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_NAME,
    CONF_SW_VERSION,
)
from hatasmota.models import TasmotaDeviceConfig
from hatasmota.mqtt import TasmotaMQTTClient

from homeassistant.components import mqtt
from homeassistant.components.mqtt import (
    async_prepare_subscribe_topics,
    async_subscribe_topics,
    async_unsubscribe_topics,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceRegistry

from . import device_automation, discovery
from .const import (
    CONF_DISCOVERY_PREFIX,
    DATA_REMOVE_DISCOVER_COMPONENT,
    DATA_UNSUB,
    PLATFORMS,
    DOMAIN,
)
from .update import get_installed_version, get_latest_version, async_update_firmware_info
from .update_entity import TasmotaUpdateEntity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from datetime import timedelta

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tasmota from a config entry."""
    hass.data[DOMAIN] = hass.data.get(DOMAIN, {})

    async def _publish(
        topic: str,
        payload: mqtt.PublishPayloadType,
        qos: int | None,
        retain: bool | None,
    ) -> None:
        await mqtt.async_publish(hass, topic, payload, qos, retain)

    async def _subscribe_topics(sub_state: dict | None, topics: dict) -> dict:
        # Optionally mark message handlers as callback
        for topic in topics.values():
            if "msg_callback" in topic and "event_loop_safe" in topic:
                topic["msg_callback"] = callback(topic["msg_callback"])
        sub_state = async_prepare_subscribe_topics(hass, sub_state, topics)
        await async_subscribe_topics(hass, sub_state)
        return sub_state

    async def _unsubscribe_topics(sub_state: dict | None) -> dict:
        return async_unsubscribe_topics(hass, sub_state)

    tasmota_mqtt = TasmotaMQTTClient(_publish, _subscribe_topics, _unsubscribe_topics)

    device_registry = dr.async_get(hass)

    async def async_discover_device(config: TasmotaDeviceConfig, mac: str) -> None:
        """Discover and add a Tasmota device."""
        await async_setup_device(
            hass, mac, config, entry, tasmota_mqtt, device_registry
        )

    await device_automation.async_setup_entry(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    discovery_prefix = entry.data[CONF_DISCOVERY_PREFIX]
    await discovery.async_start(
        hass, discovery_prefix, entry, tasmota_mqtt, async_discover_device
    )

    # Setup the update sensor
    name = entry.data[CONF_NAME]

    installed_version = await get_installed_version(entry)
    latest_version = await get_latest_version()
    
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"Tasmota firmware update {name}",
        update_method=lambda: async_update_firmware_info(tasmota_mqtt, entry),
        update_interval=timedelta(hours=1),
    )
    
    await coordinator.async_refresh()
    
    hass.data[DOMAIN][entry.entry_id] = {
        "mqtt": tasmota_mqtt,
        "update": TasmotaUpdateEntity(
            name=name,
            installed_version=installed_version,
            latest_version=latest_version,
            tasmota_mqtt=tasmota_mqtt,
            topic=f"{entry.data[CONF_DISCOVERY_PREFIX]}/cmnd/Upgrade"  # Ensure this is correct
        ),
        "coordinator": coordinator
    }
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    # cleanup platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    # disable discovery
    await discovery.async_stop(hass)

    # cleanup subscriptions
    for unsub in hass.data[DATA_UNSUB]:
        unsub()
    hass.data.pop(DATA_REMOVE_DISCOVER_COMPONENT.format("device_automation"))()
    for platform in PLATFORMS:
        hass.data.pop(DATA_REMOVE_DISCOVER_COMPONENT.format(platform))()

    # detach device triggers
    device_registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    for device in devices:
        await device_automation.async_remove_automations(hass, device.id)

    return True

async def _remove_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    mac: str,
    tasmota_mqtt: TasmotaMQTTClient,
    device_registry: DeviceRegistry,
) -> None:
    """Remove a discovered Tasmota device."""
    device = device_registry.async_get_device(
        connections={(CONNECTION_NETWORK_MAC, mac)}
    )

    if device is None or config_entry.entry_id not in device.config_entries:
        return

    _LOGGER.debug("Removing tasmota from device %s", mac)
    device_registry.async_update_device(
        device.id, remove_config_entry_id=config_entry.entry_id
    )

def _update_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    config: TasmotaDeviceConfig,
    device_registry: DeviceRegistry,
) -> None:
    """Add or update device registry."""
    _LOGGER.debug("Adding or updating tasmota device %s", config[CONF_MAC])
    device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        configuration_url=f"http://{config[CONF_IP]}/",
        connections={(CONNECTION_NETWORK_MAC, config[CONF_MAC])},
        manufacturer=config[CONF_MANUFACTURER],
        model=config[CONF_MODEL],
        name=config[CONF_NAME],
        sw_version=config[CONF_SW_VERSION],
    )

async def async_setup_device(
    hass: HomeAssistant,
    mac: str,
    config: TasmotaDeviceConfig,
    config_entry: ConfigEntry,
    tasmota_mqtt: TasmotaMQTTClient,
    device_registry: DeviceRegistry,
) -> None:
    """Set up the Tasmota device."""
    if not config:
        await _remove_device(hass, config_entry, mac, tasmota_mqtt, device_registry)
    else:
        _update_device(hass, config_entry, config, device_registry)

async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Remove Tasmota config entry from a device."""

    connections = device_entry.connections
    macs = [c[1] for c in connections if c[0] == CONNECTION_NETWORK_MAC]
    tasmota_discovery = hass.data[discovery.TASMOTA_DISCOVERY_INSTANCE]
    for mac in macs:
        await tasmota_discovery.clear_discovery_topic(
            mac, config_entry.data[CONF_DISCOVERY_PREFIX]
        )

    return True
