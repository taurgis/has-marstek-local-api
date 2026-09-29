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
from dataclasses import dataclass, field

_LOGGER = logging.getLogger("fc41d")

CRLF = b"\r\n"
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


@dataclass
class UdpService:
    """One ``AT+QIOPEN=<id>,"UDP SERVICE",...`` socket."""

    conn_id: int
    local_port: int
    transport: asyncio.DatagramTransport | None = None
    last_peer: tuple[str, int] | None = None


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
    pending_payload: tuple[int, object] | None = None  # (length, handler)

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
                handler(payload)  # type: ignore[operator]
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
        elif up == "AT+QBLESTAT":
            self.reply("+QBLESTAT:ADVERTISING", "OK")
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
    )
    while True:
        try:
            reader, writer = await asyncio.open_connection(args.uart_host, args.uart_port)
        except OSError:
            await asyncio.sleep(0.5)
            continue
        _LOGGER.info("connected to firmware UART at %s:%d", args.uart_host, args.uart_port)
        modem = FC41D(config, writer)
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
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
