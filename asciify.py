#!/usr/bin/env python3
"""
Green Screen to ASCII Video Converter v3

Pipeline: (key) -> extract -> ASCII convert -> render -> assemble
(with fused parallel convert+render, JSON run-state, and artifact reuse)

Key features:
- Chromakey green -> black background (auto-detect or manual key color)
- --no-key for pre-keyed footage
- Direct Python luminance->ASCII conversion (no jp2a)
- Parallel processing via ProcessPoolExecutor
- --hist-stretch global luminance histogram stretching
- Consistent output geometry via --target-resolution
- --keep-temp with JSON state file; --report; --reuse extracted|ascii|final
"""

import datetime
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Tuple

from tqdm import tqdm

# ------------------------------- Constants --------------------------------

DEFAULT = {
    'RAMP': " .'`^\",:;Il!i><~+_-?][}{1)(|\\/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$",
    'FONT-COLOR': '#64FF64',
    'KEY-COLOR': 'auto-detect',
    'TOLERANCE': 0.25,
    'EDGE-BLEEDING': 0.05,
    'SCALE-DIVISOR': 8,
    'ASCII-WIDTH': 160,
    'BLACK-THRESHOLD': 50,
    'SAMPLE-REGION': 'corners',
    'RASTERIZATION-QUALITY': 64,
    'BACKGROUND-COLOR': '#000000',
    'BACKGROUND-ALPHA': 1,
    'VPX-PRESET': 'ultrafast',
}

JP2A_RAMP = " .',;:clodxkO0KXNWM"

STATE_DIR = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'asciify'
STATE_FILE = STATE_DIR / 'state.json'

TQDM_DISABLED = False   # set from --silent-progress in main()

STAGES = ['probe', 'key', 'extract', 'ascii', 'render', 'assemble']

# ============================================================================
# Reuse Semantics Mapping
# Maps CLI options to their pipeline stage for reuse-aware messaging
# ============================================================================

STAGE_FLAG_MAP = {
    'keying': [
        '--key-color', '--detect-green', '--tolerance',
        '--edge-blending', '--sample-region', '--no-key'
    ],
    'conversion': [
        '--ramp', '--hist-stretch', '--ascii-width', '--scale-divisor'
    ],
    'rendering': [
        '--render-font-size', '--colorized', '--font-color',
        '--leave-black-empty', '--black-threshold',
        '--background-color', '--background-alpha', '--jobs'
    ],
}

REUSE_SKIPPED_STAGES = {
    'extracted': ['keying'],
    'ascii':     ['keying', 'conversion'],
    'final':     ['keying', 'conversion', 'rendering'],
}

# ------------------------------ Run state ----------------------------------

def _color_as_rgb(fc_hex):
    color_hex = fc_hex.lstrip('#')
    color_rgb = (
        int(color_hex[0:2], 16),
        int(color_hex[2:4], 16),
        int(color_hex[4:6], 16),
    )
    return color_rgb


# ------------------------------ Run state ----------------------------------

def load_state():
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            return {
                'version': 1,
                'updated': None, 'input': None, 'temp_dir': None,
                'keep_temp': False, 'completed': False,
                'stages_completed': [], 'probe': None, 'key': None,
                'grid': None, 'render': None, 'counts': {},
                'output': None, 'duration_s': None,
                **data,
            }
        except (json.JSONDecodeError, OSError):
            print(f"  Warning: could not parse {STATE_FILE}; starting fresh")
    return {
        'version': 1, 'updated': None, 'input': None, 'temp_dir': None,
        'keep_temp': False, 'completed': False, 'stages_completed': [],
        'probe': None, 'key': None, 'grid': None, 'render': None,
        'counts': {}, 'output': None, 'duration_s': None,
    }

def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state['updated'] = datetime.datetime.now().isoformat(timespec='seconds')
    STATE_FILE.write_text(json.dumps(state, indent=2))

def count_dir(d):
    return len(list(d.glob('*'))) if d and d.is_dir() else 0

def report_state(state):
    """Human-readable dump of the state file."""
    print(f"State file: {STATE_FILE}")
    if not STATE_FILE.exists():
        print("  (no state recorded yet)")
        return
    print(f"Updated:    {state.get('updated', '?')}")
    if state.get('input'):
        print(f"Input:      {state['input']}")
    tmp = state.get('temp_dir')
    if tmp:
        print(f"Temp dir:   {tmp}  "
              f"({'present' if Path(tmp).is_dir() else 'MISSING'})")
    print(f"Completed:  {'yes' if state.get('completed') else 'NO'}")
    print(f"Stages:     "
          f"{', '.join(state.get('stages_completed') or []) or '(none)'}")

    p = state.get('probe') or {}
    if p:
        print(f"Source:     {p.get('width')}x{p.get('height')} @ "
              f"{p.get('fps', 0):.2f} fps, "
              f"audio={'yes' if p.get('has_audio') else 'no'}")

    k = state.get('key') or {}
    if k.get('mode') == 'none':
        print("Keying:     skipped (--no-key)")
    elif k.get('color'):
        note = ' (auto-detected)' if k.get('detected') else ''
        print(f"Keying:     {k['color']}{note}, tol={k.get('tolerance')}, "
              f"blend={k.get('edge_blending')}")

    g = state.get('grid') or {}
    if g.get('ascii_width'):
        s = g.get('stretch')
        stretch_s = f"{s[0]}-{s[1]}" if s else 'off'
        print(f"Grid:       {g['ascii_width']} cols x {g.get('rows')} rows, "
              f"stretch={stretch_s}, ramp='{g.get('ramp', 'default')}'")

    r = state.get('render') or {}
    if r.get('target_width'):
        if r.get('colorized'):
            color_note = 'source'
        else:
            color_note = r.get('font_color', DEFAULT['FONT-COLOR'])
        print(f"Render:     {r['target_width']}x{r['target_height']}, "
              f"font={r.get('font_size')}, color={color_note}, "
              f"black-empty={'on' if r.get('leave_black_empty') else 'off'}"
              + (f" thr={r.get('black_threshold')}"
                 if r.get('leave_black_empty') else ""))

        bg = r.get('bg_color')
        if bg:
            alpha = r.get('bg_alpha')
            print(f"Background: {bg}, alpha={alpha:.2f}"
                  + ("  (transparent WebM)" if alpha is not None and alpha < 1.0
                     else ""))

    c = state.get('counts') or {}
    if any(v is not None for v in c.values()):
        print(f"Artifacts:  frames={c.get('frames')}, "
              f"ascii={c.get('ascii_files')}, "
              f"rendered={c.get('rendered_images')}")

    if state.get('output'):
        print(f"Last output: {state['output']}")

    if tmp and Path(tmp).is_dir():
        print(f"\nReuse:      {sys.argv[0]} IN OUT --reuse "
              f"extracted|ascii|final")
    else:
        print("\nNo reusable temp dir — run with --keep-temp to enable "
              "--reuse")

# ---------------------------- Command helpers ------------------------------

def run_command(cmd, check=True):
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        print(f"\nSTDERR: {result.stderr}")
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")
    return result

# --------------------------- Video processing -------------------------------

def _flattened_pixels(img):
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

def sample_green_color(input_path, sample_frames=10, region=DEFAULT['SAMPLE-REGION'],
                       src_width=300, src_height=300):
    """Sample frames and detect a green key color.

    region options: corners (default) | center | full | WxH+X+Y
    """
    print(f"  Sampling {sample_frames} frames ({region} region) for green color detection...")
    try:
        probe_cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json',
                     '-show_entries', 'format=duration', str(input_path)]
        duration = float(json.loads(run_command(probe_cmd).stdout)['format']['duration'])

        start_time = duration * 0.25
        interval = (duration * 0.5) / sample_frames
        rgb_values = []

        with tqdm(total=sample_frames, desc="  Sampling colors",
                  leave=False, disable=TQDM_DISABLED) as pbar:
            for i in range(sample_frames):
                sample_time = start_time + (interval * i)
                cmd = ['ffmpeg', '-y', '-ss', str(sample_time),
                       '-i', str(input_path), '-frames:v', '1',
                       '-vf', 'scale=160:-2',
                       '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']
                result = subprocess.run(cmd, capture_output=True)
                if result.returncode != 0 or len(result.stdout) < 3:
                    pbar.update(1)
                    continue

                data = result.stdout
                fw = 160
                fh = (len(data) // 3) // fw
                if fh == 0:
                    pbar.update(1)
                    continue

                import re

                def in_region(px, py):
                    if region == 'full':
                        return True
                    if region == 'center':
                        return (fw/3 <= px < 2*fw/3) and (fh/3 <= py < 2*fh/3)
                    if region == 'corners':
                        bw, bh = fw // 6, fh // 6
                        return (px < bw or px >= fw - bw) and (py < bh or py >= fh - bh)
                    m = re.match(r'^(\d+)x(\d+)\+(\d+)\+(\d+)$', region)
                    if not m:
                        raise ValueError(f"Invalid region spec: {region}")
                    cw, ch, cx, cy = (int(v) for v in m.groups())
                    sx, sy = px * src_width / fw, py * src_height / fh
                    return (cx <= sx < cx + cw) and (cy <= sy < cy + ch)

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
                            rgb_values.append((r, g, b))
                pbar.update(1)

        if not rgb_values:
            print("  Warning: Could not detect strong green. "
                  "Using default chroma-key green.")
            return '0x00FF00', False

        avg_r = int(sum(c[0] for c in rgb_values) / len(rgb_values))
        avg_g = int(sum(c[1] for c in rgb_values) / len(rgb_values))
        avg_b = int(sum(c[2] for c in rgb_values) / len(rgb_values))
        hex_color = f'0x{avg_r:02X}{avg_g:02X}{avg_b:02X}'
        print(f"  Detected green: RGB({avg_r},{avg_g},{avg_b}) → {hex_color}")
        return hex_color, True

    except Exception as e:
        print(f"  Error detecting green color: {e}")
        print("  Falling back to default chroma-key green (0x00FF00)")
        return '0x00FF00', False


def probe_video(filepath):
    cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json',
           '-show_streams', str(filepath)]
    info = json.loads(run_command(cmd).stdout)

    video_stream = next((s for s in info['streams']
                         if s['codec_type'] == 'video'), None)
    audio_stream = next((s for s in info['streams']
                         if s['codec_type'] == 'audio'), None)
    if not video_stream:
        raise ValueError("No video stream found in input file")

    width = int(video_stream.get('width', 1280))
    height = int(video_stream.get('height', 720))
    num, denom = video_stream.get('r_frame_rate', '30/1').split('/')
    fps = float(num) / float(denom)
    return {'width': width, 'height': height, 'fps': fps,
            'has_audio': audio_stream is not None}


def chromakey_to_black(input_path, output_path, key_color, tolerance,
                       edge_blending, width, height, fps):
    cmd = ['ffmpeg', '-stats', '-y', '-i', str(input_path),
           '-filter_complex',
           f'[0:v]chromakey={key_color}:{tolerance}:{edge_blending}[fg];'
           f'color=c=black:s={width}x{height}:r={fps}[bg];'
           '[bg][fg]overlay=shortest=1,format=yuv420p[v]',
           '-map', '[v]', '-map', '0:a?',
           '-c:a', 'copy', '-c:v', 'libx264', '-crf', '18',
           '-pix_fmt', 'yuv420p', str(output_path)]
    run_command(cmd, check=True)
    return output_path


def extract_frames(video_path, frames_dir, target_fps=None, ascii_width=240):
    """Extract frames at exactly the ASCII grid width (1:1 pixel mapping)."""
    frames_dir.mkdir(parents=True, exist_ok=True)
    scale_filter = f'scale={ascii_width}:-2:flags=lanczos,fps={target_fps}'
    cmd = ['ffmpeg', '-y', '-i', str(video_path), '-vf', scale_filter,
           str(frames_dir / 'frame_%06d.png')]
    run_command(cmd)

    frames = list(frames_dir.glob('frame_*.png'))
    frames.sort(key=lambda x: int(x.stem.replace('frame_', '')))
    print(f"  Extracted {len(frames)} frames to {frames_dir}")
    return frames


def compute_global_stretch(frames, sample_step=10, percentile=0.5):
    """Global lo/hi luminance bounds from sampled frames (percentile-clipped)."""
    from PIL import Image
    vals = []
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


def load_monospace_font(font_size=12):
    """Find a usable monospace font across platforms."""
    from PIL import ImageFont

    candidates = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',
        '/usr/share/fonts/TTF/DejaVuSansMono.ttf',
        '/usr/share/fonts/liberation/LiberationMono-Regular.ttf',
        'C:/Windows/Fonts/consola.ttf',
        '/System/Library/Fonts/Supplemental/Courier New.ttf',
        '/System/Library/Fonts/Menlo.ttc',
        '/System/Library/Fonts/SFNSMono.ttf',
    ]
    for fp in candidates:
        try:
            return ImageFont.truetype(fp, font_size)
        except OSError:
            continue

    patterns = [
        '/System/Library/Fonts/**/*.tt[fc]', '/Library/Fonts/**/*.tt[fc]',
        '/usr/share/fonts/**/*.tt[fc]', '/usr/local/share/fonts/**/*.tt[fc]',
    ]
    for pat in patterns:
        for fp in sorted(glob.glob(pat, recursive=True))[:50]:
            try:
                return ImageFont.truetype(fp, font_size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=font_size)
    except TypeError:
        return ImageFont.load_default()

# --------------------- Fused parallel convert + render ---------------------

def _process_one_frame(task):
    """Worker: frame PNG -> ASCII (in memory) -> rendered PNG.

    ascii_mode: 'compute' (convert from frame) or 'file' (reuse existing .txt)
    colorized: if True, sample character colors from source frames
    bg_color_rgb: tuple (r, g, b) for background color
    bg_alpha: float 0-1 for background opacity
    Returns (out_path, ascii_text).
    """
    (frame_path, out_path, ascii_mode, ascii_path, ramp, stretch, metrics,
     leave_black_empty, black_threshold, colorized,
     bg_color_rgb, bg_alpha, font_color_rgb) = task

    from PIL import Image, ImageDraw

    (max_cols, max_rows, cell_w, cell_h, off_x, off_y,
     target_width, target_height, base_font_size) = metrics

    # --- Stage A: obtain ASCII lines (+ per-cell colors if colorized) ---
    color_grid = None
    lum_grid = None          # small L image at grid res, for black masking
    rows = max_rows

    if ascii_mode == 'file':
        with open(ascii_path, 'r') as f:
            lines = f.read().rstrip('\n').split('\n')
        # Reference frame still supplies color / black-mask data
        src_rgb = Image.open(frame_path).convert('RGB')
        rgb_small = src_rgb.resize((max_cols, max_rows), Image.BOX)
        if colorized:
            color_grid = _flattened_pixels(rgb_small)   # was: list(rgb_small.getdata())
        if leave_black_empty:
            lum_grid = rgb_small.convert('L')
    else:
        # Resize in RGB first, then take luminance: averaging happens in
        # color space, which weights channels correctly (unlike L-then-resize)
        src_rgb = Image.open(frame_path).convert('RGB')
        rgb_small = src_rgb.resize((max_cols, rows), Image.BOX)
        lum = rgb_small.convert('L')
        pixels = list(lum.tobytes())

        if stretch is not None:
            lo, hi = stretch
            span = max(hi - lo, 1)
            pixels = [min(255, max(0, (p - lo) * 255 // span)) for p in pixels]

        scale = len(ramp) / 256
        lines = []
        for y in range(rows):
            row = pixels[y * max_cols:(y + 1) * max_cols]
            lines.append(''.join(
                ramp[min(len(ramp) - 1, int(p * scale))] for p in row))

        if colorized:
            color_grid = _flattened_pixels(rgb_small)   # was: list(rgb_small.getdata())
        if leave_black_empty:
            lum_grid = lum

    ascii_text = '\n'.join(lines)

    # --- Stage B: render ---
    # Transparent RGBA canvas instead of solid black RGB
    bg_color_with_alpha = (*bg_color_rgb, int(bg_alpha * 255))
    blank = Image.new('RGBA', (target_width, target_height), bg_color_with_alpha)
    if not any(l.strip() for l in lines):
        blank.save(out_path)
        return out_path, ascii_text

    lines = [l.ljust(max_cols)[:max_cols] for l in lines]
    if len(lines) > max_rows:
        lines = lines[:max_rows]

    font = load_monospace_font(base_font_size)
    glyph_cache = {}   # char -> L-mode alpha mask at cell size

    def get_glyph(ch):
        if ch not in glyph_cache:
            b = font.getbbox(ch)
            gw, gh = max(1, b[2] - b[0]), max(1, b[3] - b[1])
            gi = Image.new('L', (gw, gh), 0)
            ImageDraw.Draw(gi).text((-b[0], -b[1]), ch,
                                    font=font, fill=255)
            glyph_cache[ch] = gi.resize((cell_w, cell_h), Image.LANCZOS)
        return glyph_cache[ch]

    grid_w, grid_h = cell_w * max_cols, cell_h * max_rows

    if colorized:
        # --- Composite path: coverage mask + color layer, one paste ---
        coverage = Image.new('L', (grid_w, grid_h), 0)
        cell_colors = [(0, 0, 0)] * (max_cols * max_rows)

        for y, line in enumerate(lines):
            for x, ch in enumerate(line):
                if ch == ' ':
                    continue
                idx = y * max_cols + x
                if leave_black_empty and lum_grid is not None:
                    if lum_grid.getpixel((x, y)) < black_threshold:
                        continue
                if color_grid is not None:
                    cell_colors[idx] = color_grid[idx]
                coverage.paste(get_glyph(ch), (x * cell_w, y * cell_h))

        color_layer = Image.new('RGB', (max_cols, max_rows), 'black')
        color_layer.putdata(cell_colors)
        color_layer = color_layer.resize((grid_w, grid_h), Image.NEAREST)

        img = blank.copy()
        img.paste(color_layer, (off_x, off_y), coverage)
    else:
        # --- Monochrome path (unchanged fast single-color glyphs) ---
        img = blank.copy()
        glyph_rgba = {}
        for y, line in enumerate(lines):
            for x, ch in enumerate(line):
                if ch == ' ':
                    continue
                if leave_black_empty and lum_grid is not None:
                    if lum_grid.getpixel((x, y)) < black_threshold:
                        continue
                if ch not in glyph_rgba:
                    m = get_glyph(ch)
                    rgba = Image.new('RGBA', m.size, (0, 0, 0, 0))
                    rgba.putalpha(m)
                    solid = Image.new('RGBA', m.size, (*font_color_rgb, 255))
                    rgba.paste(solid, (0, 0), m)
                    glyph_rgba[ch] = rgba
                img.paste(glyph_rgba[ch],
                          (off_x + x * cell_w, off_y + y * cell_h),
                          glyph_rgba[ch])

    img.save(out_path)
    return out_path, ascii_text

def frames_to_rendered_images(frames, img_dir, metrics, ramp, stretch,
                              leave_black_empty=False, black_threshold=DEFAULT['BLACK-THRESHOLD'],
                              jobs=None, ascii_files=None, txt_dir=None,
                              colorized=False, bg_color_rgb=_color_as_rgb(DEFAULT['BACKGROUND-COLOR']),
                              bg_alpha=DEFAULT['BACKGROUND-ALPHA'], font_color_rgb=_color_as_rgb(DEFAULT['FONT-COLOR'])):
    """Fused parallel convert+render.

    ascii_files: None to convert from frames (fused); or a list of .txt
    paths aligned with frames (--reuse ascii).
    txt_dir: if given, persist ASCII text files (--keep-temp).
    """
    jobs = jobs or max(1, (os.cpu_count() or 2) - 1)
    img_dir.mkdir(parents=True, exist_ok=True)

    max_cols, max_rows = metrics[0], metrics[1]
    tasks = []
    for i, frame in enumerate(frames):
        out_path = img_dir / f'rendered_{frame.stem.replace("frame_", "").zfill(6)}.png'
        if ascii_files is not None:
            ascii_mode, ascii_path = 'file', str(ascii_files[i])
        else:
            ascii_mode, ascii_path = 'compute', None
        tasks.append((str(frame), str(out_path), ascii_mode, ascii_path,
                      ramp, stretch, metrics, leave_black_empty,
                      black_threshold, colorized, bg_color_rgb, bg_alpha, font_color_rgb))

    rendered_images = []
    texts = [None] * len(tasks)
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        futures = {ex.submit(_process_one_frame, t): i
                   for i, t in enumerate(tasks)}
        for fut in tqdm(as_completed(futures), total=len(futures),
                       desc="  Converting+rendering", unit="frame",
                       disable=TQDM_DISABLED):
            i = futures[fut]
            out_path, text = fut.result()
            texts[i] = text
            rendered_images.append(out_path)

    rendered_images.sort()

    if txt_dir is not None:
        txt_dir.mkdir(parents=True, exist_ok=True)
        for i, text in enumerate(texts):
            if text is not None:
                (txt_dir / f'frame_{i:06d}.txt').write_text(text)

    print(f"  Grid: {max_cols}x{max_rows}, cell={metrics[2]}x{metrics[3]}px, "
          f"canvas={metrics[6]}x{metrics[7]} (target)")
    return rendered_images

# ------------------------------- Assembly ----------------------------------
def assemble_video(rendered_images, output_path, fps, source_video,
                   has_audio, target_width, target_height,
                   check_frames=True, bg_alpha=DEFAULT['BACKGROUND-ALPHA'], vpx_preset=DEFAULT['VPX-PRESET']):
    """Assemble rendered frames into the final video."""
    print("  Assembling video...")
    pattern = str(Path(rendered_images[0]).parent / 'rendered_*.png')

    use_alpha = bg_alpha < 1.0

    cmd = ['ffmpeg', '-stats', '-y']

    # --- Critical: Force pixel format at INPUT for alpha mode ---
    if use_alpha:
        print(f"  Output: WebM (VP9 + alpha)")
        print(f"  Note: Using -pix_fmt at input to prevent encoder hang")
        cmd += ['-pix_fmt', 'rgba',  # Force PNG decoder to output RGBA
                '-framerate', f'{fps:.6f}',
                '-pattern_type', 'glob',
                '-i', pattern]

        # Check Opus availability
        try:
            check = subprocess.run(['ffmpeg', '-encoders'], capture_output=True, text=True, timeout=5)
            has_opus = 'libopus' in check.stdout
        except:
            has_opus = False

        if has_audio and source_video and Path(source_video).exists():
            cmd += ['-i', str(source_video)]
            if has_opus:
                cmd += ['-map', '1:a', '-c:a', 'libopus', '-b:a', '128k']
            else:
                print("  Warning: libopus unavailable, using Vorbis for audio")
                cmd += ['-map', '1:a', '-c:a', 'libvorbis', '-b:a', '128k']
            cmd += ['-shortest']
    else:
        print(f"  Output: MP4 (H.264)")
        cmd += ['-framerate', f'{fps:.6f}',
                '-pattern_type', 'glob',
                '-i', pattern]

        if has_audio and source_video and Path(source_video).exists():
            cmd += ['-i', str(source_video)]

    # --- Optional scaling ---
    if check_frames and rendered_images and Path(rendered_images[0]).exists():
        from PIL import Image
        try:
            frame = Image.open(rendered_images[0])
            fw, fh = frame.size
            frame.close()

            if fw != target_width or fh != target_height:
                print(f"  Scaling: {fw}x{fh} -> {target_width}x{target_height}")
                cmd += ['-vf',
                        f'scale={target_width}:{target_height}:flags=lanczos']
            else:
                print(f"  Frames at target: {fw}x{fh}")
        except Exception as e:
            print(f"  Warning: Could not probe frame size: {e}")
            cmd += ['-vf',
                    f'scale={target_width}:{target_height}:flags=lanczos']
    elif check_frames:
        cmd += ['-vf',
                f'scale={target_width}:{target_height}:flags=lanczos']

    # --- Video codec ---
    cmd += ['-map', '0:v']

    if use_alpha:
        # VP9 with alpha - simplified parameters that don't hang
        speed_val = vpx_speed_to_int(vpx_preset)
        print(f"  VP9 preset: {vpx_preset} (cpu-used={speed_val})")
        cmd += ['-c:v', 'libvpx-vp9',
                '-crf', '20',
                '-b:v', '0',
                '-cpu-used', str(speed_val),
                '-pix_fmt', 'yuva420p',  # Now safe to set at encoder
                '-deadline', 'realtime',
                '-realtime', 'true']
    else:
        if has_audio and source_video and Path(source_video).exists():
            cmd += ['-map', '1:a', '-c:a', 'aac', '-b:a', '192k', '-shortest']
        cmd += ['-c:v', 'libx264', '-crf', '18',
                '-preset', 'medium', '-pix_fmt', 'yuv420p']

    cmd += [str(output_path)]

    print(f"  Executing ffmpeg...")
    run_command(cmd, check=True)
    return output_path

def vpx_speed_to_int(preset):
    """Map named presets to cpu-used values."""
    mapping = {
        'ultrafast': 8,
        'veryfast': 6,
        'faster': 4,
        'fast': 2,
        'medium': 1,
        'slow': 0,
    }
    return mapping.get(preset, 4)

# ----------------------------- Reuse helpers --------------------------------

def resolve_reuse_dir(explicit_dir, state):
    """Priority: --reuse-dir > $ASCII_VIDEO_TEMP > state temp_dir."""
    if explicit_dir:
        d = Path(explicit_dir)
    elif os.environ.get('ASCII_VIDEO_TEMP'):
        d = Path(os.environ['ASCII_VIDEO_TEMP'])
    elif state.get('temp_dir'):
        d = Path(state['temp_dir'])
    else:
        return None
    return d if d.is_dir() else None


def validate_reuse(reuse_dir, level):
    """Verify the artifacts needed at this reuse level exist."""
    needs = {'extracted': ['frames'],
             'ascii':     ['frames', 'txt_frames'],
             'final':     ['images']}[level]
    for sub in needs:
        d = reuse_dir / sub
        if not d.is_dir() or not list(d.glob('*')):
            raise RuntimeError(
                f"--reuse {level}: missing or empty '{d}'. "
                f"Re-run with --keep-temp first, or point --reuse-dir at "
                f"a complete temp directory.")

def validate_reuse_frame_sizes(reuse_dir, state, target_width, target_height):
    """Check one rendered frame matches the state's recorded target size."""
    img_dir = reuse_dir / 'images'
    sample = next(img_dir.glob('rendered_*.png'), None)
    if not sample:
        return True   # nothing to validate

    try:
        from PIL import Image
        frame = Image.open(sample)
        fw, fh = frame.size
        frame.close()

        st_tw = state.get('render', {}).get('target_width')
        st_th = state.get('render', {}).get('target_height')

        # Check actual frame size against current target
        if fw != target_width or fh != target_height:
            print(f"  Warning: reused frames are {fw}x{fh}, "
                  f"target is {target_width}x{target_height}; "
                  f"will scale during assembly")

        # Also check against state-file claim (sanity check)
        if st_tw and st_th and (fw != st_tw or fh != st_th):
            print(f"  Note: state claims {st_tw}x{st_th}, "
                  f"frame is {fw}x{fh}; possible mismatch")
        return True
    except Exception as e:
        print(f"  Warning: Could not validate frame size: {e}")
        return False

def frame_num(p):
    stem = Path(p).stem
    digits = ''.join(ch for ch in stem if ch.isdigit())
    return int(digits) if digits else 0

# --------------------------------- Main --------------------------------------

def cleanup_temp(temp_dir):
    shutil.rmtree(temp_dir, ignore_errors=True)

# --------------------------- Programmatic entry ---------------------------

@dataclass
class ConversionConfig:
    """All knobs for one conversion run.

    Built by the CLI from parsed arguments, or directly for programmatic
    use (tests, embedding). Values arrive pre-normalized: colors are RGB
    tuples, alpha is clamped to [0, 1], target_resolution is (w, h).
    """
    input: Path
    output: Path

    # --- keying ---
    no_key: bool = False
    key_color: Optional[str] = None          # None => auto-detect
    detect_green: bool = False               # force detection despite key_color
    tolerance: float = DEFAULT['TOLERANCE']
    edge_blending: float = DEFAULT['EDGE-BLEEDING']
    sample_region: str = DEFAULT['SAMPLE-REGION']

    # --- grid / conversion ---
    ascii_width: int = DEFAULT['ASCII-WIDTH']
    ramp: Optional[str] = None               # None => DEFAULT['RAMP']
    hist_stretch: bool = False
    fps_override: Optional[float] = None     # None => use source fps

    # --- rendering ---
    render_font_size: int = DEFAULT['RASTERIZATION-QUALITY']
    colorized: bool = False
    font_color: Optional[str] = None         # raw string, for state records
    font_color_rgb: Tuple[int, int, int] = _color_as_rgb(DEFAULT['FONT-COLOR'])
    leave_black_empty: bool = False
    black_threshold: int = DEFAULT['BLACK-THRESHOLD']
    background_color: str = DEFAULT['BACKGROUND-COLOR']   # raw, for records
    background_rgb: Tuple[int, int, int] = _color_as_rgb(DEFAULT['BACKGROUND-COLOR'])
    background_alpha: float = DEFAULT['BACKGROUND-ALPHA']
    target_resolution: Optional[Tuple[int, int]] = None
    jobs: Optional[int] = None
    vpx_preset: str = DEFAULT['VPX-PRESET']

    # --- run control ---
    keep_temp: bool = False
    silent_progress: bool = False
    reuse_level: Optional[str] = None        # extracted | ascii | final
    reuse_dir: Optional[Path] = None         # resolved by the caller (CLI)


def run_conversion(cfg: ConversionConfig) -> Path:
    """Execute the full pipeline described by cfg.

    Pure-execution counterpart to the CLI: no argument parsing and no
    option validation (the caller guarantees normalized values). Raises
    on failure after cleaning up this run's temp directory (unless
    cfg.keep_temp). Returns the output path.
    """
    global TQDM_DISABLED
    TQDM_DISABLED = cfg.silent_progress

    input_path, output_path = cfg.input, cfg.output
    reuse_level, reuse_dir = cfg.reuse_level, cfg.reuse_dir

    state = load_state()
    run_start = time.time()
    temp_dir = Path(tempfile.mkdtemp(prefix='asciify_'))

    state.update({
        'input': str(input_path), 'keep_temp': cfg.keep_temp,
        'completed': False, 'stages_completed': [], 'counts': {},
        'temp_dir': None, 'output': None, 'duration_s': None,
    })
    save_state(state)

    try:
        print("\n" + "="*60)
        print("ASCIIFY")
        print("="*60 + "\n")

        # ---- Stage 1: Probe (restored from state when reusing) ----
        print("[1/6] Probing input video...")
        st_probe = None
        if reuse_dir and state.get('temp_dir') == str(reuse_dir):
            st_probe = state.get('probe')     # only trust if state matches dir
        if st_probe:
            width = st_probe['width']; height = st_probe['height']
            fps = st_probe['fps']; has_audio = st_probe['has_audio']
            print(f"  Restored from state file: "
                  f"{width}x{height} @ {fps:.2f} fps, "
                  f"audio={'yes' if has_audio else 'no'}")
        else:
            video_info = probe_video(input_path)
            width = video_info['width']; height = video_info['height']
            fps = video_info['fps']; has_audio = video_info['has_audio']
            print(f"  Resolution: {width}x{height}")
            print(f"  Frame rate: {fps:.2f} fps")
            print(f"  Audio: {'Yes' if has_audio else 'No'}")
        fps = cfg.fps_override if cfg.fps_override else fps

        # Resolve ramp
        if cfg.ramp == 'jp2a':
            ramp = JP2A_RAMP
        elif cfg.ramp:
            ramp = cfg.ramp
        else:
            ramp = DEFAULT['RAMP']

        state['probe'] = {'width': width, 'height': height, 'fps': fps,
                          'has_audio': has_audio}
        state['stages_completed'] += ['probe']
        save_state(state)
        print()

        # ---- Stage 2: Key color selection ----
        print("[2/6] Green color selection...")
        if reuse_level:
            print(f"  Skipped (reuse: {reuse_level})")
            key_color, was_detected = 'N/A (reused)', False
        elif cfg.no_key:
            print("  Skipping key color detection (--no-key)")
            key_color, was_detected = 'N/A', False
        elif cfg.detect_green or cfg.key_color is None:
            key_color, was_detected = sample_green_color(
                input_path, region=cfg.sample_region,
                src_width=width, src_height=height)
        else:
            key_color, was_detected = cfg.key_color, False

        if reuse_level:
            key_mode = 'none'
        elif cfg.no_key:
            key_mode = 'none'
        elif was_detected:
            key_mode = 'auto'
        else:
            key_mode = 'manual'
        state['key'] = {'mode': key_mode,
                        'color': None if key_color.startswith('N/A') else key_color,
                        'detected': was_detected,
                        'tolerance': cfg.tolerance,
                        'edge_blending': cfg.edge_blending}
        state['stages_completed'] += ['key']
        save_state(state)
        if not reuse_level:
            print(f"  Using chromakey color: {key_color}"
                  + (" (auto-detected)" if was_detected else ""))
        print()

        # ---- Stage 3: Chromakey ----
        if reuse_level:
            print(f"[3/6] Skipping chromakey/extraction (reuse: {reuse_level})")
        elif cfg.no_key:
            print("[3/6] Skipping chromakey (--no-key)")
            chroma_output = input_path
        else:
            print("[3/6] Applying chromakey and compositing over black...")
            chroma_output = temp_dir / 'chroma_keyed.mp4'
            with tqdm(total=1, desc="  Processing video",
                      disable=TQDM_DISABLED) as pbar:
                chromakey_to_black(input_path, chroma_output, key_color,
                                   cfg.tolerance, cfg.edge_blending,
                                   width, height, fps)
                pbar.update(1)
            print(f"  Output: {chroma_output}")
        print()

        # ---- Stage 4: Extract frames ----
        if reuse_level:
            print(f"[4/6] Using existing frames: {reuse_dir / 'frames'}")
            frames_dir = reuse_dir / 'frames'
            frames = sorted(frames_dir.glob('frame_*.png'), key=frame_num)
            print(f"  {len(frames)} frames")
        else:
            print("[4/6] Extracting frames...")
            print(f"  Extracting at {cfg.ascii_width}px wide "
                  f"for 1:1 character mapping")
            frames_dir = temp_dir / 'frames'
            frames = extract_frames(chroma_output, frames_dir, fps,
                                    cfg.ascii_width)
        state['counts']['frames'] = len(frames)
        state['stages_completed'] += ['extract']
        save_state(state)
        print()

        if not frames:
            raise RuntimeError("No frames extracted/found — cannot continue")

        # ---- Target resolution & grid metrics ----
        if cfg.target_resolution:
            tw, th = cfg.target_resolution
        else:
            tw, th = width, height

        # ---- Stage 5+6: convert+render (fused, parallel) ----
        if reuse_level == 'final':
            print("[5/6][6/6] Skipping conversion+rendering "
                  "(reuse: final)")
            rendered_images = sorted((reuse_dir / 'images')
                                      .glob('rendered_*.png'), key=frame_num)
            print(f"  {len(rendered_images)} rendered frames")
            max_cols = max_rows = None
            stretch = None
        else:
            print("[5/6][6/6] Converting and rendering "
                  "(fused, parallel)...")

            stretch = None
            if cfg.hist_stretch and reuse_level != 'ascii':
                print("  Computing global histogram bounds...")
                stretch = compute_global_stretch(frames)
                print(f"  Stretch range: {stretch[0]}-{stretch[1]} "
                      f"(gain x{255 / max(stretch[1] - stretch[0], 1):.2f})")
            elif reuse_level == 'ascii':
                if cfg.hist_stretch:
                    print("  Note: --hist-stretch affects conversion; "
                          "ignored when reusing ascii text")

            img_dir = temp_dir / 'images'

            if reuse_level == 'ascii':
                txt_dir_r = reuse_dir / 'txt_frames'
                ascii_files = sorted(txt_dir_r.glob('*.txt'), key=frame_num)
                if len(ascii_files) != len(frames):
                    raise RuntimeError(
                        f"ascii/frame count mismatch: "
                        f"{len(ascii_files)} txt vs {len(frames)} frames")
                with open(ascii_files[0]) as f:
                    first = f.read().rstrip('\n').split('\n')
                max_cols = max(len(l) for l in first)
                max_rows = len(first)
                if cfg.ascii_width != max_cols:
                    print(f"  Note: reused ASCII grid is {max_cols} cols; "
                          f"--ascii-width={cfg.ascii_width} ignored")
                print(f"  Using {len(ascii_files)} reused ASCII files "
                      f"(grid {max_cols}x{max_rows})")

                cell_w = max(1, tw // max_cols)
                cell_h = max(1, th // max_rows)
                off_x = (tw - cell_w * max_cols) // 2
                off_y = (th - cell_h * max_rows) // 2
                metrics = (max_cols, max_rows, cell_w, cell_h,
                           off_x, off_y, tw, th, cfg.render_font_size)

                rendered_images = frames_to_rendered_images(
                    frames, img_dir, metrics, ramp, None,
                    leave_black_empty=cfg.leave_black_empty,
                    black_threshold=cfg.black_threshold,
                    jobs=cfg.jobs, ascii_files=ascii_files,
                    colorized=cfg.colorized,
                    bg_color_rgb=cfg.background_rgb,
                    bg_alpha=cfg.background_alpha,
                    font_color_rgb=cfg.font_color_rgb)
            else:
                # Fresh (or reuse 'extracted'): grid is analytic
                from PIL import Image
                _, fh = Image.open(frames[0]).size
                max_cols = cfg.ascii_width
                max_rows = max(1, fh // 2)
                cell_w = max(1, tw // max_cols)
                cell_h = max(1, th // max_rows)
                off_x = (tw - cell_w * max_cols) // 2
                off_y = (th - cell_h * max_rows) // 2
                metrics = (max_cols, max_rows, cell_w, cell_h,
                           off_x, off_y, tw, th, cfg.render_font_size)

                rendered_images = frames_to_rendered_images(
                    frames, img_dir, metrics, ramp, stretch,
                    leave_black_empty=cfg.leave_black_empty,
                    black_threshold=cfg.black_threshold,
                    jobs=cfg.jobs,
                    txt_dir=(temp_dir / 'txt_frames') if cfg.keep_temp else None,
                    colorized=cfg.colorized,
                    bg_color_rgb=cfg.background_rgb,
                    bg_alpha=cfg.background_alpha,
                    font_color_rgb=cfg.font_color_rgb)

        state['grid'] = {
            'ascii_width': max_cols, 'rows': max_rows,
            'stretch': list(stretch) if stretch else None,
            'ramp': ('jp2a' if ramp == JP2A_RAMP
                     else ('default' if ramp == DEFAULT['RAMP'] else ramp)),
        }
        state['render'] = {
            'target_width': tw, 'target_height': th,
            'font_size': cfg.render_font_size,
            'font_color': cfg.font_color,
            'colorized': cfg.colorized,
            'leave_black_empty': cfg.leave_black_empty,
            'black_threshold': cfg.black_threshold,
            'bg_color': cfg.background_color,
            'bg_alpha': cfg.background_alpha,
        }
        state['counts']['ascii_files'] = (
            count_dir(reuse_dir / 'txt_frames')
            if reuse_level in ('ascii',) else state['counts']['frames'])
        state['counts']['rendered_images'] = len(rendered_images)
        state['stages_completed'] += ['ascii', 'render']
        save_state(state)
        print(f"  Rendered {len(rendered_images)} images")
        print()

        # ---- Stage 7: Assemble ----
        print("[7/6] Assembling final video...")
        if reuse_level == 'final':
            # Resolution may differ from the run that created these frames
            validate_reuse_frame_sizes(reuse_dir, state, tw, th)

        assemble_video(rendered_images, output_path, fps, input_path,
                       has_audio, tw, th,
                       check_frames=(reuse_level == 'final'),
                       bg_alpha=cfg.background_alpha,
                       vpx_preset=cfg.vpx_preset)
        print(f"  Final output: {output_path}")
        print()

        print("="*60)
        print("CONVERSION COMPLETE!")
        print("="*60)
        state['output'] = str(output_path)
        state['completed'] = True
        state['duration_s'] = round(time.time() - run_start, 1)
        state['stages_completed'] += ['assemble']
        if cfg.keep_temp:
            state['temp_dir'] = str(temp_dir)
            print(f"\n  Temp directory (kept): {temp_dir}")
            print(f"  State file: {STATE_FILE}")
        else:
            state['temp_dir'] = None
        save_state(state)
        print()
        return output_path

    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
        save_state(state)
        raise
    except Exception:
        if not cfg.keep_temp:
            print("Cleaning up temporary files...")
        save_state(state)
        raise
    finally:
        # Never clean a reused directory; only this run's own temp dir
        if not cfg.keep_temp:
            cleanup_temp(temp_dir)


if __name__ == '__main__':
    # Backward-compatible entry: delegate to the CLI module.
    from asciify_cli import main as cli_main
    sys.exit(cli_main())
