"""Run configuration for one conversion, and colour parsing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from asciify_video.constants import DEFAULT

#: An ``(red, green, blue)`` colour with 0-255 components.
RGB = tuple[int, int, int]


def color_as_rgb(hex_color: str) -> RGB:
    """Parse a CSS-style hex colour into an RGB tuple.

    Only the first six hex digits are read, so trailing characters (such
    as an alpha component) are ignored.

    Args:
        hex_color: Colour as ``'#RRGGBB'``; the leading ``#`` is optional.

    Returns:
        The ``(red, green, blue)`` components, each 0-255.

    Raises:
        ValueError: If the string has fewer than six digits or contains
            non-hexadecimal characters.

    Example:
        >>> color_as_rgb('#64FF64')
        (100, 255, 100)
    """
    color_hex = hex_color.lstrip('#')
    return (
        int(color_hex[0:2], 16),
        int(color_hex[2:4], 16),
        int(color_hex[4:6], 16),
    )


@dataclass
class ConversionConfig:
    """All knobs for one conversion run.

    Built by the CLI from parsed arguments, or directly for programmatic
    use (tests, embedding). Values arrive pre-normalized: colours are RGB
    tuples, alpha is clamped to [0, 1], and ``target_resolution`` is a
    ``(width, height)`` pair. :func:`~asciify_video.pipeline.run_conversion`
    performs no validation of its own.

    Attributes:
        input: Source video file.
        output: File to write. Must end in ``.webm`` when
            ``background_alpha`` is below 1.
        no_key: Skip chromakeying; use for footage already on black.
        key_color: Key colour as an ffmpeg hex string such as
            ``'0x00B140'``. ``None`` means detect it from the footage.
        detect_green: Detect the key colour even if ``key_color`` is set.
        tolerance: Chromakey similarity threshold, 0-1.
        edge_blending: Chromakey edge blend amount, 0-1.
        sample_region: Where key detection samples: ``'corners'``,
            ``'center'``, ``'full'``, or a ``'WxH+X+Y'`` crop in source
            pixels.
        ascii_width: Character columns. ``None`` derives the width as
            output width // ``scale_divisor``.
        scale_divisor: Divisor used when ``ascii_width`` is ``None``; it
            is also the resulting cell width in pixels. Must be positive.
        ramp: ``'jp2a'``, or a custom string of glyphs ordered dark to
            bright. ``None`` selects the built-in full-density ramp.
        hist_stretch: Stretch luminance over the whole clip before
            mapping to glyphs.
        fps_override: Output frame rate. ``None`` keeps the source rate.
        render_font_size: Point size at which glyphs are rasterized
            before being scaled to the cell; higher is sharper.
        colorized: Colour each glyph from the source frame instead of
            using ``font_color_rgb``.
        font_color: The font colour as the user wrote it, kept only for
            the state record.
        font_color_rgb: Glyph colour when not ``colorized``.
        leave_black_empty: Draw nothing in cells darker than
            ``black_threshold``.
        black_threshold: Luminance (0-255) below which a cell counts as
            black.
        background_color: The background colour as the user wrote it,
            kept only for the state record.
        background_rgb: Canvas colour.
        background_alpha: Canvas opacity, 0 (transparent) to 1 (opaque).
            Anything below 1 produces VP9/WebM with an alpha channel.
        target_resolution: Output ``(width, height)`` in pixels. ``None``
            uses the source dimensions.
        jobs: Worker processes for convert+render. ``None`` uses the
            CPU count minus one.
        vpx_preset: VP9 speed preset for alpha output, from
            ``'ultrafast'`` to ``'slow'``.
        keep_temp: Keep the temp directory and record it in the state
            file so later runs can reuse its artifacts.
        silent_progress: Suppress progress bars.
        reuse_level: ``'extracted'``, ``'ascii'`` or ``'final'`` to
            reuse artifacts from ``reuse_dir``; ``None`` for a fresh run.
        reuse_dir: Existing temp directory to reuse. Required when
            ``reuse_level`` is set; the caller resolves and validates it.
    """

    input: Path
    output: Path

    # --- keying ---
    no_key: bool = False
    key_color: str | None = None
    detect_green: bool = False
    tolerance: float = DEFAULT['TOLERANCE']
    edge_blending: float = DEFAULT['EDGE-BLEEDING']
    sample_region: str = DEFAULT['SAMPLE-REGION']

    # --- grid / conversion ---
    ascii_width: int | None = None
    scale_divisor: int = DEFAULT['SCALE-DIVISOR']
    ramp: str | None = None
    hist_stretch: bool = False
    fps_override: float | None = None

    # --- rendering ---
    render_font_size: int = DEFAULT['RASTERIZATION-QUALITY']
    colorized: bool = False
    font_color: str | None = None
    font_color_rgb: RGB = color_as_rgb(DEFAULT['FONT-COLOR'])
    leave_black_empty: bool = False
    black_threshold: int = DEFAULT['BLACK-THRESHOLD']
    background_color: str = DEFAULT['BACKGROUND-COLOR']
    background_rgb: RGB = color_as_rgb(DEFAULT['BACKGROUND-COLOR'])
    background_alpha: float = DEFAULT['BACKGROUND-ALPHA']
    target_resolution: tuple[int, int] | None = None
    jobs: int | None = None
    vpx_preset: str = DEFAULT['VPX-PRESET']

    # --- run control ---
    keep_temp: bool = False
    silent_progress: bool = False
    reuse_level: str | None = None
    reuse_dir: Path | None = None
