---
"ha-marstek-release-tools": patch
---

Fix setup and polling by sending UDP from each device's configured Open API port so firmware that replies there can answer, including mixed custom ports. Reuse the existing UDP client for same-port GetDevice during manual add and Confirm device instead of binding a second socket.
