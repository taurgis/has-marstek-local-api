# Marstek firmware research

Issue [#15](https://github.com/taurgis/has-marstek-local-api/issues/15) (random
Local API disable / settings wipe while Home Assistant polls) is a **Control
firmware** defect. Venus E 3.0 Control **150** is the vendor fix
(“Optimized Local API send anomaly on Ethernet”), confirmed on the issue.

This directory stores **analysis and hashes only**. The `.bin` images are
Marstek/Hamedata copyright and are not checked into git.

| File | Purpose |
|------|---------|
| `ANALYSIS.md` | What 144 / 1476 / 148 / 150 contain and what 150 changed |
| `catalog.json` | SHA-256, OTA URLs, build stamps, initial SP |
| `fetch_firmware.py` | Download catalog images into `blobs/` and verify hashes |

## Fetch locally

```bash
python3 tools/firmware/fetch_firmware.py
```

Files land in `tools/firmware/blobs/` (gitignored). Re-run after changing
`catalog.json`.

Upstream copies:

- Community archive: https://github.com/rweijnen/marstek-firmware-archive
- Firmware checker: https://rweijnen.github.io/marstek-fw-checker/
- Official EU OTA CDN: https://static-eu.marstekenergy.com/

## Encoding trap: 1476 is 147.6

`VNSEE3-0_app_1476_*.bin` reports Open API `ver` **1476**. That is app
firmware **147.6** (March 2026), **older** than 148 and 150. The integration
folds 1000–1999 down to `ver // 10` for capability and safety gates so 1476
does not unlock SYS/UPS or skip the reset warning.
