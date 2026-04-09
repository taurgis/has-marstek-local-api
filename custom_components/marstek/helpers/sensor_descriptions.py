"""Sensor descriptions for Marstek devices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.helpers.typing import StateType

from ..const import OPERATING_MODES
from ..coordinator import MarstekDataUpdateCoordinator
from ..pymarstek.const import (
    CMD_BATTERY_STATUS,
    CMD_EM_STATUS,
    CMD_ES_MODE,
    CMD_ES_SET_MODE,
    CMD_ES_STATUS,
    CMD_PV_GET_STATUS,
    CMD_WIFI_STATUS,
)
from .sensor_stats import (
    command_stats_attributes,
    command_success_rate,
    overall_command_stats_attributes,
    overall_command_success_rate,
)


@dataclass(kw_only=True)
class MarstekSensorEntityDescription(SensorEntityDescription):  # type: ignore[misc]
    """Marstek sensor entity description."""

    value_fn: Callable[
        [
            MarstekDataUpdateCoordinator,
            dict[str, Any],
            ConfigEntry | None,
        ],
        StateType,
    ]
    attributes_fn: Callable[
        [
            MarstekDataUpdateCoordinator,
            dict[str, Any],
            ConfigEntry | None,
        ],
        dict[str, Any] | None,
    ] | None = None
    exists_fn: Callable[[dict[str, Any]], bool] = lambda data: True


def _value_from_data(key: str, data: dict[str, Any]) -> StateType:
    value = data.get(key)
    if isinstance(value, (int, float, str)):
        return cast(StateType, value)
    return None


def _exists_key_with_value(key: str, data: dict[str, Any]) -> bool:
    return key in data


def _exists_key_with_non_none_value(key: str, data: dict[str, Any]) -> bool:
    """Return True only when key is present and its value is not None.

    Used for optional sensors (e.g. CT energy totals) that should only be
    created when the device actually reports the field.
    """
    return key in data and data[key] is not None


def _api_success_rate_sensor(
    method: str, translation_key: str
) -> MarstekSensorEntityDescription:
    return MarstekSensorEntityDescription(
        key=translation_key,
        translation_key=translation_key,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=1,
        value_fn=lambda coordinator, _info, _entry, method=method: (  # type: ignore[misc]
            command_success_rate(coordinator, method)
        ),
        attributes_fn=lambda coordinator, _info, _entry, method=method: (  # type: ignore[misc]
            command_stats_attributes(coordinator, method)
        ),
    )


def _overall_success_rate_sensor(
    translation_key: str,
) -> MarstekSensorEntityDescription:
    return MarstekSensorEntityDescription(
        key=translation_key,
        translation_key=translation_key,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=1,
        value_fn=lambda coordinator, _info, _entry: (
            overall_command_success_rate(coordinator)
        ),
        attributes_fn=lambda coordinator, _info, _entry: (
            overall_command_stats_attributes(coordinator)
        ),
    )


SENSORS: tuple[MarstekSensorEntityDescription, ...] = (
    MarstekSensorEntityDescription(
        key="battery_soc",
        translation_key="battery_level",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("battery_soc", coordinator.data or {})
        ),
    ),
    MarstekSensorEntityDescription(
        key="battery_power",
        translation_key="battery_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("battery_power", coordinator.data or {})
        ),
    ),
    MarstekSensorEntityDescription(
        key="ongrid_power",
        translation_key="ongrid_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("ongrid_power", coordinator.data or {})
        ),
        exists_fn=lambda data: _exists_key_with_value("ongrid_power", data),
    ),
    MarstekSensorEntityDescription(
        key="offgrid_power",
        translation_key="offgrid_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("offgrid_power", coordinator.data or {})
        ),
        exists_fn=lambda data: _exists_key_with_value("offgrid_power", data),
    ),
    MarstekSensorEntityDescription(
        key="pv_power",
        translation_key="pv_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("pv_power", coordinator.data or {})
        ),
        exists_fn=lambda data: _exists_key_with_value("pv_power", data),
    ),
    MarstekSensorEntityDescription(
        key="bat_cap",
        translation_key="battery_total_capacity",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("bat_cap", coordinator.data or {})
        ),
        exists_fn=lambda data: _exists_key_with_value("bat_cap", data),
    ),
    MarstekSensorEntityDescription(
        key="device_mode",
        translation_key="device_mode",
        device_class=SensorDeviceClass.ENUM,
        options=OPERATING_MODES,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("device_mode", coordinator.data or {})
        ),
    ),
    MarstekSensorEntityDescription(
        key="battery_status",
        translation_key="battery_status",
        device_class=SensorDeviceClass.ENUM,
        options=["charging", "discharging", "idle"],
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("battery_status", coordinator.data or {})
        ),
    ),
    MarstekSensorEntityDescription(
        key="wifi_rssi",
        translation_key="wifi_rssi",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=0,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("wifi_rssi", coordinator.data or {})
        ),
    ),
    MarstekSensorEntityDescription(
        key="wifi_sta_ip",
        translation_key="wifi_ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("wifi_sta_ip", coordinator.data or {})
        ),
    ),
    MarstekSensorEntityDescription(
        key="wifi_sta_gate",
        translation_key="wifi_gateway",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("wifi_sta_gate", coordinator.data or {})
        ),
    ),
    MarstekSensorEntityDescription(
        key="wifi_sta_mask",
        translation_key="wifi_subnet_mask",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("wifi_sta_mask", coordinator.data or {})
        ),
    ),
    MarstekSensorEntityDescription(
        key="wifi_sta_dns",
        translation_key="wifi_dns",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("wifi_sta_dns", coordinator.data or {})
        ),
    ),
    MarstekSensorEntityDescription(
        key="bat_temp",
        translation_key="battery_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("bat_temp", coordinator.data or {})
        ),
    ),
    MarstekSensorEntityDescription(
        key="bat_capacity",
        translation_key="battery_remaining_capacity",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("bat_capacity", coordinator.data or {})
        ),
    ),
    MarstekSensorEntityDescription(
        key="bat_rated_capacity",
        translation_key="battery_rated_capacity",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("bat_rated_capacity", coordinator.data or {})
        ),
    ),
    MarstekSensorEntityDescription(
        key="em_total_power",
        translation_key="em_total_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("em_total_power", coordinator.data or {})
        ),
    ),
    MarstekSensorEntityDescription(
        key="em_a_power",
        translation_key="em_a_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("em_a_power", coordinator.data or {})
        ),
    ),
    MarstekSensorEntityDescription(
        key="em_b_power",
        translation_key="em_b_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("em_b_power", coordinator.data or {})
        ),
    ),
    MarstekSensorEntityDescription(
        key="em_c_power",
        translation_key="em_c_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("em_c_power", coordinator.data or {})
        ),
    ),
    MarstekSensorEntityDescription(
        key="em_input_energy",
        translation_key="em_input_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("em_input_energy", coordinator.data or {})
        ),
        exists_fn=lambda data: _exists_key_with_non_none_value("em_input_energy", data),
    ),
    MarstekSensorEntityDescription(
        key="em_output_energy",
        translation_key="em_output_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("em_output_energy", coordinator.data or {})
        ),
        exists_fn=lambda data: _exists_key_with_non_none_value("em_output_energy", data),
    ),
    MarstekSensorEntityDescription(
        key="total_pv_energy",
        translation_key="total_pv_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("total_pv_energy", coordinator.data or {})
        ),
    ),
    MarstekSensorEntityDescription(
        key="total_grid_output_energy",
        translation_key="total_grid_output_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("total_grid_output_energy", coordinator.data or {})
        ),
    ),
    MarstekSensorEntityDescription(
        key="total_grid_input_energy",
        translation_key="total_grid_input_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("total_grid_input_energy", coordinator.data or {})
        ),
    ),
    MarstekSensorEntityDescription(
        key="total_load_energy",
        translation_key="total_load_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda coordinator, _info, _entry: (
            _value_from_data("total_load_energy", coordinator.data or {})
        ),
    ),
    MarstekSensorEntityDescription(
        key="device_ip",
        translation_key="device_ip",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda _coord, info, _entry: info.get("ip", ""),
    ),
    MarstekSensorEntityDescription(
        key="device_version",
        translation_key="device_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda _coord, info, _entry: str(info.get("version", "")),
    ),
    MarstekSensorEntityDescription(
        key="wifi_name",
        translation_key="wifi_name",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda _coord, info, _entry: info.get("wifi_name", ""),
    ),
    MarstekSensorEntityDescription(
        key="ble_mac",
        translation_key="ble_mac",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda _coord, info, _entry: info.get("ble_mac", ""),
    ),
    MarstekSensorEntityDescription(
        key="wifi_mac",
        translation_key="wifi_mac",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda _coord, info, _entry: info.get("wifi_mac", ""),
    ),
    MarstekSensorEntityDescription(
        key="mac",
        translation_key="mac",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda _coord, info, _entry: info.get("mac", ""),
    ),
)


API_STABILITY_SENSORS: tuple[MarstekSensorEntityDescription, ...] = (
    _overall_success_rate_sensor("api_success_rate_overall"),
    _api_success_rate_sensor(CMD_ES_MODE, "api_success_rate_es_get_mode"),
    _api_success_rate_sensor(CMD_ES_STATUS, "api_success_rate_es_get_status"),
    _api_success_rate_sensor(CMD_EM_STATUS, "api_success_rate_em_get_status"),
    _api_success_rate_sensor(CMD_PV_GET_STATUS, "api_success_rate_pv_get_status"),
    _api_success_rate_sensor(CMD_WIFI_STATUS, "api_success_rate_wifi_get_status"),
    _api_success_rate_sensor(CMD_BATTERY_STATUS, "api_success_rate_bat_get_status"),
    _api_success_rate_sensor(CMD_ES_SET_MODE, "api_success_rate_es_set_mode"),
)


def _pv_sensor_descriptions() -> tuple[MarstekSensorEntityDescription, ...]:
    """Generate PV sensor descriptions with appropriate display precision."""
    descriptions: list[MarstekSensorEntityDescription] = []
    for pv_channel in range(1, 5):
        pv_metrics: list[tuple[str, str | None, SensorDeviceClass | None, int | None]] = [
            ("power", UnitOfPower.WATT, SensorDeviceClass.POWER, 1),
            ("voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, 1),
            ("current", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT, 2),
            ("state", None, None, None),
        ]
        for metric_type, unit, device_class, precision in pv_metrics:
            sensor_key = f"pv{pv_channel}_{metric_type}"
            descriptions.append(
                MarstekSensorEntityDescription(
                    key=sensor_key,
                    translation_key=sensor_key,
                    native_unit_of_measurement=unit,
                    device_class=device_class,
                    state_class=(
                        SensorStateClass.MEASUREMENT
                        if metric_type != "state"
                        else None
                    ),
                    suggested_display_precision=precision,
                    value_fn=lambda coordinator, _info, _entry, key=sensor_key: (  # type: ignore[misc]
                        _value_from_data(key, coordinator.data or {})
                    ),
                    exists_fn=lambda data, key=sensor_key: _exists_key_with_value(  # type: ignore[misc]
                        key, data
                    ),
                )
            )
    return tuple(descriptions)


PV_SENSORS = _pv_sensor_descriptions()
