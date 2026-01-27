"""Sensor platform for Marstek devices."""

from __future__ import annotations

import logging
from typing import Any, cast

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

try:
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
except ImportError:
    # Fallback for older Home Assistant versions
    from collections.abc import Callable, Iterable
    from typing import TYPE_CHECKING, Protocol

    if TYPE_CHECKING:
        from homeassistant.helpers.entity import Entity
    else:
        Entity = object  # type: ignore[assignment, misc]

    class AddConfigEntryEntitiesCallback(Protocol):  # type: ignore[no-redef]
        """Protocol type for EntityPlatform.add_entities callback (fallback)."""

        def __call__(
            self,
            new_entities: Iterable[Entity],
            update_before_add: bool = False,
        ) -> None:
            """Define add_entities type."""

from . import MarstekConfigEntry
from .const import DOMAIN
from .coordinator import MarstekDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


class MarstekSensor(CoordinatorEntity[MarstekDataUpdateCoordinator], SensorEntity):
    """Representation of a Marstek sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        sensor_type: str,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device_info = device_info
        self._sensor_type = sensor_type
        self._config_entry = config_entry
        # Use BLE-MAC as device identifier for stability (beardhatcode & mik-laj feedback)
        # BLE-MAC is more stable than IP and ensures device history continuity
        device_identifier = (
            device_info.get("ble_mac")
            or device_info.get("mac")
            or device_info.get("wifi_mac")
        )
        if not device_identifier:
            raise ValueError("Marstek device identifier (MAC) is required for stable entities")
        # Get current IP for device name (supports dynamic IP updates)
        device_ip = (
            config_entry.data.get(CONF_HOST)
            if config_entry
            else device_info.get("ip", "Unknown")
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_identifier)},
            name=f"Marstek {device_info['device_type']} v{device_info['version']} ({device_ip})",
            manufacturer="Marstek",
            model=device_info["device_type"],
            sw_version=str(device_info["version"]),
            hw_version=device_info.get("wifi_mac", ""),
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        # Use BLE-MAC as device identifier for stability (beardhatcode & mik-laj feedback)
        device_id = (
            self._device_info.get("ble_mac")
            or self._device_info.get("mac")
            or self._device_info.get("wifi_mac")
        )
        if not device_id:
            raise ValueError("Marstek unique_id requires MAC-based identifier")
        return f"{device_id}_{self._sensor_type}"

    def _get_current_ip(self) -> str:
        """Get current device IP from config_entry (supports dynamic IP updates)."""
        if self._config_entry:
            return self._config_entry.data.get(
                CONF_HOST, self._device_info.get("ip", "Unknown")
            )
        return self._device_info.get("ip", "Unknown")

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self._sensor_type.replace("_", " ").title()

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.get(self._sensor_type)
        if isinstance(value, (int, float, str)):
            return cast(StateType, value)
        return None


class MarstekBatterySensor(MarstekSensor):
    """Representation of a Marstek battery sensor."""

    _attr_translation_key = "battery_level"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the battery sensor."""
        super().__init__(coordinator, device_info, "battery_soc", config_entry)

    @property
    def native_value(self) -> StateType:
        """Return the battery level."""
        if not self.coordinator.data:
            return None
        battery_soc = self.coordinator.data.get("battery_soc")
        if battery_soc is None:
            return None
        return int(battery_soc)


class MarstekPowerSensor(MarstekSensor):
    """Representation of a Marstek power sensor."""

    _attr_translation_key = "grid_power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the power sensor."""
        super().__init__(coordinator, device_info, "battery_power", config_entry)

    @property
    def native_value(self) -> StateType:
        """Return the battery power."""
        if not self.coordinator.data:
            return None
        battery_power = self.coordinator.data.get("battery_power")
        if battery_power is None:
            return None
        return int(battery_power)


class MarstekDeviceInfoSensor(MarstekSensor):
    """Representation of a Marstek device info sensor."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        info_type: str,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the device info sensor."""
        super().__init__(coordinator, device_info, info_type, config_entry)
        self._info_type = info_type
        self._attr_translation_key = info_type
        self._attr_device_class = None
        self._attr_state_class = None

    @property
    def native_value(self) -> StateType:
        """Return the device info."""
        if self._info_type == "device_ip":
            # Get current IP from config_entry if available (supports dynamic IP updates)
            if self._config_entry:
                return self._config_entry.data.get(CONF_HOST, "")
            return self._device_info.get("ip", "")
        if self._info_type == "device_version":
            return str(self._device_info.get("version", ""))
        if self._info_type == "wifi_name":
            return self._device_info.get("wifi_name", "")
        if self._info_type == "ble_mac":
            return self._device_info.get("ble_mac", "")
        if self._info_type == "wifi_mac":
            return self._device_info.get("wifi_mac", "")
        if self._info_type == "mac":
            return self._device_info.get("mac", "")
        return None


class MarstekDeviceModeSensor(MarstekSensor):
    """Representation of a Marstek device mode sensor."""

    _attr_translation_key = "device_mode"
    _attr_device_class = None
    _attr_state_class = None

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the device mode sensor."""
        super().__init__(coordinator, device_info, "device_mode", config_entry)


class MarstekBatteryStatusSensor(MarstekSensor):
    """Representation of a Marstek battery status sensor."""

    _attr_translation_key = "battery_status"
    _attr_device_class = None
    _attr_state_class = None

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the battery status sensor."""
        super().__init__(coordinator, device_info, "battery_status", config_entry)


class MarstekPVSensor(MarstekSensor):
    """Representation of a Marstek PV sensor."""

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        pv_channel: int,
        metric_type: str,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the PV sensor."""
        sensor_key = f"pv{pv_channel}_{metric_type}"
        super().__init__(coordinator, device_info, sensor_key, config_entry)
        self._pv_channel = pv_channel
        self._metric_type = metric_type
        # Use translation_key for proper entity naming
        self._attr_translation_key = sensor_key

        if metric_type == "power":
            self._attr_native_unit_of_measurement = UnitOfPower.WATT
            self._attr_device_class = SensorDeviceClass.POWER
        elif metric_type == "voltage":
            self._attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
            self._attr_device_class = SensorDeviceClass.VOLTAGE
        elif metric_type == "current":
            self._attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
            self._attr_device_class = SensorDeviceClass.CURRENT
        elif metric_type == "state":
            self._attr_device_class = None
            self._attr_state_class = None

        if metric_type != "state":
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> StateType:
        """Return the PV metric value."""
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.get(self._sensor_type)
        if isinstance(value, (int, float)):
            return cast(StateType, value)
        return None


class MarstekWiFiRSSISensor(MarstekSensor):
    """Representation of a Marstek WiFi signal strength sensor."""

    _attr_translation_key = "wifi_rssi"
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the WiFi RSSI sensor."""
        super().__init__(coordinator, device_info, "wifi_rssi", config_entry)

    @property
    def native_value(self) -> StateType:
        """Return the WiFi signal strength."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("wifi_rssi")


class MarstekCTConnectionSensor(MarstekSensor):
    """Representation of a Marstek CT connection status sensor."""

    _attr_translation_key = "ct_connection"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_device_class = None
    _attr_state_class = None

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the CT connection sensor."""
        super().__init__(coordinator, device_info, "ct_state", config_entry)

    @property
    def native_value(self) -> StateType:
        """Return the CT connection status."""
        if not self.coordinator.data:
            return None
        ct_connected = self.coordinator.data.get("ct_connected")
        if ct_connected is None:
            return None
        return "Connected" if ct_connected else "Not Connected"


class MarstekBatteryTemperatureSensor(MarstekSensor):
    """Representation of a Marstek battery temperature sensor."""

    _attr_translation_key = "battery_temperature"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the battery temperature sensor."""
        super().__init__(coordinator, device_info, "bat_temp", config_entry)

    @property
    def native_value(self) -> StateType:
        """Return the battery temperature."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("bat_temp")


class MarstekGridPowerSensor(MarstekSensor):
    """Representation of a Marstek grid power sensor (from Energy Meter)."""

    _attr_translation_key = "grid_total_power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the grid total power sensor."""
        super().__init__(coordinator, device_info, "em_total_power", config_entry)

    @property
    def native_value(self) -> StateType:
        """Return the total grid power from energy meter."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("em_total_power")


class MarstekPhasePowerSensor(MarstekSensor):
    """Representation of a Marstek phase power sensor (from Energy Meter)."""

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        phase: str,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the phase power sensor."""
        sensor_key = f"em_{phase}_power"
        super().__init__(coordinator, device_info, sensor_key, config_entry)
        self._phase = phase
        self._attr_translation_key = f"phase_{phase}_power"

    @property
    def native_value(self) -> StateType:
        """Return the phase power."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self._sensor_type)


class MarstekTotalPVEnergySensor(MarstekSensor):
    """Representation of a Marstek total PV energy sensor."""

    _attr_translation_key = "total_pv_energy"
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the total PV energy sensor."""
        super().__init__(coordinator, device_info, "total_pv_energy", config_entry)

    @property
    def native_value(self) -> StateType:
        """Return the total PV energy."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("total_pv_energy")


class MarstekGridOutputEnergySensor(MarstekSensor):
    """Representation of a Marstek grid output (export) energy sensor."""

    _attr_translation_key = "total_grid_output_energy"
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the grid output energy sensor."""
        super().__init__(coordinator, device_info, "total_grid_output_energy", config_entry)

    @property
    def native_value(self) -> StateType:
        """Return the total grid output energy."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("total_grid_output_energy")


class MarstekGridInputEnergySensor(MarstekSensor):
    """Representation of a Marstek grid input (import) energy sensor."""

    _attr_translation_key = "total_grid_input_energy"
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the grid input energy sensor."""
        super().__init__(coordinator, device_info, "total_grid_input_energy", config_entry)

    @property
    def native_value(self) -> StateType:
        """Return the total grid input energy."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("total_grid_input_energy")


class MarstekLoadEnergySensor(MarstekSensor):
    """Representation of a Marstek total load energy sensor."""

    _attr_translation_key = "total_load_energy"
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator,
        device_info: dict[str, Any],
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the total load energy sensor."""
        super().__init__(coordinator, device_info, "total_load_energy", config_entry)

    @property
    def native_value(self) -> StateType:
        """Return the total load energy."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("total_load_energy")


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MarstekConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Marstek sensors based on a config entry."""
    # Use shared coordinator and device_info from __init__.py (mik-laj feedback)
    coordinator = config_entry.runtime_data.coordinator
    device_info = config_entry.runtime_data.device_info
    device_ip = device_info["ip"]
    _LOGGER.info("Setting up Marstek sensors: %s", device_ip)

    data_keys = set(coordinator.data or {})

    # Core sensors - always created, show unavailable until data arrives
    sensors: list[MarstekSensor] = [
        MarstekBatterySensor(coordinator, device_info, config_entry),
        MarstekPowerSensor(coordinator, device_info, config_entry),
        MarstekDeviceModeSensor(coordinator, device_info, config_entry),
        MarstekBatteryStatusSensor(coordinator, device_info, config_entry),
        MarstekDeviceInfoSensor(coordinator, device_info, "device_ip", config_entry),
        MarstekDeviceInfoSensor(
            coordinator, device_info, "device_version", config_entry
        ),
        MarstekDeviceInfoSensor(coordinator, device_info, "ble_mac", config_entry),
        MarstekDeviceInfoSensor(coordinator, device_info, "wifi_mac", config_entry),
        MarstekDeviceInfoSensor(coordinator, device_info, "mac", config_entry),
        # Slow-tier sensors - always created to avoid orphaned entities on reload
        MarstekWiFiRSSISensor(coordinator, device_info, config_entry),
        MarstekCTConnectionSensor(coordinator, device_info, config_entry),
        MarstekBatteryTemperatureSensor(coordinator, device_info, config_entry),
        # Energy meter sensors - always created
        MarstekGridPowerSensor(coordinator, device_info, config_entry),
        MarstekPhasePowerSensor(coordinator, device_info, "a", config_entry),
        MarstekPhasePowerSensor(coordinator, device_info, "b", config_entry),
        MarstekPhasePowerSensor(coordinator, device_info, "c", config_entry),
        # Energy total sensors - always created for Energy Dashboard
        MarstekTotalPVEnergySensor(coordinator, device_info, config_entry),
        MarstekGridOutputEnergySensor(coordinator, device_info, config_entry),
        MarstekGridInputEnergySensor(coordinator, device_info, config_entry),
        MarstekLoadEnergySensor(coordinator, device_info, config_entry),
    ]

    # Only register PV sensors when data is actually available
    # PV is only supported by Venus A/D; Venus C/E do NOT have PV
    # The coordinator and data parser only include PV keys for supporting devices
    for pv_channel in range(1, 5):
        for metric_type in ("power", "voltage", "current", "state"):
            sensor_key = f"pv{pv_channel}_{metric_type}"
            # Check both key presence AND non-None value to avoid Unknown entities
            if sensor_key in data_keys and coordinator.data.get(sensor_key) is not None:
                sensors.append(
                    MarstekPVSensor(
                        coordinator,
                        device_info,
                        pv_channel,
                        metric_type,
                        config_entry,
                    )
                )

    _LOGGER.info("Device %s sensors set up, total %d", device_ip, len(sensors))
    async_add_entities(sensors)
