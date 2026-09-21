---
"ha-marstek-release-tools": patch
---

Keep the integration's shared state behind one typed `HassKey` so the UDP client pool, its locks and the lease bookkeeping are reached by attribute instead of by string key.
