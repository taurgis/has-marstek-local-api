"""Frame layout for the Venus E mini PMU peers.

The checksums are the ones the PMU 301 image computes (CRC tables at
``0x0802e62c`` / ``0x0802e72c``). The repl assertions keep those DMA wires off
every board except ``control-mini``.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

_EMU = Path(__file__).resolve().parents[2] / "tools" / "firmware_emulator"
if str(_EMU) not in sys.path:
    sys.path.insert(0, str(_EMU))

import mini_peers  # noqa: E402
import run_firmware  # noqa: E402

# Firmware TX ``aa 80 81 00 08 01`` and the Modbus write of register 0xC865.
_AA_BODY = bytes.fromhex("aa8081000801")
_MODBUS_BODY = bytes.fromhex("0110c8650001020001")


def test_crc16_matches_the_pmu_tables() -> None:
    """The peer's CRC is the PMU's, including the stored little-endian order."""
    assert mini_peers.crc16(_AA_BODY) == 0xF3F7
    assert mini_peers.crc16(_MODBUS_BODY) == 0xA927
    assert mini_peers.modbus_write(0xC865, 1) == _MODBUS_BODY + bytes.fromhex("27a9")


def test_status_frame_carries_the_udp_gate_readings() -> None:
    """Command 0x02's first two big-endian halfwords are what the UDP gate reads."""
    payload = mini_peers.status_payload()
    assert payload[:4] == bytes.fromhex("10000400")
    frame = mini_peers.aa_frame(0x81, 0x02, payload)
    assert frame[:3] == bytes.fromhex("aa8180")
    assert int.from_bytes(frame[3:5], "big") == len(frame)
    assert mini_peers.crc16(frame[:-2]) == int.from_bytes(frame[-2:], "little")


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
