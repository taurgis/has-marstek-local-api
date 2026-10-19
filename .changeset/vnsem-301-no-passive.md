---
"ha-marstek-release-tools": patch
---

Fix Passive mode on Venus E mini firmware 301 by hiding it from the operating-mode select and refusing the Set passive mode action, since that firmware has no Passive mode and only answers with a parse error.
