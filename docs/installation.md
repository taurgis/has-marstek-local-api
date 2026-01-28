# Installation

## HACS (recommended)

1. Open **HACS** in Home Assistant.
2. **Custom repositories** → add this repository as **Integration**.
3. Search for **Marstek** and install.
4. Restart Home Assistant.

## Manual

1. Copy `custom_components/marstek` into your HA config folder as `config/custom_components/marstek`.
2. Restart Home Assistant.

## Requirements checklist

- Home Assistant Core **2025.10+**
- Device and HA on the **same LAN segment**
- **Open API enabled** in the Marstek app
- UDP **port 30000** reachable
