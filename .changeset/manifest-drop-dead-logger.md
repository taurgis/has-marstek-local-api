---
"ha-marstek-release-tools": patch
---

Drop the unused `marstek` logger name from the manifest, so enabling debug logging no longer targets a namespace the integration never writes to.
