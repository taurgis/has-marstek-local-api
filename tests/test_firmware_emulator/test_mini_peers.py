"""Frame layout and pack simulation for the Venus E mini PMU peers.

The checksums are the ones the PMU 301 image computes (CRC tables at
``0x0802e62c`` / ``0x0802e72c``). The repl assertions keep those DMA wires and
the mini's timer clocks off every board except ``control-mini``.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

import pytest

_EMU = Path(__file__).resolve().parents[2] / "tools" / "firmware_emulator"
if str(_EMU) not in sys.path:
    sys.path.insert(0, str(_EMU))

import mini_peers  # noqa: E402
import run_firmware  # noqa: E402

# Firmware TX ``aa 80 81 00 08 01`` and a Modbus write of register 0xC865.
_AA_BODY = bytes.fromhex("aa8081000801")
_MODBUS_BODY = bytes.fromhex("0110c8650001020001")


def test_crc16_matches_the_pmu_tables() -> None:
    """The peer's CRC is the PMU's, including the stored little-endian order."""
    assert mini_peers.crc16(_AA_BODY) == 0xF3F7
    assert mini_peers.crc16(_MODBUS_BODY) == 0xA927


def _poll(address: int, command: int, payload: bytes = b"") -> bytes:
    """A PMU poll as it arrives on the wire."""
    return mini_peers.aa_frame(address, command, payload)


def _sim(soc: float = 50.0, state: Path | None = None) -> mini_peers.Simulation:
    return mini_peers.Simulation.load(
        mini_peers.RATED_WH, soc, mini_peers.IDLE_W, str(state) if state else None
    )


def test_status_frame_carries_the_gate_readings_and_ac_power() -> None:
    """Command 0x02: the UDP gate halfwords first, then s16 AC power at byte 6."""
    sim = _sim()
    sim.inverter.ac_power = -600.4
    reply = sim.on_inverter_frame(_poll(0x81, 0x02))
    assert reply[:3] == bytes.fromhex("aa8180")
    assert reply[5] == 0x02
    assert int.from_bytes(reply[3:5], "big") == len(reply)
    assert mini_peers.crc16(reply[:-2]) == int.from_bytes(reply[-2:], "little")
    payload = reply[6:-2]
    assert payload[:4] == bytes.fromhex("10000400")
    assert struct.unpack_from(">h", payload, 6) == (-600,)


def test_every_reply_echoes_the_polled_command() -> None:
    """The PMU only accepts a reply whose command matches its poll."""
    sim = _sim()
    for command in (0x01, 0x21, 0x40, 0x43, 0x46, 0xA0):
        assert sim.on_inverter_frame(_poll(0x81, command))[5] == command
    for command in (0x80, 0x81, 0x90):
        assert sim.on_dcdc_frame(_poll(0x82, command))[5] == command


def test_register_writes_are_echoed_and_read_back() -> None:
    """A 0x6X write comes back unchanged and the matching 0x4X read returns it."""
    sim = _sim()
    settings = bytes(range(68))
    assert sim.on_inverter_frame(_poll(0x81, 0x60, settings))[6:-2] == settings
    assert sim.on_inverter_frame(_poll(0x81, 0x40))[6:-2] == settings
    # A group never written reads as zeros.
    zeros = bytes(mini_peers._REGISTER_BYTES)
    assert sim.on_inverter_frame(_poll(0x81, 0x41))[6:-2] == zeros


def test_force_command_sets_direction_and_power() -> None:
    """cmd 0x72: direction 1 charges, 2 discharges, 0 hands back to the meter."""
    sim = _sim()
    sim.on_inverter_frame(_poll(0x81, 0x72, bytes.fromhex("00020320") + bytes(12)))
    assert sim.inverter.setpoint == 800
    reply = sim.on_inverter_frame(_poll(0x81, 0x72, bytes.fromhex("00010258") + bytes(12)))
    assert sim.inverter.setpoint == -600
    assert reply[6:10] == bytes.fromhex("00010258")
    sim.on_inverter_frame(_poll(0x81, 0x72, bytes.fromhex("0000ffff") + bytes(12)))
    assert sim.inverter.setpoint == 0
    sim.inverter.on_force(b"\x00")  # too short: ignored
    assert sim.inverter.direction == mini_peers.FORCE_NONE


def test_force_power_is_clamped_to_the_inverter_rating() -> None:
    """A forced power above 1500 W stops at the rating."""
    inverter = mini_peers.Inverter()
    inverter.on_force(struct.pack(">HH", mini_peers.FORCE_DISCHARGE, 4000))
    assert inverter.setpoint == mini_peers.MAX_AC_W


def test_meter_demand_regulates_only_without_a_forced_direction() -> None:
    """cmd 0x21 moves the output by the grid import; 0 W leaves it alone."""
    inverter = mini_peers.Inverter()
    inverter.ac_power = 100.0
    inverter.on_demand(struct.pack(">h", 250))
    assert inverter.setpoint == 350
    inverter.on_demand(struct.pack(">h", 0))
    assert inverter.setpoint == 350
    inverter.on_demand(b"\x01")  # too short: ignored
    assert inverter.setpoint == 350
    inverter.on_force(struct.pack(">HH", mini_peers.FORCE_CHARGE, 500))
    inverter.on_demand(struct.pack(">h", 900))
    assert inverter.setpoint == -500


def test_demand_frame_reaches_the_inverter() -> None:
    """The simulation hands the cmd 0x21 payload to the inverter."""
    sim = _sim()
    sim.on_inverter_frame(_poll(0x81, 0x21, struct.pack(">h", 300) + bytes(18)))
    assert sim.inverter.setpoint == 300


def test_discharge_ramps_and_drains_the_pack() -> None:
    """The AC stage closes half the gap per tick and the pack pays the losses."""
    sim = _sim(soc=50)
    sim.inverter.on_force(struct.pack(">HH", mini_peers.FORCE_DISCHARGE, 800))
    before = sim.pack.energy_wh
    sim.tick(1.0)
    assert sim.inverter.ac_power == 400
    for _ in range(20):
        sim.tick(1.0)
    assert round(sim.inverter.ac_power) == 800
    assert sim.pack.current_a < -100
    assert sim.pack.energy_wh < before


def test_charge_fills_the_pack_and_stops_when_full() -> None:
    """Charging raises the energy; a full pack takes nothing and the AC side stops."""
    sim = _sim(soc=99.999)
    sim.inverter.on_force(struct.pack(">HH", mini_peers.FORCE_CHARGE, 1000))
    sim.tick(1.0)
    assert sim.pack.soc == 100
    for _ in range(30):
        sim.tick(1.0)
        # Latched full: the standby draw does not restart charging.
        assert sim.inverter.ac_power == 0
        assert sim.pack.current_a < 0
    assert sim.pack.soc > 99.99
    sim.pack.energy_wh = sim.pack.capacity_wh * 0.98
    sim.tick(1.0)
    assert sim.inverter.ac_power < 0  # below the recharge point it charges again


def test_discharge_stops_when_empty() -> None:
    """An empty pack gives nothing."""
    sim = _sim(soc=0)
    sim.inverter.on_force(struct.pack(">HH", mini_peers.FORCE_DISCHARGE, 500))
    sim.tick(1.0)
    assert sim.inverter.ac_power == 0
    assert sim.pack.energy_wh == 0
    assert sim.pack.move(-100, 0) == 0


def test_idle_pack_reads_idle_to_the_pmu() -> None:
    """The standby draw stays inside the PMU's +/-250 mA idle band."""
    sim = _sim(soc=60)
    sim.tick(1.0)
    assert -0.25 < sim.pack.current_a < 0


def test_dcdc_status_layout() -> None:
    """Pack mV, current twice, two cells a millivolt apart and the temperatures."""
    sim = _sim(soc=60)
    sim.pack.current_a = 86.2
    payload = sim.on_dcdc_frame(_poll(0x82, 0x80))[6:-2]
    assert len(payload) == 40
    pack_mv, amps_hi, amps_lo = struct.unpack_from(">Iii", payload, 2)
    cell_hi, cell_lo, temp_a, temp_b = struct.unpack_from(">HHhh", payload, 14)
    cell = round(sim.pack.cell_mv())
    assert pack_mv == cell * mini_peers.CELLS
    assert amps_hi == amps_lo == 86200
    assert (cell_hi, cell_lo) == (cell + 1, cell - 1)
    assert temp_a == temp_b == mini_peers.TEMP_DECI_C
    zeros = bytes(mini_peers._REGISTER_BYTES)
    assert sim.on_dcdc_frame(_poll(0x82, 0x81))[6:-2] == zeros


def test_cell_voltage_follows_the_pmu_ocv_table() -> None:
    """60 % reads 3305 mV at rest, on the flat LFP plateau the PMU looks up."""
    assert round(mini_peers.ocv_mv(60)) == 3305
    assert mini_peers.ocv_mv(-5) == mini_peers.ocv_mv(0)
    assert mini_peers.ocv_mv(150) == mini_peers.OCV_KNOTS[-1][1]
    pack = mini_peers.Pack(mini_peers.RATED_WH, 99.5)
    pack.current_a = 50.0
    assert pack.cell_mv() > mini_peers.ocv_mv(99.5) + 50  # top-of-charge knee


def test_state_survives_a_restart(tmp_path: Path) -> None:
    """The pack energy is saved and resumed; an unreadable file starts at --soc."""
    state = tmp_path / "mini_pack.json"
    _sim(soc=42, state=state).save()
    saved = json.loads(state.read_text())["energy_wh"]
    assert saved == round(mini_peers.RATED_WH * 0.42, 3)
    assert round(_sim(soc=90, state=state).pack.soc, 3) == 42
    state.write_text("not json")
    assert _sim(soc=90, state=state).pack.soc == 90
    _sim(soc=10).save()  # no state file: nothing to write
    _sim(soc=10, state=tmp_path / "missing" / "pack.json").save()  # unwritable: ignored


def test_tick_saves_once_a_minute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The periodic save keeps a crash from losing more than a minute."""
    state = tmp_path / "mini_pack.json"
    sim = _sim(soc=30, state=state)
    sim.tick(1.0)
    assert not state.exists()
    monkeypatch.setattr(sim, "_saved_at", sim._saved_at - 61)
    sim.tick(1.0)
    assert state.exists()


def test_take_frames_keeps_valid_frames_and_a_short_tail() -> None:
    """A bad checksum is dropped; a short tail waits for the next recv."""
    good = mini_peers.aa_frame(0x82, 0x80, bytes(4))
    bad = bytearray(good)
    bad[-1] ^= 0xFF
    frames, tail = mini_peers.take_frames(bytes(bad) + good + b"\xaa\x82")
    assert frames == [good]
    assert tail == b"\xaa\x82"


def _render(board_name: str, work: Path) -> tuple[str, str]:
    blob = work / "blob.bin"
    blob.write_bytes(struct.pack("<II", 0x20020000, 0x080081AD))
    args = argparse.Namespace(
        eeprom=work / "eeprom.bin",
        local_api_port=30000,
        can_port=3457,
        uart_port=3456,
        modem_reset_port=3466,
        ble_mac="02:ee:00:00:00:02",
        quantum=None,
        mips=None,
    )
    board = run_firmware.BOARDS[board_name]
    resc = run_firmware._render(args, work, board, blob, board.app_base)
    repl_name = board.repl.removesuffix(".repl.in")
    return (work / f"{repl_name}.repl").read_text(), resc.read_text()


def test_shared_control_board_has_no_mini_dma_or_peer_sockets(tmp_path: Path) -> None:
    """Venus E/A/D keep the repl they had before the mini peers existed."""
    repl, resc = _render("control", tmp_path)
    assert "dma1@8" not in repl
    assert "dma2@6" not in repl
    assert "@MINI_" not in repl
    assert "[5-6] -> nvic@[68-69]" not in repl
    assert "link_usart4" not in resc
    assert run_firmware.BOARDS["control"].eeprom_wait is True
    assert run_firmware.BOARDS["control"].link_uarts == ()


def test_mini_board_wires_the_peer_uarts(tmp_path: Path) -> None:
    """USART5 and UART7 gain DMA only on the mini, and the script connects them."""
    repl, resc = _render("control-mini", tmp_path)
    assert "DMARequest -> dma1@8" in repl
    assert "DMARequest -> dma2@6 | dma2@7" in repl
    assert "[5-6] -> nvic@[68-69]" in repl
    assert 'CreateServerSocketTerminal 3463 "link_usart4" false' in resc
    assert "connector Connect sysbus.usart5 link_usart5" in resc
    assert "connector Connect sysbus.uart7 link_uart7" in resc
    assert run_firmware.BOARDS["control-mini"].eeprom_wait is False


def test_shared_control_board_keeps_timer6_dma_and_default_uart_clock(tmp_path: Path) -> None:
    """The mini-only clock, TIMER6 and bus-hook changes stay off the shared boards."""
    repl, resc = _render("control", tmp_path)
    assert "UpdateInterrupt -> dma2@3" in repl
    assert "frequency: 36000000" not in repl
    assert "frequency: 72000000" not in repl
    assert "systickFrequency: 72000000" in repl
    assert "@TIMER6_CLOCK@" not in repl
    assert "@SYSTICK_CLOCK@" not in repl
    assert "@TIMER6_DMA@" not in repl
    assert "@SPI1_SCRIPT@" not in repl
    assert str(run_firmware.HERE / "renode" / "spi2.py") in repl
    # Only the FC41D reset line is hooked on the shared board.
    assert resc.count("SetHookBeforePeripheralWrite") == 1
    assert "sysbus.gpioPortE" in resc
    assert not (tmp_path / "spiflash.py").exists()


def test_mini_board_clock_timer6_and_hooks(tmp_path: Path) -> None:
    """PMU 301 gets its 36 MHz link UARTs, a TIMER6 without DMA and both hooks."""
    repl, resc = _render("control-mini", tmp_path)
    assert repl.count("frequency: 36000000") == 3
    assert "UpdateInterrupt -> dma2@3" not in repl
    timer6 = repl[repl.index("timer6:") : repl.index("timer7:")]
    assert "frequency: 72000000" in timer6
    assert "systickFrequency: 144000000" in repl
    assert str(tmp_path / "spiflash.py") in repl
    assert run_firmware.SPI_CS_HOOK in resc
    assert run_firmware.UART7_TC_HOOK in resc
    script = (tmp_path / "spiflash.py").read_text()
    assert "@SPI_FLASH@" not in script
    assert 'int("30000")' in script
    assert str(tmp_path / "spiflash.bin") in script


def test_mini_board_asks_the_modem_for_a_provisioned_slash24_link() -> None:
    """The mini's fc41d options are the ones its QGETIP gate and reply address need."""
    args = run_firmware.BOARDS["control-mini"].modem_args
    assert "--autojoin" in args
    assert args[args.index("--netmask") + 1] == "255.255.255.0"
    assert "--lan-broadcast" in args
    assert run_firmware.BOARDS["control"].modem_args == ()


def test_mini_peers_no_longer_write_modbus() -> None:
    """The UART7 peer only drains; the factory-join write is gone."""
    assert not hasattr(mini_peers, "modbus_write")


def test_mini_pack_args_pass_soc_capacity_and_state(tmp_path: Path) -> None:
    """run_firmware hands the pack its start SoC, rating and a state file by the EEPROM."""
    args = argparse.Namespace(soc=60.0, capacity=2009.0, eeprom=tmp_path / "eeprom.bin")
    assert run_firmware._mini_pack_args(args) == [
        "--soc",
        "60.0",
        "--state",
        str(tmp_path / "mini_pack.json"),
        "--capacity",
        "2009.0",
    ]
    args.capacity = None
    assert "--capacity" not in run_firmware._mini_pack_args(args)


class _FakeMonitor:
    """Answers ReadDoubleWord with queued values and records every command."""

    def __init__(self, values: list[int]) -> None:
        self.values = values
        self.commands: list[str] = []

    def run(self, command: str, wait: float = 1.5) -> str:
        self.commands.append(command)
        if "ReadDoubleWord" in command:
            value = self.values.pop(0) if self.values else 0
            return f"{command}\r\n{value:#010x}\r\n(marstek) "
        return f"{command}\r\n(marstek) "


def test_mini_soc_restarts_from_ocv_once_the_pack_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The re-init flag is written only after the PMU holds a pack voltage."""
    monkeypatch.setattr(run_firmware.time, "sleep", lambda _s: None)
    mon = _FakeMonitor([0, 0, 0x19D2])
    board = run_firmware.BOARDS["control-mini"]
    assert run_firmware._mini_soc_from_ocv(mon, board) is True  # type: ignore[arg-type]
    assert sum("ReadDoubleWord" in c for c in mon.commands) == 3
    assert mon.commands[-1] == f"sysbus WriteWord {run_firmware.MINI_SOC_REINIT:#x} 1"


def test_mini_soc_restart_gives_up_and_skips_other_boards(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No pack voltage before the deadline leaves the PMU alone; other boards skip it."""
    monkeypatch.setattr(run_firmware.time, "sleep", lambda _s: None)
    mon = _FakeMonitor([])
    mini = run_firmware.BOARDS["control-mini"]
    assert run_firmware._mini_soc_from_ocv(mon, mini, timeout=0.0) is False  # type: ignore[arg-type]
    assert "No pack voltage" in capsys.readouterr().out
    assert not any("WriteWord" in c for c in mon.commands)
    control = run_firmware.BOARDS["control"]
    assert run_firmware._mini_soc_from_ocv(mon, control) is False  # type: ignore[arg-type]
