"""Venus E mini (VNSEM-0 301) wire behaviour captured in issue #86."""

from __future__ import annotations

import json
import socket
from unittest.mock import MagicMock, patch

from mock_device import MockMarstekDevice
from mock_device.device import _IN_PKTINFO
from mock_device.utils import LIMITED_BROADCAST, get_broadcast_address

_LAN_SENDER = ("192.168.1.10", 30000)
_SUBNET_BROADCAST = "192.168.1.255"


def _mini(device: str = "VNSEM-0", ver: int = 301) -> MockMarstekDevice:
    mock = MockMarstekDevice(simulate=False, device_config={"device": device, "ver": ver})
    mock.sock = MagicMock()
    mock._broadcast_addr = _SUBNET_BROADCAST
    return mock


def _pktinfo(destination: str) -> list[tuple[int, int, bytes]]:
    payload = _IN_PKTINFO.pack(2, socket.inet_aton("192.168.1.50"), socket.inet_aton(destination))
    return [(socket.IPPROTO_IP, socket.IP_PKTINFO, payload)]


def _deliver(
    device: MockMarstekDevice,
    request: dict[str, object],
    sender: tuple[str, int] = _LAN_SENDER,
    destination: str = "192.168.1.50",
) -> None:
    assert device.sock is not None
    device.sock.recvmsg.return_value = (
        json.dumps(request).encode(),
        _pktinfo(destination),
        0,
        sender,
    )
    device._handle_request()


def _get_device() -> dict[str, object]:
    return {"id": 7, "method": "Marstek.GetDevice", "params": {"ble_mac": "0"}}


class TestMiniUdpRules:
    """The mini answers from its API port only, by broadcast, never discovery."""

    def test_reply_goes_to_subnet_broadcast_on_api_port(self) -> None:
        device = _mini()
        _deliver(device, _get_device())

        assert device.sock is not None
        payload, target = device.sock.sendto.call_args[0]
        assert target == (_SUBNET_BROADCAST, device.port)
        assert json.loads(payload)["result"]["device"] == "VNSEM-0"

    def test_request_from_other_source_port_is_ignored(self) -> None:
        device = _mini()
        _deliver(device, _get_device(), sender=("192.168.1.10", 51234))

        assert device.sock is not None
        device.sock.sendto.assert_not_called()

    def test_broadcast_get_device_is_ignored(self) -> None:
        device = _mini()
        _deliver(device, _get_device(), destination=_SUBNET_BROADCAST)
        _deliver(device, _get_device(), destination=LIMITED_BROADCAST)

        assert device.sock is not None
        device.sock.sendto.assert_not_called()

    def test_pv_and_unknown_methods_get_no_reply(self) -> None:
        device = _mini()
        _deliver(device, {"id": 1, "method": "PV.GetStatus", "params": {"id": 0}})
        _deliver(device, {"id": 2, "method": "Foo.Get", "params": {}})

        assert device.sock is not None
        device.sock.sendto.assert_not_called()

    def test_loopback_keeps_unit_test_addressing(self) -> None:
        """Loopback senders (unit tests) still get replies on their source port."""
        device = _mini()
        assert device.sock is not None
        device.sock.recvmsg.return_value = (
            json.dumps(_get_device()).encode(),
            [],
            0,
            ("127.0.0.1", 40000),
        )
        device._handle_request()

        assert device.sock.sendto.call_args[0][1] == ("127.0.0.1", 40000)

    def test_es_bat_cap_is_remaining_energy(self) -> None:
        device = _mini()
        response = device.build_response(1, "ES.GetStatus", {"id": 0})
        bat = device.build_response(2, "Bat.GetStatus", {"id": 0})

        assert response is not None
        assert bat is not None
        assert response["result"]["bat_cap"] == bat["result"]["bat_capacity"]
        assert response["result"]["bat_cap"] < bat["result"]["rated_capacity"]

    def test_control_firmware_is_unchanged(self) -> None:
        """A Venus E 3.0 still answers -32601, unicast to the sender's API port."""
        device = _mini(device="VenusE 3.0", ver=150)
        assert device.sock is not None
        device.sock.recvfrom.return_value = (
            json.dumps({"id": 3, "method": "Foo.Get", "params": {}}).encode(),
            _LAN_SENDER,
        )
        device._handle_request()

        payload, target = device.sock.sendto.call_args[0]
        assert target == ("192.168.1.10", device.port)
        assert json.loads(payload)["error"]["code"] == -32601


class TestBroadcastAddress:
    """Subnet broadcast lookup for the interface that holds the mock's IP."""

    def test_unknown_ip_falls_back_to_limited_broadcast(self) -> None:
        assert get_broadcast_address("203.0.113.77") == LIMITED_BROADCAST

    def test_loopback_interface_is_found(self) -> None:
        # lo carries 127.0.0.1 with no broadcast address; the ioctl answers
        # 0.0.0.0 or fails, and either way the lookup does not raise.
        assert isinstance(get_broadcast_address("127.0.0.1"), str)

    def test_missing_fcntl_falls_back(self) -> None:
        with patch.dict("sys.modules", {"fcntl": None}):
            assert get_broadcast_address("192.168.1.50") == LIMITED_BROADCAST
