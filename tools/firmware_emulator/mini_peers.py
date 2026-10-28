"""A simulated pack, DCDC and inverter behind the VNSEMINI-0 PMU 301 image.

The Venus E mini PMU talks to two boards over AA-framed UARTs and keeps the
battery's state of charge itself. ``mini_peers.py`` plays both boards around
an energy-counted pack, so the firmware's own SoC, power and energy counters
move the way they would on a real unit. The Open API replies still come from
the firmware.

Frames are ``AA <addr> 80 <len BE> <cmd> <payload> <crc LE>`` with the image's
Modbus CRC-16; payload fields are big-endian. Every reply carries the command
it answers: the parsers (inverter ``0x080259c0``, DCDC ``0x08022060``) only
count a poll as answered when the reply's command matches the one sent, and
the inverter sequence (``0x08025b78``) retries and stalls on anything else.
What each field is, as the PMU reads it (struct addresses are PMU 301 RAM,
handlers are PMU 301 code):

* **USART4, inverter, address 0x81.** Reply ``cmd 0x02`` (handler
  ``0x080181c8``) copies payload byte ``k`` to ``0x2000b780 + 0x56 + k``:

  * bytes 0-1 and 2-3: two readings the UDP gate wants above ``0xFA0`` and
    ``0x320`` before ``AT+QGETIP`` (meaning not identified; kept constant)
  * bytes 6-7: s16 AC power in W, + = export. ``ES.GetStatus`` and
    ``ES.GetMode`` report it as ``ongrid_power``; the 1 s energy task
    (``0x08019fc0``) counts it into ``total_grid_output_energy`` or, below
    zero while ``cmd 0x72`` forces a direction, ``total_grid_input_energy``
  * bytes 28-29: u16 off-grid power, reported as ``offgrid_power`` and
    counted into ``total_load_energy``. The mini has no EPS socket: 0

  The PMU drives the inverter with two commands:

  * ``cmd 0x72`` (builder ``0x0801cd3c``, from ``0x2000b780+0x4b6``): u16
    direction (1 charge, 2 discharge, 0 none) and u16 power in W. Manual
    mode sets them from the schedule (``manual_set`` 1/2, ``power``); Auto
    sends direction 0.
  * ``cmd 0x21`` (builder ``0x0801b664``): bytes 0-1 are the s16 CT meter
    power from ``0x2000b780+0xaa`` (``0x0801e560``), the Auto demand. With
    no CT it stays 0.

  ``0x40``-``0x46`` read register groups and ``0x60``-``0x66`` (plus
  ``0x70``-``0x72``) write them. The PMU keeps the values it wants beside
  the inverter's last echo and rewrites a group while the two differ
  (``0x08025748``), so writes are echoed and kept, and reads return them.
* **USART5, DCDC, address 0x82.** Reply ``cmd 0x80`` (handler
  ``0x08019790``) fills ``0x2001b644 + 0x148`` onwards:

  * bytes 2-5: u32 pack voltage in mV (``0x2001dde8+0xc``), compared with
    the cell sum at ``0x0800b874``
  * bytes 6-9 and 10-13: s32 battery current in mA, + = charge. The PMU
    uses bytes 10-13 below 70 A and bytes 6-9 above (``0x0801fb9c``); a
    current inside +/-250 mA is idle (``0x08021e10``)
  * bytes 14-15 and 16-17: u16 cell voltages in mV. The pack is 2S LFP; the
    PMU sums them as the pack voltage (``0x080239be``)
  * bytes 18-19 and 20-21: s16 cell temperatures in 0.1 C. The higher one
    is ``bat_temp`` (``Bat.GetStatus``, divided by 10)
  * bytes 32-39: status words, all clear here

  ``cmd 0x90`` and ``0x81`` get zeros; their meaning is not identified.

The PMU counts SoC itself (``0x0802b2d8``): it integrates the DCDC current
over a 314 Ah capacity and the configured SoH, holds 99.9 % until the pack
voltage passes its full threshold, and saves the counter to SPI flash
(``0xF000``) only on its power-down path. When it starts from the
open-circuit voltage (``0x08009fe8``) it looks the pack voltage up in its LFP
tables for the cell temperature, in whole percent. The cells here follow the
25 C table, and ``run_firmware.py`` asks for that OCV start once the PMU has
pack readings, so the PMU begins near ``--soc`` or the saved pack energy.
``Bat.GetStatus`` reports ``bat_capacity`` = SoC x ``rated_capacity`` (2009 Wh).

The Open API has no Passive mode on this image (``ES.SetMode`` answers
``Parse error``); Manual drives the inverter instead.

UART7 is the PMU's Modbus master. It is kept connected and drained so its
DMA keeps moving; nothing is written back. A provisioned module joins on its
own (``fc41d.py --autojoin``), so the factory-join Modbus write is not needed.
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import signal
import socket
import struct
import threading
import time
from collections.abc import Callable

_LOGGER = logging.getLogger("mini_peers")

INV_ADDR = 0x81
DCDC_ADDR = 0x82
INV_STATUS = 0x02
INV_DEMAND = 0x21
INV_FORCE = 0x72
FORCE_NONE, FORCE_CHARGE, FORCE_DISCHARGE = 0, 1, 2
DCDC_STATUS = 0x80
# Register reads (0x40-0x46) and writes (0x60-0x66 to the same groups, plus
# 0x70-0x72). The PMU keeps what it wants written and what the inverter last
# echoed side by side and rewrites a group while the two differ (0x08025748).
INV_READS = range(0x40, 0x47)
INV_WRITES = (*range(0x60, 0x67), 0x70, 0x71, 0x72)
# The PMU's built-in pack (0x08020074): 314 Ah of 2S LFP, 2009 Wh rated,
# 1500 W AC.
CELLS = 2
CELL_AH = 314
RATED_WH = 2009
MAX_AC_W = 1500
# Two constant inverter readings the PMU's UDP gate checks (> 0xFA0, > 0x320).
_GATE_READINGS = (0x1000, 0x0400)
_STATUS_BYTES = 96
# Every other reply is a register image of at most 116 bytes (cmd 0x43,
# 0x08018898). Zeros read as "no fault, nothing set".
_REGISTER_BYTES = 120
TICK = 1.0
RAMP = 0.5  # fraction of the setpoint error closed per tick
EFFICIENCY = 0.95
CELL_OHM = 0.0003  # per 314 Ah cell
TEMP_DECI_C = 250
# Standby draw while the AC stage is off. 1.5 W at ~6.6 V is ~230 mA, under the
# 250 mA the PMU reads as idle (0x08021e10), so an idle pack reports idle.
IDLE_W = 1.5
# Mean of the PMU's 20 C and 30 C open-circuit tables (0x0802f78c,
# 0x0802f856), which is what it looks up at 25 C. (SoC %, cell mV) knots;
# linear between them stays within 0.5 mV of the table.
OCV_KNOTS = (
    (0, 2839), (1, 2949), (2, 3033), (3, 3096), (4, 3140), (5, 3179), (6, 3185),
    (8, 3191), (15, 3219), (16, 3225), (17, 3227), (22, 3248), (24, 3255),
    (28, 3270), (31, 3279), (34, 3286), (41, 3292), (43, 3293), (55, 3302),
    (59, 3304), (65, 3309), (67, 3310), (87, 3325), (89, 3326), (97, 3332),
    (99, 3333), (100, 3370),
)  # fmt: skip
# Charging into a full LFP cell: the voltage climbs to this over the last 1 %.
FULL_CHARGE_MV = 3550
# A full pack charges again only once it has fallen below this.
RECHARGE_SOC = 99.0


def ocv_mv(soc: float) -> float:
    """Open-circuit cell voltage at ``soc`` %, from the PMU's own table."""
    soc = min(max(soc, 0.0), 100.0)
    for (s0, v0), (s1, v1) in itertools.pairwise(OCV_KNOTS):
        if soc <= s1:
            return v0 + (v1 - v0) * (soc - s0) / (s1 - s0)
    return float(OCV_KNOTS[-1][1])


class Pack:
    """2S LFP pack, energy counted. The PMU derives SoC from its current."""

    def __init__(self, capacity_wh: float, soc: float) -> None:
        self.capacity_wh = capacity_wh
        self.energy_wh = capacity_wh * min(max(soc, 0.0), 100.0) / 100
        self.current_a = 0.0  # + = charge

    @property
    def soc(self) -> float:
        return 100 * self.energy_wh / self.capacity_wh

    def cell_mv(self) -> float:
        """Terminal cell voltage: OCV plus the IR drop, and the top-of-charge knee."""
        volt = ocv_mv(self.soc) + self.current_a * CELL_OHM * 1000
        if self.current_a > 0 and self.soc > 99:
            volt = max(volt, ocv_mv(99) + (FULL_CHARGE_MV - ocv_mv(99)) * (self.soc - 99))
        return volt

    def move(self, dc_w: float, dt: float) -> float:
        """Move ``dc_w`` (+ = charge) for ``dt`` s. Returns the power that flowed."""
        if (dc_w > 0 and self.soc >= 100) or (dc_w < 0 and self.energy_wh <= 0):
            dc_w = 0.0
        before = self.energy_wh
        self.energy_wh = min(max(before + dc_w * dt / 3600, 0.0), self.capacity_wh)
        moved = (self.energy_wh - before) * 3600 / dt if dt else 0.0
        self.current_a = moved / (CELLS * ocv_mv(self.soc) / 1000)
        return moved


class Inverter:
    """AC stage, driven the way the PMU drives it.

    ``cmd 0x72`` sets a forced direction and power (Manual: 1 charge,
    2 discharge, 0 none). With no forced direction the stage regulates on the
    meter reading the PMU forwards in ``cmd 0x21``: it moves its output by the
    grid import, so a CT that reads 0 W leaves it where it is.
    """

    def __init__(self, max_w: int = MAX_AC_W, idle_w: float = IDLE_W) -> None:
        self.max_w = max_w
        self.idle_w = idle_w  # standby draw from the pack while the AC stage is off
        self.direction = FORCE_NONE
        self.forced_w = 0
        self.setpoint = 0  # W, + = export (discharge)
        self.ac_power = 0.0  # + = export (discharge)
        self.full = False  # latched at 100 %, released below RECHARGE_SOC

    def _clamp(self, watts: float) -> int:
        return round(max(-self.max_w, min(self.max_w, watts)))

    def on_force(self, payload: bytes) -> None:
        """``cmd 0x72``: u16 direction, u16 power in W (``0x2000b780+0x4b6``)."""
        if len(payload) < 4:
            return
        self.direction, self.forced_w = struct.unpack_from(">HH", payload)
        if self.direction == FORCE_CHARGE:
            self.setpoint = self._clamp(-self.forced_w)
        elif self.direction == FORCE_DISCHARGE:
            self.setpoint = self._clamp(self.forced_w)
        else:
            # Back to meter regulation: start from standby, not the forced power.
            self.setpoint = 0

    def on_demand(self, payload: bytes) -> None:
        """``cmd 0x21``: s16 meter power in W, + = import (``0x2000b780+0xaa``)."""
        if len(payload) < 2 or self.direction in (FORCE_CHARGE, FORCE_DISCHARGE):
            return
        (demand,) = struct.unpack_from(">h", payload)
        if demand:
            # Correct from what the stage delivers now, so a ramp in progress
            # is not counted twice.
            self.setpoint = self._clamp(self.ac_power + demand)

    def step(self, pack: Pack, dt: float) -> None:
        """Ramp towards the setpoint and move the matching DC energy."""
        self.ac_power += (self.setpoint - self.ac_power) * RAMP
        if abs(self.ac_power) < 1:
            self.ac_power = 0.0
        # A full pack takes no charge until it drops below RECHARGE_SOC, so the
        # standby draw does not restart charging every other tick. An empty
        # pack gives none. Either way the AC side stops.
        if pack.soc >= 100:
            self.full = True
        elif pack.soc < RECHARGE_SOC:
            self.full = False
        if (self.ac_power < 0 and self.full) or (self.ac_power > 0 and pack.energy_wh <= 0):
            self.ac_power = 0.0
        if self.ac_power > 0:
            dc = -self.ac_power / EFFICIENCY
        elif self.ac_power < 0:
            dc = -self.ac_power * EFFICIENCY
        else:
            dc = -self.idle_w
        pack.move(dc, dt)


class Simulation:
    """Pack + inverter, stepped once per tick, with optional saved state."""

    def __init__(self, pack: Pack, inverter: Inverter, state_path: str | None = None) -> None:
        self.pack = pack
        self.inverter = inverter
        self.state_path = state_path
        self.lock = threading.Lock()
        self._saved_at = time.monotonic()
        self.registers: dict[int, bytes] = {}  # inverter groups, keyed by cmd & 0x1F

    @classmethod
    def load(
        cls, capacity_wh: float, soc: float, idle_w: float, state_path: str | None
    ) -> Simulation:
        """Resume the saved pack energy if ``state_path`` holds one, else start at ``soc``."""
        if state_path:
            try:
                with open(state_path, encoding="utf-8") as fh:
                    soc = 100 * float(json.load(fh)["energy_wh"]) / capacity_wh
            except (OSError, ValueError, KeyError, TypeError):
                pass
        return cls(Pack(capacity_wh, soc), Inverter(idle_w=idle_w), state_path)

    def save(self) -> None:
        if not self.state_path:
            return
        try:
            with open(self.state_path, "w", encoding="utf-8") as fh:
                json.dump({"energy_wh": round(self.pack.energy_wh, 3)}, fh)
        except OSError:
            pass
        self._saved_at = time.monotonic()

    def tick(self, dt: float) -> None:
        with self.lock:
            self.inverter.step(self.pack, dt)
        if time.monotonic() - self._saved_at >= 60:
            self.save()

    def inverter_payload(self) -> bytes:
        payload = bytearray(_STATUS_BYTES)
        struct.pack_into(">HH", payload, 0, *_GATE_READINGS)
        with self.lock:
            struct.pack_into(">h", payload, 6, round(self.inverter.ac_power))
        return bytes(payload)

    def dcdc_payload(self) -> bytes:
        with self.lock:
            cell = round(self.pack.cell_mv())
            milliamps = round(self.pack.current_a * 1000)
        payload = bytearray(40)
        struct.pack_into(">Iii", payload, 2, cell * CELLS, milliamps, milliamps)
        # Two cells a millivolt apart, as a balanced pack reads.
        struct.pack_into(">HHhh", payload, 14, cell + 1, cell - 1, TEMP_DECI_C, TEMP_DECI_C)
        return bytes(payload)

    def on_inverter_frame(self, frame: bytes) -> bytes:
        """Answer an inverter poll with the same command, as the PMU expects.

        A write is echoed and kept, and a read of that group returns it: the
        PMU retries a write until the echo matches what it sent.
        """
        command, payload = frame[5], frame[6:-2]
        if command == INV_STATUS:
            return aa_frame(INV_ADDR, command, self.inverter_payload())
        with self.lock:
            if command == INV_FORCE:
                self.inverter.on_force(payload)
            elif command == INV_DEMAND:
                self.inverter.on_demand(payload)
            if command in INV_WRITES:
                self.registers[command & 0x1F] = payload
                return aa_frame(INV_ADDR, command, payload)
            if command in INV_READS and command & 0x1F in self.registers:
                return aa_frame(INV_ADDR, command, self.registers[command & 0x1F])
        return aa_frame(INV_ADDR, command, bytes(_REGISTER_BYTES))

    def on_dcdc_frame(self, frame: bytes) -> bytes:
        """Answer a DCDC poll with the same command, as the PMU expects."""
        command = frame[5]
        if command == DCDC_STATUS:
            return aa_frame(DCDC_ADDR, command, self.dcdc_payload())
        return aa_frame(DCDC_ADDR, command, bytes(_REGISTER_BYTES))


def crc16(data: bytes) -> int:
    """Modbus CRC-16 (init 0xFFFF, poly 0xA001), matching the PMU's tables."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def aa_frame(address: int, command: int, payload: bytes) -> bytes:
    """One AA reply: ``AA <addr> 80 <len BE> <cmd> <payload> <crc LE>``.

    The length halfword is big-endian, matching the polls the PMU transmits
    (``aa 80 81 00 08 ...`` is an 8-byte frame). The CRC stays little-endian.
    """
    total = 6 + len(payload) + 2
    body = bytes([0xAA, address, 0x80, (total >> 8) & 0xFF, total & 0xFF, command])
    body += payload
    crc = crc16(body)
    return body + bytes((crc & 0xFF, crc >> 8))


def take_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
    """Pull complete AA frames out of ``buffer``. Return frames and the tail."""
    frames: list[bytes] = []
    while True:
        start = buffer.find(b"\xaa")
        if start < 0:
            return frames, b""
        buffer = buffer[start:]
        if len(buffer) < 8:
            return frames, buffer
        total = (buffer[3] << 8) | buffer[4]
        if total < 8 or total > 600:
            buffer = buffer[1:]
            continue
        if len(buffer) < total:
            return frames, buffer
        frame, buffer = buffer[:total], buffer[total:]
        body, crc_bytes = frame[:-2], frame[-2:]
        expected = crc_bytes[0] | (crc_bytes[1] << 8)
        if crc16(body) == expected:
            frames.append(frame)


def _connect(port: int, stop: threading.Event) -> socket.socket | None:
    while not stop.is_set():
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        except OSError:
            stop.wait(0.5)
            continue
        sock.settimeout(2.0)
        _LOGGER.info("connected to port %s", port)
        return sock
    return None


def _serve_aa(port: int, stop: threading.Event, answer: Callable[[bytes], bytes]) -> None:
    last: dict[int, bytes] = {}
    while not stop.is_set():
        sock = _connect(port, stop)
        if sock is None:
            return
        buffer = b""
        try:
            while not stop.is_set():
                try:
                    chunk = sock.recv(4096)
                except TimeoutError:
                    continue
                if not chunk:
                    break
                buffer += chunk
                frames, buffer = take_frames(buffer)
                for frame in frames:
                    sock.sendall(answer(frame))
                    if last.get(frame[5]) != frame:
                        # New commands and changed payloads; repeats only at debug.
                        last[frame[5]] = frame
                        _LOGGER.info("port %s cmd %#04x: %s", port, frame[5], frame[6:-2].hex())
                    _LOGGER.debug("port %s rx %s", port, frame.hex())
        except OSError as exc:
            _LOGGER.info("port %s closed: %s", port, exc)
        finally:
            sock.close()


def _drain(sock: socket.socket) -> None:
    """Read the PMU's bytes so a full TCP buffer cannot stall its UART."""
    try:
        while sock.recv(4096):
            pass
    except TimeoutError:
        return


def _serve_modbus(port: int, stop: threading.Event) -> None:
    """Keep the Modbus UART connected and drained. Nothing is written to it."""
    while not stop.is_set():
        sock = _connect(port, stop)
        if sock is None:
            return
        sock.settimeout(0.5)
        try:
            while not stop.is_set():
                _drain(sock)
        except OSError as exc:
            _LOGGER.info("port %s closed: %s", port, exc)
        finally:
            sock.close()


def _run(sim: Simulation, stop: threading.Event) -> None:
    last = time.monotonic()
    logged = 0.0
    while not stop.wait(TICK):
        now = time.monotonic()
        sim.tick(now - last)
        last = now
        if now - logged >= 30:
            logged = now
            _LOGGER.info(
                "soc %.2f %% cell %.0f mV current %.1f A ac %.0f W (setpoint %d W)",
                sim.pack.soc,
                sim.pack.cell_mv(),
                sim.pack.current_a,
                sim.inverter.ac_power,
                sim.inverter.setpoint,
            )


def main() -> None:
    """Answer the three mini-only UART sockets until killed."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--usart4", type=int, required=True)
    parser.add_argument("--usart5", type=int, required=True)
    parser.add_argument("--uart7", type=int, required=True)
    parser.add_argument("--soc", type=float, default=50.0, help="Initial pack SoC %%")
    parser.add_argument("--capacity", type=float, default=RATED_WH, help="Pack Wh")
    parser.add_argument("--idle-w", type=float, default=IDLE_W, help="Standby draw in W")
    parser.add_argument("--state", help="JSON file that keeps the pack energy across restarts")
    parser.add_argument("-v", "--verbose", action="store_true", help="Log every PMU frame")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s"
    )
    sim = Simulation.load(args.capacity, args.soc, args.idle_w, args.state)
    # run_firmware.py stops its children with SIGTERM; save the pack on it too.
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    stop = threading.Event()
    threads = [
        threading.Thread(target=_serve_aa, args=(args.usart4, stop, sim.on_inverter_frame)),
        threading.Thread(target=_serve_aa, args=(args.usart5, stop, sim.on_dcdc_frame)),
        threading.Thread(target=_serve_modbus, args=(args.uart7, stop)),
        threading.Thread(target=_run, args=(sim, stop)),
    ]
    for thread in threads:
        thread.daemon = True
        thread.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop.set()
    finally:
        sim.save()


if __name__ == "__main__":
    main()
