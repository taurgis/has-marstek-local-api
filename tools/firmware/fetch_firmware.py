#!/usr/bin/env python3
"""Download catalogued Marstek firmware images and verify SHA-256."""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

CATALOG = Path(__file__).resolve().parent / "catalog.json"
BLOBS = Path(__file__).resolve().parent / "blobs"
USER_AGENT = "has-marstek-local-api-firmware-fetch/1.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, dest: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        dest.write_bytes(response.read())


def _header_error(image: dict[str, object], dest: Path) -> str | None:
    """Return why a catalogued 32-byte OTA header does not match, or None.

    Venus E mini images (VNSEMINI-0) start with an ASCII header whose embedded
    size is the payload after the header. The SHA-256 covers the whole file;
    this check stops a headerless blob with a colliding hash from being
    treated as loadable firmware.
    """
    raw_size = image.get("headerSize")
    if not raw_size:
        return None
    header_size = int(raw_size) if isinstance(raw_size, int) else 0
    data = dest.read_bytes()
    if header_size <= 0 or len(data) < header_size:
        return f"header shorter than {header_size} bytes"
    header = data[:header_size]
    expected = image.get("header")
    if isinstance(expected, str) and header.decode("ascii", "replace") != expected:
        return f"header {header!r} != {expected!r}"
    payload = image.get("payloadSize")
    if isinstance(payload, int) and len(data) - header_size != payload:
        return f"payload size {len(data) - header_size} != {payload}"
    return None


def main() -> int:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    BLOBS.mkdir(parents=True, exist_ok=True)
    failed = 0
    for image in catalog["images"]:
        dest = BLOBS / image["filename"]
        expected = image["sha256"]
        if dest.exists() and _sha256(dest) == expected:
            header_error = _header_error(image, dest)
            if header_error is not None:
                print(f"fail {dest.name}: {header_error}", file=sys.stderr)
                failed += 1
                continue
            print(f"ok   {dest.name} (cached)")
            continue
        url = image["url"]
        print(f"get  {dest.name}")
        try:
            _download(url, dest)
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            print(f"fail {dest.name}: {err}", file=sys.stderr)
            failed += 1
            continue
        actual = _sha256(dest)
        if actual != expected:
            print(
                f"fail {dest.name}: sha256 {actual} != {expected}",
                file=sys.stderr,
            )
            dest.unlink(missing_ok=True)
            failed += 1
            continue
        header_error = _header_error(image, dest)
        if header_error is not None:
            print(f"fail {dest.name}: {header_error}", file=sys.stderr)
            failed += 1
            continue
        print(f"ok   {dest.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
