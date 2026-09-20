#!/usr/bin/env python3
"""Verify battery power logic against a real device."""

import asyncio
import json
import socket


async def query_es_status(host: str = "192.168.0.152", port: int = 30000) -> None:
    """Query ES.GetStatus and verify our battery power logic."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)

    request = {"id": 1, "method": "ES.GetStatus", "params": {"id": 0}}
    message = json.dumps(request).encode()
    sock.sendto(message, (host, port))
    print(f"Querying ES.GetStatus at {host}:{port}...")
    print(f"Request: {json.dumps(request)}")
    print()

    loop = asyncio.get_running_loop()
    try:
        data, _addr = await asyncio.wait_for(loop.sock_recvfrom(sock, 4096), timeout=5)
        response = json.loads(data.decode())
        result = response.get("result", {})

        print("=== RAW API RESPONSE ===")
        print(json.dumps(response, indent=2))
        print()

        # Extract values
        pv_power = result.get("pv_power", 0)
        ongrid_power = result.get("ongrid_power", 0)
        bat_power = result.get("bat_power")
        bat_soc = result.get("bat_soc", 0)

        print("=== EXTRACTED VALUES ===")
        print(f"pv_power: {pv_power}")
        print(f"ongrid_power: {ongrid_power}")
        print(f"bat_power (from API): {bat_power}")
        print(f"bat_soc: {bat_soc}%")
        print()

        # Apply our logic
        print("=== APPLYING OUR LOGIC ===")
        if bat_power is None and "bat_power" not in result:
            raw_bat_power = pv_power - ongrid_power
            print(
                f"bat_power missing - using fallback: "
                f"pv_power - ongrid_power = {pv_power} - {ongrid_power} = {raw_bat_power}"
            )
        else:
            raw_bat_power = bat_power if bat_power is not None else 0
            print(f"bat_power from API: {raw_bat_power}")

        # Negate for HA convention
        battery_power = -raw_bat_power
        print(f"HA battery_power (negated): -{raw_bat_power} = {battery_power}")
        print()

        # Determine status
        if battery_power > 0:
            status = "discharging"
        elif battery_power < 0:
            status = "charging"
        else:
            status = "idle"

        print("=== FINAL HA VALUES ===")
        print(f"battery_power: {battery_power} W")
        print(f"battery_status: {status}")
        print()

        # Sanity check
        print("=== SANITY CHECK ===")
        if ongrid_power > 0:
            print(f"ongrid_power={ongrid_power} > 0 -> Device is EXPORTING to grid")
            if pv_power == 0:
                print("pv_power=0 -> No solar, so battery must be DISCHARGING")
                if status == "discharging":
                    print("✅ CORRECT: battery_status is discharging")
                else:
                    print(f"❌ WRONG: battery_status should be discharging, got {status}")
            else:
                print(f"pv_power={pv_power} -> Solar is producing")
                if pv_power > ongrid_power:
                    print("PV > grid export -> excess charges battery (should be charging)")
                    if status == "charging":
                        print("✅ CORRECT: battery_status is charging")
                    else:
                        print(f"❌ WRONG: battery_status should be charging, got {status}")
                else:
                    print("PV <= grid export -> battery helping export (should be discharging)")
                    if status == "discharging":
                        print("✅ CORRECT: battery_status is discharging")
                    else:
                        print(f"❌ WRONG: expected discharging, got {status}")
        elif ongrid_power < 0:
            print(f"ongrid_power={ongrid_power} < 0 -> Device is IMPORTING from grid")
            print("Grid import typically means battery is CHARGING")
            if status == "charging":
                print("✅ CORRECT: battery_status is charging")
            else:
                print(f"Note: battery_status is {status} (might be correct if PV is producing)")
        else:
            print("ongrid_power=0 -> No grid exchange")

    except TimeoutError:
        print("Timeout - no response from device")
    finally:
        sock.close()


if __name__ == "__main__":
    import sys

    host = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.152"
    asyncio.run(query_es_status(host))
