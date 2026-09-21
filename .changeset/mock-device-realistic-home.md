---
"ha-marstek-release-tools": patch
---

Make the mock devices emit physically realistic values: report `ongrid_power` as the inverter's own AC port instead of the meter reading, regulate Auto mode as a closed loop on the simulated P1/CT, and give the simulated home a two-peak load curve and a rooftop PV array.
