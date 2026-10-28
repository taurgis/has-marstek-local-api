"""Boot a vendor Venus E 3.0 / A / D / C / E 2.0 Control firmware image in Renode.

Renders the Renode platform/script templates, starts Renode headless, starts the
FC41D modem emulator (``fc41d.py``) on the firmware's Wi-Fi UART and runs the
machine. The firmware's Local API then answers on real UDP port 30000 of this
host, so Home Assistant (or ``tools/query_device.py``) talks to it unmodified.
``can_peers.py`` plays the BMS, inverter and PV MPPT on the CAN bus and serves
the grid meter reading that AstraMeter turns into CT003 replies (see README.md).

VNSE3-0, VNSA-0 and VNSD-0 images share one board, so ``--firmware DEVICE:VERSION``
picks any of them from ``tools/firmware/catalog.json``. HMG-50 (Venus C / Venus E
2.0) images run on their own STM32G474 board (``renode/hmg50.repl.in``); the
catalog's device type selects it. VNSEMINI-0 PMU 301 uses that same GD32F30x
board, loaded at the catalog ``loadAddress`` after its ASCII header is stripped,
with the modem on USART2 (``0x40004400``, IRQ 38) instead of the IRQ 74
UART the other Control images use. That image also gets ``mini_peers.py`` on
USART4 (inverter), USART5 (DCDC) and UART7 (Modbus): the binary waits for a
pack-voltage frame and a register write before it joins Wi-Fi and opens UDP.
It never programs the I2C EEPROM, so that board skips the fresh-EEPROM reboot.
Its settings live in an SPI1 NOR flash (``renode/spiflash.py``) instead, which
keeps the Open API on at ``--local-api-port`` the way the EEPROM model does.
The stack pointer and entry point come from each image's vector table.

The firmware images are committed under ``tools/firmware/blobs/``;
``tools/firmware/fetch_firmware.py`` verifies them against the catalog.

A fresh EEPROM image gets its defaults from the firmware on the first boot,
with the Local API disabled. The EEPROM model then forces it on, and this
script reboots once so the firmware reads the enabled setting.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
DEFAULT_BLOB = REPO / "tools/firmware/blobs/VNSEE3-0_app_0150_0804_151249.bin"
CATALOG = REPO / "tools/firmware/catalog.json"
UART_PORT = 3456
CT_PORT = 12345  # CT003 meter protocol (AstraMeter listens here)
CAN_PORT = 3457
METER_PORT = 8099
MODEM_RESET_PORT = 3466  # fc41d.py hears the module reset line here

RESC = """\
mach create "marstek"
{includes}
machine LoadPlatformDescription @{repl}
sysbus LoadBinary @{blob} {base:#010x}
{boot}
cpu VectorTableOffset {base:#010x}
sysbus.cpu SP {sp:#010x}
sysbus.cpu PC {pc:#010x}
logLevel 2
{usarts}
emulation CreateServerSocketTerminal {uart_port} "wifi" false
connector Connect sysbus.{wifi_uart} wifi
emulation CreateCANHub "canhub"
connector Connect sysbus.{can} canhub
connector Connect sysbus.canbridge canhub
{links}{hooks}{tuning}"""

# PA4 is the SPI1 flash chip select. BSRR bit 4 raises it, BSRR bit 20 and BRR
# bit 4 lower it. The hook hands the level to renode/spiflash.py at 0x3F0.
SPI_CS_HOOK = (
    'sysbus SetHookBeforePeripheralWrite sysbus.gpioPortA "'
    "cs = 1 if (offset == 0x10 and value & 0x10) else "
    "(0 if ((offset == 0x10 and value & 0x100000) or (offset == 0x14 and value & 0x10)) "
    "else -1); machine.SystemBus.WriteDoubleWord(0x400133F0, cs) if cs >= 0 else None"
    '"'
)

# STM32_UART raises TC the moment DR is written and never again. On a real
# UART TC follows the last stop bit, so PMU 301's DMA TX-complete IRQ clears
# the stale TC on UART7 and waits for a fresh one before posting the Modbus
# task. Keeping TC on through SR writes stands in for that later edge.
# The FC41D reset line. Control 150's reset routine (0x0800b224) writes BRR
# to pull PE5 and PE4 low, waits 1 s and writes BSRR to raise them. The hook
# sends the level (0 or 1) to fc41d.py, which switches the module off while
# the line is low, the way the real module loses its Wi-Fi link and sockets.
MODEM_RESET_HOOK = (
    'sysbus SetHookBeforePeripheralWrite sysbus.{port} "'
    "lvl = 0 if ((offset == 0x14 and value & {mask:#x}) or "
    "(offset == 0x10 and value & {high:#x})) else "
    "(1 if (offset == 0x10 and value & {mask:#x}) else -1); "
    "(lambda s: (s.sendto(str(lvl), ('127.0.0.1', {udp})), s.close()))"
    "(__import__('socket').socket(2, 2)) if lvl >= 0 else None"
    '"'
)

UART7_TC_HOOK = (
    'sysbus SetHookBeforePeripheralWrite sysbus.uart7 "'
    "value = (value | 0x40) if offset == 0 else value"
    '"'
)


@dataclass(frozen=True)
class Board:
    """Everything that differs between the emulated boards."""

    repl: str
    app_base: int
    wifi_uart: str
    can: str
    uarts: tuple[str, ...]
    models: tuple[str, ...] = ("I2CEeprom.cs", "CanBridge.cs")
    dialect: str = "control"  # fc41d.py AT dialect
    protocol: str = "control"  # can_peers.py bus layout
    boot_vector: bool = False  # reset reads 0x0; mirror SP/PC at the flash base
    uid: bool = False  # write a 96-bit device UID and the flash size
    dwt: bool = False  # CYCCNT that advances, for images that busy-wait on it
    # (uart name, TCP port) sockets for a peer that answers this board.
    # Empty on the shared Control and HMG-50 boards.
    link_uarts: tuple[tuple[str, int], ...] = ()
    eeprom_wait: bool = True  # reboot once so a fresh EEPROM is read back
    spi_flash: bool = False  # settings in SPI1 NOR flash (renode/spiflash.py), CS on PA4
    modem_args: tuple[str, ...] = ()  # extra fc41d.py options for this board
    # (GPIO port, pin mask) of the FC41D reset line, or None where it is unknown.
    modem_reset: tuple[str, int] | None = None


BOARDS = {
    # GD32F30x-class; the Control images sit above an 18 KiB bootloader.
    "control": Board(
        repl="control.repl.in",
        app_base=0x08004800,
        wifi_uart="wifi_uart",
        can="can1",
        uarts=tuple(f"usart{n}" for n in range(1, 6)),
        modem_reset=("gpioPortE", 0x30),  # PE4/PE5
    ),
    # Same repl. VNSEMINI-0 PMU 301 loads at 0x08008000 (catalog loadAddress).
    # A boot log showed the FC41D AT stream on USART2 (0x40004400, IRQ 38),
    # so the modem is connected there instead of the IRQ 74 UART.
    "control-mini": Board(
        repl="control.repl.in",
        app_base=0x08008000,
        wifi_uart="usart2",
        can="can1",
        uarts=("usart1", "usart3", "usart4", "usart5", "uart7"),
        dwt=True,
        # USART4 (INV, addr 0x81), USART5 (DCDC, addr 0x82), UART7 (Modbus).
        link_uarts=(("usart4", 3463), ("usart5", 3464), ("uart7", 3465)),
        eeprom_wait=False,  # this image never touches the I2C EEPROM
        spi_flash=True,  # Open API enable and port live at SPI flash 0x14000
        # QGETIP only runs while the pack is idle, which after start-up means
        # the first 750 control ticks. A provisioned module has GOT_IP by then
        # (--autojoin). The +QGETIP handler keeps the first three octets and
        # sends every reply to <a.b.c>.255, so the module reports a /24 and
        # puts that broadcast on the wider Docker network as 255.255.255.255.
        modem_args=("--autojoin", "--netmask", "255.255.255.0", "--lan-broadcast"),
    ),
    # STM32G474 (HMG-50: Venus C / Venus E 2.0); the app sits above a 10 KiB bootloader.
    "hmg50": Board(
        repl="hmg50.repl.in",
        app_base=0x08002800,
        wifi_uart="lpuart1",
        can="fdcan2",
        uarts=("usart1", "usart2", "usart3", "uart4", "uart5"),
        models=("I2CEeprom.cs", "CanBridge.cs", "STM32G4_USART.cs"),
        dialect="hmg50",
        protocol="pylontech",
        boot_vector=True,
        uid=True,
    ),
}
FLASH_BASE = 0x08000000
UID_BASE = 0x1FFF7590  # STM32G4 96-bit unique ID
FLASH_SIZE_REG = 0x1FFF75E0


class Monitor:
    """Minimal client for the Renode telnet monitor."""

    def __init__(self, port: int) -> None:
        deadline = time.monotonic() + 60
        while True:
            try:
                self._sock = socket.create_connection(("127.0.0.1", port))
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        self._sock.settimeout(0.5)
        self._drain(1.0)

    def _drain(self, seconds: float) -> str:
        buf = b""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                data = self._sock.recv(65536)
            except TimeoutError:
                continue
            if not data:
                break
            buf += data
        return re.sub(rb"\x1b\[[0-9;]*[a-zA-Z]", b"", buf).decode("latin1")

    def close(self) -> None:
        """Release the monitor; Renode serves one client at a time."""
        self._sock.close()

    def run(self, command: str, wait: float = 1.5) -> str:
        self._sock.sendall(command.encode() + b"\n")
        return self._drain(wait)


def _vector_table(blob: Path) -> tuple[int, int]:
    """Initial SP and reset vector: the first two words of the loaded image."""
    sp, pc = struct.unpack("<II", blob.read_bytes()[:8])
    return sp, pc


def _catalog_images() -> list[dict[str, Any]]:
    """Return the catalog image list."""
    images = json.loads(CATALOG.read_text())["images"]
    if not isinstance(images, list):
        raise SystemExit(f"{CATALOG} has no image list")
    return images


def _catalog_entry(spec: str) -> dict[str, Any]:
    """Return the catalog object for ``DEVICE:VERSION``."""
    device, _, version = spec.partition(":")
    for entry in _catalog_images():
        if entry["deviceType"] == device and str(entry["version"]) == version:
            return entry
    raise SystemExit(f"{spec} is not in {CATALOG}")


def _entry_for_blob(blob: Path) -> dict[str, Any] | None:
    """Return the catalog object whose filename is ``blob``, if there is one."""
    for entry in _catalog_images():
        if entry["filename"] == blob.name:
            return entry
    return None


def _catalog_blob(spec: str) -> Path:
    """Resolve ``DEVICE:VERSION`` (e.g. ``VNSA-0:150``) through the catalog."""
    entry = _catalog_entry(spec)
    return CATALOG.parent / "blobs" / str(entry["filename"])


def _loadable_blob(entry: dict[str, Any] | None, blob: Path, work: Path) -> tuple[Path, int | None]:
    """Strip a catalogued ASCII header and return ``(payload, load address)``.

    The load address is None when the catalog leaves it to the board. A
    ``runnable: false`` part (Venus E mini INV / DCDC) has no Open API image
    to boot.
    """
    if entry is None:
        return blob, None
    if entry.get("runnable") is False:
        raise SystemExit(
            f"{entry['filename']} is catalogued but not a runnable Control image "
            f"({entry.get('firmwareType')})"
        )
    header_size = entry.get("headerSize")
    payload = blob
    if isinstance(header_size, int) and header_size > 0:
        data = blob.read_bytes()
        if len(data) <= header_size:
            raise SystemExit(f"{blob.name} is shorter than its {header_size}-byte header")
        payload = work / f"{blob.name}.payload"
        payload.write_bytes(data[header_size:])
    load_address: int | None = None
    raw_load = entry.get("loadAddress")
    if isinstance(raw_load, str):
        load_address = int(raw_load, 16)
    return payload, load_address


def _device_type(args: argparse.Namespace) -> str:
    """Catalog device type of the image, from --firmware or the blob filename."""
    if args.firmware:
        return str(args.firmware.partition(":")[0])
    return str(args.blob.name.split("_", 1)[0])


def _board_for(args: argparse.Namespace) -> str:
    """``--board``, else the catalog device's board, else the Control board."""
    if args.board:
        return str(args.board)
    device = args.firmware.partition(":")[0] if args.firmware else ""
    if not device:
        entry = _entry_for_blob(args.blob)
        device = str(entry["deviceType"]) if entry else ""
    if device == "HMG-50":
        return "hmg50"
    # PMU 301 emits FC41D AT commands on USART2. The shared Control UART
    # (IRQ 74, 0x40015000) is the default handler in that image.
    if device == "VNSEMINI-0":
        return "control-mini"
    return "control"


def _uid_words(ble_mac: str) -> tuple[int, int, int]:
    """A stable per-device UID, so each emulated unit reports its own ``uid=``."""
    mac = bytes.fromhex(ble_mac.replace(":", ""))
    return 0x00470031, int.from_bytes(mac[:4], "little"), int.from_bytes(mac[4:] + b"PH", "little")


def _start_mini_peers(board: Board, work: Path) -> subprocess.Popen[bytes] | None:
    """Answer the mini PMU's INV, DCDC and Modbus UARTs. Other boards skip this."""
    if not board.link_uarts:
        return None
    command = [sys.executable, str(HERE / "mini_peers.py")]
    for name, port in board.link_uarts:
        command += [f"--{name}", str(port)]
    # The child keeps its own copy of the log fd; the parent closes this one.
    log = (work / "mini_peers.log").open("ab")
    try:
        return subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
    finally:
        log.close()


def _link_lines(board: Board) -> str:
    """Server sockets for ``mini_peers.py``. Empty on every other board."""
    lines: list[str] = []
    for name, port in board.link_uarts:
        terminal = f"link_{name}"
        lines.append(f'emulation CreateServerSocketTerminal {port} "{terminal}" false')
        lines.append(f"connector Connect sysbus.{name} {terminal}")
    return "\n".join(lines) + ("\n" if lines else "")


def _hook_lines(board: Board, reset_port: int) -> str:
    """Bus hooks that stand in for hardware the emulator has no model of."""
    lines = [SPI_CS_HOOK] if board.spi_flash else []
    if board.link_uarts:
        lines.append(UART7_TC_HOOK)
    if board.modem_reset:
        port, mask = board.modem_reset
        lines.append(MODEM_RESET_HOOK.format(port=port, mask=mask, high=mask << 16, udp=reset_port))
    return "".join(line + "\n" for line in lines)


def _render(args: argparse.Namespace, work: Path, board: Board, blob: Path, load_base: int) -> Path:
    renode_dir = HERE / "renode"
    repl = (renode_dir / board.repl).read_text()
    for key, value in {
        "@RENODE_DIR@": str(renode_dir),
        "@EEPROM@": str(args.eeprom),
        "@LOCAL_API_PORT@": str(args.local_api_port),
        "@CAN_PORT@": str(args.can_port),
        "@DWT@": (
            (
                "dwt: Python.PythonPeripheral @ sysbus 0xE0001000\n"
                "    size: 0x100\n"
                "    initable: true\n"
                f'    filename: "{renode_dir / "dwt.py"}"\n'
            )
            if board.dwt
            else ""
        ),
        # Only the mini image drives these DMA channels. Leaving the lines in
        # the shared repl would deliver requests the other images never armed.
        "@MINI_USART5_DMA@": "    DMARequest -> dma1@8\n" if board.link_uarts else "",
        "@MINI_UART7_DMA@": ("    DMARequest -> dma2@6 | dma2@7\n" if board.link_uarts else ""),
        "@MINI_DMA2_IRQ@": "    [5-6] -> nvic@[68-69]\n" if board.link_uarts else "",
        # Renode clocks STM32_UART at 8 MHz unless told otherwise. PMU 301 sets
        # BRR for a 36 MHz PCLK1 (USART4 19200, USART5 115200, UART7 57600);
        # at 8 MHz an inverter reply outlasts the next poll and the frames
        # merge, so the parser (which wants one frame per IDLE) drops them.
        "@MINI_LINK_CLOCK@": "    frequency: 36000000\n" if board.link_uarts else "",
        # STM32_Timer raises its DMA line on every update, UDE or not. PMU 301
        # runs TIMER6 with only UIE set and owns DMA2 channel 3 for USART4 RX,
        # so the shared line would read an empty DR into every inverter frame.
        "@TIMER6_DMA@": "" if board.link_uarts else "    UpdateInterrupt -> dma2@3\n",
    }.items():
        repl = repl.replace(key, value)
    spi1 = renode_dir / "spi2.py"
    if board.spi_flash:
        spi1 = work / "spiflash.py"
        script = (renode_dir / "spiflash.py").read_text()
        script = script.replace("@SPI_FLASH@", str(args.eeprom.parent / "spiflash.bin"))
        spi1.write_text(script.replace("@LOCAL_API_PORT@", str(args.local_api_port)))
    repl = repl.replace("@SPI1_SCRIPT@", str(spi1))
    name = board.repl.removesuffix(".repl.in")
    (work / f"{name}.repl").write_text(repl)
    usarts = "\n".join(f"{u} CreateFileBackend @{work}/{u}.txt true" for u in board.uarts)
    sp, pc = _vector_table(blob)
    boot: list[str] = []
    if board.boot_vector:
        boot += [
            f"sysbus WriteDoubleWord {FLASH_BASE:#010x} {sp:#010x}",
            f"sysbus WriteDoubleWord {FLASH_BASE + 4:#010x} {pc:#010x}",
        ]
    if board.uid:
        for n, word in enumerate(_uid_words(args.ble_mac)):
            boot.append(f"sysbus WriteDoubleWord {UID_BASE + 4 * n:#010x} {word:#010x}")
        boot.append(f"sysbus WriteWord {FLASH_SIZE_REG:#010x} 0x0200")  # 512 KiB
    resc = RESC.format(
        includes="\n".join(f"include @{renode_dir}/{m}" for m in board.models),
        repl=work / f"{name}.repl",
        blob=blob,
        base=load_base,
        boot="\n".join(boot),
        sp=sp,
        pc=pc,
        usarts=usarts,
        uart_port=args.uart_port,
        wifi_uart=board.wifi_uart,
        can=board.can,
        links=_link_lines(board),
        hooks=_hook_lines(board, args.modem_reset_port),
        tuning="".join(
            [
                f'emulation SetGlobalQuantum "{args.quantum}"\n' if args.quantum else "",
                f"cpu PerformanceInMips {args.mips}\n" if args.mips else "",
            ]
        ),
    )
    (work / f"{name}.resc").write_text(resc)
    return work / f"{name}.resc"


def _eeprom_initialised(path: Path) -> bool:
    try:
        return path.read_bytes()[:1] not in (b"", b"\xff")
    except OSError:
        return False


def _own_ip(gateway: str) -> str:
    """The address this host routes to ``gateway`` from (a container's own IP)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((gateway, 9))  # UDP connect sends nothing
        return str(sock.getsockname()[0])


def _rotate_logs(work: Path, max_bytes: int) -> None:
    """Copy-truncate ``WORK/*.log`` past ``max_bytes``: one ``.1`` generation is kept.

    The children write with ``O_APPEND``, so truncating in place is safe.
    """
    for log in work.glob("*.log"):
        try:
            if log.stat().st_size <= max_bytes:
                continue
            log.with_suffix(".log.1").write_bytes(log.read_bytes())
            os.truncate(log, 0)
        except OSError:
            continue


def _boot(mon: Monitor, resc: Path, fc41d_cmd: list[str], log: Path) -> subprocess.Popen[bytes]:
    mon.run("mach clear")
    mon.run("Clear")
    mon.run(f"include @{resc}", wait=15)
    with log.open("ab") as out:
        modem = subprocess.Popen(fc41d_cmd, stdout=out, stderr=subprocess.STDOUT)
    time.sleep(1)
    mon.run("start")
    return modem


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the emulator command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--renode",
        default=os.environ.get("RENODE"),
        help="Path to the renode launcher (default: $RENODE)",
    )
    parser.add_argument("--blob", type=Path, default=DEFAULT_BLOB)
    parser.add_argument(
        "--firmware", metavar="DEVICE:VERSION", help="Catalog image, e.g. VNSA-0:150"
    )
    parser.add_argument(
        "--ip", required=True, help="IP the emulated device reports; 'auto' = route to --gateway"
    )
    parser.add_argument("--gateway", default="172.28.0.1")
    parser.add_argument("--local-api-port", type=int, default=30000)
    parser.add_argument(
        "--ble-mac", default="02:ee:00:00:00:02", help="BLE MAC (the integration's unique ID)"
    )
    parser.add_argument(
        "--wifi-mac",
        default="02:00:00:00:00:01",
        help="wifi_mac the firmware reports (the modem's BSSID answer, see README)",
    )
    parser.add_argument(
        "--ct-loopback",
        action="store_true",
        help=f"Keep CT003 traffic (UDP {CT_PORT}) on 127.0.0.1 for a same-namespace AstraMeter",
    )
    parser.add_argument("--work", type=Path, default=Path("/tmp/marstek-fw"))
    parser.add_argument("--eeprom", type=Path, help="EEPROM image (default: WORK/eeprom.bin)")
    parser.add_argument("--monitor-port", type=int, default=41234)
    parser.add_argument("--board", choices=sorted(BOARDS), help="Default: from the catalog entry")
    parser.add_argument("--uart-port", type=int, default=UART_PORT, help="Wi-Fi UART TCP port")
    parser.add_argument("--can-port", type=int, default=CAN_PORT, help="CAN bridge TCP port")
    parser.add_argument(
        "--modem-reset-port",
        type=int,
        default=MODEM_RESET_PORT,
        help="UDP port on 127.0.0.1 where fc41d.py hears the module reset line",
    )
    parser.add_argument(
        "--meter-port", type=int, default=METER_PORT, help="can_peers.py grid meter HTTP port"
    )
    parser.add_argument("--meter-bind", default="127.0.0.1", help="Address the meter HTTP binds")
    parser.add_argument("--bind-ip", default="0.0.0.0", help="Host address the modem sockets bind")
    parser.add_argument(
        "--broadcast-to", default="", help="Send the firmware's LAN broadcasts to this IP instead"
    )
    parser.add_argument(
        "--bssid", help="AP BSSID the modem reports (default: --wifi-mac, which it becomes)"
    )
    parser.add_argument("--soc", type=float, default=50.0, help="Initial battery SoC")
    parser.add_argument("--house-load", default="300,150,150", help="Per-phase load in W")
    parser.add_argument("--pv", default="", help="PV channel power in W (Venus A/D), e.g. 400,350")
    parser.add_argument(
        "--ac-limits", help="Inverter max discharge,charge W (default per family, see can_peers)"
    )
    parser.add_argument(
        "--capacity",
        type=int,
        help="Pack Wh (default per family and --packs; HMG-50 reports VenusC at <=3000)",
    )
    parser.add_argument("--packs", type=int, default=1, help="Battery modules on the BMS")
    parser.add_argument("--pv-energy", type=float, default=0.0, help="Initial MPPT yield in kWh")
    parser.add_argument("--offgrid-load", type=float, default=0.0, help="EPS socket W (Venus A)")
    parser.add_argument(
        "--bms-charge-error", type=float, default=0.0, help="BMS counter charge error %% (-6)"
    )
    parser.add_argument(
        "--bms-discharge-error", type=float, default=0.0, help="BMS counter discharge error %%"
    )
    parser.add_argument(
        "--bms-resync",
        choices=("jump", "creep", "hold", "none"),
        default="jump",
        help="How a drifted BMS counter lands on 100 %% (see can_peers.SocCounter)",
    )
    parser.add_argument(
        "--quantum",
        type=float,
        help="Renode global quantum in s (default 0.0001). The firmware never sleeps, so "
        "a larger quantum (0.01) roughly halves host CPU by syncing less often",
    )
    parser.add_argument(
        "--mips",
        type=int,
        help="Emulated CPU speed (Renode default 100). Host CPU scales with it; a lower "
        "value is a slower MCU with the same timers",
    )
    parser.add_argument(
        "--log-max-mb", type=float, default=0, help="Rotate WORK/*.log past this size (0 = never)"
    )
    return parser.parse_args(argv)


def main() -> int:
    args = _parse_args()
    if not args.renode:
        raise SystemExit("--renode (or $RENODE) is required")
    if args.ip == "auto":
        args.ip = _own_ip(args.gateway)
    if args.firmware:
        args.blob = _catalog_blob(args.firmware)
    if not args.blob.is_file():
        raise SystemExit(
            f"firmware blob not found: {args.blob} (run tools/firmware/fetch_firmware.py)"
        )
    board = BOARDS[_board_for(args)]
    args.work.mkdir(parents=True, exist_ok=True)
    entry = _catalog_entry(args.firmware) if args.firmware else _entry_for_blob(args.blob)
    blob, load_address = _loadable_blob(entry, args.blob, args.work)
    args.eeprom = (args.eeprom or args.work / "eeprom.bin").resolve()
    resc = _render(args, args.work, board, blob, load_address or board.app_base)
    fc41d_cmd = [
        sys.executable, str(HERE / "fc41d.py"), "--uart-port", str(args.uart_port),
        "--ip", args.ip, "--gateway", args.gateway, "--ble-mac", args.ble_mac,
        "--wifi-mac", args.wifi_mac,
        "--bind-ip", args.bind_ip, "--dialect", board.dialect,
        "--bssid", args.bssid or args.wifi_mac, "-v",
    ]  # fmt: skip
    if args.ct_loopback:
        fc41d_cmd += ["--loopback-port", str(CT_PORT)]
    if args.broadcast_to:
        fc41d_cmd += ["--broadcast-to", args.broadcast_to]
    if board.modem_reset:
        fc41d_cmd += ["--reset-port", str(args.modem_reset_port)]
    fc41d_cmd += board.modem_args
    peers_cmd = [
        sys.executable, str(HERE / "can_peers.py"), "--port", str(args.can_port),
        "--soc", str(args.soc), "--house-load", args.house_load, "--pv", args.pv,
        "--meter-port", str(args.meter_port), "--meter-bind", args.meter_bind,
        "--protocol", board.protocol, "--family", _device_type(args), "--packs", str(args.packs),
        "--pv-energy", str(args.pv_energy), "--offgrid-load", str(args.offgrid_load),
        "--pv-state", str(args.eeprom.parent / "pv_energy.json"),
    ]  # fmt: skip
    # The catalog deviceType picks the peers' ratings; explicit flags win.
    if args.ac_limits:
        peers_cmd += ["--ac-limits", args.ac_limits]
    if args.capacity:
        peers_cmd += ["--capacity", str(args.capacity)]
    peers_cmd += [
        "--bms-charge-error", str(args.bms_charge_error),
        "--bms-discharge-error", str(args.bms_discharge_error),
        "--bms-resync", args.bms_resync,
    ]  # fmt: skip
    modem_log = args.work / "fc41d.log"

    renode = subprocess.Popen(
        [args.renode, "--disable-gui", "-P", str(args.monitor_port)],
        stdout=(args.work / "renode.log").open("ab"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    modem: subprocess.Popen[bytes] | None = None
    peers: subprocess.Popen[bytes] | None = None
    mini: subprocess.Popen[bytes] | None = None
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        mon = Monitor(args.monitor_port)
        fresh = board.eeprom_wait and not _eeprom_initialised(args.eeprom)
        modem = _boot(mon, resc, fc41d_cmd, modem_log)
        if fresh:
            print("Fresh EEPROM: waiting for the defaults, then rebooting once", flush=True)
            deadline = time.monotonic() + 180
            while not _eeprom_initialised(args.eeprom) and time.monotonic() < deadline:
                time.sleep(2)
            time.sleep(5)
            modem.terminate()
            modem.wait()
            modem = _boot(mon, resc, fc41d_cmd, modem_log)
        with (args.work / "can_peers.log").open("ab") as out:
            peers = subprocess.Popen(peers_cmd, stdout=out, stderr=subprocess.STDOUT)
        mini = _start_mini_peers(board, args.work)
        print(
            f"Running {args.blob.name} as {args.ip}. Local API on UDP {args.local_api_port};"
            f" modem log {modem_log}",
            flush=True,
        )
        mon.close()
        while True:
            try:
                renode.wait(timeout=30)
                break
            except subprocess.TimeoutExpired:
                if args.log_max_mb:
                    _rotate_logs(args.work, int(args.log_max_mb * 1024 * 1024))
        print(f"Renode exited with {renode.returncode}", flush=True)
        return 1
    except KeyboardInterrupt:
        pass
    finally:
        for child in (mini, peers, modem):
            if child is not None:
                child.terminate()
        if renode.poll() is None:
            os.killpg(renode.pid, signal.SIGTERM)
    return 0


if __name__ == "__main__":
    sys.exit(main())
