"""ch395.py: the CH395 command set the Control firmware drives over SPI2."""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

_EMU = Path(__file__).resolve().parents[2] / "tools" / "firmware_emulator"
if str(_EMU) not in sys.path:
    sys.path.insert(0, str(_EMU))

import ch395  # noqa: E402
import run_firmware  # noqa: E402


class _Net:
    """Records what the chip asks of the host network."""

    def __init__(self, bind_ok: bool = True) -> None:
        self.bind_ok = bind_ok
        self.opened: list[tuple[int, int]] = []
        self.closed: list[int] = []
        self.sent: list[tuple[int, bytes, tuple[str, int]]] = []
        self.connects: list[tuple[int, tuple[str, int]]] = []

    def open_udp(self, sock: int, port: int) -> bool:
        self.opened.append((sock, port))
        return self.bind_ok

    def close(self, sock: int) -> None:
        self.closed.append(sock)

    def send_udp(self, sock: int, data: bytes, dest: tuple[str, int]) -> None:
        self.sent.append((sock, data, dest))

    def tcp_connect(self, sock: int, dest: tuple[str, int]) -> None:
        self.connects.append((sock, dest))


def _chip(net: _Net | None = None) -> tuple[ch395.CH395, _Net]:
    net = net or _Net()
    config = ch395.ChipConfig(ip="172.28.0.80", gateway="172.28.0.1")
    return ch395.CH395(config, net), net


def _frame(chip: ch395.CH395, *mosi: int, reads: int = 0) -> bytes:
    """One chip-select frame the way the firmware clocks it; returns read bytes.

    Like ``xReadCH395Data`` (``0x0805255c``), reads clock out 0xFF.
    """
    chip.select()
    for byte in mosi:
        chip.exchange(byte)
    out = bytes(chip.exchange(0xFF) for _ in range(reads))
    chip.deselect()
    return out


def _open_udp_server(
    chip: ch395.CH395, sock: int = 5, port: int = 30000, *, recv_buf: bool = True
) -> None:
    """The firmware's UDP server open (``0x08030a50`` type 0, from ``0x08017128``).

    ``recv_buf`` first gives the socket the receive blocks ``0x08027d28`` assigns.
    """
    if recv_buf:
        _frame(chip, ch395.CMD_SET_RECV_BUF, sock, 0x14, 2)
    _frame(chip, ch395.CMD_SET_IP_ADDR_SN, sock, 0xFF, 0xFF, 0xFF, 0xFF)
    _frame(chip, ch395.CMD_SET_PROTO_TYPE_SN, sock, ch395.PROTO_TYPE_UDP)
    _frame(chip, ch395.CMD_SET_DES_PORT_SN, sock, port & 0xFF, port >> 8)
    _frame(chip, ch395.CMD_SET_SOUR_PORT_SN, sock, port & 0xFF, port >> 8)
    _frame(chip, ch395.CMD_OPEN_SOCKET_SN, sock)


def test_check_exist_and_version() -> None:
    chip, _ = _chip()
    assert _frame(chip, ch395.CMD_CHECK_EXIST, 0x57, reads=1) == b"\xa8"
    version = _frame(chip, ch395.CMD_GET_IC_VER, reads=1)[0]
    # Bit 7 clear, bit 6 set; >= 0x44 has GET_GLOB_INT_STATUS_ALL, which the firmware uses.
    assert version & 0xC0 == 0x40
    assert version >= 0x44


def test_answer_starts_after_the_parameters() -> None:
    """Bytes clocked while the command and its parameters go out read 0x00."""
    chip, _ = _chip()
    chip.select()
    assert chip.exchange(ch395.CMD_CHECK_EXIST) == 0
    assert chip.exchange(0x0F) == 0
    assert chip.exchange(0xFF) == 0xF0
    assert chip.exchange(0xFF) == 0  # past the answer
    chip.deselect()


def test_unknown_command_is_ignored() -> None:
    chip, _ = _chip()
    assert _frame(chip, 0x56, 1, 2, 3, reads=2) == b"\0\0"  # keep-alive setting
    assert not chip.int_pending


def test_init_then_dhcp_raises_link_and_lease() -> None:
    """``0x08027b04``: INIT_CH395, status poll, DHCP_ENABLE(1), then the INT# poll."""
    chip, _ = _chip()
    _frame(chip, ch395.CMD_INIT_CH395)
    assert _frame(chip, ch395.CMD_GET_CMD_STATUS, reads=1) == b"\x00"
    assert not chip.int_pending
    assert _frame(chip, ch395.CMD_GET_IP_INF, reads=20) == b"\0" * 20

    _frame(chip, ch395.CMD_DHCP_ENABLE, 1)
    assert chip.int_pending
    status = _frame(chip, ch395.CMD_GET_GLOB_INT_STATUS_ALL, reads=2)
    # Low byte first: the handler at 0x08002af8 builds r4 + (r0 << 8).
    assert status == bytes([ch395.GINT_STAT_PHY_CHANGE | ch395.GINT_STAT_DHCP, 0])
    assert not chip.int_pending  # reading the global status clears it
    assert _frame(chip, ch395.CMD_GET_PHY_STATUS, reads=1) == bytes([ch395.PHY_100M_FLL])
    assert _frame(chip, ch395.CMD_GET_DHCP_STATUS, reads=1) == b"\x00"
    info = _frame(chip, ch395.CMD_GET_IP_INF, reads=20)
    assert info[:4] == bytes([172, 28, 0, 80])
    assert info[4:8] == bytes([172, 28, 0, 1])
    assert info[8:12] == bytes([255, 255, 0, 0])


def test_udp_header_round_trip() -> None:
    header = ch395.pack_udp_header(5, 51234, "172.28.0.2")
    assert header == bytes([5, 0, 0x22, 0xC8, 172, 28, 0, 2])
    assert ch395.unpack_udp_header(header) == (5, 51234, "172.28.0.2")


def test_udp_server_receive_sets_interrupts_and_header() -> None:
    chip, net = _chip()
    _open_udp_server(chip)
    assert net.opened == [(5, 30000)]
    assert _frame(chip, ch395.CMD_GET_SOCKET_STATUS_SN, 5, reads=2) == bytes(
        [ch395.SOCKET_OPEN, ch395.TCP_CLOSED]
    )

    payload = b'{"id":1,"method":"ES.GetStatus","params":{"id":0}}'
    assert chip.deliver(5, payload, ("172.28.0.2", 40000))
    assert chip.int_pending
    status = _frame(chip, ch395.CMD_GET_GLOB_INT_STATUS_ALL, reads=2)
    assert int.from_bytes(status, "little") == ch395.GINT_STAT_SOCK0 << 5  # bit 9
    assert _frame(chip, ch395.CMD_GET_INT_STATUS_SN, 5, reads=1)[0] & ch395.SINT_STAT_RECV

    length = int.from_bytes(_frame(chip, ch395.CMD_GET_RECV_LEN_SN, 5, reads=2), "little")
    assert length == len(payload) + 8
    data = _frame(chip, ch395.CMD_READ_RECV_BUF_SN, 5, length & 0xFF, length >> 8, reads=length)
    assert ch395.unpack_udp_header(data) == (len(payload), 40000, "172.28.0.2")
    assert data[8:] == payload
    assert not chip.int_pending


def test_receive_delivers_one_datagram_at_a_time() -> None:
    """The firmware reads the whole reported length into one string buffer."""
    chip, _ = _chip()
    _open_udp_server(chip)
    chip.deliver(5, b"first", ("10.0.0.1", 1))
    chip.deliver(5, b"second", ("10.0.0.2", 2))
    assert chip.sockets[5].recv_len() == 8 + 5
    chip.sockets[5].read(8 + 5)
    assert chip.int_pending  # the second one is still waiting
    assert chip.sockets[5].recv_len() == 8 + 6


def test_partial_read_keeps_the_rest() -> None:
    chip, _ = _chip()
    _open_udp_server(chip)
    chip.deliver(5, b"abcdef", ("10.0.0.1", 1))
    assert _frame(chip, ch395.CMD_READ_RECV_BUF_SN, 5, 10, 0, reads=10)[8:] == b"ab"
    assert _frame(chip, ch395.CMD_GET_RECV_LEN_SN, 5, reads=2) == b"\x04\x00"


def test_send_goes_to_the_destination_set_before_it() -> None:
    """``0x08003bb4``: SET_IP_ADDR_SN and SET_DES_PORT_SN, then WRITE_SEND_BUF_SN."""
    chip, net = _chip()
    _open_udp_server(chip)
    _frame(chip, ch395.CMD_SET_IP_ADDR_SN, 5, 172, 28, 0, 2)
    _frame(chip, ch395.CMD_SET_DES_PORT_SN, 5, 0x40, 0x9C)  # 40000
    _frame(chip, ch395.CMD_WRITE_SEND_BUF_SN, 5, 3, 0, *b"abc")
    assert net.sent == [(5, b"abc", ("172.28.0.2", 40000))]
    # 0x08003510 marks the socket busy until SENBUF_FREE comes back.
    flags = _frame(chip, ch395.CMD_GET_INT_STATUS_SN, 5, reads=1)[0]
    assert flags == ch395.SINT_STAT_SENBUF_FREE | ch395.SINT_STAT_SEND_OK
    assert _frame(chip, ch395.CMD_GET_INT_STATUS_SN, 5, reads=1) == b"\x00"


def test_udp_client_mode_takes_raw_payload_from_its_peer_only() -> None:
    chip, _ = _chip()
    _frame(chip, ch395.CMD_SET_IP_ADDR_SN, 2, 10, 0, 0, 9)
    _frame(chip, ch395.CMD_SET_PROTO_TYPE_SN, 2, ch395.PROTO_TYPE_UDP)
    _frame(chip, ch395.CMD_SET_SOUR_PORT_SN, 2, 0x9A, 0x1F)
    _frame(chip, ch395.CMD_OPEN_SOCKET_SN, 2)
    assert not chip.deliver(2, b"x", ("10.0.0.8", 1))
    assert chip.deliver(2, b"hello", ("10.0.0.9", 1))
    assert chip.sockets[2].read(100) == b"hello"


def test_failed_bind_reports_an_error_status() -> None:
    chip, _ = _chip(_Net(bind_ok=False))
    _open_udp_server(chip)
    assert _frame(chip, ch395.CMD_GET_CMD_STATUS, reads=1) == bytes([ch395.CH395_ERR_USE])
    assert _frame(chip, ch395.CMD_GET_SOCKET_STATUS_SN, 5, reads=2)[0] == ch395.SOCKET_CLOSED
    assert not chip.deliver(5, b"x", ("10.0.0.1", 1))


def test_sockets_four_to_seven_need_a_buffer() -> None:
    """Only sockets 0-3 own receive blocks after INIT; 0x08027d28 assigns the rest."""
    chip, _ = _chip()
    _open_udp_server(chip, recv_buf=False)
    assert not chip.deliver(5, b"x", ("10.0.0.1", 1))
    _frame(chip, ch395.CMD_SET_RECV_BUF, 5, 0x14, 2)
    assert chip.deliver(5, b"x", ("10.0.0.1", 1))


def test_tcp_connect_times_out() -> None:
    chip, net = _chip()
    _frame(chip, ch395.CMD_SET_IP_ADDR_SN, 1, 10, 0, 0, 9)
    _frame(chip, ch395.CMD_SET_PROTO_TYPE_SN, 1, ch395.PROTO_TYPE_TCP)
    _frame(chip, ch395.CMD_SET_DES_PORT_SN, 1, 0x9B, 0x1F)
    _frame(chip, ch395.CMD_OPEN_SOCKET_SN, 1)
    _frame(chip, ch395.CMD_TCP_CONNECT_SN, 1)
    assert net.opened == []  # TCP is not bridged
    assert net.connects == [(1, ("10.0.0.9", 8091))]
    assert _frame(chip, ch395.CMD_GET_SOCKET_STATUS_SN, 1, reads=2) == bytes(
        [ch395.SOCKET_OPEN, ch395.TCP_SYN_SENT]
    )
    chip.tcp_timed_out(1)
    assert _frame(chip, ch395.CMD_GET_GLOB_INT_STATUS, reads=1) == bytes(
        [ch395.GINT_STAT_SOCK0 << 1]
    )
    assert _frame(chip, ch395.CMD_GET_INT_STATUS_SN, 1, reads=1) == bytes([ch395.SINT_STAT_TIM_OUT])
    assert _frame(chip, ch395.CMD_GET_SOCKET_STATUS_SN, 1, reads=2)[0] == ch395.SOCKET_CLOSED


def test_tcp_listen() -> None:
    """Socket 0 is the Modbus TCP server on port 502 (``0x080466f0``)."""
    chip, _ = _chip()
    _frame(chip, ch395.CMD_SET_PROTO_TYPE_SN, 0, ch395.PROTO_TYPE_TCP)
    _frame(chip, ch395.CMD_SET_SOUR_PORT_SN, 0, 0xF6, 0x01)
    _frame(chip, ch395.CMD_OPEN_SOCKET_SN, 0)
    _frame(chip, ch395.CMD_TCP_LISTEN_SN, 0)
    assert _frame(chip, ch395.CMD_GET_SOCKET_STATUS_SN, 0, reads=2) == bytes(
        [ch395.SOCKET_OPEN, ch395.TCP_LISTEN]
    )


def test_init_closes_open_sockets() -> None:
    chip, net = _chip()
    _open_udp_server(chip)
    chip.deliver(5, b"x", ("10.0.0.1", 1))
    _frame(chip, ch395.CMD_INIT_CH395)
    assert net.closed == [5]
    assert not chip.int_pending
    assert chip.sockets[5].recv_size == 0


def test_bridge_link_messages() -> None:
    """``X`` gets one ``D`` reply; ``C`` edges frame the commands."""
    bridge = ch395.Bridge(ch395.ChipConfig(ip="172.28.0.80", gateway="172.28.0.1"))
    bridge.chip = ch395.CH395(bridge.config, _Net())
    assert bridge.handle(ord("C"), 0) == b""
    assert bridge.handle(ord("X"), ch395.CMD_CHECK_EXIST) == b"D\x00"
    assert bridge.handle(ord("X"), 0x01) == b"D\x00"
    assert bridge.handle(ord("X"), 0xFF) == b"D\xfe"
    assert bridge.handle(ord("C"), 1) == b""


def _render_control(work: Path, *, ethernet: bool) -> tuple[str, str]:
    blob = work / "blob.bin"
    blob.write_bytes(struct.pack("<II", 0x20020000, 0x080081AD))
    args = argparse.Namespace(
        eeprom=work / "eeprom.bin",
        local_api_port=30000,
        can_port=3457,
        uart_port=3456,
        ble_mac="02:ee:00:00:00:02",
        quantum=None,
        mips=None,
        modem_reset_port=3466,
        ethernet=ethernet,
        eth_port=3999,
    )
    board = run_firmware.BOARDS["control"]
    resc = run_firmware._render(args, work, board, blob, board.app_base)
    return (work / "control.repl").read_text(), resc.read_text()


def test_render_without_ethernet_keeps_the_idle_spi2(tmp_path: Path) -> None:
    repl, resc = _render_control(tmp_path, ethernet=False)
    assert str(run_firmware.HERE / "renode" / "spi2.py") in repl
    assert "gpioPortB" not in resc
    assert "gpioPortD" not in resc
    assert not (tmp_path / "ch395_spi.py").exists()


def test_render_with_ethernet_links_the_ch395(tmp_path: Path) -> None:
    repl, resc = _render_control(tmp_path, ethernet=True)
    script = tmp_path / "ch395_spi.py"
    assert str(script) in repl
    assert 'int("3999")' in script.read_text()
    # Both hooks reach spi2's spare offsets 0x3F0/0x3F4 (base 0x40003800).
    assert "SetHookBeforePeripheralWrite sysbus.gpioPortB" in resc
    assert "WriteDoubleWord(0x40003BF0, cs)" in resc
    assert "SetHookAfterPeripheralRead sysbus.gpioPortD" in resc
    assert "ReadDoubleWord(0x40003BF4)" in resc


def test_bridge_listens_for_broadcasts_only_on_a_specific_bind() -> None:
    """A socket bound to one address misses broadcasts; the chip gets them."""
    config = ch395.ChipConfig(ip="172.28.0.80", gateway="172.28.0.1")
    bridge = ch395.Bridge(config, bind_ip="172.28.0.80")
    assert bridge._broadcast_addresses() == ["172.28.255.255", "255.255.255.255"]
    assert ch395.Bridge(config, bind_ip="0.0.0.0")._broadcast_addresses() == []
