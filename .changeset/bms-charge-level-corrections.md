---
"ha-marstek-release-tools": minor
---

Detect when the battery's BMS corrects its charge level near 100% (a jump, a creep or a hold at 99%), report it on the battery health card with the counting-drift bound it implies, lower the capacity confidence when that drift is large, and restart the smart-plug round trip after a correction.
