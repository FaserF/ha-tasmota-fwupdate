import logging
import re
import aiohttp
from datetime import timedelta
from typing import Any
from homeassistant.config_entries import ConfigEntry
from homeassistant.components.update import UpdateEntity, UpdateEntityFeature, UpdateEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.components import mqtt
from hatasmota.models import TasmotaDeviceConfig
from hatasmota.mqtt import TasmotaMQTTClient
from hatasmota.const import CONF_NAME, CONF_MAC
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

GITHUB_API_URL = "https://api.github.com/repos/arendst/Tasmota/releases/latest"

@dataclass(frozen=True, kw_only=True)
class TasmotaUpdateEntityDescription(UpdateEntityDescription):
    """Update description / warning."""
    update_description: str


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    """Set up Tasmota update entities."""
    _LOGGER.debug("Setting up Tasmota update entities")
    coordinator = TasmotaUpdateCoordinator(hass)
    await coordinator.async_config_entry_first_refresh()

    device_registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(device_registry, config_entry.entry_id)
    _LOGGER.debug("Found devices: %s", devices)
    if not devices:
        _LOGGER.warning("No devices found for config_entry: %s", config_entry.entry_id)

    async def _publish(topic: str, payload: str, qos: int | None, retain: bool | None) -> None:
        await mqtt.async_publish(hass, topic, payload, qos, retain)

    async def _subscribe_topics(sub_state: dict | None, topics: dict) -> dict:
        sub_state = await hass.components.mqtt.async_subscribe(topics)
        return sub_state

    async def _unsubscribe_topics(sub_state: dict | None) -> dict:
        return await hass.components.mqtt.async_unsubscribe(sub_state)

    mqtt_client = TasmotaMQTTClient(_publish, _subscribe_topics, _unsubscribe_topics)

    entities = []
    for device in devices:
        mac = next((conn[1] for conn in device.connections if conn[0] == "mac"), None)
        version = device.sw_version
        config = TasmotaDeviceConfig(
            name=device.name,
            sw_version=version,
            mac=mac,
        )

        entities.append(TasmotaUpdateEntity(coordinator, device, mqtt_client, config, version))

    if entities:
        _LOGGER.debug("Adding firmware entities: %s", entities)
        async_add_entities(entities)
    else:
        _LOGGER.warning("No entities to add")


class TasmotaUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Tasmota update data."""
    def __init__(self, hass: HomeAssistant):
        super().__init__(
            hass,
            _LOGGER,
            name="Tasmota update",
            update_interval=timedelta(hours=12),
        )
        self.latest_version = None

    def _normalize_version(self, version: str) -> str:
        match = re.search(r"(\d+\.\d+\.\d+)", version)
        return match.group(1) if match else None

    async def _async_update_data(self):
        async with aiohttp.ClientSession() as session:
            async with session.get(GITHUB_API_URL) as response:
                if response.status != 200:
                    raise UpdateFailed(f"Error fetching data: {response.status}")
                try:
                    data = await response.json()
                    if not isinstance(data, dict) or "tag_name" not in data:
                        raise UpdateFailed(f"Unexpected response format: {data}")

                    self.latest_version = self._normalize_version(data["tag_name"])
                    return self.latest_version
                except Exception as e:
                    raise UpdateFailed(f"Error parsing data: {e}")

    @property
    def release_url(self) -> str | None:
        return f"https://github.com/arendst/Tasmota/releases/tag/v{self.latest_version}" if self.latest_version else None


class TasmotaUpdateEntity(UpdateEntity):
    """Representation of a Tasmota update entity."""

    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(self, coordinator: TasmotaUpdateCoordinator, device, mqtt_client: TasmotaMQTTClient, config: TasmotaDeviceConfig, sw_version):
        self.coordinator = coordinator
        self.config = config
        self.device = device
        self._mqtt_client = mqtt_client
        self._attr_name = f"{config[CONF_NAME]} Firmware Update"
        self._attr_unique_id = f"{config[CONF_MAC]}_firmware_update"
        self._sw_version = sw_version
        self._attr_in_progress = False
        self.topic = f"tasmota_{self.config[CONF_MAC].replace(':', '')[-6:].upper()}"

        self.entity_description = TasmotaUpdateEntityDescription(
            key="update",
            name=self._attr_name,
            update_description="Warning: Please be cautious when updating firmware while using a non-default bin image. Make sure your device is connected and powered properly before proceeding. Default OtaUrl will be used."
        )

    @property
    def installed_version(self):
        """Currently installed Tasmota Firmware."""
        return self._sw_version

    @property
    def latest_version(self):
        """Latest version available for install."""
        return self.coordinator.latest_version

    @property
    def release_url(self) -> str | None:
        """URL to GitHub release notes."""
        return self.coordinator.release_url

    @property
    def update_description(self) -> str:
        """Warnung for the Update."""
        return self.entity_description.update_description

    async def async_update(self):
        """Fetch latest data."""
        await self.coordinator.async_request_refresh()

    async def async_install(self, version: str = None, backup: bool = False, **kwargs: Any) -> None:
        """Trigger the firmware upgrade."""
        _LOGGER.info("Triggering firmware upgrade for %s with topic %s", self._attr_name, self.topic)
        self._attr_in_progress = True

        try:
            response = await self._mqtt_client.publish(f"cmnd/{self.topic}/Upgrade", "1", 0, False)
            _LOGGER.debug("MQTT publish response: %s", response)
        except Exception as e:
            self._attr_in_progress = False
            _LOGGER.error(f"Failed to send MQTT upgrade command: {e}")
