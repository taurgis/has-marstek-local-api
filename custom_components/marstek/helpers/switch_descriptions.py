"""Switch entity descriptions for Marstek SYS controls."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.switch import SwitchEntityDescription
from homeassistant.const import EntityCategory

from ..firmware_profile import FirmwareProfile
from ..pymarstek.const import (
    BLE_ADV_DISABLED,
    BLE_ADV_ENABLED,
    CMD_BLE_ADV,
    CMD_LED_CTRL,
    LED_OFF,
    LED_ON,
)


@dataclass(kw_only=True)
class MarstekSwitchEntityDescription(SwitchEntityDescription):  # type: ignore[misc]
    """Marstek switch entity description."""

    method: str
    param_key: str
    on_value: int
    off_value: int
    supported_fn: Callable[[FirmwareProfile], bool]


SWITCH_ENTITIES: tuple[MarstekSwitchEntityDescription, ...] = (
    MarstekSwitchEntityDescription(
        key="bluetooth_advertising",
        translation_key="bluetooth_advertising",
        entity_category=EntityCategory.CONFIG,
        method=CMD_BLE_ADV,
        param_key="enable",
        on_value=BLE_ADV_ENABLED,
        off_value=BLE_ADV_DISABLED,
        supported_fn=lambda profile: profile.supports_sys_ble_advertising,
    ),
    MarstekSwitchEntityDescription(
        key="panel_led",
        translation_key="panel_led",
        entity_category=EntityCategory.CONFIG,
        method=CMD_LED_CTRL,
        param_key="state",
        on_value=LED_ON,
        off_value=LED_OFF,
        supported_fn=lambda profile: profile.supports_sys_led,
    ),
)
