# Marstek firmware research

Issue [#15](https://github.com/taurgis/has-marstek-local-api/issues/15) (random
Local API disable / settings wipe while Home Assistant polls) is a **Control
firmware** defect. Venus E 3.0 Control **150** is the vendor fix
(“Optimized Local API send anomaly on Ethernet”), confirmed on the issue.

`catalog.json` now lists every Control image hashed from
[rweijnen/marstek-firmware-archive](https://github.com/rweijnen/marstek-firmware-archive)
and [sphings79/marstek-firmware-archiv](https://github.com/sphings79/marstek-firmware-archiv).
The images themselves are committed under `blobs/`, so the emulator and the
research here do not depend on those archives staying online.

Issue [#82](https://github.com/taurgis/has-marstek-local-api/issues/82)
(Venus C 2.0 stops self-consumption charging while Home Assistant polls) is a
second, separate Control defect: HMG-50 shares one Wi-Fi receive channel
between the Local API server and the device's own UDP meter client. No
HMG-50 build fixes it, 156 included. See `HMG50_METER_CHANNEL.md`.

| File | Purpose |
|------|---------|
| `ANALYSIS.md` | What 144 / 1476 / 148 / 150 / 151 contain and what 150 and 151 changed |
| `HMG50_METER_CHANNEL.md` | Why Open API polling costs HMG-50 (Venus C 2.0 / E 2.0) its meter |
| `WIFI_UDP_RELIABILITY.md` | Why Wi-Fi Open API still times out on 150; RFC/Quectel/HA sources |
| `catalog.json` | SHA-256, OTA URLs, build stamps, initial SP |
| `blobs/` | The catalogued images (vendor copyright, kept for research and emulation) |
| `fetch_firmware.py` | Verify `blobs/` against the catalog; download any image that is missing |

## Verify or refetch

```bash
python3 tools/firmware/fetch_firmware.py
```

Cached images whose SHA-256 matches print `ok … (cached)`; anything missing is
downloaded from its catalog URL. Commit new images after adding them to
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
