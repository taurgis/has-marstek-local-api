---
"ha-marstek-release-tools": patch
---

Align the config flow, runtime data and setup errors with Home Assistant core review conventions: the manual step now collects errors and shows one form at the end like the other steps, config_flow.py lists its steps in walk-through order with the helpers below them, runtime data is frozen, and setup and polling failures carry translated messages.
