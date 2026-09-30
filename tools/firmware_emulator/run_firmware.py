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
catalog's device type selects it. The stack pointer and entry point come from each
image's vector table.

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

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
DEFAULT_BLOB = REPO / "tools/firmware/blobs/VNSEE3-0_app_0150_0804_151249.bin"
CATALOG = REPO / "tools/firmware/catalog.json"
UART_PORT = 3456
CT_PORT = 12345  # CT003 meter protocol (AstraMeter listens here)
CAN_PORT = 3457
METER_PORT = 8099

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
{tuning}"""


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


BOARDS = {
    # GD32F30x-class; the Control images sit above an 18 KiB bootloader.
    "control": Board(
        repl="control.repl.in",
        app_base=0x08004800,
        wifi_uart="wifi_uart",
        can="can1",
        uarts=tuple(f"usart{n}" for n in range(1, 6)),
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
    """Initial SP and reset vector: the first two words of the image."""
    sp, pc = struct.unpack("<II", blob.read_bytes()[:8])
    return sp, pc


def _catalog_blob(spec: str) -> Path:
    """Resolve ``DEVICE:VERSION`` (e.g. ``VNSA-0:150``) through the catalog."""
    device, _, version = spec.partition(":")
    for entry in json.loads(CATALOG.read_text())["images"]:
        if entry["deviceType"] == device and str(entry["version"]) == version:
            return CATALOG.parent / "blobs" / entry["filename"]
    raise SystemExit(f"{spec} is not in {CATALOG}")


def _device_type(args: argparse.Namespace) -> str:
    """Catalog device type of the image, from --firmware or the blob filename."""
    if args.firmware:
        return str(args.firmware.partition(":")[0])
    return str(args.blob.name.split("_", 1)[0])


def _board_for(args: argparse.Namespace) -> str:
    """``--board``, else HMG-50 for its catalog entries, else the Control board."""
    if args.board:
        return str(args.board)
    if args.firmware and args.firmware.partition(":")[0] == "HMG-50":
        return "hmg50"
    for entry in json.loads(CATALOG.read_text())["images"]:
        if entry["filename"] == args.blob.name and entry["deviceType"] == "HMG-50":
            return "hmg50"
    return "control"


def _uid_words(ble_mac: str) -> tuple[int, int, int]:
    """A stable per-device UID, so each emulated unit reports its own ``uid=``."""
    mac = bytes.fromhex(ble_mac.replace(":", ""))
    return 0x00470031, int.from_bytes(mac[:4], "little"), int.from_bytes(mac[4:] + b"PH", "little")


def _render(args: argparse.Namespace, work: Path, board: Board) -> Path:
    renode_dir = HERE / "renode"
    repl = (renode_dir / board.repl).read_text()
    for key, value in {
        "@RENODE_DIR@": str(renode_dir),
        "@EEPROM@": str(args.eeprom),
        "@LOCAL_API_PORT@": str(args.local_api_port),
        "@CAN_PORT@": str(args.can_port),
    }.items():
        repl = repl.replace(key, value)
    name = board.repl.removesuffix(".repl.in")
    (work / f"{name}.repl").write_text(repl)
    usarts = "\n".join(f"{u} CreateFileBackend @{work}/{u}.txt true" for u in board.uarts)
    sp, pc = _vector_table(args.blob)
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
        blob=args.blob,
        base=board.app_base,
        boot="\n".join(boot),
        sp=sp,
        pc=pc,
        usarts=usarts,
        uart_port=args.uart_port,
        wifi_uart=board.wifi_uart,
        can=board.can,
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


def main() -> int:
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
    args = parser.parse_args()

    if not args.renode:
        parser.error("--renode (or $RENODE) is required")
    if args.ip == "auto":
        args.ip = _own_ip(args.gateway)
    if args.firmware:
        args.blob = _catalog_blob(args.firmware)
    if not args.blob.is_file():
        parser.error(f"firmware blob not found: {args.blob} (run tools/firmware/fetch_firmware.py)")
    board = BOARDS[_board_for(args)]
    args.work.mkdir(parents=True, exist_ok=True)
    args.eeprom = (args.eeprom or args.work / "eeprom.bin").resolve()
    resc = _render(args, args.work, board)
    fc41d_cmd = [
        sys.executable, str(HERE / "fc41d.py"), "--uart-port", str(args.uart_port),
        "--ip", args.ip, "--gateway", args.gateway, "--ble-mac", args.ble_mac,
        "--bind-ip", args.bind_ip, "--dialect", board.dialect,
        "--bssid", args.bssid or args.wifi_mac, "-v",
    ]  # fmt: skip
    if args.ct_loopback:
        fc41d_cmd += ["--loopback-port", str(CT_PORT)]
    if args.broadcast_to:
        fc41d_cmd += ["--broadcast-to", args.broadcast_to]
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
    modem_log = args.work / "fc41d.log"

    renode = subprocess.Popen(
        [args.renode, "--disable-gui", "-P", str(args.monitor_port)],
        stdout=(args.work / "renode.log").open("ab"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    modem: subprocess.Popen[bytes] | None = None
    peers: subprocess.Popen[bytes] | None = None
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        mon = Monitor(args.monitor_port)
        fresh = not _eeprom_initialised(args.eeprom)
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
        for child in (peers, modem):
            if child is not None:
                child.terminate()
        if renode.poll() is None:
            os.killpg(renode.pid, signal.SIGTERM)
    return 0


if __name__ == "__main__":
    sys.exit(main())
