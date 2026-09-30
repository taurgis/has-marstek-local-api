---
"ha-marstek-release-tools": patch
---

Fix the derived battery power on devices that omit `bat_power` (Venus A and Venus E) ignoring the off-grid (EPS) socket load, which overstated charging and understated discharging.
