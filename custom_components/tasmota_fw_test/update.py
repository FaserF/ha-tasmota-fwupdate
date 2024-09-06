from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import aiohttp
import logging
from datetime import timedelta
from .const import DOMAIN
from .update_entity import TasmotaUpdateEntity

_LOGGER = logging.getLogger(__name__)

async def install_firmware_update(tasmota_mqtt, topic):
    """Trigger the firmware update via MQTT."""
    await tasmota_mqtt.publish(f"{topic}/cmnd/Upgrade", "1")

async def get_installed_version(config_entry):
    """Fetch the installed firmware version from the config entry."""
    return config_entry.data.get("sw_version", "Unknown")

async def get_latest_version():
    """Fetch the latest firmware version from GitHub."""
    url = "https://api.github.com/repos/arendst/Tasmota/releases/latest"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                response.raise_for_status()
                data = await response.json()
                return data["tag_name"].lstrip("v")
    except Exception as err:
        _LOGGER.error(f"Error fetching the latest firmware: {err}")
        return None

async def async_update_firmware_info(tasmota_mqtt, config_entry):
    """Update the firmware info from the Tasmota device."""
    current_version = await get_installed_version(config_entry)
    latest_version = await get_latest_version()
    
    if current_version and latest_version:
        return TasmotaUpdateEntity(
            name="Tasmota Firmware Update",
            installed_version=current_version,
            latest_version=latest_version,
            tasmota_mqtt=tasmota_mqtt,
        )
    return None
