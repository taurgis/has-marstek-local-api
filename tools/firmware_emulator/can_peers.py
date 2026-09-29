"""Play the BMS, inverter and PV MPPT on the emulated Control firmware's CAN bus.

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
  (s16 ``ongrid_power`` W, + = export; u16 ``offgrid_power``, the Venus A
  EPS socket, see ``--offgrid-load``). Page 5 bytes 4-7
  are the AC limits the Auto loop clamps to (s16 max discharge, s16 max charge,
  charge negative). Page 6 holds the lifetime grid energy (u32 input Wh, u32
  output Wh).
* **PV MPPT** frames (Venus A/D) use the same layout with ``src_type`` 2 and
  ``cmd 0x03``. Pages 2-4 hold four channels as u16 volt (0.1 V), current
  (0.1 A) and power (0.1 W), in that order: PV1 V/I/P and PV2 V, then PV2 I/P
  and PV3 V/I, then PV3 P and PV4 V/I/P. ``PV.GetStatus`` drops a channel below
  10 W and reports ``pv*_state`` from volt > 14 V. Pages 5-6 carry the MPPT's
  own yield counters in 0.01 kWh (u32 ``capd`` day at page 5 bytes 0-3, u32
  ``capm`` month and u32 ``capy`` at page 6). ``ES.GetStatus`` reports ``capy``
  unscaled as ``total_pv_energy`` on every Venus A build except 148, which
  hard-codes 0. The MCU switches the MPPT with ``cmd 0x01`` to type 2, addr 1
  (byte 4: 1 on, 0 off); it sends off only after an hour without PV voltage.
* The MCU commands the inverter with ``cmd 0x01`` to type 4: a signed 32-bit AC
  setpoint in W (+ = discharge). The inverter here ramps towards it and the
  battery follows. ``cmd 0x16`` restores the lifetime grid energy the MCU keeps
  in EEPROM, in the page 6 layout.

HMG-50 (Venus C / Venus E 2.0, ``--protocol pylontech``) runs its own inverter
stage and talks to the pack over 11-bit standard frames in the Pylontech /
SMA layout instead. Its dispatcher (156: ``0x080056f8``, reached from the FDCAN
FIFO0 callback for standard ids) copies these payloads raw:

* ``0x351``: charge volt (0.1 V), charge and discharge current limits (0.1 A)
* ``0x355``: SoC (%), SoH (%), rated energy (Wh). The MCU names itself from
  the energy: 1-3000 Wh is ``HMG-25`` (``VenusC``), above 3000 ``HMG-50``
  (``VenusE``), and it stores that type in EEPROM ``0x3000``
* ``0x356``: volt (10 mV), curr (100 mA, + = charge), temp (0.1 C)
* ``0x359``: protection and alarm words, module count, ``"PN"``
* ``0x35C``: request flags (bit 7 charge, bit 6 discharge enable); the MCU
  timestamps it and treats the pack as gone after 20 s without one
* ``0x35E``: manufacturer name

The MCU sends an empty ``0x305`` keep-alive. It never commands a setpoint on
this bus, so the pack here sits at the given SoC with no current.

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
MPPT = 2
MPPT_ADDR = 1
PV_VOLT = 40.0


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


class PylontechBattery(Battery):
    """HMG-50 pack: Pylontech-layout standard frames, 16s LFP."""

    @property
    def volt(self) -> float:
        return 49.6 + 0.05 * self.soc

    def frames(self, dc_power: float) -> list[tuple[int, bytes]]:
        curr = dc_power / self.volt  # + = charge
        soc = self.soc
        return [
            (0x351, struct.pack("<HhhH", 568, 500, 500, 448)),
            (0x355, struct.pack("<HHHH", round(soc), 100, self.capacity_wh, 0)),
            (
                0x356,
                struct.pack(
                    "<hhhH", round(self.volt * 100), round(curr * 10), round(self.temp_c * 10), 0
                ),
            ),
            (0x359, bytes([0, 0, 0, 0, 1, ord("P"), ord("N"), 0])),
            (0x35C, bytes([0xC0, 0, 0, 0])),
            (0x35E, b"PYLON   "),
        ]


class Inverter:
    """AC side: follows the MCU's setpoint and counts grid energy."""

    def __init__(self, max_discharge: int, max_charge: int, offgrid_load: float = 0.0) -> None:
        self.max_discharge = max_discharge
        self.max_charge = max_charge
        self.offgrid_load = offgrid_load  # EPS socket load, served from the battery
        self.offgrid_power = 0.0
        self.setpoint = 0
        self.ac_power = 0.0  # + = export (discharge)
        self.input_wh = 0.0
        self.output_wh = 0.0

    def step(self, battery: Battery, dt: float, pv: float = 0.0) -> float:
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
        # The EPS socket runs off the battery whatever the grid setpoint says,
        # until the pack reaches the DoD floor.
        floor = battery.soc <= 100 - battery.dod
        self.offgrid_power = 0.0 if floor else self.offgrid_load
        dc += pv - self.offgrid_power / EFFICIENCY
        battery.energy_wh = min(max(battery.energy_wh + dc * hours, 0), battery.capacity_wh)
        return dc

    def frames(self) -> list[tuple[int, bytes]]:
        def page(n: int, payload: bytes) -> tuple[int, bytes]:
            return (n << 24) | (INVERTER << 20) | (1 << 16) | (1 << 8) | 0x10, payload

        return [
            page(4, struct.pack("<hHhH", round(self.ac_power), round(self.offgrid_power), 0, 0)),
            page(5, struct.pack("<hhhh", 0, 0, self.max_discharge, -self.max_charge)),
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


class Mppt:
    """PV charger: on/off state from the MCU and 0.01 kWh yield counters."""

    def __init__(self, total_kwh: float = 0.0) -> None:
        self.on = True
        self.day_wh = 0.0
        self.total_wh = total_kwh * 1000

    def harvest(self, channels: list[float], dt: float) -> list[float]:
        """Channel power actually delivered for ``dt`` seconds, counted as yield."""
        out = channels if self.on else [0.0] * len(channels)
        wh = sum(out) * dt / 3600
        self.day_wh += wh
        self.total_wh += wh
        return out

    def on_frame(self, can_id: int, data: bytes) -> bool:
        """Apply the MCU's on/off command; True when it changed the state."""
        dst, addr, cmd = (can_id >> 12) & 0xF, (can_id >> 8) & 0xF, can_id & 0xFF
        if dst != MPPT or addr != MPPT_ADDR or cmd != 0x01 or len(data) < 5:
            return False
        on = bool(data[4])
        changed, self.on = on != self.on, on
        return changed

    def frames(self, channels: list[float]) -> list[tuple[int, bytes]]:
        """Pages 1-6; page 7 (the MPPT's battery-side readings) is never reported."""

        def page(n: int, payload: bytes) -> tuple[int, bytes]:
            return (n << 24) | (MPPT << 20) | (1 << 16) | (MPPT_ADDR << 8) | 0x03, payload

        # capd/capm/capy in 0.01 kWh; month and year counters start with the day.
        day, total = round(self.day_wh / 10), round(self.total_wh / 10)
        return [
            *pv_frames(channels),
            page(5, struct.pack("<IBBH", day, 0, 0, 0)),
            page(6, struct.pack("<II", day, total)),
        ]


def pv_frames(channels: list[float]) -> list[tuple[int, bytes]]:
    """MPPT pages 1-4 for up to four channels of ``channels`` W."""
    words: list[int] = []
    for n in range(4):
        power = channels[n] if n < len(channels) else 0.0
        volt = PV_VOLT if power > 0 else 0.0
        words += [round(volt * 10), round(power / volt * 10) if volt else 0, round(power * 10)]

    def page(n: int, values: list[int]) -> tuple[int, bytes]:
        return (n << 24) | (MPPT << 20) | (1 << 16) | (1 << 8) | 0x03, struct.pack("<4H", *values)

    return [page(1, [0] * 4)] + [page(n + 2, words[4 * n : 4 * n + 4]) for n in range(3)]


def serve_meter(port: int, load: list[float], inverter: Inverter, bind: str = "127.0.0.1") -> None:
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

    server = ThreadingHTTPServer((bind, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=3457, help="CanBridge TCP port")
    parser.add_argument("--soc", type=float, default=50.0)
    parser.add_argument("--capacity", type=int, default=5120, help="Rated energy in Wh")
    parser.add_argument("--meter-port", type=int, default=8099, help="HTTP port for AstraMeter")
    parser.add_argument("--meter-bind", default="127.0.0.1", help="Address the meter HTTP binds")
    parser.add_argument(
        "--protocol",
        choices=("control", "pylontech"),
        default="control",
        help="BMS bus layout: Control (Venus A/D/E 3.0) or Pylontech (HMG-50)",
    )
    parser.add_argument(
        "--house-load", default="300,150,150", help="Per-phase house load in W (A,B,C)"
    )
    parser.add_argument(
        "--ac-limits", default="800,2500", help="Max AC discharge,charge in W (Venus E 3.0)"
    )
    parser.add_argument("--pv", default="", help="PV channel power in W, e.g. 400,350 (Venus A/D)")
    parser.add_argument(
        "--pv-energy", type=float, default=0.0, help="Initial MPPT yield in kWh (total_pv_energy)"
    )
    parser.add_argument(
        "--offgrid-load", type=float, default=0.0, help="EPS socket load in W (Venus A)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Log every changed MCU frame")
    args = parser.parse_args()

    pylontech = args.protocol == "pylontech"
    battery = (PylontechBattery if pylontech else Battery)(args.capacity, args.soc)
    max_discharge, max_charge = (int(v) for v in args.ac_limits.split(","))
    inverter = Inverter(max_discharge, max_charge, args.offgrid_load)
    mppt = Mppt(args.pv_energy)
    pv = [float(v) for v in args.pv.split(",") if v]
    if len(pv) > 4:
        parser.error("--pv takes at most four channels")
    load = [float(v) for v in args.house_load.split(",")]
    if len(load) != 3:
        parser.error("--house-load needs three values")
    serve_meter(args.meter_port, load, inverter, args.meter_bind)
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
            full = battery.energy_wh >= battery.capacity_wh
            harvest = [0.0 if full else p for p in pv]
            if not pylontech:
                harvest = mppt.harvest(harvest, now - last)
                dc = inverter.step(battery, now - last, sum(harvest))
            last = now
            out = battery.frames(dc)
            if not pylontech:
                out += inverter.frames()
            if pv and not pylontech:
                out += mppt.frames(harvest)
            width = 3 if pylontech else 8
            sock.sendall(b"".join(b"%0*x %s\n" % (width, i, d.hex().encode()) for i, d in out))
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
            if mppt.on_frame(can_id, data):
                print(f"MPPT switched {'on' if mppt.on else 'off'}", flush=True)
            if inverter.on_frame(can_id, data) and seen.get(can_id) != data:
                print(f"setpoint {inverter.setpoint} W (SoC {battery.soc:.1f} %)", flush=True)
            elif args.verbose and seen.get(can_id) != data:
                print(f"mcu {can_id:08x} {data.hex()}", flush=True)
            seen[can_id] = data


if __name__ == "__main__":
    raise SystemExit(main())
