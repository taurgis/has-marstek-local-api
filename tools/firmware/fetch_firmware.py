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


def main() -> int:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    BLOBS.mkdir(parents=True, exist_ok=True)
    failed = 0
    for image in catalog["images"]:
        dest = BLOBS / image["filename"]
        expected = image["sha256"]
        if dest.exists() and _sha256(dest) == expected:
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
        print(f"ok   {dest.name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
