"""fc41d.py options the Venus E mini PMU 301 board relies on."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, ClassVar

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


class _Closable:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


async def test_reset_line_switches_the_module_off_and_back_on() -> None:
    """Low closes every socket and silences the module; high boots and rejoins."""
    modem, writer = _modem(boot_s=0.0, rejoin_s=0.0)
    transport = _Closable()
    modem.services[3] = fc41d.UdpService(3, 30000, transport=transport)  # type: ignore[arg-type]

    modem.reset_line(0)
    assert transport.closed is True
    assert modem.services == {}
    assert modem.powered is False

    modem.handle('AT+QIOPEN=3,"UDP SERVICE","0.0.0.0",0,30000,1')
    assert b"+QIOPEN: 3,1" in writer.data  # no address until the rejoin

    writer.data = b""
    modem.reset_line(1)
    await asyncio.sleep(0.01)
    assert modem.powered is True
    assert modem.linked is True
    assert writer.data.index(b"WLAN_CONNECTED") < writer.data.index(b"GOT_IP")


async def test_a_second_pulse_cancels_the_first_boot() -> None:
    """Only the boot after the last pulse brings the module back."""
    modem, writer = _modem(boot_s=0.02, rejoin_s=0.0)
    modem.reset_line(0)
    modem.reset_line(1)
    modem.reset_line(0)
    await asyncio.sleep(0.05)
    assert modem.powered is False
    assert b"GOT_IP" not in writer.data


def test_reset_line_ignores_a_high_level_while_powered() -> None:
    """Boot-time GPIO set-up raises the line on a module that is already on."""
    modem, writer = _modem()
    modem.reset_line(1)
    assert modem.powered is True
    assert writer.data == b""


class _Port:
    """A UDP port that frees on the next loop pass after close, as asyncio does."""

    held: ClassVar[set[int]] = set()

    def __init__(self, *_args: Any) -> None:
        self.port = 0

    def setsockopt(self, *_args: Any) -> None:
        pass

    def bind(self, addr: tuple[str, int]) -> None:
        if addr[1] in self.held:
            raise OSError(98, "Address already in use")
        self.port = addr[1]
        self.held.add(self.port)

    def close(self) -> None:
        asyncio.get_running_loop().call_soon(self.held.discard, self.port)


async def test_reopen_without_close_rebinds_the_same_port(monkeypatch: Any) -> None:
    """HMG-50 reopens its CT socket with no AT+QICLOSE in between."""

    async def endpoint(_factory: Any, sock: _Port) -> tuple[_Port, None]:
        return sock, None

    _Port.held.clear()
    monkeypatch.setattr(fc41d.socket, "socket", _Port)
    monkeypatch.setattr(asyncio.get_running_loop(), "create_datagram_endpoint", endpoint)
    modem, writer = _modem()
    await modem._open_udp(0, 22222, "0.0.0.0")
    await modem._open_udp(0, 22222, "0.0.0.0")
    assert writer.data.count(b"+QIOPEN: 0,0") == 2
