"""Brand icons shipped beside the integration manifest.

Home Assistant 2026.3 and newer read ``custom_components/marstek/brand/``.
The pixels are the icon already published for this domain in
home-assistant/brands ``custom_integrations/marstek``, losslessly optimized.
That set has no separate logo or dark variant, and the brands spec uses the
icon when a logo would be the same square image.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "custom_components" / "marstek" / "brand"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# PNG color type: 2 is RGB, 6 is RGBA. The published icon has no separate plate
# to cut away, so it stays opaque RGB.
_ALLOWED_COLOR_TYPES = {2, 6}


def _png_header(path: Path) -> tuple[int, int, int]:
    """Return width, height, and color type from a PNG IHDR chunk."""
    data = path.read_bytes()
    assert data.startswith(PNG_SIGNATURE), path.name
    assert data[12:16] == b"IHDR", path.name
    width, height, _depth, color_type = struct.unpack(">IIBB", data[16:26])
    return width, height, color_type


def test_brand_icons_have_the_required_pixel_sizes() -> None:
    """HACS asks for a 256 icon and a 512 high-DPI icon, both square PNGs."""
    icon_w, icon_h, icon_color = _png_header(BRAND / "icon.png")
    hidpi_w, hidpi_h, hidpi_color = _png_header(BRAND / "icon@2x.png")

    assert (icon_w, icon_h) == (256, 256)
    assert (hidpi_w, hidpi_h) == (512, 512)
    assert icon_color in _ALLOWED_COLOR_TYPES
    assert hidpi_color in _ALLOWED_COLOR_TYPES
    assert (BRAND / "icon.png").read_bytes() != (BRAND / "icon@2x.png").read_bytes()


def test_hacs_release_archive_includes_the_brand_folder() -> None:
    """Without zip_release, HACS installs the tag archive under custom_components.

    A separate release zip could omit ``brand/`` while the Git tree still
    passed the brands check. This integration does not use that option.
    """
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert "zip_release" not in hacs
    assert "filename" not in hacs
    assert (ROOT / "custom_components" / "marstek" / "manifest.json").is_file()
