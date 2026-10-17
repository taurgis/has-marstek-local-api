"""Venus E firmware that keeps a previous DHCP address in GetDevice."""

from __future__ import annotations

from mock_device import MockMarstekDevice

_STALE_IP = "192.168.2.173"
_LIVE_IP = "192.168.3.22"


def test_getdevice_ip_can_stay_stale_while_sta_ip_is_live() -> None:
    """Issue #108: GetDevice ip is the old lease; Wifi.GetStatus sta_ip is current."""
    device = MockMarstekDevice(
        simulate=False,
        ip_override=_LIVE_IP,
        device_config={
            "device": "VenusE 3.0",
            "ver": 147,
            "ble_mac": "3c1acc00ab34",
            "wifi_mac": "dab37000ab34",
            "wifi_name": "house",
            "stale_ip": _STALE_IP,
        },
    )

    get_device = device.build_response(0, "Marstek.GetDevice", {"ble_mac": "0"})
    wifi = device.build_response(1, "Wifi.GetStatus", {"id": 0})

    assert get_device is not None
    assert wifi is not None
    assert get_device["result"]["ip"] == _STALE_IP
    assert get_device["result"]["ver"] == 147
    assert wifi["result"]["sta_ip"] == _LIVE_IP
