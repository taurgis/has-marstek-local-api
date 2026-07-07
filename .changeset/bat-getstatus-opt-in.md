---
"ha-marstek-release-tools": patch
---

Make the Bat.GetStatus polling call opt-in: the battery detail entities (temperature, remaining/rated capacity, charge/discharge permission) are now disabled by default, and the integration only sends Bat.GetStatus while at least one of them is enabled. The call is suspected to trigger spontaneous device resets on some Marstek firmwares (#14). Existing installations keep their currently enabled entities; disable the battery detail entities manually to stop the call.
