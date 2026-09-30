---
"ha-marstek-release-tools": patch
---

Fix Venus E mini battery capacity by reading `ES.GetStatus` `bat_cap` as the remaining energy, so a drained pack reads 0 Wh and no misleading total-capacity sensor is created.
