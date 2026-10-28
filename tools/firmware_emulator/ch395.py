"""WCH CH395 Ethernet chip emulator for the Renode-hosted Control firmware.

The Control MCU drives a CH395 over SPI2 (``0x40003800``, chip select PB12,
INT# on PD8, reset on PD9; see ``tools/firmware/ANALYSIS.md``). With
``run_firmware.py --ethernet``, ``renode/ch395_spi.py`` forwards every SPI
byte and chip-select edge to this process over TCP and reads the reply byte
back. This process plays the chip: it answers the command set the firmware
uses, runs DHCP as an instant lease from the command line, and bridges the
chip's UDP sockets to host UDP sockets, so the Local API answers over
"Ethernet" as well as over the FC41D.

Link protocol (two bytes per message, both directions):

- Renode to host: ``C`` + level (chip select, 0 = selected), ``X`` + MOSI byte.
- Host to Renode: ``D`` + MISO byte (one per ``X``), ``I`` + INT# level
  (0 = interrupt pending), sent whenever the level changes.

TCP sockets open, listen and connect, but nothing is bridged: a connect fails
with ``SINT_STAT_TIM_OUT`` after ``--tcp-timeout`` seconds, like a network with
no route to the cloud.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import socket
import struct
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import ClassVar, Protocol

_LOGGER = logging.getLogger("ch395")

# Command codes (CH395 datasheet section 5, CH395INC.H names).
CMD_GET_IC_VER = 0x01
CMD_RESET_ALL = 0x05
CMD_CHECK_EXIST = 0x06
CMD_GET_GLOB_INT_STATUS_ALL = 0x19
CMD_SET_MAC_ADDR = 0x21
CMD_SET_IP_ADDR = 0x22
CMD_SET_GWIP_ADDR = 0x23
CMD_SET_MASK_ADDR = 0x24
CMD_GET_PHY_STATUS = 0x26
CMD_INIT_CH395 = 0x27
CMD_GET_UNREACH_IPPORT = 0x28
CMD_GET_GLOB_INT_STATUS = 0x29
CMD_GET_CMD_STATUS = 0x2C
CMD_GET_REMOT_IPP_SN = 0x2D
CMD_CLEAR_RECV_BUF_SN = 0x2E
CMD_GET_SOCKET_STATUS_SN = 0x2F
CMD_GET_INT_STATUS_SN = 0x30
CMD_SET_IP_ADDR_SN = 0x31
CMD_SET_DES_PORT_SN = 0x32
CMD_SET_SOUR_PORT_SN = 0x33
CMD_SET_PROTO_TYPE_SN = 0x34
CMD_OPEN_SOCKET_SN = 0x35
CMD_TCP_LISTEN_SN = 0x36
CMD_TCP_CONNECT_SN = 0x37
CMD_TCP_DISNCONNECT_SN = 0x38
CMD_WRITE_SEND_BUF_SN = 0x39
CMD_GET_RECV_LEN_SN = 0x3B
CMD_READ_RECV_BUF_SN = 0x3C
CMD_CLOSE_SOCKET_SN = 0x3D
CMD_GET_MAC_ADDR = 0x40
CMD_DHCP_ENABLE = 0x41
CMD_GET_DHCP_STATUS = 0x42
CMD_GET_IP_INF = 0x43
CMD_SET_RECV_BUF = 0x52
CMD_EEPROM_READ = 0xEB
CMD_READ_GPIO_REG = 0xEC

# Commands that answer inside the same chip-select frame, mapped to the number
# of parameter bytes that come before the answer. Everything else is a write
# and runs when chip select goes high.
READ_PARAMS = {
    CMD_GET_IC_VER: 0,
    CMD_CHECK_EXIST: 1,
    CMD_GET_GLOB_INT_STATUS_ALL: 0,
    CMD_GET_PHY_STATUS: 0,
    CMD_GET_UNREACH_IPPORT: 0,
    CMD_GET_GLOB_INT_STATUS: 0,
    CMD_GET_CMD_STATUS: 0,
    CMD_GET_REMOT_IPP_SN: 1,
    CMD_GET_SOCKET_STATUS_SN: 1,
    CMD_GET_INT_STATUS_SN: 1,
    CMD_GET_RECV_LEN_SN: 1,
    CMD_READ_RECV_BUF_SN: 3,
    CMD_GET_MAC_ADDR: 0,
    CMD_GET_DHCP_STATUS: 0,
    CMD_GET_IP_INF: 0,
    CMD_EEPROM_READ: 3,
    CMD_READ_GPIO_REG: 1,
}

# Global interrupt bits (CMD_GET_GLOB_INT_STATUS[_ALL]); socket n is bit 4 + n.
GINT_STAT_UNREACH = 0x01
GINT_STAT_IP_CONFLI = 0x02
GINT_STAT_PHY_CHANGE = 0x04
GINT_STAT_DHCP = 0x08
GINT_STAT_SOCK0 = 0x10

# Socket interrupt bits (CMD_GET_INT_STATUS_SN).
SINT_STAT_SENBUF_FREE = 0x01
SINT_STAT_SEND_OK = 0x02
SINT_STAT_RECV = 0x04
SINT_STAT_CONNECT = 0x08
SINT_STAT_DISCONNECT = 0x10
SINT_STAT_TIM_OUT = 0x40

# CMD_GET_CMD_STATUS values.
CH395_ERR_SUCCESS = 0x00
CH395_ERR_BUSY = 0x10
CH395_ERR_USE = 0x1B

PROTO_TYPE_IP_RAW = 0
PROTO_TYPE_MAC_RAW = 1
PROTO_TYPE_UDP = 2
PROTO_TYPE_TCP = 3

SOCKET_CLOSED = 0x00
SOCKET_OPEN = 0x05
TCP_CLOSED = 0x00
TCP_LISTEN = 0x01
TCP_SYN_SENT = 0x02

PHY_DISCONN = 0x01
PHY_100M_FLL = 0x08

IC_VERSION = 0x46  # bit 6 set, version 6: new enough for the _ALL interrupt command
SOCKETS = 8
BLOCK = 512
UDP_SERVER_IP = b"\xff\xff\xff\xff"  # destination that puts a UDP socket in server mode
UDP_HEADER = struct.Struct("<HH4s")  # payload length, remote port, remote IP

# Receive buffer blocks per socket after CMD_INIT_CH395 (datasheet 5.48).
DEFAULT_RECV_BLOCKS = (8, 8, 8, 8, 0, 0, 0, 0)


def pack_udp_header(payload_len: int, port: int, ip: str) -> bytes:
    """Server-mode receive header: length and port low byte first, IP in wire order.

    The firmware's ``Extract_udp_data_ch395`` reads the port from bytes 2-3 low
    byte first and prints bytes 4-7 as ``a.b.c.d`` (``0x0802c05a``).
    """
    return UDP_HEADER.pack(payload_len, port, socket.inet_aton(ip))


def unpack_udp_header(data: bytes) -> tuple[int, int, str]:
    """Inverse of :func:`pack_udp_header`: ``(payload length, port, ip)``."""
    length, port, raw_ip = UDP_HEADER.unpack_from(data)
    return length, port, socket.inet_ntoa(raw_ip)


class Network(Protocol):
    """What the chip asks of the host network."""

    def open_udp(self, sock: int, port: int) -> bool:
        """Bind host UDP ``port`` for chip socket ``sock``; False when it fails."""

    def close(self, sock: int) -> None:
        """Release whatever the host holds for chip socket ``sock``."""

    def send_udp(self, sock: int, data: bytes, dest: tuple[str, int]) -> None:
        """Send one datagram from chip socket ``sock``."""

    def tcp_connect(self, sock: int, dest: tuple[str, int]) -> None:
        """A TCP connect was started; the host reports the outcome later."""


@dataclass
class ChipConfig:
    """Network facts the chip hands the MCU (the DHCP lease and the link)."""

    ip: str
    gateway: str
    netmask: str = "255.255.0.0"
    dns: str = ""
    mac: str = "02:ee:00:00:00:03"
    link: int = PHY_100M_FLL


@dataclass
class Socket:
    """One of the chip's eight sockets."""

    index: int
    proto: int = PROTO_TYPE_UDP
    dest_ip: bytes = b"\0\0\0\0"
    dest_port: int = 0
    src_port: int = 0
    is_open: bool = False
    server: bool = False  # UDP server mode: any peer, 8-byte header per datagram
    tcp_state: int = TCP_CLOSED
    flags: int = 0
    recv_size: int = 0
    rx: deque[bytes] = field(default_factory=deque)

    @property
    def dest(self) -> tuple[str, int]:
        return socket.inet_ntoa(self.dest_ip), self.dest_port

    def pending(self) -> int:
        """Interrupt bits still to report; RECV holds while data is buffered."""
        return self.flags | (SINT_STAT_RECV if self.rx else 0)

    def recv_len(self) -> int:
        """Bytes ``GET_RECV_LEN_SN`` reports: one datagram at a time."""
        return len(self.rx[0]) if self.rx else 0

    def read(self, length: int) -> bytes:
        """Take ``length`` bytes off the receive buffer (``READ_RECV_BUF_SN``)."""
        out = b""
        while self.rx and len(out) < length:
            head = self.rx.popleft()
            take = length - len(out)
            out += head[:take]
            if len(head) > take:
                self.rx.appendleft(head[take:])
        return out


def _u16(data: bytes, offset: int = 0) -> int:
    return int.from_bytes(data[offset : offset + 2], "little")


class CH395:
    """Command-level model of the chip, fed one SPI byte at a time.

    ``select``/``deselect`` follow the chip-select line and ``exchange`` clocks
    one byte: it returns the MISO byte for this clock and consumes MOSI. The
    first byte of a frame is the command. A read command answers once its
    parameters are in; a write command runs when chip select goes high.
    """

    def __init__(self, config: ChipConfig, network: Network) -> None:
        self.config = config
        self.network = network
        self.sockets = [Socket(n) for n in range(SOCKETS)]
        self.global_flags = 0
        self.cmd_status = CH395_ERR_SUCCESS
        self.dhcp_done = False
        self.static: dict[int, bytes] = {}
        self._frame = bytearray()
        self._out: deque[int] = deque()
        self._answered = False
        self._reset_sockets()

    # ---- SPI ------------------------------------------------------------
    def select(self) -> None:
        """Chip select low: the next byte is a command."""
        self._end_frame()

    def deselect(self) -> None:
        """Chip select high: run a pending write command."""
        self._end_frame()

    def exchange(self, mosi: int) -> int:
        miso = self._out.popleft() if self._out else 0x00
        self._frame.append(mosi & 0xFF)
        if not self._answered:
            need = READ_PARAMS.get(self._frame[0])
            if need is not None and len(self._frame) == need + 1:
                self._answered = True
                self._out.extend(self._read(self._frame[0], bytes(self._frame[1:])))
        return miso

    def _end_frame(self) -> None:
        if self._frame:
            _LOGGER.debug("frame %s", self._frame.hex(" "))
        if self._frame and self._frame[0] not in READ_PARAMS:
            self._write(self._frame[0], bytes(self._frame[1:]))
        self._frame.clear()
        self._out.clear()
        self._answered = False

    # ---- interrupts -----------------------------------------------------
    def global_status(self) -> int:
        """``GET_GLOB_INT_STATUS_ALL`` value: global bits plus one bit per socket."""
        status = self.global_flags
        for sock in self.sockets:
            if sock.pending():
                status |= GINT_STAT_SOCK0 << sock.index
        return status

    @property
    def int_pending(self) -> bool:
        """INT# is driven low while this is true."""
        return self.global_status() != 0

    # ---- host side ------------------------------------------------------
    def deliver(self, index: int, data: bytes, addr: tuple[str, int]) -> bool:
        """Queue a datagram that arrived for socket ``index``; False if dropped."""
        sock = self.sockets[index]
        if not (sock.is_open and sock.proto == PROTO_TYPE_UDP):
            return False
        if sock.server:
            record = pack_udp_header(len(data), addr[1], addr[0]) + data
        elif addr[0] == sock.dest[0]:
            record = data
        else:
            return False
        if len(record) > sock.recv_size:
            _LOGGER.warning("socket %d: %d-byte datagram exceeds its buffer", index, len(record))
            return False
        sock.rx.append(record)
        return True

    def tcp_timed_out(self, index: int) -> None:
        """A connect started with ``TCP_CONNECT_SN`` failed."""
        sock = self.sockets[index]
        if sock.tcp_state == TCP_SYN_SENT:
            sock.flags |= SINT_STAT_TIM_OUT
            sock.tcp_state = TCP_CLOSED
            sock.is_open = False

    # ---- commands -------------------------------------------------------
    def _read(self, cmd: int, params: bytes) -> bytes:
        handler = self._READS.get(cmd)
        return handler(self, params) if handler else b""

    def _write(self, cmd: int, params: bytes) -> None:
        handler = self._WRITES.get(cmd)
        if handler is None:
            _LOGGER.debug("command %#04x %s ignored", cmd, params.hex())
            return
        handler(self, params)

    def _sock(self, params: bytes) -> Socket | None:
        if params and params[0] < SOCKETS:
            return self.sockets[params[0]]
        return None

    def _read_glob_all(self, _params: bytes) -> bytes:
        status = self.global_status()
        self.global_flags = 0
        return status.to_bytes(2, "little")

    def _read_glob(self, params: bytes) -> bytes:
        return self._read_glob_all(params)[:1]

    def _read_int_sn(self, params: bytes) -> bytes:
        sock = self._sock(params)
        if sock is None:
            return b"\0"
        status = sock.pending()
        sock.flags = 0
        return bytes([status])

    def _read_recv_len(self, params: bytes) -> bytes:
        sock = self._sock(params)
        return (sock.recv_len() if sock else 0).to_bytes(2, "little")

    def _read_recv_buf(self, params: bytes) -> bytes:
        sock = self._sock(params)
        return sock.read(_u16(params, 1)) if sock else b""

    def _read_socket_status(self, params: bytes) -> bytes:
        sock = self._sock(params)
        if sock is None or not sock.is_open:
            return bytes([SOCKET_CLOSED, TCP_CLOSED])
        return bytes([SOCKET_OPEN, sock.tcp_state])

    def _read_remote(self, params: bytes) -> bytes:
        sock = self._sock(params)
        return (sock.dest_ip + sock.dest_port.to_bytes(2, "little")) if sock else b"\0" * 6

    def _read_ip_inf(self, _params: bytes) -> bytes:
        if not self.dhcp_done:
            return b"\0" * 20
        c = self.config
        return b"".join(_ip(a) for a in (c.ip, c.gateway, c.netmask, c.dns or c.gateway, ""))

    def _read_phy(self, _params: bytes) -> bytes:
        return bytes([self.config.link])

    def _write_init(self, _params: bytes) -> None:
        _LOGGER.info("INIT_CH395")
        self._reset_sockets()
        self.cmd_status = CH395_ERR_SUCCESS

    def _reset_sockets(self) -> None:
        for sock in self.sockets:
            if sock.is_open:
                self.network.close(sock.index)
            self.sockets[sock.index] = Socket(
                sock.index, recv_size=DEFAULT_RECV_BLOCKS[sock.index] * BLOCK
            )
        self.global_flags = 0
        self.dhcp_done = False

    def _write_dhcp(self, params: bytes) -> None:
        self.cmd_status = CH395_ERR_SUCCESS
        if not params or not params[0]:
            return
        # Instant lease: report the link and the DHCP result together.
        self.dhcp_done = True
        self.global_flags |= GINT_STAT_PHY_CHANGE | GINT_STAT_DHCP
        _LOGGER.info("DHCP lease %s gw %s mask %s", self.config.ip, self.config.gateway,
                     self.config.netmask)  # fmt: skip

    def _write_sock_ip(self, params: bytes) -> None:
        sock = self._sock(params)
        if sock and len(params) >= 5:
            sock.dest_ip = params[1:5]

    def _write_dest_port(self, params: bytes) -> None:
        sock = self._sock(params)
        if sock and len(params) >= 3:
            sock.dest_port = _u16(params, 1)

    def _write_src_port(self, params: bytes) -> None:
        sock = self._sock(params)
        if sock and len(params) >= 3:
            sock.src_port = _u16(params, 1)

    def _write_proto(self, params: bytes) -> None:
        sock = self._sock(params)
        if sock and len(params) >= 2:
            sock.proto = params[1]

    def _write_recv_buf(self, params: bytes) -> None:
        sock = self._sock(params)
        if sock and len(params) >= 3:
            sock.recv_size = params[2] * BLOCK

    def _write_open(self, params: bytes) -> None:
        sock = self._sock(params)
        if sock is None:
            return
        if sock.is_open:
            self.network.close(sock.index)
        sock.rx.clear()
        sock.flags = 0
        sock.tcp_state = TCP_CLOSED
        sock.server = sock.proto == PROTO_TYPE_UDP and sock.dest_ip == UDP_SERVER_IP
        ok = True
        if sock.proto == PROTO_TYPE_UDP:
            ok = self.network.open_udp(sock.index, sock.src_port)
        sock.is_open = ok
        self.cmd_status = CH395_ERR_SUCCESS if ok else CH395_ERR_USE
        _LOGGER.info(
            "OPEN socket %d proto %d src %d dest %s:%d%s%s",
            sock.index, sock.proto, sock.src_port, *sock.dest,
            " (UDP server)" if sock.server else "", "" if ok else " FAILED",
        )  # fmt: skip

    def _write_listen(self, params: bytes) -> None:
        sock = self._sock(params)
        if sock and sock.is_open:
            sock.tcp_state = TCP_LISTEN
        self.cmd_status = CH395_ERR_SUCCESS

    def _write_connect(self, params: bytes) -> None:
        sock = self._sock(params)
        self.cmd_status = CH395_ERR_SUCCESS
        if sock and sock.is_open:
            sock.tcp_state = TCP_SYN_SENT
            self.network.tcp_connect(sock.index, sock.dest)

    def _write_disconnect(self, params: bytes) -> None:
        sock = self._sock(params)
        self.cmd_status = CH395_ERR_SUCCESS
        if sock and sock.tcp_state != TCP_CLOSED:
            sock.tcp_state = TCP_CLOSED
            sock.flags |= SINT_STAT_DISCONNECT

    def _write_close(self, params: bytes) -> None:
        sock = self._sock(params)
        self.cmd_status = CH395_ERR_SUCCESS
        if sock is None:
            return
        if sock.is_open:
            self.network.close(sock.index)
        sock.is_open = False
        sock.tcp_state = TCP_CLOSED
        sock.rx.clear()

    def _write_clear_recv(self, params: bytes) -> None:
        sock = self._sock(params)
        if sock:
            sock.rx.clear()

    def _write_send(self, params: bytes) -> None:
        sock = self._sock(params)
        if sock is None or len(params) < 3:
            return
        data = params[3 : 3 + _u16(params, 1)]
        if sock.is_open and sock.proto == PROTO_TYPE_UDP:
            self.network.send_udp(sock.index, data, sock.dest)
            sock.flags |= SINT_STAT_SENBUF_FREE | SINT_STAT_SEND_OK
        else:
            sock.flags |= SINT_STAT_SENBUF_FREE

    def _write_static(self, params: bytes) -> None:
        # SET_MAC/IP/GW/MASK: kept for the log; the lease comes from the config.
        self.static[len(self.static)] = params

    _READS: ClassVar[dict[int, Callable[[CH395, bytes], bytes]]] = {
        CMD_GET_IC_VER: lambda _s, _p: bytes([IC_VERSION]),
        CMD_CHECK_EXIST: lambda _s, p: bytes([~p[0] & 0xFF]),
        CMD_GET_GLOB_INT_STATUS_ALL: _read_glob_all,
        CMD_GET_GLOB_INT_STATUS: _read_glob,
        CMD_GET_PHY_STATUS: _read_phy,
        CMD_GET_UNREACH_IPPORT: lambda _s, _p: b"\0" * 8,
        CMD_GET_CMD_STATUS: lambda s, _p: bytes([s.cmd_status]),
        CMD_GET_REMOT_IPP_SN: _read_remote,
        CMD_GET_SOCKET_STATUS_SN: _read_socket_status,
        CMD_GET_INT_STATUS_SN: _read_int_sn,
        CMD_GET_RECV_LEN_SN: _read_recv_len,
        CMD_READ_RECV_BUF_SN: _read_recv_buf,
        CMD_GET_MAC_ADDR: lambda s, _p: bytes.fromhex(s.config.mac.replace(":", "")),
        CMD_GET_DHCP_STATUS: lambda s, _p: bytes([0 if s.dhcp_done else 1]),
        CMD_GET_IP_INF: _read_ip_inf,
        CMD_EEPROM_READ: lambda _s, p: b"\xff" * p[2],
        CMD_READ_GPIO_REG: lambda _s, _p: b"\0",
    }
    _WRITES: ClassVar[dict[int, Callable[[CH395, bytes], None]]] = {
        CMD_RESET_ALL: _write_init,
        CMD_INIT_CH395: _write_init,
        CMD_SET_MAC_ADDR: _write_static,
        CMD_SET_IP_ADDR: _write_static,
        CMD_SET_GWIP_ADDR: _write_static,
        CMD_SET_MASK_ADDR: _write_static,
        CMD_DHCP_ENABLE: _write_dhcp,
        CMD_SET_IP_ADDR_SN: _write_sock_ip,
        CMD_SET_DES_PORT_SN: _write_dest_port,
        CMD_SET_SOUR_PORT_SN: _write_src_port,
        CMD_SET_PROTO_TYPE_SN: _write_proto,
        CMD_SET_RECV_BUF: _write_recv_buf,
        CMD_OPEN_SOCKET_SN: _write_open,
        CMD_TCP_LISTEN_SN: _write_listen,
        CMD_TCP_CONNECT_SN: _write_connect,
        CMD_TCP_DISNCONNECT_SN: _write_disconnect,
        CMD_CLOSE_SOCKET_SN: _write_close,
        CMD_CLEAR_RECV_BUF_SN: _write_clear_recv,
        CMD_WRITE_SEND_BUF_SN: _write_send,
    }


def _ip(text: str) -> bytes:
    return socket.inet_aton(text) if text else b"\0\0\0\0"


@dataclass
class Bridge:
    """Host side of the chip: the Renode link plus real UDP sockets."""

    config: ChipConfig
    bind_ip: str = "0.0.0.0"
    tcp_timeout: float = 3.0
    broadcast_to: str = ""
    chip: CH395 | None = None
    writer: asyncio.StreamWriter | None = None
    udp: dict[int, list[socket.socket]] = field(default_factory=dict)
    int_level: int = 1

    # ---- Network --------------------------------------------------------
    def open_udp(self, sock: int, port: int) -> bool:
        self.close(sock)
        raw = self._bind(self.bind_ip, port)
        if raw is None:
            _LOGGER.error("socket %d: bind %s:%d failed", sock, self.bind_ip, port)
            return False
        # A socket bound to one address never sees broadcasts. The chip does,
        # so also listen on the subnet and limited broadcast addresses (Linux
        # hands a broadcast to the sockets bound to that address).
        held = [raw]
        for addr in self._broadcast_addresses():
            extra = self._bind(addr, port)
            if extra is not None:
                held.append(extra)
        loop = asyncio.get_running_loop()
        for each in held:
            loop.add_reader(each.fileno(), self._readable, sock, each)
        self.udp[sock] = held
        _LOGGER.info("socket %d: UDP on %s:%d", sock, self.bind_ip, port)
        return True

    @staticmethod
    def _bind(addr: str, port: int) -> socket.socket | None:
        raw = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # The firmware's DNS client opens sockets 6 and 7 on the same source
        # port (2000); the chip allows that, so the host must too.
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            raw.bind((addr, port))
        except OSError:
            raw.close()
            return None
        raw.setblocking(False)
        return raw

    def _broadcast_addresses(self) -> list[str]:
        if self.bind_ip in ("", "0.0.0.0"):
            return []
        net = ipaddress.IPv4Network(f"{self.config.ip}/{self.config.netmask}", strict=False)
        return [str(net.broadcast_address), "255.255.255.255"]

    def _readable(self, sock: int, raw: socket.socket) -> None:
        try:
            data, addr = raw.recvfrom(65535)
        except OSError:
            return
        self.datagram(sock, data, addr)

    def close(self, sock: int) -> None:
        loop = asyncio.get_running_loop()
        for raw in self.udp.pop(sock, []):
            loop.remove_reader(raw.fileno())
            raw.close()

    def send_udp(self, sock: int, data: bytes, dest: tuple[str, int]) -> None:
        held = self.udp.get(sock)
        if self.broadcast_to and self._is_broadcast(dest[0]):
            dest = (self.broadcast_to, dest[1])
        if not held:
            _LOGGER.warning("socket %d: send with no host socket", sock)
            return
        _LOGGER.info("UDP eth[%d] -> %s:%d %r", sock, dest[0], dest[1], data[:300])
        try:
            held[0].sendto(data, dest)
        except OSError as err:
            _LOGGER.warning("sendto %s failed: %s", dest, err)

    def tcp_connect(self, sock: int, dest: tuple[str, int]) -> None:
        _LOGGER.info("socket %d: TCP connect %s:%d (fails in %.0f s)", sock, *dest,
                     self.tcp_timeout)  # fmt: skip
        chip = self.chip
        loop = asyncio.get_running_loop()
        loop.call_later(self.tcp_timeout, self._tcp_fail, chip, sock)

    def _tcp_fail(self, chip: CH395 | None, sock: int) -> None:
        if chip is not None and chip is self.chip:
            chip.tcp_timed_out(sock)
            self.push_int()

    def _is_broadcast(self, ip: str) -> bool:
        if ip == "255.255.255.255":
            return True
        net = ipaddress.IPv4Network(f"{self.config.ip}/{self.config.netmask}", strict=False)
        return ipaddress.IPv4Address(ip) == net.broadcast_address

    # ---- link -----------------------------------------------------------
    def datagram(self, sock: int, data: bytes, addr: tuple[str, int]) -> None:
        if self.chip is None:
            return
        if self.chip.deliver(sock, data, addr):
            _LOGGER.info("UDP %s:%d -> eth[%d] %r", addr[0], addr[1], sock, data[:200])
        self.push_int()

    def push_int(self) -> None:
        level = 0 if self.chip is not None and self.chip.int_pending else 1
        if level != self.int_level and self.writer is not None:
            self.int_level = level
            self.writer.write(bytes([ord("I"), level]))

    def handle(self, kind: int, value: int) -> bytes:
        """Apply one link message; return the reply bytes (empty for none)."""
        assert self.chip is not None
        reply = b""
        if kind == ord("X"):
            reply = bytes([ord("D"), self.chip.exchange(value)])
        elif kind == ord("C"):
            if value:
                self.chip.deselect()
            else:
                self.chip.select()
        return reply

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """One Renode connection is one power-on of the chip."""
        _LOGGER.info("SPI link connected")
        for sock in list(self.udp):
            self.close(sock)
        self.writer = writer
        self.chip = CH395(self.config, self)
        self.int_level = 1
        writer.write(b"I\x01")
        try:
            while True:
                msg = await reader.readexactly(2)
                reply = self.handle(msg[0], msg[1])
                if reply:
                    writer.write(reply)
                self.push_int()
        except (asyncio.IncompleteReadError, ConnectionError):
            _LOGGER.warning("SPI link closed")
        finally:
            if self.writer is writer:
                self.writer = None


async def _main(args: argparse.Namespace) -> None:
    config = ChipConfig(
        ip=args.ip, gateway=args.gateway, netmask=args.netmask, dns=args.dns, mac=args.mac
    )
    bridge = Bridge(
        config,
        bind_ip=args.bind_ip or args.ip,
        tcp_timeout=args.tcp_timeout,
        broadcast_to=args.broadcast_to,
    )
    server = await asyncio.start_server(bridge.serve, args.link_host, args.link_port)
    _LOGGER.info("waiting for the SPI link on %s:%d", args.link_host, args.link_port)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--link-host", default="127.0.0.1")
    parser.add_argument("--link-port", type=int, default=3458, help="TCP port for ch395_spi.py")
    parser.add_argument("--ip", required=True, help="Address the DHCP lease hands out")
    parser.add_argument("--gateway", default="172.28.0.1")
    parser.add_argument("--netmask", default="255.255.0.0")
    parser.add_argument("--dns", default="", help="DNS server in the lease (default: gateway)")
    parser.add_argument("--mac", default="02:ee:00:00:00:03")
    parser.add_argument("--bind-ip", default="", help="Host address for the sockets (default --ip)")
    parser.add_argument(
        "--broadcast-to", default="", metavar="IP", help="Send LAN broadcasts to this IP instead"
    )
    parser.add_argument(
        "--tcp-timeout", type=float, default=3.0, help="Seconds before a TCP connect fails"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
