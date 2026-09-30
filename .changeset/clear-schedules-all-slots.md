---
"ha-marstek-release-tools": patch
---

Fix `marstek.clear_manual_schedules` stopping at the first slot that failed, so the remaining slots are still cleared and the failed slots are reported together.
