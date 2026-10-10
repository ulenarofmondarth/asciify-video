"""Automatic detection of the green-screen key colour.

A handful of frames are decoded at low resolution, the pixels inside a
sampling region are filtered down to those that are clearly green, and
their average becomes the key colour.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable

from tqdm import tqdm

from asciify_video.config import RGB
from asciify_video.constants import DEFAULT
from asciify_video.ffmpeg import StrPath, run_command

#: Pure chroma-key green, used whenever detection fails.
FALLBACK_KEY_COLOR: str = '0x00FF00'

#: Tests whether the pixel at ``(x, y)`` lies inside the sampling region.
RegionTest = Callable[[int, int], bool]

_SAMPLE_WIDTH = 160                      # frames are downscaled to this width
_REGION_SPEC = re.compile(r'^(\d+)x(\d+)\+(\d+)\+(\d+)$')


def _region_predicate(region: str, fw: int, fh: int, src_width: int,
                      src_height: int) -> RegionTest:
    """Build the pixel test for a sampling region.

    Args:
        region: ``'full'``, ``'center'`` (middle third on both axes),
            ``'corners'`` (outer sixth on both axes), or a ``'WxH+X+Y'``
            crop given in source pixels.
        fw: Width of the downscaled frame being tested.
        fh: Height of the downscaled frame being tested.
        src_width: Source video width, used to scale a crop spec.
        src_height: Source video height.

    Returns:
        A test taking ``(x, y)`` in downscaled-frame coordinates.

    Raises:
        ValueError: If ``region`` is not a recognised spec.
    """
    if region == 'full':
        return lambda px, py: True
    if region == 'center':
        return lambda px, py: (fw/3 <= px < 2*fw/3) and (fh/3 <= py < 2*fh/3)
    if region == 'corners':
        bw, bh = fw // 6, fh // 6
        return lambda px, py: ((px < bw or px >= fw - bw)
                               and (py < bh or py >= fh - bh))
    m = _REGION_SPEC.match(region)
    if not m:
        raise ValueError(f"Invalid region spec: {region}")
    cw, ch, cx, cy = (int(v) for v in m.groups())

    def in_crop(px: int, py: int) -> bool:
        sx, sy = px * src_width / fw, py * src_height / fh
        return (cx <= sx < cx + cw) and (cy <= sy < cy + ch)
    return in_crop


def _grab_frame(input_path: StrPath, sample_time: float) -> tuple[bytes, int] | None:
    """Decode one downscaled RGB24 frame at a timestamp.

    Returns:
        ``(raw bytes, frame height)``, or ``None`` if ffmpeg failed or
        produced less than one row of pixels.
    """
    cmd = ['ffmpeg', '-y', '-ss', str(sample_time),
           '-i', str(input_path), '-frames:v', '1',
           '-vf', f'scale={_SAMPLE_WIDTH}:-2',
           '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0 or len(result.stdout) < 3:
        return None
    data = result.stdout
    fh = (len(data) // 3) // _SAMPLE_WIDTH
    if fh == 0:
        return None
    return data, fh


def _green_pixels(data: bytes, fw: int, fh: int, in_region: RegionTest) -> list[RGB]:
    """Collect the clearly green pixels inside the region.

    A pixel qualifies when green exceeds both red and blue by more than
    30 and its mean brightness is 30-220, which excludes shadows, blown
    highlights and greys that would drag the average off the backdrop.
    """
    found = []
    for py in range(fh):
        for px in range(fw):
            if not in_region(px, py):
                continue
            j = (py * fw + px) * 3
            r, g, b = data[j], data[j+1], data[j+2]
            brightness = (r + g + b) / 3
            if brightness < 30 or brightness > 220:
                continue
            if g > r and g > b and g - r > 30 and g - b > 30:
                found.append((r, g, b))
    return found


def _collect_samples(input_path: StrPath, sample_frames: int, region: str,
                     src_width: int, src_height: int,
                     show_progress: bool) -> list[RGB]:
    """Gather green pixels from frames spread over the middle half of the video.

    The first and last quarters are skipped because they often hold
    fades, slates or an empty stage.

    Raises:
        ValueError: If ``region`` is not a recognised spec.
        RuntimeError: If ``ffprobe`` cannot report the duration.
    """
    probe_cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json',
                 '-show_entries', 'format=duration', str(input_path)]
    duration = float(json.loads(run_command(probe_cmd).stdout)['format']['duration'])

    start_time = duration * 0.25
    interval = (duration * 0.5) / sample_frames
    rgb_values: list[RGB] = []

    with tqdm(total=sample_frames, desc="  Sampling colors",
              leave=False, disable=not show_progress) as pbar:
        for i in range(sample_frames):
            grabbed = _grab_frame(input_path, start_time + (interval * i))
            if grabbed is not None:
                data, fh = grabbed
                in_region = _region_predicate(region, _SAMPLE_WIDTH, fh,
                                              src_width, src_height)
                rgb_values.extend(
                    _green_pixels(data, _SAMPLE_WIDTH, fh, in_region))
            pbar.update(1)
    return rgb_values


def sample_green_color(input_path: StrPath, sample_frames: int = 10,
                       region: str = DEFAULT['SAMPLE-REGION'],
                       src_width: int = 300, src_height: int = 300,
                       show_progress: bool = True) -> tuple[str, bool]:
    """Detect the green-screen key colour by sampling frames.

    Never raises: any failure, including an invalid ``region``, is
    reported on stdout and falls back to :data:`FALLBACK_KEY_COLOR`.

    Args:
        input_path: Video to sample.
        sample_frames: Number of frames to decode, evenly spaced over
            the middle half of the video.
        region: Where to look for the backdrop: ``'corners'``,
            ``'center'``, ``'full'``, or a ``'WxH+X+Y'`` crop in source
            pixels.
        src_width: Source width in pixels; only used to scale a
            ``'WxH+X+Y'`` region.
        src_height: Source height in pixels.
        show_progress: Draw a progress bar on stderr.

    Returns:
        ``(color, detected)`` where ``color`` is an ffmpeg hex colour
        such as ``'0x16BE1E'`` and ``detected`` is ``False`` when the
        fallback colour was used.
    """
    print(f"  Sampling {sample_frames} frames ({region} region) for green color detection...")
    try:
        rgb_values = _collect_samples(input_path, sample_frames, region,
                                      src_width, src_height, show_progress)
    except Exception as e:
        print(f"  Error detecting green color: {e}")
        print(f"  Falling back to default chroma-key green ({FALLBACK_KEY_COLOR})")
        return FALLBACK_KEY_COLOR, False

    if not rgb_values:
        print("  Warning: Could not detect strong green. "
              "Using default chroma-key green.")
        return FALLBACK_KEY_COLOR, False

    avg_r = int(sum(c[0] for c in rgb_values) / len(rgb_values))
    avg_g = int(sum(c[1] for c in rgb_values) / len(rgb_values))
    avg_b = int(sum(c[2] for c in rgb_values) / len(rgb_values))
    hex_color = f'0x{avg_r:02X}{avg_g:02X}{avg_b:02X}'
    print(f"  Detected green: RGB({avg_r},{avg_g},{avg_b}) → {hex_color}")
    return hex_color, True
