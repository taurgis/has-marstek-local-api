---
"ha-marstek-release-tools": patch
---

Listen on every discovery port at once instead of polling them in turn, so a reply is never delayed or dropped by a quiet port, and resolve a hostname once per query rather than blocking the event loop on every received datagram.
