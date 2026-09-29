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
  (s16 ``ongrid_power`` W, + = export; u16 ``offgrid_power``). Page 5 bytes 4-7
  are the AC limits the Auto loop clamps to (s16 max discharge, s16 max charge,
  charge negative). Page 6 holds the lifetime grid energy (u32 input Wh, u32
  output Wh).
* **PV MPPT** frames (Venus A/D) use the same layout with ``src_type`` 2 and
  ``cmd 0x03``. Pages 2-4 hold four channels as u16 volt (0.1 V), current
  (0.1 A) and power (0.1 W), in that order: PV1 V/I/P and PV2 V, then PV2 I/P
  and PV3 V/I, then PV3 P and PV4 V/I/P. ``PV.GetStatus`` drops a channel below
  10 W and reports ``pv*_state`` from volt > 14 V.
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
  ``back_func`` matches, so the inverter echoes it. The Venus D also gets its
  ratings: 2560 Wh per pack (``--packs`` fills the 0x1802 count and online mask)
  and 2500 W both ways, the ceiling ``Set.Ver`` accepts on VNSD-0.
* The MPPT also reports pages 5 and 6 (any family with ``--pv``): u32 PV energy
  today (10 Wh), u8 error, u8 state, u16 command power; then u32 this month and
  u32 this year (10 Wh). VNSA-0 / VNSD-0 149 and later put the page 6 year
  counter into ``ES.GetStatus`` ``total_pv_energy`` unscaled, so the reply is in
  10 Wh units. ``--pv-state FILE`` keeps the counters across restarts.

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
PV_VOLT = 40.0
# Per-family ratings and extras, keyed by the catalog ``deviceType``. Families
# not listed keep the historical defaults (5120 Wh, 800,2500 W, power pages only).
# Each entry is ``(Wh per pack, "max discharge,max charge" W, grid pages)``.
DEFAULT_FAMILY = (5120, "800,2500", False)
FAMILIES: dict[str, tuple[int, str, bool]] = {
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
            bms(0x1803, struct.pack("<HHHBB", 576, limit_a * 10, limit_a * 10, 0b11, 0)),
            bms(0x1804, bytes(8)),
        ]


class Inverter:
    """AC side: follows the MCU's setpoint and counts grid energy."""

    def __init__(self, max_discharge: int, max_charge: int, grid: bool = False) -> None:
        self.max_discharge = max_discharge
        self.max_charge = max_charge
        self.grid = grid  # also send pages 1 and 3 and the page 5 temperatures
        self.back_func = 0  # backup (EPS) function, as commanded by cmd 0x02
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
        dc += pv
        battery.energy_wh = min(max(battery.energy_wh + dc * hours, 0), battery.capacity_wh)
        return dc

    def frames(self) -> list[tuple[int, bytes]]:
        def page(n: int, payload: bytes) -> tuple[int, bytes]:
            return (n << 24) | (INVERTER << 20) | (1 << 16) | (1 << 8) | 0x10, payload

        temps = (300, 350) if self.grid else (0, 0)  # ambient, heat sink (0.1 C)
        out = [
            page(4, struct.pack("<hHhH", round(self.ac_power), 0, 0, 0)),
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
        if cmd != 0x01 or len(data) < 4:
            return False
        (self.setpoint,) = struct.unpack_from("<i", data)
        return True


class PvEnergy:
    """The MPPT's own day / month / year PV energy counters, in Wh."""

    def __init__(self, path: str | None) -> None:
        self.path = path
        self.period = self._period()
        self.day = self.month = self.year = 0.0
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


def pv_frames(channels: list[float], energy: PvEnergy | None = None) -> list[tuple[int, bytes]]:
    """MPPT pages 1-6 for up to four channels of ``channels`` W."""
    words: list[int] = []
    for n in range(4):
        power = channels[n] if n < len(channels) else 0.0
        volt = PV_VOLT if power > 0 else 0.0
        words += [round(volt * 10), round(power / volt * 10) if volt else 0, round(power * 10)]

    def page(n: int, values: list[int]) -> tuple[int, bytes]:
        return (n << 24) | (MPPT << 20) | (1 << 16) | (1 << 8) | 0x03, struct.pack("<4H", *values)

    frames = [page(1, [0] * 4)] + [page(n + 2, words[4 * n : 4 * n + 4]) for n in range(3)]
    if energy is not None:
        # Pages 5-6 carry raw bytes, not four u16 words: re-pack the id/payload.
        mppt_id = frames[0][0] & 0x00FFFFFF
        day, month, year = (int(v // 10) for v in (energy.day, energy.month, energy.year))
        frames.append(((5 << 24) | mppt_id, struct.pack("<IBBH", day, 0, 0, 0)))
        frames.append(((6 << 24) | mppt_id, struct.pack("<II", month, year)))
    return frames


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
    parser.add_argument(
        "--family", default="", help="Catalog deviceType (e.g. VNSD-0) for ratings and extras"
    )
    parser.add_argument(
        "--capacity", type=int, help="Rated energy in Wh (default 5120, Venus D 2560 per pack)"
    )
    parser.add_argument("--packs", type=int, default=1, help="Battery modules (1-8)")
    parser.add_argument("--meter-port", type=int, default=8099, help="HTTP port for AstraMeter")
    parser.add_argument(
        "--house-load", default="300,150,150", help="Per-phase house load in W (A,B,C)"
    )
    parser.add_argument(
        "--ac-limits", help="Max AC discharge,charge in W (default 800,2500; Venus D 2500,2500)"
    )
    parser.add_argument("--pv", default="", help="PV channel power in W, e.g. 400,350 (Venus A/D)")
    parser.add_argument("--pv-state", help="JSON file that keeps the MPPT energy counters")
    parser.add_argument("-v", "--verbose", action="store_true", help="Log every changed MCU frame")
    args = parser.parse_args()

    pack_wh, default_limits, grid = FAMILIES.get(args.family, DEFAULT_FAMILY)
    if not 1 <= args.packs <= 8:
        parser.error("--packs takes 1-8 (the firmware caps the 0x1802 count at 8)")
    battery = Battery(args.capacity or pack_wh * args.packs, args.soc, args.packs)
    ac_limits = args.ac_limits or default_limits
    max_discharge, max_charge = (int(v) for v in ac_limits.split(","))
    inverter = Inverter(max_discharge, max_charge, grid=grid)
    pv_energy = PvEnergy(args.pv_state)
    pv = [float(v) for v in args.pv.split(",") if v]
    if len(pv) > 4:
        parser.error("--pv takes at most four channels")
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
            full = battery.energy_wh >= battery.capacity_wh
            harvest = [0.0 if full else p for p in pv]
            dc = inverter.step(battery, now - last, sum(harvest))
            pv_energy.add(sum(harvest), now - last)
            last = now
            out = battery.frames(dc) + inverter.frames()
            if pv:
                out += pv_frames(harvest, pv_energy)
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
