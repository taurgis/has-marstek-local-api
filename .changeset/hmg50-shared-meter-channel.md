---
"ha-marstek-release-tools": patch
---

Keep Open API traffic minimal on HMG-50 Control (Venus C 2.0), where the Local API shares one Wi-Fi receive channel with the device's own UDP meter client (a Marstek CT or a Shelly): parallel requests and Wi-Fi retransmits now stay off on every HMG-50 build including 156, and a repair warning explains why Self-consumption charging can stall.
