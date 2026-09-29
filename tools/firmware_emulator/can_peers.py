"""Play the BMS and the inverter on the emulated VNSE3-0 firmware's CAN bus.

Connects to ``renode/CanBridge.cs`` (one frame per line, ``"<id hex> <data hex>"``)
and answers with the frames the firmware reads its battery and AC figures from,
so ``ES.GetStatus`` / ``Bat.GetStatus`` report a battery that charges and
discharges instead of zeros.

The layout comes from the firmware's CAN dispatcher (build 150):

* **BMS** frames carry ``(PGN << 16) | 0xAA00 | src``. Their payloads are copied raw
  into one struct:

  * ``0x1801``: volt (10 mV), curr (100 mA, + = charge), temp (0.1 C), SoC (0.1 %)
  * ``0x1802``: rated energy (Wh), battery count, online mask, working index,
    DoD, sleep flag
  * ``0x1803``: charge volt (0.1 V), charge and discharge current limits
    (100 mA), permission bits (bit 0 charge, bit 1 discharge). The firmware caps
    its power command at volt x limit, and the CT phase test needs the bit for
    the direction it tries (discharge above 50 % SoC, charge below).
  * ``0x1804``: error and warning words (all clear here)

* **Inverter** frames are ``page << 24 | src_type << 20 | 1 << 16 | dst_type << 12
  | addr << 8 | cmd``, with the inverter as type 4 and the MCU as type 0.
  Replies to the MCU use ``cmd 0x10``. Page 4 holds the AC port power
  (s16 ``ongrid_power`` W, + = export; u16 ``offgrid_power``). Page 5 bytes 4-7
  are the AC limits the Auto loop clamps to (s16 max discharge, s16 max charge,
  charge negative). Page 6 holds the lifetime grid energy (u32 input Wh, u32
  output Wh).
* The MCU commands the inverter with ``cmd 0x01`` to type 4: a signed 32-bit AC
  setpoint in W (+ = discharge). The inverter here ramps towards it and the
  battery follows. ``cmd 0x16`` restores the lifetime grid energy the MCU keeps
  in EEPROM, in the page 6 layout.

It also serves the grid meter reading for AstraMeter's ``[JSON_HTTP]`` source
(``--meter-port``): per-phase house load minus the inverter output on phase A.
Auto mode then closes its loop through the real CT003 protocol, and the
firmware's CT phase test sees its own test power on the meter.
"""

from __future__ import annotations

import argparse
import json
import select
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BMS_SRC = 0x01
INVERTER = 4
TICK = 1.0
RAMP = 0.5  # fraction of the setpoint error closed per tick
EFFICIENCY = 0.95
MAX_DISCHARGE_W = 800  # Venus E 3.0 AC ratings
MAX_CHARGE_W = 2500


class Battery:
    """Single pack, energy-counted SoC."""

    def __init__(self, capacity_wh: int, soc: float) -> None:
        self.capacity_wh = capacity_wh
        self.energy_wh = capacity_wh * soc / 100
        self.temp_c = 25.0
        self.dod = 88

    @property
    def soc(self) -> float:
        return 100 * self.energy_wh / self.capacity_wh

    @property
    def volt(self) -> float:
        return 48.0 + 0.08 * self.soc

    def frames(self, dc_power: float) -> list[tuple[int, bytes]]:
        curr = dc_power / self.volt  # + = charge
        limit_a = 50

        def bms(pgn: int, payload: bytes) -> tuple[int, bytes]:
            return (pgn << 16) | 0xAA00 | BMS_SRC, payload

        return [
            bms(
                0x1801,
                struct.pack(
                    "<HhhH",
                    round(self.volt * 100),
                    round(curr * 10),
                    round(self.temp_c * 10),
                    round(self.soc * 10),
                ),
            ),
            bms(0x1802, struct.pack("<HBHBBB", self.capacity_wh, 1, 1, 0, self.dod, 0)),
            bms(0x1803, struct.pack("<HHHBB", 576, limit_a * 10, limit_a * 10, 0b11, 0)),
            bms(0x1804, bytes(8)),
        ]


class Inverter:
    """AC side: follows the MCU's setpoint and counts grid energy."""

    def __init__(self) -> None:
        self.setpoint = 0
        self.ac_power = 0.0  # + = export (discharge)
        self.input_wh = 0.0
        self.output_wh = 0.0

    def step(self, battery: Battery, dt: float) -> float:
        """Advance ``dt`` seconds; returns battery DC power (+ = charge)."""
        self.ac_power += (self.setpoint - self.ac_power) * RAMP
        if abs(self.ac_power) < 1:
            self.ac_power = 0.0
        if self.ac_power > 0 and battery.soc <= 100 - battery.dod:
            self.ac_power = 0.0
        if self.ac_power < 0 and battery.soc >= 100:
            self.ac_power = 0.0
        hours = dt / 3600
        if self.ac_power > 0:
            dc = -self.ac_power / EFFICIENCY
            self.output_wh += self.ac_power * hours
        else:
            dc = -self.ac_power * EFFICIENCY
            self.input_wh += -self.ac_power * hours
        battery.energy_wh = min(max(battery.energy_wh + dc * hours, 0), battery.capacity_wh)
        return dc

    def frames(self) -> list[tuple[int, bytes]]:
        def page(n: int, payload: bytes) -> tuple[int, bytes]:
            return (n << 24) | (INVERTER << 20) | (1 << 16) | (1 << 8) | 0x10, payload

        return [
            page(4, struct.pack("<hHhH", round(self.ac_power), 0, 0, 0)),
            page(5, struct.pack("<hhhh", 0, 0, MAX_DISCHARGE_W, -MAX_CHARGE_W)),
            page(6, struct.pack("<II", round(self.input_wh), round(self.output_wh))),
        ]

    def on_frame(self, can_id: int, data: bytes) -> bool:
        """Apply an MCU command; True when it was the power setpoint."""
        if (can_id >> 12) & 0xF != INVERTER:
            return False
        cmd = can_id & 0xFF
        if cmd == 0x16 and len(data) == 8:
            self.input_wh, self.output_wh = (float(v) for v in struct.unpack("<II", data))
        if cmd != 0x01 or len(data) < 4:
            return False
        (self.setpoint,) = struct.unpack_from("<i", data)
        return True


def serve_meter(port: int, load: list[float], inverter: Inverter) -> None:
    """Serve ``{"a": W, "b": W, "c": W}`` grid power (+ = import) over HTTP."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            phases = [load[0] - inverter.ac_power, load[1], load[2]]
            body = json.dumps(dict(zip("abc", (round(p) for p in phases), strict=True))).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=3457, help="CanBridge TCP port")
    parser.add_argument("--soc", type=float, default=50.0)
    parser.add_argument("--capacity", type=int, default=5120, help="Rated energy in Wh")
    parser.add_argument("--meter-port", type=int, default=8099, help="HTTP port for AstraMeter")
    parser.add_argument(
        "--house-load", default="300,150,150", help="Per-phase house load in W (A,B,C)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Log every changed MCU frame")
    args = parser.parse_args()

    battery = Battery(args.capacity, args.soc)
    inverter = Inverter()
    load = [float(v) for v in args.house_load.split(",")]
    if len(load) != 3:
        parser.error("--house-load needs three values")
    serve_meter(args.meter_port, load, inverter)
    deadline = time.monotonic() + 60
    while True:
        try:
            sock = socket.create_connection(("127.0.0.1", args.port))
            break
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)
    print(f"Connected to CAN bridge on {args.port}", flush=True)

    buf = b""
    seen: dict[int, bytes] = {}
    last = time.monotonic() - TICK
    dc = 0.0
    while True:
        now = time.monotonic()
        if now - last >= TICK:
            dc = inverter.step(battery, now - last)
            last = now
            out = battery.frames(dc) + inverter.frames()
            sock.sendall(b"".join(b"%08x %s\n" % (i, d.hex().encode()) for i, d in out))
        ready, _, _ = select.select([sock], [], [], max(0.0, last + TICK - time.monotonic()))
        if not ready:
            continue
        chunk = sock.recv(65536)
        if not chunk:
            return 1
        buf += chunk
        *lines, buf = buf.split(b"\n")
        for line in lines:
            parts = line.split()
            can_id = int(parts[0], 16)
            data = bytes.fromhex(parts[1].decode()) if len(parts) > 1 else b""
            if inverter.on_frame(can_id, data) and seen.get(can_id) != data:
                print(f"setpoint {inverter.setpoint} W (SoC {battery.soc:.1f} %)", flush=True)
            elif args.verbose and seen.get(can_id) != data:
                print(f"mcu {can_id:08x} {data.hex()}", flush=True)
            seen[can_id] = data


if __name__ == "__main__":
    raise SystemExit(main())
