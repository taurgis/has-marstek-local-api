#!/usr/bin/env python3
"""Capture responses from a real Marstek device to improve mock data.

This script queries a real device and saves its responses, which can be used
to update the mock device with realistic data.
"""

import argparse
import asyncio
import json
import socket
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom_components.marstek.firmware_profile import (  # noqa: E402
    extract_discovery_version,
    resolve_firmware_profile,
)


# Methods to capture from the device
# Note: ES.* and PV.* methods use {"id": 0} as params
# Important: Device needs ~10 seconds between requests for stability
METHODS_TO_CAPTURE = [
    ("Marstek.GetDevice", {"ble_mac": "0"}),
    ("ES.GetMode", {"id": 0}),
    ("ES.GetStatus", {"id": 0}),
    ("PV.GetStatus", {"id": 0}),
    ("Wifi.GetStatus", {"id": 0}),
    ("EM.GetStatus", {"id": 0}),
    ("Bat.GetStatus", {"id": 0}),
    ("BLE.GetStatus", {"id": 0}),
]

# Delay between requests in seconds (device is unstable with fast requests)
REQUEST_DELAY = 10.0
_SAFE_JSON_RPC_ID = 1


async def send_request(
    host: str,
    port: int,
    method: str,
    params: dict[str, Any],
    timeout: float = 5.0,
    *,
    request_id: int,
) -> dict[str, Any] | None:
    """Send a UDP request and wait for a response.

    Bind the Open API listen port: firmware replies there, not to an
    ephemeral source port. Discovery may use JSON-RPC id 0; other methods
    use 1..65535 so the MCU id cannot wrap to the parse-error id.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.setblocking(False)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as err:
        print(f"Failed to bind UDP port {port}: {err}")
        sock.close()
        return None

    wire_id = 0 if method == "Marstek.GetDevice" else request_id
    request = {"id": wire_id, "method": method, "params": params}
    message = json.dumps(request).encode()

    loop = asyncio.get_running_loop()

    try:
        sock.sendto(message, (host, port))

        start = loop.time()
        while (loop.time() - start) < timeout:
            try:
                data, _addr = await asyncio.wait_for(
                    loop.sock_recvfrom(sock, 4096), timeout=0.5
                )
                if not data:
                    continue
                try:
                    response = json.loads(data.decode())
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue

                # Skip echoes (have method+params, no result)
                if "result" in response:
                    return response
                if "method" in response and "params" in response:
                    continue

            except TimeoutError:
                continue

    except OSError as err:
        print(f"Socket error: {err}")
    finally:
        sock.close()

    return None


def _methods_for_profile(
    include_bat_status: bool, reset_prone: bool
) -> list[tuple[str, dict[str, Any]]]:
    """Skip Bat.GetStatus on reset-prone firmware unless explicitly requested."""
    methods = list(METHODS_TO_CAPTURE)
    if include_bat_status or not reset_prone:
        return methods
    return [item for item in methods if item[0] != "Bat.GetStatus"]


async def capture_device_data(
    host: str,
    port: int = 30000,
    *,
    include_bat_status: bool = False,
) -> dict[str, Any]:
    """Capture all relevant data from a Marstek device."""
    print(f"Capturing data from {host}:{port}...")
    print(f"Note: Using {REQUEST_DELAY}s delay between requests for device stability")
    print("=" * 60)

    captured: dict[str, Any] = {
        "capture_time": datetime.now().isoformat(),
        "device_ip": host,
        "device_port": port,
        "responses": {},
    }

    print("\n📡 Marstek.GetDevice...")
    device_response = await send_request(
        host,
        port,
        "Marstek.GetDevice",
        {"ble_mac": "0"},
        request_id=0,
    )
    captured["responses"]["Marstek.GetDevice"] = device_response
    if device_response:
        print("   ✅ Got response")
        result = device_response.get("result")
        if isinstance(result, dict):
            print(f"   Result: {json.dumps(result, indent=6)}")
    else:
        print("   ❌ No response")

    reset_prone = True
    device_result = (
        device_response.get("result") if isinstance(device_response, dict) else None
    )
    if isinstance(device_result, dict):
        profile = resolve_firmware_profile(
            device_result.get("device"),
            extract_discovery_version(device_result),
        )
        reset_prone = profile.openapi_reset_prone
        print(
            f"   Profile: {profile.family.value} gen={profile.control_generation} "
            f"reset_prone={reset_prone}"
        )
        if reset_prone and not include_bat_status:
            print(
                "   Skipping Bat.GetStatus on reset-prone firmware "
                "(pass --include-bat-status to override)"
            )

    methods = [
        item
        for item in _methods_for_profile(include_bat_status, reset_prone)
        if item[0] != "Marstek.GetDevice"
    ]

    next_id = _SAFE_JSON_RPC_ID
    for method, params in methods:
        print(f"\n⏳ Waiting {REQUEST_DELAY}s before next request...")
        await asyncio.sleep(REQUEST_DELAY)

        print(f"\n📡 {method}...")
        if params:
            print(f"   Params: {params}")
        response = await send_request(host, port, method, params, request_id=next_id)
        next_id = next_id + 1 if next_id < 65535 else 1

        if response:
            print("   ✅ Got response")
            captured["responses"][method] = response
            if "result" in response:
                result = response["result"]
                print(f"   Result: {json.dumps(result, indent=6)}")
        else:
            print("   ❌ No response")
            captured["responses"][method] = None

    return captured


def generate_mock_config(captured: dict[str, Any]) -> str:
    """Generate Python code for mock device configuration."""
    device_resp = captured["responses"].get("Marstek.GetDevice", {})
    status_resp = captured["responses"].get("ES.GetStatus", {})
    mode_resp = captured["responses"].get("ES.GetMode", {})
    pv_resp = captured["responses"].get("PV.GetStatus", {})

    device_result = device_resp.get("result", {}) if device_resp else {}
    status_result = status_resp.get("result", {}) if status_resp else {}
    mode_result = mode_resp.get("result", {}) if mode_resp else {}
    pv_result = pv_resp.get("result", {}) if pv_resp else {}

    code = f'''# Mock device configuration captured from real device
# Captured: {captured["capture_time"]}
# Device: {captured["device_ip"]}:{captured["device_port"]}

DEFAULT_CONFIG = {json.dumps(device_result, indent=4)}

MOCK_STATUS = {json.dumps(status_result, indent=4)}

MOCK_MODE = {json.dumps(mode_result, indent=4)}

MOCK_PV_STATUS = {json.dumps(pv_result, indent=4)}
'''
    return code


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture data from a real Marstek device"
    )
    parser.add_argument("host", help="Device IP address")
    parser.add_argument(
        "--port", type=int, default=30000, help="UDP port (default: 30000)"
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        help="Output JSON file (default: captured_device_<ip>.json)",
    )
    parser.add_argument(
        "--update-mock",
        action="store_true",
        help="Update mock_marstek.py with captured data",
    )
    parser.add_argument(
        "--include-bat-status",
        action="store_true",
        help="Query Bat.GetStatus even on reset-prone firmware (can reboot the device)",
    )
    args = parser.parse_args()

    captured = await capture_device_data(
        args.host, args.port, include_bat_status=args.include_bat_status
    )

    # Determine output file
    if args.output:
        output_file = Path(args.output)
    else:
        safe_ip = args.host.replace(".", "_")
        output_file = Path(__file__).parent / f"captured_device_{safe_ip}.json"

    # Save captured data
    with open(output_file, "w") as f:
        json.dump(captured, f, indent=2)
    print(f"\n💾 Saved captured data to: {output_file}")

    # Generate mock config
    mock_config = generate_mock_config(captured)
    config_file = output_file.with_suffix(".py")
    with open(config_file, "w") as f:
        f.write(mock_config)
    print(f"💾 Saved mock config to: {config_file}")

    if args.update_mock:
        print("\n🔧 Updating mock_marstek.py with captured data...")
        update_mock_device(captured)
        print("✅ Mock device updated!")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    device_info = captured["responses"].get("Marstek.GetDevice", {})
    if device_info and "result" in device_info:
        result = device_info["result"]
        print(f"Device: {result.get('device', 'Unknown')}")
        print(f"Version: {result.get('ver', 'Unknown')}")
        print(f"BLE MAC: {result.get('ble_mac', 'Unknown')}")
        print(f"WiFi MAC: {result.get('wifi_mac', 'Unknown')}")

    status_info = captured["responses"].get("ES.GetStatus", {})
    if status_info and "result" in status_info:
        result = status_info["result"]
        print("\nStatus snapshot:")
        print(f"  SOC: {result.get('soc', 'N/A')}%")
        print(f"  Power: {result.get('power', 'N/A')}W")
        print(f"  Mode: {result.get('mode', 'N/A')}")


def update_mock_device(captured: dict[str, Any]) -> None:
    """Update mock_marstek.py with captured data."""
    mock_file = Path(__file__).parent / "mock_device" / "mock_marstek.py"
    if not mock_file.exists():
        print(f"   ⚠️  Mock file not found: {mock_file}")
        return

    content = mock_file.read_text()

    device_resp = captured["responses"].get("Marstek.GetDevice", {})
    status_resp = captured["responses"].get("ES.GetStatus", {})
    mode_resp = captured["responses"].get("ES.GetMode", {})

    if device_resp and "result" in device_resp:
        result = device_resp["result"]
        new_config = f'''DEFAULT_CONFIG = {{
    "device": "{result.get('device', 'VenusE 3.0')}",
    "ver": {result.get('ver', 145)},
    "ble_mac": "{result.get('ble_mac', '009b08a5aa39')}",
    "wifi_mac": "{result.get('wifi_mac', '7483c2315cf8')}",
    "wifi_name": "{result.get('wifi_name', 'MockNetwork')}",
}}'''
        # Replace DEFAULT_CONFIG
        import re
        content = re.sub(
            r'DEFAULT_CONFIG = \{[^}]+\}',
            new_config,
            content,
            flags=re.DOTALL
        )

    if status_resp and "result" in status_resp:
        result = status_resp["result"]
        # Build status dict with available fields
        status_fields = []
        for key in ["soc", "power", "voltage", "current", "temp", "grid_power",
                    "home_power", "pv_power", "pv1_power", "pv2_power", "mode",
                    "charge_power", "discharge_power"]:
            if key in result:
                val = result[key]
                if isinstance(val, str):
                    status_fields.append(f'    "{key}": "{val}"')
                else:
                    status_fields.append(f'    "{key}": {val}')

        if status_fields:
            new_status = "MOCK_STATUS = {\n" + ",\n".join(status_fields) + ",\n}"
            import re
            content = re.sub(
                r'MOCK_STATUS = \{[^}]+\}',
                new_status,
                content,
                flags=re.DOTALL
            )

    if mode_resp and "result" in mode_resp:
        result = mode_resp["result"]
        mode_fields = []
        for key, default in [("mode", "auto_cfg"), ("passive_power", 0), ("passive_duration", 0)]:
            val = result.get(key, default)
            if isinstance(val, str):
                mode_fields.append(f'    "{key}": "{val}"')
            else:
                mode_fields.append(f'    "{key}": {val}')

        new_mode = "MOCK_MODE = {\n" + ",\n".join(mode_fields) + ",\n}"
        import re
        content = re.sub(
            r'MOCK_MODE = \{[^}]+\}',
            new_mode,
            content,
            flags=re.DOTALL
        )

    mock_file.write_text(content)


if __name__ == "__main__":
    asyncio.run(main())
