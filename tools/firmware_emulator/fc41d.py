"""Quectel FC41D Wi-Fi/BLE module emulator for the Renode-hosted Control firmware.

The Control MCU drives the FC41D over a UART with Quectel AT commands. Renode
exposes that UART as a TCP server socket; this script connects to it, answers
the AT commands and bridges the firmware's ``"UDP SERVICE"`` sockets to real
host UDP sockets, so Home Assistant talks to the vendor firmware unmodified.

Only the commands the firmware actually issues are modelled. Anything else is
acknowledged with ``OK`` and logged, so gaps show up in the log.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass, field

from cloud import StandInCloud

_LOGGER = logging.getLogger("fc41d")

CRLF = b"\r\n"
LIMITED_BROADCAST = "255.255.255.255"
_INLINE_SEND = re.compile(rb'AT\+QISEND=(\d+),(\d+),"', re.I)
_SEND_TAIL = re.compile(rb'"(?:,"([^"]*)",(\d+))?\r\n?')


@dataclass
class ModemConfig:
    """Identity and network facts the module reports to the MCU."""

    ip: str
    netmask: str = "255.255.0.0"
    gateway: str = "172.28.0.1"
    ssid: str = "MarstekEmu"
    bssid: str = "02:00:00:00:00:01"
    rssi: int = -52
    wifi_mac: str = "02:ee:00:00:00:01"
    ble_mac: str = "02:ee:00:00:00:02"
    version: str = "FC41DAAR03A05"
    bind_ip: str = "0.0.0.0"
    # Remote ports whose UDP SERVICE sockets stay on 127.0.0.1 (see --loopback-port).
    loopback_ports: frozenset[int] = frozenset()
    dialect: str = "control"  # "control" (VNSE3-0/VNSA-0/VNSD-0) or "hmg50"
    broadcast_to: str = ""  # unicast target for LAN broadcasts; "" sends them as is
    # Join the saved AP unprompted once the MCU has finished module setup, the
    # way a provisioned module reconnects after power-up (see --autojoin).
    autojoin: bool = False
    # Send a broadcast to the reported subnet as 255.255.255.255 (--lan-broadcast).
    lan_broadcast: bool = False
    # After the reset line goes high: seconds until the module answers AT
    # commands, then until it has rejoined the saved AP and has an address.
    boot_s: float = 1.0
    rejoin_s: float = 4.0
    # Answer MQTT/HTTP as a reachable Marstek cloud (cloud.py). False fails
    # them fast, like a battery whose internet access is blocked (--no-cloud).
    cloud: bool = True


@dataclass
class UdpService:
    """One ``AT+QIOPEN=<id>,"UDP SERVICE",...`` socket."""

    conn_id: int
    local_port: int
    transport: asyncio.DatagramTransport | None = None
    last_peer: tuple[str, int] | None = None


class _ResetLine(asyncio.DatagramProtocol):
    """The reset line level, sent by run_firmware.py's GPIO hook as b"0" or b"1"."""

    def __init__(self, current: list[FC41D]) -> None:
        self._current = current

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if self._current and data.strip() in (b"0", b"1"):
            self._current[0].reset_line(int(data.strip()))


class _UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, modem: FC41D, service: UdpService) -> None:
        self._modem = modem
        self._service = service

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._service.last_peer = addr
        self._modem.push_udp(self._service, data, addr)


@dataclass
class FC41D:
    """AT command state machine on top of the Renode UART socket."""

    config: ModemConfig
    writer: asyncio.StreamWriter | None = None
    services: dict[int, UdpService] = field(default_factory=dict)
    pending_payload: tuple[int, Callable[[bytes], None]] | None = None  # (length, handler)
    # The MCU's BLE task (build 150: 0x8006a1c) re-issues AT+QBLEADVSTOP/START
    # until AT+QBLESTAT reports the state it asked for, and Ble.Adv only answers
    # once it does. A fixed "ADVERTISING" left every disable request unanswered.
    ble_advertising: bool = True
    joined: bool = False  # --autojoin already reported GOT_IP on this UART link
    # False while the MCU holds the reset line low and until the module has
    # booted again. An unpowered module answers nothing and has no sockets.
    powered: bool = True
    resets: int = 0  # reset pulses seen; a newer pulse cancels an older boot
    linked: bool = True  # has an address; False from a reset until the rejoin
    held: bool = False  # the reset line is low right now
    cloud: StandInCloud = field(default_factory=StandInCloud)

    # ---- output -------------------------------------------------------
    def send(self, data: bytes) -> None:
        assert self.writer is not None
        self.writer.write(data)

    def reply(self, *lines: str) -> None:
        for line in lines:
            _LOGGER.debug("<- %s", line)
            self.send(CRLF + line.encode() + CRLF)

    def urc(self, line: str) -> None:
        _LOGGER.info("URC <- %s", line)
        self.send(CRLF + line.encode() + CRLF)

    def expect_payload(self, length: int, handler: Callable[[bytes], None]) -> None:
        """Hand the next ``length`` raw bytes from the MCU to ``handler``."""
        self.pending_payload = (length, handler)

    def reset_line(self, level: int) -> None:
        """Follow the module reset line: 0 switches it off, 1 starts its boot.

        The Control firmware does not rejoin Wi-Fi or reopen its sockets after
        a reset. The module rejoins the AP it saved (``AT+QSTAAPINFODEF``) by
        itself, and the firmware's ``AT+QISTATE`` checks find the sockets gone
        and reopen them, so the outage is as long as the firmware makes it.
        """
        if level == 0 and not self.held:
            # A pulse during a boot starts it over.
            self.resets += 1
            self.held = True
            self.powered = False
            self.joined = False
            self.linked = False
            self.pending_payload = None
            self.cloud.reset()
            for service in self.services.values():
                if service.transport:
                    service.transport.close()
            self.services.clear()
            _LOGGER.warning("reset line low: module off, sockets closed")
        elif level == 1 and self.held:
            self.held = False
            asyncio.get_running_loop().create_task(self._boot(self.resets))

    async def _boot(self, pulse: int) -> None:
        await asyncio.sleep(self.config.boot_s)
        if pulse != self.resets:
            return
        self.powered = True
        _LOGGER.warning("module booted after reset #%d", pulse)
        await asyncio.sleep(self.config.rejoin_s)
        if pulse == self.resets and self.powered:
            self.linked = True
            self.urc("+QSTASTAT:WLAN_CONNECTED")
            self.urc("+QSTASTAT:GOT_IP")

    def push_udp(self, service: UdpService, data: bytes, addr: tuple[str, int]) -> None:
        _LOGGER.info("UDP %s:%d -> fw[%d] %r", addr[0], addr[1], service.conn_id, data[:200])
        head = f'+QIURC: "recv",{service.conn_id},{len(data)},"{addr[0]}",{addr[1]}'
        self.send(CRLF + head.encode() + CRLF + data)

    # ---- input --------------------------------------------------------
    async def run(self, reader: asyncio.StreamReader) -> None:
        buf = b""
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                _LOGGER.warning("UART socket closed")
                return
            if not self.powered:
                buf = b""  # an unpowered module hears nothing
                continue
            buf += chunk
            buf = await self._consume(buf)

    async def _consume(self, buf: bytes) -> bytes:
        while True:
            if self.pending_payload is not None:
                length, handler = self.pending_payload
                if len(buf) < length:
                    return buf
                payload, buf = buf[:length], buf[length:]
                self.pending_payload = None
                handler(payload)
                continue
            buf = buf.lstrip(b"\r\n")
            inline = _INLINE_SEND.match(buf)
            if inline:
                # AT+QISEND=<id>,<len>,"<len raw bytes>","<ip>",<port>\r
                start, length = inline.end(), int(inline.group(2))
                tail = _SEND_TAIL.match(buf, start + length)
                if tail is None:
                    if len(buf) < start + length + 64:
                        return buf
                    _LOGGER.warning("unparsable inline QISEND: %r", buf[:120])
                    buf = buf[buf.find(b"\r") + 1 :]
                    continue
                self._send_udp(
                    int(inline.group(1)),
                    buf[start : start + length],
                    tail.group(1).decode(),
                    int(tail.group(2)) if tail.group(2) else None,
                )
                buf = buf[tail.end() :]
                continue
            idx = buf.find(b"\r")
            if idx < 0:
                return buf
            line, buf = buf[:idx], buf[idx + 1 :]
            if buf.startswith(b"\n"):
                buf = buf[1:]
            text = line.decode("latin1").strip()
            if text:
                self.handle(text)

    # ---- commands -----------------------------------------------------
    def handle(self, cmd: str) -> None:
        _LOGGER.debug("-> %s", cmd)
        up = cmd.upper()
        if up == "AT" or up.startswith("ATE"):
            self.reply("OK")
        elif up == "AT+QVERSION":
            self.reply(f"+QVERSION: {self.config.version}", "OK")
        elif up.startswith("AT+QSSLCERT"):
            self._sslcert(cmd)
        elif up.startswith("AT+QSTAAPINFO"):
            self.reply("OK")
            self.urc("+QSTASTAT:WLAN_CONNECTED")
            self.urc("+QSTASTAT:GOT_IP")
        elif up == "AT+QGETIP=STATION":
            c = self.config
            # The MCU copies "ip:...dns:..." up to the first CRLF, so no leading CRLF.
            # It scans ip: up to "gate" and mask: up to "dns", fixing the field order.
            self.send(
                f"+QGETIP: ip:{c.ip},gateway:{c.gateway},mask:{c.netmask},dns:{c.gateway}".encode()
                + CRLF
                + CRLF
                + b"OK"
                + CRLF
            )
        elif up == "AT+QGETWIFISTATE":
            c = self.config
            self.reply(
                f"+QGETWIFISTATE: ssid={c.ssid},bssid={c.bssid},rssi={c.rssi},channel=6",
                "OK",
            )
        elif up == "AT+QBLEADDR?":
            self.reply(f"+QBLEADDR:{self.config.ble_mac}", "OK")
        elif up == "AT+QWLMAC":
            # VNSEMINI-0 PMU 301 asks this after BLE setup and waits for the
            # info line. OK alone leaves it stuck. Same colon style as QBLEADDR.
            self.reply(f"+QWLMAC:{self.config.wifi_mac}", "OK")
            if self.config.autojoin and not self.joined:
                self.joined = True
                self.urc("+QSTASTAT:WLAN_CONNECTED")
                self.urc("+QSTASTAT:GOT_IP")
        elif up == "AT+QBLESTAT":
            state = "ADVERTISING" if self.ble_advertising else "NOADVERTISING"
            self.reply(f"+QBLESTAT:{state}", "OK")
        elif up in ("AT+QBLEADVSTART", "AT+QBLEADVSTOP"):
            self.ble_advertising = up == "AT+QBLEADVSTART"
            self.reply("OK")
        elif up.startswith("AT+QIOPEN="):
            self._qiopen(cmd)
        elif up.startswith("AT+QISEND="):
            self._qisend(cmd)
        elif up.startswith("AT+QICLOSE="):
            self._qiclose(cmd)
        elif up.startswith("AT+QISTATE"):
            lines = [
                f'+QISTATE: {s.conn_id},"UDP SERVICE","0.0.0.0",0,{s.local_port},2'
                for s in self.services.values()
            ]
            self.reply(*lines, "OK")
        elif self.config.cloud and self.cloud.handle(cmd, self):
            pass
        elif up.startswith(("AT+QMTOPEN=", "AT+QMTCONN=", "AT+QHTTPGET")):
            # No cloud: MQTT/HTTP fail fast the way a module with no route would.
            self.reply("ERROR")
        else:
            _LOGGER.info("unmodelled: %s -> OK", cmd)
            self.reply("OK")

    def _sslcert(self, cmd: str) -> None:
        m = re.match(r'AT\+QSSLCERT="([^"]+)",(\d+)(?:,(\d+))?', cmd, re.I)
        if m and m.group(2) == "2" and m.group(3):
            length = int(m.group(3))
            # VNSE3/A/D wait for the ">" prompt; HMG-50 waits for "CONNECT".
            self.send(CRLF + b"CONNECT" + CRLF if self.config.dialect == "hmg50" else CRLF + b">")
            self.pending_payload = (length, lambda _data: self.reply("OK"))
            return
        if cmd.upper() == "AT+QSSLCERT?":
            self.reply(
                '+QSSLCERT: "CA",1', '+QSSLCERT: "User Cert",1', '+QSSLCERT: "User Key",1', "OK"
            )
            return
        self.reply("OK")

    def _qiopen(self, cmd: str) -> None:
        m = re.match(r'AT\+QIOPEN=(\d+),"UDP SERVICE","([^"]*)",(\d+),(\d+)(?:,(\d+))?', cmd, re.I)
        if not m:
            _LOGGER.warning("unsupported QIOPEN: %s", cmd)
            self.reply("ERROR")
            return
        conn_id, remote_port, local_port = int(m.group(1)), int(m.group(3)), int(m.group(4))
        self.reply("OK")
        if not self.linked:
            self.urc(f"+QIOPEN: {conn_id},1")  # no address yet
            return
        bind_ip = "127.0.0.1" if remote_port in self.config.loopback_ports else self.config.bind_ip
        asyncio.get_running_loop().create_task(self._open_udp(conn_id, local_port, bind_ip))

    async def _open_udp(self, conn_id: int, local_port: int, bind_ip: str) -> None:
        old = self.services.pop(conn_id, None)
        if old and old.transport:
            old.transport.close()
        service = UdpService(conn_id, local_port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # No SO_REUSEADDR: a second emulator on the port must fail, not split traffic.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sock.bind((bind_ip, local_port))
        except OSError as err:
            _LOGGER.error("bind %s:%d failed: %s", bind_ip, local_port, err)
            self.urc(f"+QIOPEN: {conn_id},1")
            return
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _UdpProtocol(self, service), sock=sock
        )
        service.transport = transport
        self.services[conn_id] = service
        _LOGGER.info("UDP SERVICE %d listening on %s:%d", conn_id, bind_ip, local_port)
        self.urc(f"+QIOPEN: {conn_id},0")

    def _qisend(self, cmd: str) -> None:
        m = re.match(
            r'AT\+QISEND=(\d+),(\d+)(?:,"([^"]*)"(?:,"?([^",]*)"?)?(?:,(\d+))?)?', cmd, re.I
        )
        if not m:
            self.reply("ERROR")
            return
        conn_id, length = int(m.group(1)), int(m.group(2))
        ip = m.group(3)
        port_text = m.group(5) or m.group(4)

        def deliver(data: bytes) -> None:
            self._send_udp(
                conn_id,
                data,
                ip or "",
                int(port_text) if port_text and port_text.isdigit() else None,
            )

        self.send(CRLF + b"> ")
        self.pending_payload = (length, deliver)

    def _send_udp(self, conn_id: int, data: bytes, ip: str, port: int | None) -> None:
        service = self.services.get(conn_id)
        dest = service.last_peer if service else None
        if port is not None:
            # An empty IP is the firmware's LAN broadcast (Marstek app discovery).
            dest = (ip or "255.255.255.255", port)
            if port in self.config.loopback_ports:
                # Broadcasts included: the peer for this port shares our namespace.
                dest = ("127.0.0.1", port)
        if dest is not None and self.config.lan_broadcast and self._is_broadcast(dest[0]):
            # The module puts a subnet broadcast on its LAN as a link broadcast.
            # The host network may be wider than the mask the module reports.
            dest = (LIMITED_BROADCAST, dest[1])
        if dest is not None and self.config.broadcast_to and self._is_broadcast(dest[0]):
            # Keep LAN broadcasts (CT discovery, app discovery) off the shared network.
            dest = (self.config.broadcast_to, dest[1])
        if service is None or service.transport is None or dest is None:
            _LOGGER.warning("QISEND on %d with no route", conn_id)
            self.reply("SEND FAIL")
            return
        _LOGGER.info("UDP fw[%d] -> %s:%d %r", conn_id, dest[0], dest[1], data[:300])
        try:
            service.transport.sendto(data, dest)
        except OSError as err:
            _LOGGER.warning("sendto %s failed: %s", dest, err)
        self.reply("SEND OK")

    def _is_broadcast(self, ip: str) -> bool:
        if ip == "255.255.255.255":
            return True
        try:
            net = ipaddress.IPv4Network(f"{self.config.ip}/{self.config.netmask}", strict=False)
            return ipaddress.IPv4Address(ip) == net.broadcast_address
        except ValueError:
            return False

    def _qiclose(self, cmd: str) -> None:
        conn_id = int(re.findall(r"\d+", cmd)[0])
        service = self.services.pop(conn_id, None)
        if service and service.transport:
            service.transport.close()
        self.reply("OK")


async def _main(args: argparse.Namespace) -> None:
    config = ModemConfig(
        ip=args.ip,
        gateway=args.gateway,
        bssid=args.bssid,
        wifi_mac=args.wifi_mac,
        ble_mac=args.ble_mac,
        bind_ip=args.bind_ip,
        loopback_ports=frozenset(args.loopback_port),
        dialect=args.dialect,
        broadcast_to=args.broadcast_to,
        autojoin=args.autojoin,
        netmask=args.netmask,
        lan_broadcast=args.lan_broadcast,
        boot_s=args.boot_seconds,
        rejoin_s=args.rejoin_seconds,
        cloud=not args.no_cloud,
    )
    current: list[FC41D] = []
    if args.reset_port:
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(
            lambda: _ResetLine(current), local_addr=("127.0.0.1", args.reset_port)
        )
    while True:
        try:
            reader, writer = await asyncio.open_connection(args.uart_host, args.uart_port)
        except OSError:
            await asyncio.sleep(0.5)
            continue
        _LOGGER.info("connected to firmware UART at %s:%d", args.uart_host, args.uart_port)
        modem = FC41D(config, writer)
        current[:] = [modem]
        await modem.run(reader)
        await asyncio.sleep(0.5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uart-host", default="127.0.0.1")
    parser.add_argument("--uart-port", type=int, default=3456)
    parser.add_argument("--ip", required=True, help="IP the module reports (AT+QGETIP)")
    parser.add_argument("--gateway", default="172.28.0.1")
    parser.add_argument("--bind-ip", default="0.0.0.0")
    parser.add_argument("--wifi-mac", default="02:ee:00:00:00:01")
    parser.add_argument(
        "--bssid",
        default="02:00:00:00:00:01",
        help="AP BSSID in AT+QGETWIFISTATE; the firmware reports it as wifi_mac",
    )
    parser.add_argument(
        "--loopback-port",
        type=int,
        action="append",
        default=[],
        metavar="PORT",
        help="Keep UDP SERVICE traffic to remote PORT on 127.0.0.1, broadcasts included "
        "(12345 keeps CT003 polls to a same-namespace AstraMeter). Repeatable.",
    )
    parser.add_argument("--ble-mac", default="02:ee:00:00:00:02")
    parser.add_argument(
        "--dialect",
        choices=("control", "hmg50"),
        default="control",
        help="Firmware line: VNSE3-0/VNSA-0/VNSD-0 (control) or HMG-50",
    )
    parser.add_argument(
        "--broadcast-to",
        default="",
        metavar="IP",
        help="Send the firmware's LAN broadcasts (CT discovery) to this host instead",
    )
    parser.add_argument(
        "--netmask", default="255.255.0.0", help="Mask the module reports (AT+QGETIP)"
    )
    parser.add_argument(
        "--lan-broadcast",
        action="store_true",
        help="Send broadcasts to the reported subnet as 255.255.255.255",
    )
    parser.add_argument(
        "--autojoin",
        action="store_true",
        help="Report WLAN_CONNECTED and GOT_IP after AT+QWLMAC without a join command, "
        "like a module that already holds AP credentials (VNSEMINI-0 PMU 301)",
    )
    parser.add_argument(
        "--reset-port",
        type=int,
        default=0,
        help="UDP port on 127.0.0.1 for the module reset line (run_firmware.py's GPIO hook)",
    )
    parser.add_argument(
        "--boot-seconds", type=float, default=1.0, help="Module boot time after a reset"
    )
    parser.add_argument(
        "--rejoin-seconds",
        type=float,
        default=4.0,
        help="Wi-Fi join and DHCP time after that boot",
    )
    parser.add_argument(
        "--no-cloud",
        action="store_true",
        help="Fail MQTT and HTTP like a battery with no internet access. Control 150 "
        "and newer then reset the module about every 30 minutes",
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
