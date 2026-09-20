---
"ha-marstek-release-tools": patch
---

Keep a failed status read from ending a parallel poll early and leaving its sibling reads sending to a device whose poll is already over.
