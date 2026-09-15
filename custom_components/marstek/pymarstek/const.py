"""Constants for the pymarstek library."""

from __future__ import annotations

from typing import Final

DEFAULT_UDP_PORT: Final = 30000
DISCOVERY_TIMEOUT: Final = 10.0

CMD_DISCOVER: Final = "Marstek.GetDevice"
CMD_BATTERY_STATUS: Final = "Bat.GetStatus"
CMD_ES_STATUS: Final = "ES.GetStatus"
CMD_ES_MODE: Final = "ES.GetMode"
CMD_ES_SET_MODE: Final = "ES.SetMode"
CMD_PV_GET_STATUS: Final = "PV.GetStatus"
CMD_WIFI_STATUS: Final = "Wifi.GetStatus"
CMD_EM_STATUS: Final = "EM.GetStatus"
CMD_DOD_SET: Final = "DOD.SET"
CMD_BLE_ADV: Final = "Ble.Adv"
CMD_LED_CTRL: Final = "Led.Ctrl"

DOD_MIN_VALUE: Final = 30
DOD_MAX_VALUE: Final = 88
DOD_DEFAULT_VALUE: Final = 88
BLE_ADV_ENABLED: Final = 0
BLE_ADV_DISABLED: Final = 1
LED_ON: Final = 1
LED_OFF: Final = 0
