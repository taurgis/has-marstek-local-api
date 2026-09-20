---
"ha-marstek-release-tools": patch
---

Send every device write through one retry loop and one polling-pause helper, and share the SYS entity, schema and command-builder code that the number, switch, select, service and device-action paths had each copied, so a fix to the write path now reaches all of them.
