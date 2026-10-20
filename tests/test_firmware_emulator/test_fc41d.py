"""fc41d.py options the Venus E mini PMU 301 board relies on."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_EMU = Path(__file__).resolve().parents[2] / "tools" / "firmware_emulator"
if str(_EMU) not in sys.path:
    sys.path.insert(0, str(_EMU))

import fc41d  # noqa: E402


class _Writer:
    def __init__(self) -> None:
        self.data = b""

    def write(self, data: bytes) -> None:
        self.data += data


class _Transport:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, data: bytes, dest: tuple[str, int]) -> None:
        self.sent.append((data, dest))


def _modem(**config: Any) -> tuple[fc41d.FC41D, _Writer]:
    writer = _Writer()
    modem = fc41d.FC41D(config=fc41d.ModemConfig(ip="172.28.0.75", **config))
    modem.writer = writer  # type: ignore[assignment]
    return modem, writer


def test_autojoin_reports_got_ip_once_after_qwlmac() -> None:
    """A provisioned module joins after setup, once per UART link."""
    modem, writer = _modem(autojoin=True)
    modem.handle("AT+QWLMAC")
    modem.handle("AT+QWLMAC")
    assert writer.data.count(b"+QSTASTAT:GOT_IP") == 1
    assert writer.data.index(b"WLAN_CONNECTED") < writer.data.index(b"GOT_IP")


def test_without_autojoin_qwlmac_only_answers() -> None:
    """Other boards keep waiting for their own join command."""
    modem, writer = _modem()
    modem.handle("AT+QWLMAC")
    assert b"QSTASTAT" not in writer.data
    assert b"+QWLMAC:" in writer.data


def _send(modem: fc41d.FC41D, ip: str) -> tuple[str, int]:
    transport = _Transport()
    modem.services[3] = fc41d.UdpService(3, 30000, transport=transport)  # type: ignore[arg-type]
    modem._send_udp(3, b"{}", ip, 30000)
    return transport.sent[0][1]


def test_lan_broadcast_sends_the_reported_subnet_broadcast_as_limited() -> None:
    """``<a.b.c>.255`` on a /24 module goes out as 255.255.255.255."""
    modem, _writer = _modem(netmask="255.255.255.0", lan_broadcast=True)
    assert _send(modem, "172.28.0.255") == ("255.255.255.255", 30000)
    assert _send(modem, "172.28.0.10") == ("172.28.0.10", 30000)


def test_without_lan_broadcast_the_subnet_broadcast_is_kept() -> None:
    """The other boards still send the address the firmware chose."""
    modem, _writer = _modem(netmask="255.255.255.0")
    assert _send(modem, "172.28.0.255") == ("172.28.0.255", 30000)
