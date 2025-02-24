import logging
import re
import aiohttp
from datetime import timedelta
from homeassistant.config_entries import ConfigEntry
from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers import device_registry as dr
from hatasmota.models import TasmotaDeviceConfig
from hatasmota.mqtt import TasmotaMQTTClient
from hatasmota.const import CONF_NAME, CONF_MAC

_LOGGER = logging.getLogger(__name__)

GITHUB_API_URL = "https://api.github.com/repos/arendst/Tasmota/releases/latest"

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
        await hass.components.mqtt.async_publish(topic, payload, qos, retain)

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
                data = await response.json()
                self.latest_version = self._normalize_version(data["tag_name"])
                return self.latest_version

    @property
    def release_url(self) -> str | None:
        return f"https://github.com/arendst/Tasmota/releases/tag/v{self.latest_version}" if self.latest_version else None


class TasmotaUpdateEntity(UpdateEntity):
    """Representation of a Tasmota update entity."""

    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(self, coordinator: TasmotaUpdateCoordinator, device, mqtt_client: TasmotaMQTTClient, config: TasmotaDeviceConfig, sw_version):
        self.coordinator = coordinator
        self.device = device
        self._mqtt_client = mqtt_client
        self.config = config
        self._attr_name = f"{config[CONF_NAME]} Firmware Update"
        self._attr_unique_id = f"{config[CONF_MAC]}_firmware_update"
        self._sw_version = sw_version

    @property
    def installed_version(self):
        return self._sw_version

    @property
    def latest_version(self):
        return self.coordinator.data

    @property
    def release_url(self) -> str | None:
        return self.coordinator.release_url

    @property
    def update_description(self) -> str:
        return self._attr_update_description

    async def async_update(self):
        await self.coordinator.async_request_refresh()

    async def async_install(self, version: str = None, backup: bool = False, **kwargs):
        _LOGGER.info("Triggering firmware upgrade for %s", self._attr_name)
        await self._mqtt_client.publish(f"cmnd/{self.config[CONF_MAC]}/Upgrade", "1", 0, False)
