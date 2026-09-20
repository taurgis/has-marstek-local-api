---
"ha-marstek-release-tools": patch
---

Report a numeric sensor as unknown when firmware answers with a placeholder string or a boolean instead of a number, so a reading is no longer dropped with a traceback on every poll, while a number the device quoted is still read as a number.
