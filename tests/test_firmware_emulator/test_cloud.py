"""The stand-in Marstek cloud behind fc41d.py's MQTT and HTTP commands."""

from __future__ import annotations

import sys
import time
from pathlib import Path

_EMU = Path(__file__).resolve().parents[2] / "tools" / "firmware_emulator"
if str(_EMU) not in sys.path:
    sys.path.insert(0, str(_EMU))

import fc41d  # noqa: E402

_NOW = time.struct_time((2026, 3, 7, 9, 5, 2, 5, 66, 0))
_DATE_URL = 'AT+QHTTPCFG="url","http://eu.hamedata.com/app/neng/getDateInfoeu.php?uid=x"'


class _Writer:
    def __init__(self) -> None:
        self.data = b""

    def write(self, data: bytes) -> None:
        self.data += data


def _modem(*, cloud: bool = True) -> tuple[fc41d.FC41D, _Writer]:
    writer = _Writer()
    modem = fc41d.FC41D(config=fc41d.ModemConfig(ip="172.28.0.50", cloud=cloud))
    modem.writer = writer  # type: ignore[assignment]
    modem.cloud.clock = lambda: _NOW
    return modem, writer


def _mqtt_up(modem: fc41d.FC41D) -> None:
    modem.handle('AT+QMTOPEN=0,"broker.example",8883')
    modem.handle('AT+QMTCONN=0,"mst_abc"')


def test_mqtt_answers_with_the_urcs_the_firmware_matches() -> None:
    """Control 150 compares these URCs byte for byte."""
    modem, writer = _modem()
    _mqtt_up(modem)
    modem.handle('AT+QMTSUB=0,1,"marstek_energy/x/App/y/ctrl",0')
    modem.handle('AT+QMTPUB=0,0,0,0,"marstek_energy/x/device/y/ctrl",2,"{}"')
    for urc in (b"+QMTOPEN: 0,0", b"+QMTCONN: 0,0,0", b"+QMTSUB: 0,1,0,0", b"+QMTPUB: 0,0,0"):
        assert urc in writer.data
    modem.handle("AT+QMTCONN?")
    modem.handle("AT+QMTOPEN?")
    assert b"+QMTCONN: 0,3" in writer.data
    assert b'+QMTOPEN: 0,"broker.example",8883' in writer.data


def test_mqtt_close_and_unsubscribe() -> None:
    modem, writer = _modem()
    _mqtt_up(modem)
    modem.handle('AT+QMTUNS=0,2,"t"')
    modem.handle("AT+QMTDISC=0")
    modem.handle("AT+QMTCLOSE=0")
    assert b"+QMTUNS: 0,2,0" in writer.data
    assert b"+QMTDISC: 0,0" in writer.data
    assert b"+QMTCLOSE: 0,0" in writer.data
    writer.data = b""
    modem.handle("AT+QMTCONN?")
    assert writer.data == b"\r\nOK\r\n"


def test_mqtt_fails_without_a_session() -> None:
    """Publishing before connecting, connecting before opening, opening unlinked."""
    modem, writer = _modem()
    modem.handle('AT+QMTPUB=0,0,0,0,"t",2,"{}"')
    modem.handle('AT+QMTCONN=0,"mst_abc"')
    modem.handle('AT+QMTCFG="keepalive",0,120')
    modem.linked = False
    modem.handle('AT+QMTOPEN=0,"broker.example",8883')
    assert b"ERROR" in writer.data
    assert b"+QMTCONN: 0,1" in writer.data
    assert b"+QMTOPEN: 0,-1" in writer.data


def test_date_info_sets_the_clock_format() -> None:
    """The firmware parses the digits after the first underscore."""
    modem, writer = _modem()
    modem.handle(_DATE_URL)
    modem.handle("AT+QHTTPGET=60")
    modem.handle("AT+QHTTPREAD=60")
    assert b"+QHTTPGET: 0,200,20" in writer.data
    assert b"CONNECT\r\n_2026_03_07_09_05_02\r\nOK\r\n\r\n+QHTTPREAD: 0" in writer.data


def test_other_get_returns_an_empty_object() -> None:
    modem, writer = _modem()
    modem.handle('AT+QHTTPCFG="url","http://example/backup-configs"')
    modem.handle("AT+QHTTPGET=60")
    modem.handle("AT+QHTTPREAD=60")
    assert b"CONNECT\r\n{}\r\nOK" in writer.data


async def test_upload_post_reads_the_body_then_reports_success() -> None:
    modem, writer = _modem()
    modem.handle('AT+QHTTPCFG="header","Content-Type","application/json"')
    modem.handle("AT+QHTTPPOST=5,60,60")
    assert writer.data.endswith(b"CONNECT\r\n")
    assert await modem._consume(b"hello AT\r") == b""
    modem.handle("AT+QHTTPREAD=60")
    modem.handle("AT+QHTTPSTOP")
    assert b"+QHTTPPOST: 0,200,10" in writer.data
    assert b'CONNECT\r\n{"code":0}\r\nOK' in writer.data
    assert modem.cloud.uploads == 1


def test_http_fails_while_the_module_has_no_address() -> None:
    modem, writer = _modem()
    modem.linked = False
    modem.handle("AT+QHTTPGET=60")
    assert writer.data == b"\r\nERROR\r\n"


def test_no_cloud_keeps_failing_fast() -> None:
    modem, writer = _modem(cloud=False)
    modem.handle('AT+QMTOPEN=0,"broker.example",8883')
    modem.handle("AT+QHTTPGET=60")
    assert writer.data == b"\r\nERROR\r\n\r\nERROR\r\n"


def test_reset_drops_the_cloud_session() -> None:
    modem, _writer = _modem()
    _mqtt_up(modem)
    modem.handle(_DATE_URL)
    modem.reset_line(0)
    assert not modem.cloud.mqtt_open
    assert not modem.cloud.mqtt_connected
    assert modem.cloud.url == ""
