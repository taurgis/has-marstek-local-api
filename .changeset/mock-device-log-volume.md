---
"ha-marstek-release-tools": patch
---

Stop the mock device answering JSON-RPC replies, so a shared UDP port can no longer spin it into a packet storm that fills the disk with log output.
