---
"ha-marstek-release-tools": patch
---

Fix the Bluetooth advertising switch sending the inverted `Ble.Adv` value, so turning it on stopped advertising; the firmware uses 1 to enable and 0 to disable, the reverse of the Open API PDF.
