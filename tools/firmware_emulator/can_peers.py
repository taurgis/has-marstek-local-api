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
  ``capm`` month and u32 ``capy`` year at page 6). ``ES.GetStatus`` reports
  ``capy`` unscaled as ``total_pv_energy`` on VNSA-0 / VNSD-0 149 and later
  (and Venus A 1487); Venus A 148 hard-codes 0. ``--pv-state FILE`` keeps the
  counters across restarts. The MCU switches the MPPT with ``cmd 0x01`` to type 2, addr 1
  (byte 4: 1 on, 0 off); it sends off only after an hour without PV voltage.
* The MCU commands the inverter with ``cmd 0x01`` to type 4: a signed 32-bit AC
  setpoint in W (+ = discharge). The inverter here ramps towards it and the
  battery follows. ``cmd 0x16`` restores the lifetime grid energy the MCU keeps
  in EEPROM, in the page 6 layout.
* ``--family VNSD-0`` (Venus D) adds what that inverter reports besides power:
  page 1 (``inv_state``, ``buz_state``, ``chrg_flag``, ``back_func``, u32 warning),
  page 3 (u16 grid volt 0.1 V, grid frequency 0.1 Hz, off-grid volt 0.1 V,
  ``grid_permit``) and the page 5 temperatures (s16 ambient and heat sink,
  0.1 C). The firmware only drives LEDs, telemetry and its idle-sleep timer
  from them, but a real unit on the grid never reports 0 V / 0 Hz. ``cmd 0x02``
  (1 byte) switches the backup (EPS) function; the MCU resends it until page 1
  ``back_func`` matches, so the inverter echoes it.
* ``--family`` also picks the ratings (see ``FAMILIES``): Venus D 2560 Wh per
  pack (``--packs`` fills the 0x1802 count and online mask) and 2500 W both
  ways, the ceiling ``Set.Ver`` accepts on VNSD-0; Venus A 2080 Wh and 1500 W.

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
# Per-family ratings and extras, keyed by the catalog ``deviceType``. Families
# not listed keep the Venus E 3.0 defaults (5120 Wh, 800,2500 W, power pages only).
# Each entry is ``(Wh per pack, "max discharge,max charge" W, grid pages)``.
DEFAULT_FAMILY = (5120, "800,2500", False)
FAMILIES: dict[str, tuple[int, str, bool]] = {
    # Venus A: 2.08 kWh pack, 1500 W AC both ways.
    "VNSA-0": (2080, "1500,1500", False),
    # Venus D: 2.56 kWh LFP packs, 2500 W AC both ways (Set.Ver 2500).
    "VNSD-0": (2560, "2500,2500", True),
}


class Battery:
    """Energy-counted SoC; ``packs`` modules reported as one aggregate BMS."""

    def __init__(self, capacity_wh: int, soc: float, packs: int = 1) -> None:
        self.capacity_wh = capacity_wh
        self.packs = packs
        self.energy_wh = capacity_wh * soc / 100
        self.temp_c = 25.0
        self.dod = 88

    @property
    def soc(self) -> float:
        return 100 * self.energy_wh / self.capacity_wh

    @property
    def permits(self) -> int:
        """0x1803 byte 6: bit 0 charge allowed, bit 1 discharge allowed.

        A BMS withdraws charge at full and discharge at its DoD floor. The
        firmware's own loop honours each bit for its direction. Its
        ``Bat.GetStatus``, though, copies the whole byte into both
        ``charg_flag`` and ``dischrg_flag`` (build 150: 0x801fff4), so both
        read true until both bits are clear, as on a real device.
        """
        charge = 0b01 if self.soc < 100 else 0
        discharge = 0b10 if self.soc > 100 - self.dod else 0
        return charge | discharge

    @property
    def volt(self) -> float:
        return 48.0 + 0.08 * self.soc

    def frames(self, dc_power: float) -> list[tuple[int, bytes]]:
        curr = dc_power / self.volt  # + = charge
        limit_a = 50 * self.packs

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
            bms(
                0x1802,
                struct.pack(
                    "<HBHBBB", self.capacity_wh, self.packs, (1 << self.packs) - 1, 0, self.dod, 0
                ),
            ),
            bms(0x1803, struct.pack("<HHHBB", 576, limit_a * 10, limit_a * 10, self.permits, 0)),
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

    def __init__(
        self, max_discharge: int, max_charge: int, offgrid_load: float = 0.0, grid: bool = False
    ) -> None:
        self.max_discharge = max_discharge
        self.max_charge = max_charge
        self.grid = grid  # also send pages 1 and 3 and the page 5 temperatures
        self.back_func = 0  # backup (EPS) function, as commanded by cmd 0x02
        self.offgrid_load = offgrid_load  # EPS socket load, served from the battery
        self.offgrid_power = 0.0
        self.setpoint = 0
        self.ac_power = 0.0  # + = export (discharge)
        self.input_wh = 0.0
        self.output_wh = 0.0
        self.dod = 0  # DoD from the MCU's last cmd 0x0A (DOD.SET); 0 = keep the BMS's

    def step(self, battery: Battery, dt: float, pv: float = 0.0) -> float:
        """Advance ``dt`` seconds; returns battery DC power (+ = charge)."""
        if self.dod:
            battery.dod = self.dod
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

        temps = (300, 350) if self.grid else (0, 0)  # ambient, heat sink (0.1 C)
        out = [
            page(4, struct.pack("<hHhH", round(self.ac_power), round(self.offgrid_power), 0, 0)),
            page(5, struct.pack("<hhhh", *temps, self.max_discharge, -self.max_charge)),
            page(6, struct.pack("<II", round(self.input_wh), round(self.output_wh))),
        ]
        if self.grid:
            # On grid, 230.0 V / 50.0 Hz, no EPS output, grid tie permitted.
            out.append(page(1, struct.pack("<BBBBI", 0, 0, 0, self.back_func, 0)))
            out.append(page(3, struct.pack("<HHHH", 2300, 500, 0, 1)))
        return out

    def on_frame(self, can_id: int, data: bytes) -> bool:
        """Apply an MCU command; True when it was the power setpoint."""
        if (can_id >> 12) & 0xF != INVERTER:
            return False
        cmd = can_id & 0xFF
        if cmd == 0x16 and len(data) == 8:
            self.input_wh, self.output_wh = (float(v) for v in struct.unpack("<II", data))
        if cmd == 0x02 and data:
            self.back_func = 1 if data[0] else 0
        if cmd == 0x0A and data and 0 < data[0] < 100:
            # DOD.SET: the MCU stores 100 - DoD (the minimum SoC; 12 % for the
            # default 88) and forwards it in byte 0 (build 150: 0x800af3c,
            # 0x802f9f8 -> 0x800a33c). The discharge floor and the BMS's 0x1802
            # DoD byte follow it.
            self.dod = 100 - data[0]
        if cmd != 0x01 or len(data) < 4:
            return False
        (self.setpoint,) = struct.unpack_from("<i", data)
        return True


class PvEnergy:
    """The MPPT's own day / month / year PV energy counters, in Wh."""

    def __init__(self, path: str | None, year_kwh: float = 0.0) -> None:
        self.path = path
        self.period = self._period()
        self.day = self.month = 0.0
        self.year = year_kwh * 1000
        self._saved = 0.0
        if path:
            try:
                with open(path, encoding="utf-8") as fh:
                    saved = json.load(fh)
                if saved.get("period") == list(self.period):
                    self.day, self.month, self.year = saved["day"], saved["month"], saved["year"]
                elif saved.get("period", [None])[0] == self.period[0]:
                    self.year = saved["year"]
            except (OSError, ValueError, KeyError, TypeError):
                pass

    @staticmethod
    def _period() -> tuple[int, int, int]:
        now = time.localtime()
        return now.tm_year, now.tm_mon, now.tm_mday

    def add(self, watts: float, dt: float) -> None:
        period = self._period()
        if period[0] != self.period[0]:
            self.year = 0.0
        if period[:2] != self.period[:2]:
            self.month = 0.0
        if period != self.period:
            self.day = 0.0
        self.period = period
        wh = watts * dt / 3600
        self.day += wh
        self.month += wh
        self.year += wh
        if self.path and self.year - self._saved >= 10:
            self.save()

    def save(self) -> None:
        if not self.path:
            return
        state = {"period": list(self.period), "day": self.day, "month": self.month}
        state["year"] = self.year
        try:
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
            self._saved = self.year
        except OSError:
            pass


class Mppt:
    """PV charger: on/off state from the MCU and its 0.01 kWh yield counters."""

    def __init__(self, energy: PvEnergy) -> None:
        self.on = True
        self.energy = energy

    def harvest(self, channels: list[float], dt: float) -> list[float]:
        """Channel power actually delivered for ``dt`` seconds, counted as yield."""
        out = channels if self.on else [0.0] * len(channels)
        self.energy.add(sum(out), dt)
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

        e = self.energy
        day, month, year = (int(v // 10) for v in (e.day, e.month, e.year))  # 0.01 kWh
        return [
            *pv_frames(channels),
            page(5, struct.pack("<IBBH", day, 0, 0, 0)),
            page(6, struct.pack("<II", month, year)),
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
    parser.add_argument(
        "--family", default="", help="Catalog deviceType (e.g. VNSD-0) for ratings and extras"
    )
    parser.add_argument(
        "--capacity", type=int, help="Rated energy in Wh (default per family and --packs)"
    )
    parser.add_argument("--packs", type=int, default=1, help="Battery modules (1-8)")
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
        "--ac-limits", help="Max AC discharge,charge in W (default per family, E 3.0 800,2500)"
    )
    parser.add_argument("--pv", default="", help="PV channel power in W, e.g. 400,350 (Venus A/D)")
    parser.add_argument(
        "--pv-energy", type=float, default=0.0, help="Initial MPPT year yield in kWh"
    )
    parser.add_argument("--pv-state", help="JSON file that keeps the MPPT energy counters")
    parser.add_argument(
        "--offgrid-load", type=float, default=0.0, help="EPS socket load in W (Venus A)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Log every changed MCU frame")
    args = parser.parse_args()

    pylontech = args.protocol == "pylontech"
    pack_wh, default_limits, grid = FAMILIES.get(args.family, DEFAULT_FAMILY)
    if not 1 <= args.packs <= 8:
        parser.error("--packs takes 1-8 (the firmware caps the 0x1802 count at 8)")
    capacity = args.capacity or pack_wh * args.packs
    battery = (PylontechBattery if pylontech else Battery)(capacity, args.soc, args.packs)
    ac_limits = args.ac_limits or default_limits
    max_discharge, max_charge = (int(v) for v in ac_limits.split(","))
    inverter = Inverter(max_discharge, max_charge, args.offgrid_load, grid=grid)
    mppt = Mppt(PvEnergy(args.pv_state, args.pv_energy))
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
