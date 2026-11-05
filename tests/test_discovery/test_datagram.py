"""One discovery datagram maps to the same device dict as before the split."""

from __future__ import annotations

import json

from custom_components.marstek.discovery import _accept_discovery_datagram


def _payload(**result: object) -> bytes:
    """Encode a GetDevice reply."""
    body: dict[str, object] = {"id": 0, "result": result}
    if "src" in result:
        body["src"] = result.pop("src")
    return json.dumps(body).encode()


def test_known_device_matches_captured_dict() -> None:
    """A Venus E reply keeps the observed address and the captured field set."""
    raw = json.dumps(
        {
            "id": 0,
            "src": "VenusE-AABBCCDDEEFF",
            "result": {
                "device": "VenusE 3.0",
                "ver": 150,
                "ip": "192.168.1.40",
                "ble_mac": "aa:bb:cc:dd:ee:ff",
                "wifi_mac": "11:22:33:44:55:66",
            },
        }
    ).encode()
    device, was_echo = _accept_discovery_datagram(raw, "10.0.0.8", 54321, set())
    assert was_echo is False
    assert device == {
        "id": 0,
        "device_type": "VenusE 3.0",
        "version": 150,
        "wifi_name": "",
        "ip": "10.0.0.8",
        "wifi_mac": "11:22:33:44:55:66",
        "ble_mac": "aa:bb:cc:dd:ee:ff",
        "mac": "11:22:33:44:55:66",
        "model": "VenusE 3.0",
        "firmware": "150",
        "port": 54321,
    }


def test_echo_duplicate_and_noise_are_skipped() -> None:
    """Echoes, repeats, bad bytes and a reply with no MAC do not become devices."""
    seen: set[str] = set()
    echo = json.dumps({"id": 0, "method": "Marstek.GetDevice", "params": {"ble_mac": "0"}}).encode()
    device, was_echo = _accept_discovery_datagram(echo, "10.0.0.8", 30000, seen)
    assert device is None
    assert was_echo is True

    assert _accept_discovery_datagram(b"\xff", "10.0.0.8", 30000, seen) == (None, False)
    assert _accept_discovery_datagram(b"not-json", "10.0.0.8", 30000, seen) == (None, False)
    assert _accept_discovery_datagram(b"null", "10.0.0.8", 30000, seen) == (None, False)

    nameless = _payload(device="VenusE 3.0", ver=150, ip="10.0.0.9")
    assert _accept_discovery_datagram(nameless, "10.0.0.9", 30000, seen) == (None, False)

    first = _payload(device="VenusE 3.0", ver=150, ble_mac="aa:bb:cc:dd:ee:ff", ip="10.0.0.8")
    accepted, _echo = _accept_discovery_datagram(first, "10.0.0.8", 30000, seen)
    assert accepted is not None
    again, echo_again = _accept_discovery_datagram(first, "10.0.0.8", 30000, seen)
    assert again is None
    assert echo_again is False
