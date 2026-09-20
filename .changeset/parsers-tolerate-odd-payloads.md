---
"ha-marstek-release-tools": patch
---

Fix a poll cycle aborting when a device answers with an unexpected payload shape by making every response parser return defaults instead of raising.
