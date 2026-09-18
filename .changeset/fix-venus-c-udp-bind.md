---
"ha-marstek-release-tools": patch
---

Fix setup and polling by sending UDP from each device's configured Open API port so firmware that replies there can answer, including mixed custom ports. Pause pooled listeners during manual add and Confirm device so a second same-port device can be queried.
