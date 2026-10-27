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

from ..capacity_estimator import (
    CAPACITY_ATTR_KEY,
    CAPACITY_DATA_KEY,
    CARD_ATTRIBUTES,
    SOH_DATA_KEY,
)
from ..const import (
    CONF_CLAMP_NEGATIVE_OFFGRID_POWER,
    DEFAULT_CLAMP_NEGATIVE_OFFGRID_POWER,
    OPERATING_MODES,
    ha_operating_mode,
)
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
from .restart_detector import RESTARTS_ATTR_KEY, RESTARTS_DATA_KEY
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
    attributes_fn: (
        Callable[
            [
                MarstekDataUpdateCoordinator,
                dict[str, Any],
                ConfigEntry | None,
            ],
            dict[str, Any] | None,
        ]
        | None
    ) = None
    exists_fn: Callable[[dict[str, Any]], bool] = lambda data: True


def _value_from_data(key: str, data: dict[str, Any]) -> StateType:
    value = data.get(key)
    if isinstance(value, (int, float, str)):
        return cast(StateType, value)
    return None


def _device_mode_value(
    coordinator: MarstekDataUpdateCoordinator,
    _info: dict[str, Any],
    _entry: ConfigEntry | None,
) -> StateType:
    """Map Open API mode names onto HA enum options (required since 2026.9)."""
    return ha_operating_mode(_value_from_data("device_mode", coordinator.data or {}))


def _offgrid_power_value(
    coordinator: MarstekDataUpdateCoordinator,
    _info: dict[str, Any],
    entry: ConfigEntry | None,
) -> StateType:
    """Return off-grid power, optionally clamped to 0 W for negative readings.

    The parser keeps the signed value so derived battery power stays accurate.
    This transform is display-only: it must not write back to coordinator data.
    https://developers.home-assistant.io/docs/integration_fetching_data/
    """
    value = _value_from_data("offgrid_power", coordinator.data or {})
    if (
        entry is not None
        and entry.options.get(
            CONF_CLAMP_NEGATIVE_OFFGRID_POWER,
            DEFAULT_CLAMP_NEGATIVE_OFFGRID_POWER,
        )
        and isinstance(value, (int, float))
        and value < 0
    ):
        return 0
    return value


def _exists_key_with_value(key: str, data: dict[str, Any]) -> bool:
    return key in data


def _nameplate_capacity_present(data: dict[str, Any]) -> bool:
    """Return whether ES.GetStatus published a nameplate capacity.

    Venus E mini stores remaining energy there instead, and the parser leaves
    ``bat_cap`` empty. State of health needs the nameplate; the watt-hour
    estimate does not.
    """
    value = data.get("bat_cap")
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _capacity_attributes(
    coordinator: MarstekDataUpdateCoordinator,
    _info: dict[str, Any],
    _entry: ConfigEntry | None,
) -> dict[str, Any] | None:
    """Return the estimate's attributes, or None before the first poll."""
    raw = (coordinator.data or {}).get(CAPACITY_ATTR_KEY)
    if isinstance(raw, dict):
        return raw
    return None


def _health_attributes(
    coordinator: MarstekDataUpdateCoordinator,
    info: dict[str, Any],
    entry: ConfigEntry | None,
) -> dict[str, Any] | None:
    """Return the estimate's attributes without the card-only lists."""
    raw = _capacity_attributes(coordinator, info, entry)
    if raw is None:
        return None
    return {key: value for key, value in raw.items() if key not in CARD_ATTRIBUTES}


def _restart_attributes(
    coordinator: MarstekDataUpdateCoordinator,
    _info: dict[str, Any],
    _entry: ConfigEntry | None,
) -> dict[str, Any] | None:
    """Return the outage log, or None before the first poll."""
    raw = (coordinator.data or {}).get(RESTARTS_ATTR_KEY)
    return raw if isinstance(raw, dict) else None


def _exists_present_value(key: str, data: dict[str, Any]) -> bool:
    """Return True when the coordinator value is present, including zero."""
    return data.get(key) is not None


def _success_rate_sensor(
    translation_key: str,
    value_fn: Callable[
        [MarstekDataUpdateCoordinator, dict[str, Any], ConfigEntry | None],
        StateType,
    ],
    attributes_fn: Callable[
        [MarstekDataUpdateCoordinator, dict[str, Any], ConfigEntry | None],
        dict[str, Any] | None,
    ],
) -> MarstekSensorEntityDescription:
    """Build a diagnostic percentage sensor over the command statistics."""
    return MarstekSensorEntityDescription(
        key=translation_key,
        translation_key=translation_key,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=1,
        value_fn=value_fn,
        attributes_fn=attributes_fn,
    )


def _api_success_rate_sensor(method: str, translation_key: str) -> MarstekSensorEntityDescription:
    return _success_rate_sensor(
        translation_key,
        lambda coordinator, _info, _entry: command_success_rate(coordinator, method),
        lambda coordinator, _info, _entry: command_stats_attributes(coordinator, method),
    )


def _overall_success_rate_sensor(
    translation_key: str,
) -> MarstekSensorEntityDescription:
    return _success_rate_sensor(
        translation_key,
        lambda coordinator, _info, _entry: overall_command_success_rate(coordinator),
        lambda coordinator, _info, _entry: overall_command_stats_attributes(coordinator),
    )


SENSORS: tuple[MarstekSensorEntityDescription, ...] = (
    MarstekSensorEntityDescription(
        key="battery_soc",
        translation_key="battery_level",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "battery_soc", coordinator.data or {}
        ),
    ),
    MarstekSensorEntityDescription(
        key="battery_power",
        translation_key="battery_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "battery_power", coordinator.data or {}
        ),
    ),
    MarstekSensorEntityDescription(
        key="ongrid_power",
        translation_key="ongrid_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "ongrid_power", coordinator.data or {}
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
        value_fn=_offgrid_power_value,
        exists_fn=lambda data: _exists_key_with_value("offgrid_power", data),
    ),
    MarstekSensorEntityDescription(
        key="pv_power",
        translation_key="pv_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "pv_power", coordinator.data or {}
        ),
        exists_fn=lambda data: _exists_key_with_value("pv_power", data),
    ),
    MarstekSensorEntityDescription(
        key="bat_cap",
        translation_key="battery_total_capacity",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "bat_cap", coordinator.data or {}
        ),
        exists_fn=lambda data: _exists_key_with_value("bat_cap", data),
    ),
    # Derived from the fast-tier polls above. Unknown until three clean
    # windows exist; no extra device traffic.
    MarstekSensorEntityDescription(
        key=CAPACITY_DATA_KEY,
        translation_key="battery_usable_capacity",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            CAPACITY_DATA_KEY, coordinator.data or {}
        ),
        attributes_fn=_capacity_attributes,
    ),
    MarstekSensorEntityDescription(
        key=SOH_DATA_KEY,
        translation_key="battery_state_of_health",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            SOH_DATA_KEY, coordinator.data or {}
        ),
        attributes_fn=_health_attributes,
        exists_fn=_nameplate_capacity_present,
    ),
    # Restarts inferred from the polls above; see helpers/restart_detector.py.
    # Enabled so the battery health card can read it.
    MarstekSensorEntityDescription(
        key=RESTARTS_DATA_KEY,
        translation_key="device_restarts",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            RESTARTS_DATA_KEY, coordinator.data or {}
        ),
        attributes_fn=_restart_attributes,
    ),
    MarstekSensorEntityDescription(
        key="device_mode",
        translation_key="device_mode",
        device_class=SensorDeviceClass.ENUM,
        options=OPERATING_MODES,
        value_fn=_device_mode_value,
    ),
    MarstekSensorEntityDescription(
        key="battery_status",
        translation_key="battery_status",
        device_class=SensorDeviceClass.ENUM,
        options=["charging", "discharging", "idle"],
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "battery_status", coordinator.data or {}
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
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "wifi_rssi", coordinator.data or {}
        ),
    ),
    MarstekSensorEntityDescription(
        key="wifi_sta_ip",
        translation_key="wifi_ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "wifi_sta_ip", coordinator.data or {}
        ),
    ),
    MarstekSensorEntityDescription(
        key="wifi_sta_gate",
        translation_key="wifi_gateway",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "wifi_sta_gate", coordinator.data or {}
        ),
    ),
    MarstekSensorEntityDescription(
        key="wifi_sta_mask",
        translation_key="wifi_subnet_mask",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "wifi_sta_mask", coordinator.data or {}
        ),
    ),
    MarstekSensorEntityDescription(
        key="wifi_sta_dns",
        translation_key="wifi_dns",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "wifi_sta_dns", coordinator.data or {}
        ),
    ),
    # Bat.GetStatus sensors are disabled by default: the request is suspected
    # to trigger device resets on some firmwares (issue #14). The coordinator
    # only sends Bat.GetStatus while at least one of these entities is enabled.
    MarstekSensorEntityDescription(
        key="bat_temp",
        translation_key="battery_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=1,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "bat_temp", coordinator.data or {}
        ),
    ),
    MarstekSensorEntityDescription(
        key="bat_capacity",
        translation_key="battery_remaining_capacity",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "bat_capacity", coordinator.data or {}
        ),
    ),
    MarstekSensorEntityDescription(
        key="bat_rated_capacity",
        translation_key="battery_rated_capacity",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "bat_rated_capacity", coordinator.data or {}
        ),
    ),
    MarstekSensorEntityDescription(
        key="em_total_power",
        translation_key="em_total_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "em_total_power", coordinator.data or {}
        ),
    ),
    MarstekSensorEntityDescription(
        key="em_a_power",
        translation_key="em_a_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "em_a_power", coordinator.data or {}
        ),
    ),
    MarstekSensorEntityDescription(
        key="em_b_power",
        translation_key="em_b_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "em_b_power", coordinator.data or {}
        ),
    ),
    MarstekSensorEntityDescription(
        key="em_c_power",
        translation_key="em_c_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "em_c_power", coordinator.data or {}
        ),
    ),
    MarstekSensorEntityDescription(
        key="em_input_energy",
        translation_key="em_input_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "em_input_energy", coordinator.data or {}
        ),
        exists_fn=lambda data: _exists_present_value("em_input_energy", data),
    ),
    MarstekSensorEntityDescription(
        key="em_output_energy",
        translation_key="em_output_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "em_output_energy", coordinator.data or {}
        ),
        exists_fn=lambda data: _exists_present_value("em_output_energy", data),
    ),
    MarstekSensorEntityDescription(
        key="total_pv_energy",
        translation_key="total_pv_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "total_pv_energy", coordinator.data or {}
        ),
    ),
    MarstekSensorEntityDescription(
        key="total_grid_output_energy",
        translation_key="total_grid_output_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "total_grid_output_energy", coordinator.data or {}
        ),
    ),
    MarstekSensorEntityDescription(
        key="total_grid_input_energy",
        translation_key="total_grid_input_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "total_grid_input_energy", coordinator.data or {}
        ),
    ),
    MarstekSensorEntityDescription(
        key="total_load_energy",
        translation_key="total_load_energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda coordinator, _info, _entry: _value_from_data(
            "total_load_energy", coordinator.data or {}
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
                    state_class=(SensorStateClass.MEASUREMENT if metric_type != "state" else None),
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
