"""Cross-platform monospace font discovery."""

from __future__ import annotations

import glob
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import ImageFont

# Preferred fonts, tried in order: Linux, Windows, then macOS locations.
_CANDIDATES = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',
    '/usr/share/fonts/TTF/DejaVuSansMono.ttf',
    '/usr/share/fonts/liberation/LiberationMono-Regular.ttf',
    'C:/Windows/Fonts/consola.ttf',
    '/System/Library/Fonts/Supplemental/Courier New.ttf',
    '/System/Library/Fonts/Menlo.ttc',
    '/System/Library/Fonts/SFNSMono.ttf',
]

# Searched when no preferred font exists; any TrueType font beats the bitmap default.
_SEARCH_PATTERNS = [
    '/System/Library/Fonts/**/*.tt[fc]', '/Library/Fonts/**/*.tt[fc]',
    '/usr/share/fonts/**/*.tt[fc]', '/usr/local/share/fonts/**/*.tt[fc]',
]

_MAX_SEARCHED_PER_PATTERN = 50


def load_monospace_font(
    font_size: int = 12,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Find a usable font, preferring well-known monospace faces.

    Tries a list of known monospace fonts, then any TrueType font in the
    system font directories, and finally Pillow's built-in font. A glyph
    is rasterized once and scaled to the cell, so a proportional
    fallback still yields an aligned grid.

    Args:
        font_size: Point size to load the font at.

    Returns:
        A Pillow font object. Never raises: the built-in font is always
        available as a last resort.
    """
    from PIL import ImageFont

    for fp in _CANDIDATES:
        try:
            return ImageFont.truetype(fp, font_size)
        except OSError:
            continue

    for pat in _SEARCH_PATTERNS:
        for fp in sorted(glob.glob(pat, recursive=True))[:_MAX_SEARCHED_PER_PATTERN]:
            try:
                return ImageFont.truetype(fp, font_size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=font_size)
    except TypeError:
        # Pillow < 10.1 has no size parameter
        return ImageFont.load_default()
