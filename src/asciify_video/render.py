"""Frame-to-ASCII conversion and glyph rendering.

Conversion and rendering are *fused*: each worker process turns one
extracted frame into ASCII text and immediately draws that text to a
PNG, so the text never has to be written to disk between the two steps.

The work is split in two stages per frame:

* **Stage A** maps luminance to characters (or reads previously saved
  text when reusing).
* **Stage B** draws one pre-rasterized glyph per character cell onto a
  canvas of the target size.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from tqdm import tqdm

from asciify_video.config import RGB, color_as_rgb
from asciify_video.constants import DEFAULT
from asciify_video.ffmpeg import StrPath
from asciify_video.fonts import load_monospace_font

if TYPE_CHECKING:
    from PIL import Image, ImageFont

#: Global ``(low, high)`` luminance bounds that are stretched to 0-255.
Stretch = tuple[int, int]

#: A character cell that receives a glyph: ``(column, row, character)``.
Cell = tuple[int, int, str]

#: Looks up the cell-sized alpha mask for a character.
GlyphSource = Callable[[str], 'Image.Image']


@dataclass(frozen=True)
class GridMetrics:
    """Geometry of the character grid on the output canvas.

    Use :meth:`fit` to derive a consistent set of values.

    Attributes:
        cols: Characters per row.
        rows: Number of text rows.
        cell_w: Width of one character cell in pixels.
        cell_h: Height of one character cell in pixels.
        off_x: Left margin in pixels that centres the grid on the canvas.
        off_y: Top margin in pixels that centres the grid on the canvas.
        target_width: Canvas width in pixels.
        target_height: Canvas height in pixels.
        font_size: Point size glyphs are rasterized at before being
            scaled to the cell.
    """

    cols: int
    rows: int
    cell_w: int
    cell_h: int
    off_x: int
    off_y: int
    target_width: int
    target_height: int
    font_size: int

    @classmethod
    def fit(cls, cols: int, rows: int, target_width: int, target_height: int,
            font_size: int) -> GridMetrics:
        """Size cells to fill the target canvas and centre the grid on it.

        Cells get the largest whole-pixel size that fits, never less
        than 1x1; leftover pixels are split evenly as margins. Because
        the result depends only on the arguments, any two videos with
        the same grid and target size render with identical character
        dimensions.

        Args:
            cols: Characters per row; must be positive.
            rows: Number of text rows; must be positive.
            target_width: Canvas width in pixels.
            target_height: Canvas height in pixels.
            font_size: Glyph rasterization point size.

        Returns:
            The fitted grid geometry.

        Example:
            >>> m = GridMetrics.fit(160, 45, 1920, 1080, font_size=64)
            >>> (m.cell_w, m.cell_h, m.off_x, m.off_y)
            (12, 24, 0, 0)
        """
        cell_w = max(1, target_width // cols)
        cell_h = max(1, target_height // rows)
        return cls(cols=cols, rows=rows, cell_w=cell_w, cell_h=cell_h,
                   off_x=(target_width - cell_w * cols) // 2,
                   off_y=(target_height - cell_h * rows) // 2,
                   target_width=target_width, target_height=target_height,
                   font_size=font_size)


@dataclass(frozen=True)
class FrameTask:
    """Everything one worker needs to turn a frame PNG into a rendered PNG.

    Instances are pickled to worker processes, so every field is plain
    data.

    Attributes:
        frame_path: Extracted frame to read.
        out_path: Rendered PNG to write.
        metrics: Grid geometry.
        ramp: Glyphs ordered dark to bright; unused when ``ascii_path``
            is set.
        stretch: Luminance bounds to stretch to full range before
            mapping, or ``None`` for no stretch.
        ascii_path: Existing text file to render as-is (``--reuse
            ascii``), or ``None`` to convert from the frame's luminance.
            The frame still supplies per-cell colours and the black mask.
        leave_black_empty: Draw nothing in cells darker than
            ``black_threshold``.
        black_threshold: Luminance (0-255) below which a cell is black.
        colorized: Colour each glyph from the frame instead of using
            ``font_color_rgb``.
        bg_color_rgb: Canvas colour.
        bg_alpha: Canvas opacity, 0-1.
        font_color_rgb: Glyph colour when not ``colorized``.
    """

    frame_path: str
    out_path: str
    metrics: GridMetrics
    ramp: str
    stretch: Stretch | None = None
    ascii_path: str | None = None
    leave_black_empty: bool = False
    black_threshold: int = DEFAULT['BLACK-THRESHOLD']
    colorized: bool = False
    bg_color_rgb: RGB = color_as_rgb(DEFAULT['BACKGROUND-COLOR'])
    bg_alpha: float = DEFAULT['BACKGROUND-ALPHA']
    font_color_rgb: RGB = color_as_rgb(DEFAULT['FONT-COLOR'])


def _flattened_pixels(img: Image.Image) -> list[int] | list[tuple[int, ...]]:
    """Version-stable replacement for list(img.getdata()).

    Image.getdata() is deprecated (removal slated for Pillow 14), and
    its suggested replacement, get_flattened_data(), does not exist on
    older Pillow versions. tobytes() is available everywhere and yields
    the same values: a flat sequence of ints for single-channel images,
    interleaved bytes for multi-channel ones.
    """
    data = img.tobytes()
    if img.mode == 'L':
        return list(data)
    n = len(img.getbands())          # 3 for RGB
    return [tuple(data[i:i + n]) for i in range(0, len(data), n)]


def compute_global_stretch(frames: Sequence[StrPath], sample_step: int = 10,
                           percentile: float = 0.5) -> Stretch:
    """Find clip-wide luminance bounds for histogram stretching.

    Using one pair of bounds for every frame avoids the brightness
    "pumping" that per-frame normalization causes. The bounds are
    percentile-clipped so a few stray pixels cannot dominate.

    Args:
        frames: Extracted frame images, in order.
        sample_step: Use every Nth frame.
        percentile: Percentage of pixels to clip at each end.

    Returns:
        ``(low, high)`` luminance bounds, 0-255. Falls back to
        ``(0, 255)`` (no stretch) if there are no frames or the range is
        narrower than 16 levels, where stretching would only amplify
        noise.
    """
    from PIL import Image
    vals: list[int] = []
    for frame in frames[::sample_step]:
        img = Image.open(frame).convert('L')
        vals.extend(list(img.tobytes()))
    if not vals:
        return 0, 255
    vals.sort()
    n = len(vals)
    lo = vals[int(n * percentile / 100)]
    hi = vals[min(n - 1, int(n * (100 - percentile) / 100))]
    if hi - lo < 16:
        lo, hi = 0, 255
    return lo, hi


# --------------------------- Stage A: ASCII text ----------------------------

def _read_ascii_lines(ascii_path: str) -> list[str]:
    """Read saved ASCII text as a list of rows."""
    with open(ascii_path) as f:
        return f.read().rstrip('\n').split('\n')


def _luminance_to_lines(lum: Image.Image, metrics: GridMetrics, ramp: str,
                        stretch: Stretch | None) -> list[str]:
    """Map a grid-resolution luminance image onto ramp characters.

    Args:
        lum: Mode ``'L'`` image exactly ``metrics.cols`` x ``metrics.rows``.
        metrics: Grid geometry.
        ramp: Glyphs ordered dark to bright.
        stretch: Bounds to stretch to 0-255 first, or ``None``.

    Returns:
        One string of ``metrics.cols`` characters per row.
    """
    pixels = list(lum.tobytes())
    if stretch is not None:
        lo, hi = stretch
        span = max(hi - lo, 1)
        pixels = [min(255, max(0, (p - lo) * 255 // span)) for p in pixels]

    scale = len(ramp) / 256
    last = len(ramp) - 1
    cols = metrics.cols
    return [''.join(ramp[min(last, int(p * scale))]
                    for p in pixels[y * cols:(y + 1) * cols])
            for y in range(metrics.rows)]


# ---------------------------- Stage B: rendering ----------------------------

def _glyph_source(font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
                  cell_w: int, cell_h: int) -> GlyphSource:
    """Build a cached lookup from character to cell-sized alpha mask.

    Each glyph is rasterized once at the font's size, cropped to its ink
    box and scaled to exactly fill a cell, so glyphs tile the grid with
    no gaps whatever the font's own metrics.
    """
    from PIL import Image, ImageDraw
    cache: dict[str, Image.Image] = {}

    def get_glyph(ch: str) -> Image.Image:
        if ch not in cache:
            b = font.getbbox(ch)
            gw, gh = max(1, int(b[2] - b[0])), max(1, int(b[3] - b[1]))
            gi = Image.new('L', (gw, gh), 0)
            ImageDraw.Draw(gi).text((-b[0], -b[1]), ch, font=font, fill=255)
            cache[ch] = gi.resize((cell_w, cell_h), Image.Resampling.LANCZOS)
        return cache[ch]
    return get_glyph


def _visible_cells(lines: Sequence[str], lum_grid: Image.Image | None,
                   black_threshold: int) -> Iterator[Cell]:
    """Yield every cell that should receive a glyph.

    Args:
        lines: Text rows, already clipped to the grid.
        lum_grid: Grid-resolution mode ``'L'`` image used to skip dark
            cells, or ``None`` to draw every non-space character.
        black_threshold: Luminance below which a cell is skipped.

    Yields:
        ``(column, row, character)`` for each cell to draw.
    """
    for y, line in enumerate(lines):
        for x, ch in enumerate(line):
            if ch == ' ':
                continue
            if lum_grid is not None \
                    and cast(int, lum_grid.getpixel((x, y))) < black_threshold:
                continue
            yield x, y, ch


def _paint_colorized(canvas: Image.Image, cells: Iterable[Cell],
                     color_grid: Sequence[tuple[int, ...]], metrics: GridMetrics,
                     get_glyph: GlyphSource) -> None:
    """Draw glyphs, each in the colour of its source cell.

    Builds one coverage mask holding every glyph and one colour layer
    with a flat colour per cell, then composites them in a single paste.
    This is far cheaper than tinting and pasting each glyph separately.

    Args:
        canvas: RGBA canvas to draw on, modified in place.
        cells: Cells to draw.
        color_grid: Row-major RGB colour of every grid cell.
        metrics: Grid geometry.
        get_glyph: Glyph mask lookup.
    """
    from PIL import Image
    m = metrics
    grid_w, grid_h = m.cell_w * m.cols, m.cell_h * m.rows
    coverage = Image.new('L', (grid_w, grid_h), 0)
    cell_colors: list[tuple[int, ...]] = [(0, 0, 0)] * (m.cols * m.rows)

    for x, y, ch in cells:
        idx = y * m.cols + x
        cell_colors[idx] = color_grid[idx]
        coverage.paste(get_glyph(ch), (x * m.cell_w, y * m.cell_h))

    color_layer = Image.new('RGB', (m.cols, m.rows), 'black')
    color_layer.putdata(cell_colors)
    color_layer = color_layer.resize((grid_w, grid_h), Image.Resampling.NEAREST)
    canvas.paste(color_layer, (m.off_x, m.off_y), coverage)


def _paint_monochrome(canvas: Image.Image, cells: Iterable[Cell],
                      font_color_rgb: RGB, metrics: GridMetrics,
                      get_glyph: GlyphSource) -> None:
    """Draw glyphs in a single colour.

    Each distinct character is tinted once and the tinted image reused
    for every cell showing it.

    Args:
        canvas: RGBA canvas to draw on, modified in place.
        cells: Cells to draw.
        font_color_rgb: Glyph colour.
        metrics: Grid geometry.
        get_glyph: Glyph mask lookup.
    """
    from PIL import Image
    m = metrics
    tinted: dict[str, Image.Image] = {}
    for x, y, ch in cells:
        if ch not in tinted:
            mask = get_glyph(ch)
            rgba = Image.new('RGBA', mask.size, (0, 0, 0, 0))
            rgba.putalpha(mask)
            solid = Image.new('RGBA', mask.size, (*font_color_rgb, 255))
            rgba.paste(solid, (0, 0), mask)
            tinted[ch] = rgba
        canvas.paste(tinted[ch],
                     (m.off_x + x * m.cell_w, m.off_y + y * m.cell_h),
                     tinted[ch])


def _render_lines(lines: Sequence[str], rgb_small: Image.Image, lum: Image.Image,
                  task: FrameTask) -> Image.Image:
    """Draw ASCII text onto a fresh RGBA canvas at the target size.

    Text wider or taller than the grid is clipped; shorter rows are
    padded. A frame with no visible characters returns the bare
    background without loading a font.

    Args:
        lines: Text rows to draw.
        rgb_small: The frame at grid resolution, for per-cell colours.
        lum: Luminance of ``rgb_small``, for the black mask.
        task: Rendering options and geometry.

    Returns:
        The rendered canvas.
    """
    from PIL import Image
    m = task.metrics
    canvas = Image.new('RGBA', (m.target_width, m.target_height),
                       (*task.bg_color_rgb, int(task.bg_alpha * 255)))
    if not any(line.strip() for line in lines):
        return canvas

    lines = [line.ljust(m.cols)[:m.cols] for line in lines[:m.rows]]
    get_glyph = _glyph_source(load_monospace_font(m.font_size),
                              m.cell_w, m.cell_h)
    cells = _visible_cells(lines, lum if task.leave_black_empty else None,
                           task.black_threshold)
    if task.colorized:
        color_grid = cast('list[tuple[int, ...]]', _flattened_pixels(rgb_small))
        _paint_colorized(canvas, cells, color_grid, m, get_glyph)
    else:
        _paint_monochrome(canvas, cells, task.font_color_rgb, m, get_glyph)
    return canvas


def _process_one_frame(task: FrameTask) -> tuple[str, str]:
    """Convert one frame to ASCII and render it to a PNG.

    This is the unit of work run in each worker process.

    Args:
        task: The frame to process and how to render it.

    Returns:
        ``(out_path, ascii_text)`` where ``ascii_text`` is the frame's
        text with rows joined by newlines.
    """
    from PIL import Image
    m = task.metrics

    # Resize in RGB first, then take luminance: averaging happens in
    # color space, which weights channels correctly (unlike L-then-resize)
    src_rgb = Image.open(task.frame_path).convert('RGB')
    rgb_small = src_rgb.resize((m.cols, m.rows), Image.Resampling.BOX)
    lum = rgb_small.convert('L')

    if task.ascii_path is not None:
        lines = _read_ascii_lines(task.ascii_path)
    else:
        lines = _luminance_to_lines(lum, m, task.ramp, task.stretch)

    _render_lines(lines, rgb_small, lum, task).save(task.out_path)
    return task.out_path, '\n'.join(lines)


# --------------------- Fused parallel convert + render ---------------------

def frames_to_rendered_images(
        frames: Sequence[Path], img_dir: Path, metrics: GridMetrics, ramp: str,
        stretch: Stretch | None,
        leave_black_empty: bool = False,
        black_threshold: int = DEFAULT['BLACK-THRESHOLD'],
        jobs: int | None = None,
        ascii_files: Sequence[StrPath] | None = None,
        txt_dir: Path | None = None,
        colorized: bool = False,
        bg_color_rgb: RGB = color_as_rgb(DEFAULT['BACKGROUND-COLOR']),
        bg_alpha: float = DEFAULT['BACKGROUND-ALPHA'],
        font_color_rgb: RGB = color_as_rgb(DEFAULT['FONT-COLOR']),
        show_progress: bool = True) -> list[str]:
    """Convert and render every frame in parallel.

    Each frame becomes ``rendered_NNNNNN.png`` in ``img_dir``, numbered
    after its source frame. A summary of the grid is printed to stdout.

    Args:
        frames: Extracted frames named ``frame_NNNNNN.png``.
        img_dir: Directory to write rendered PNGs into; created if
            missing.
        metrics: Grid geometry.
        ramp: Glyphs ordered dark to bright.
        stretch: Luminance bounds from :func:`compute_global_stretch`,
            or ``None`` for no stretch.
        leave_black_empty: Draw nothing in cells darker than
            ``black_threshold``.
        black_threshold: Luminance (0-255) below which a cell is black.
        jobs: Worker processes. ``None`` uses the CPU count minus one.
        ascii_files: Saved text files aligned one-to-one with
            ``frames`` to render instead of converting (``--reuse
            ascii``), or ``None`` to convert from the frames.
        txt_dir: If given, each frame's ASCII text is also written here
            as ``frame_NNNNNN.txt``, numbered from zero in frame order.
        colorized: Colour each glyph from the frame.
        bg_color_rgb: Canvas colour.
        bg_alpha: Canvas opacity, 0-1.
        font_color_rgb: Glyph colour when not ``colorized``.
        show_progress: Draw a progress bar on stderr.

    Returns:
        Paths of the rendered PNGs in frame order.

    Raises:
        OSError: If a frame cannot be read or an output cannot be
            written; raised from the worker that failed.
    """
    jobs = jobs or max(1, (os.cpu_count() or 2) - 1)
    img_dir.mkdir(parents=True, exist_ok=True)

    tasks = [
        FrameTask(
            frame_path=str(frame),
            out_path=str(img_dir / f'rendered_{frame.stem.replace("frame_", "").zfill(6)}.png'),
            metrics=metrics, ramp=ramp, stretch=stretch,
            ascii_path=str(ascii_files[i]) if ascii_files is not None else None,
            leave_black_empty=leave_black_empty, black_threshold=black_threshold,
            colorized=colorized, bg_color_rgb=bg_color_rgb, bg_alpha=bg_alpha,
            font_color_rgb=font_color_rgb)
        for i, frame in enumerate(frames)
    ]

    rendered_images: list[str] = []
    texts: list[str | None] = [None] * len(tasks)
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        futures = {ex.submit(_process_one_frame, t): i
                   for i, t in enumerate(tasks)}
        for fut in tqdm(as_completed(futures), total=len(futures),
                        desc="  Converting+rendering", unit="frame",
                        disable=not show_progress):
            i = futures[fut]
            out_path, text = fut.result()
            texts[i] = text
            rendered_images.append(out_path)

    rendered_images.sort()

    if txt_dir is not None:
        txt_dir.mkdir(parents=True, exist_ok=True)
        for i, saved in enumerate(texts):
            if saved is not None:
                (txt_dir / f'frame_{i:06d}.txt').write_text(saved)

    print(f"  Grid: {metrics.cols}x{metrics.rows}, "
          f"cell={metrics.cell_w}x{metrics.cell_h}px, "
          f"canvas={metrics.target_width}x{metrics.target_height} (target)")
    return rendered_images
