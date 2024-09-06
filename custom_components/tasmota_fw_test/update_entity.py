from homeassistant.components.update import UpdateEntity

class TasmotaUpdateEntity(UpdateEntity):
    """Represents a Tasmota firmware update."""

    def __init__(self, name, installed_version, latest_version, tasmota_mqtt, topic):
        """Initialize the update entity."""
        self._attr_name = f"{name} Firmware Update"
        self._attr_installed_version = installed_version
        self._attr_latest_version = latest_version
        self._tasmota_mqtt = tasmota_mqtt
        self._topic = topic

    @property
    def name(self):
        """Return the name of the entity."""
        return self._attr_name

    @property
    def installed_version(self):
        """Return the installed version."""
        return self._attr_installed_version

    @property
    def latest_version(self):
        """Return the latest version."""
        return self._attr_latest_version

    async def install(self, version=None, backup=False):
        """Trigger the installation of a new firmware version."""
        await install_firmware_update(self._tasmota_mqtt, self._topic)
