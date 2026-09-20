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

# Read size for every UDP datagram. A datagram longer than the buffer is
# truncated by the kernel and the remainder is discarded, so an oversized
# reply decoded as broken JSON and the command timed out as if the device had
# never answered. 65535 is the largest length a UDP header can express and
# matches the limit ``validate_json_message`` already enforces, so nothing a
# device can put on the wire is cut short.
MAX_UDP_DATAGRAM_BYTES: Final = 65535
