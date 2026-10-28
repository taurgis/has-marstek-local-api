"""Stand-in Marstek cloud behind the emulated FC41D's MQTT and HTTP commands.

A real battery reaches the Marstek cloud through the FC41D's own MQTT and HTTP
clients (``AT+QMT*`` and ``AT+QHTTP*``). With no cloud, Control 150 and newer
reset the network chip about every 30 minutes because their uploads never
leave (see ``tools/firmware/ANALYSIS.md``, "Network chip resets"). This module
answers those commands the way the FC41D does when the cloud is reachable, so
an emulated battery behaves like one with working internet:

- MQTT opens, connects, subscribes and publishes succeed. Nothing is ever
  received on the subscribed topics.
- ``getDateInfo`` returns the host's local time in the firmware's
  ``_YYYY_MM_DD_HH_MM_SS`` format, so the RTC is set as on a real unit.
- Data uploads (``AT+QHTTPPOST``) get ``{"code":0}``, which the firmware
  takes as stored and deletes the queued records.
- Every other HTTP request gets ``{}``.

Reply formats follow the Quectel FC41D AT Commands Manual
(https://developer.quectel.com/doc/shortrange/FC41D/en/Resources/AT-Firmware/AT-Commands-Manual.html).
The URC strings the firmware matches exactly (``+QMTOPEN: 0,0``,
``+QMTCONN: 0,0,0``, ``+QMTSUB: 0,1,0,0``...) come from Control 150's
handler around ``0x08010bbc``.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

_LOGGER = logging.getLogger("fc41d.cloud")

UPLOAD_REPLY = b'{"code":0}'
EMPTY_REPLY = b"{}"
_MQTT_CONNECTED = 3  # AT+QMTCONN? state: 1 initializing, 3 connected
_IDS = re.compile(r"=\s*(\d+)(?:\s*,\s*(\d+))?")
_URL = re.compile(r'AT\+QHTTPCFG="url","([^"]*)"', re.I)
_OPEN = re.compile(r'AT\+QMTOPEN=(\d+),"([^"]*)",(\d+)', re.I)
_SUB_QOS = re.compile(r",(\d+)\s*$")


class ModemLink(Protocol):
    """What the cloud needs from the modem: the UART and its link state."""

    linked: bool

    def reply(self, *lines: str) -> None: ...

    def urc(self, line: str) -> None: ...

    def send(self, data: bytes) -> None: ...

    def expect_payload(self, length: int, handler: Callable[[bytes], None]) -> None: ...


def date_info(now: time.struct_time) -> bytes:
    """The ``getDateInfo`` body: the firmware parses the digits after ``_``."""
    return time.strftime("_%Y_%m_%d_%H_%M_%S", now).encode()


@dataclass
class StandInCloud:
    """MQTT and HTTP state of one FC41D, answered as if the cloud were up."""

    mqtt_open: dict[int, tuple[str, int]] = field(default_factory=dict)
    mqtt_connected: set[int] = field(default_factory=set)
    url: str = ""
    body: bytes = b""
    uploads: int = 0
    clock: Callable[[], time.struct_time] = time.localtime

    def reset(self) -> None:
        """A module reset drops every MQTT session and the HTTP context."""
        self.mqtt_open.clear()
        self.mqtt_connected.clear()
        self.url = ""
        self.body = b""

    def handle(self, cmd: str, modem: ModemLink) -> bool:
        """Answer an MQTT or HTTP command. False means it is not one."""
        up = cmd.upper()
        if up.startswith("AT+QMT"):
            self._mqtt(cmd, up, modem)
            return True
        if up.startswith("AT+QHTTPCFG"):
            url = _URL.match(cmd)
            if url:
                self.url = url.group(1)
            modem.reply("OK")
            return True
        if up.startswith("AT+QHTTP"):
            self._http(up, modem)
            return True
        return False

    # ---- MQTT ---------------------------------------------------------
    def _mqtt(self, cmd: str, up: str, modem: ModemLink) -> None:
        ids = _IDS.search(cmd)
        client = int(ids.group(1)) if ids else 0
        msg_id = int(ids.group(2)) if ids and ids.group(2) else 0
        if up == "AT+QMTOPEN?":
            lines = [f'+QMTOPEN: {c},"{h}",{p}' for c, (h, p) in self.mqtt_open.items()]
            modem.reply(*lines, "OK")
        elif up == "AT+QMTCONN?":
            lines = [
                f"+QMTCONN: {c},{_MQTT_CONNECTED if c in self.mqtt_connected else 1}"
                for c in self.mqtt_open
            ]
            modem.reply(*lines, "OK")
        elif up.startswith("AT+QMTOPEN="):
            opened = _OPEN.match(cmd)
            modem.reply("OK")
            if not modem.linked or opened is None:
                modem.urc(f"+QMTOPEN: {client},-1")  # no network
                return
            self.mqtt_open[client] = (opened.group(2), int(opened.group(3)))
            modem.urc(f"+QMTOPEN: {client},0")
        elif up.startswith("AT+QMTCONN="):
            modem.reply("OK")
            if client not in self.mqtt_open:
                modem.urc(f"+QMTCONN: {client},1")  # sending failed
                return
            self.mqtt_connected.add(client)
            modem.urc(f"+QMTCONN: {client},0,0")
        elif up.startswith(("AT+QMTSUB=", "AT+QMTUNS=", "AT+QMTPUB=")):
            self._mqtt_message(up, cmd, client, msg_id, modem)
        elif up.startswith("AT+QMTDISC="):
            self.mqtt_connected.discard(client)
            modem.reply("OK")
            modem.urc(f"+QMTDISC: {client},0")
        elif up.startswith("AT+QMTCLOSE="):
            self.mqtt_connected.discard(client)
            self.mqtt_open.pop(client, None)
            modem.reply("OK")
            modem.urc(f"+QMTCLOSE: {client},0")
        else:
            modem.reply("OK")  # AT+QMTCFG: settings the stand-in ignores

    def _mqtt_message(self, up: str, cmd: str, client: int, msg_id: int, modem: ModemLink) -> None:
        if client not in self.mqtt_connected:
            modem.reply("ERROR")
            return
        modem.reply("OK")
        if up.startswith("AT+QMTSUB="):
            qos = _SUB_QOS.search(cmd)
            modem.urc(f"+QMTSUB: {client},{msg_id},0,{qos.group(1) if qos else 0}")
        elif up.startswith("AT+QMTUNS="):
            modem.urc(f"+QMTUNS: {client},{msg_id},0")
        else:
            modem.urc(f"+QMTPUB: {client},{msg_id},0")

    # ---- HTTP ---------------------------------------------------------
    def _http(self, up: str, modem: ModemLink) -> None:
        if not modem.linked:
            modem.reply("ERROR")
            return
        if up.startswith("AT+QHTTPGET"):
            self.body = (
                date_info(self.clock()) if "GETDATEINFO" in self.url.upper() else EMPTY_REPLY
            )
            _LOGGER.info("GET %s -> %r", self.url, self.body)
            modem.reply("OK")
            modem.urc(f"+QHTTPGET: 0,200,{len(self.body)}")
        elif up.startswith("AT+QHTTPPOST="):
            ids = _IDS.search(up)
            length = int(ids.group(1)) if ids else 0

            def posted(data: bytes) -> None:
                self.uploads += 1
                self.body = UPLOAD_REPLY
                _LOGGER.info("POST %s (%d bytes, #%d)", self.url, len(data), self.uploads)
                modem.reply("OK")
                modem.urc(f"+QHTTPPOST: 0,200,{len(self.body)}")

            modem.send(b"\r\nCONNECT\r\n")
            modem.expect_payload(length, posted)
        elif up.startswith("AT+QHTTPREAD"):
            modem.send(b"\r\nCONNECT\r\n" + self.body + b"\r\nOK\r\n\r\n+QHTTPREAD: 0\r\n")
        else:
            modem.reply("OK")  # AT+QHTTPSTOP and the like
