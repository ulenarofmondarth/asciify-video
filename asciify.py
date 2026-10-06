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

import argparse
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

from tqdm import tqdm

# ------------------------------- Constants --------------------------------

DEFAULT_RAMP = " .'`^\",:;Il!i><~+_-?][}{1)(|\\/tfjrxnuvczXYUJCLQ0OZmwqpdbkhao*#MW&8%B@$"
JP2A_RAMP = " .',;:clodxkO0KXNWM"

STATE_DIR = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'asciify'
STATE_FILE = STATE_DIR / 'state.json'

TQDM_DISABLED = False   # set from --silent-progress in main()

STAGES = ['probe', 'key', 'extract', 'ascii', 'render', 'assemble']

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
        print(f"Render:     {r['target_width']}x{r['target_height']}, "
              f"font={r.get('font_size')}, "
              f"color={'source' if r.get('colorized') else 'mono'}, "
              f"black-empty={'on' if r.get('leave_black_empty') else 'off'}"
              + (f" thr={r.get('black_threshold')}"
                 if r.get('leave_black_empty') else ""))

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

def sample_green_color(input_path, sample_frames=10, region='corners',
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
        vals.extend(list(img.getdata()))
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
    """Worker: frame PNG -> ASCII -> rendered PNG.

    ascii_mode: 'compute' (convert from frame) or 'file' (reuse existing .txt)
    Returns (out_path, ascii_text).
    """
    (frame_path, out_path, ascii_mode, ascii_path, ramp, stretch, metrics,
     leave_black_empty, black_threshold, colorized) = task

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
            color_grid = list(rgb_small.getdata())
        if leave_black_empty:
            lum_grid = rgb_small.convert('L')
    else:
        # Resize in RGB first, then take luminance: averaging happens in
        # color space, which weights channels correctly (unlike L-then-resize)
        src_rgb = Image.open(frame_path).convert('RGB')
        rgb_small = src_rgb.resize((max_cols, rows), Image.BOX)
        lum = rgb_small.convert('L')
        pixels = list(lum.getdata())

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
            color_grid = list(rgb_small.getdata())
        if leave_black_empty:
            lum_grid = lum

    ascii_text = '\n'.join(lines)

    # --- Stage B: render ---
    blank = Image.new('RGB', (target_width, target_height), 'black')
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
                    solid = Image.new('RGBA', m.size, (100, 255, 100, 255))
                    rgba.paste(solid, (0, 0), m)
                    glyph_rgba[ch] = rgba
                img.paste(glyph_rgba[ch],
                          (off_x + x * cell_w, off_y + y * cell_h),
                          glyph_rgba[ch])

    img.save(out_path)
    return out_path, ascii_text

def frames_to_rendered_images(frames, img_dir, metrics, ramp, stretch,
                              leave_black_empty=False, black_threshold=50,
                              jobs=None, ascii_files=None, txt_dir=None,
                              colorized=False):
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
                      black_threshold, colorized))

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
                   has_audio, target_width, target_height, check_frames=True):
    """Assemble rendered frames into the final video.

    check_frames: if True, verify first frame matches target size (for --reuse)
                  and adds scale filter only on mismatch.
    """
    print("  Assembling video...")
    pattern = str(Path(rendered_images[0]).parent / 'rendered_*.png')

    cmd = ['ffmpeg', '-stats', '-y',
           '-framerate', f'{fps:.6f}',
           '-pattern_type', 'glob',
           '-i', pattern]
    if has_audio and source_video is not None and Path(source_video).exists():
        cmd += ['-i', str(source_video)]

    # Optional: verify and possibly scale
    if check_frames and rendered_images and Path(rendered_images[0]).exists():
        from PIL import Image
        try:
            frame = Image.open(rendered_images[0])
            fw, fh = frame.size
            frame.close()

            if fw != target_width or fh != target_height:
                print(f"  Scaling: {fw}x{fh} → {target_width}x{target_height}")
                cmd += ['-vf',
                        f'scale={target_width}:{target_height}:flags=lanczos']
            else:
                print(f"  Frames at target: {fw}x{fh}")
        except Exception as e:
            print(f"  Warning: Could not probe frame size: {e}")
            cmd += ['-vf',
                    f'scale={target_width}:{target_height}:flags=lanczos']
    elif check_frames:
        # No frames to probe — assume scale needed
        cmd += ['-vf',
                f'scale={target_width}:{target_height}:flags=lanczos']

    cmd += ['-map', '0:v']
    if has_audio and source_video is not None and Path(source_video).exists():
        cmd += ['-map', '1:a', '-c:a', 'aac', '-b:a', '192k', '-shortest']

    cmd += ['-c:v', 'libx264', '-crf', '18',
            '-pix_fmt', 'yuv420p', str(output_path)]
    run_command(cmd, check=True)
    return output_path

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


def main():
    parser = argparse.ArgumentParser(
        description='Convert green-screen video to ASCII art video',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s input.mp4 output.mp4
  %(prog)s input.mp4 out.mp4 --key-color 0x00FF00 --ascii-width 480
  %(prog)s input.mp4 out.mp4 --no-key --leave-black-empty
  %(prog)s input.mp4 out.mp4 --keep-temp
  %(prog)s --report
  %(prog)s input.mp4 out2.mp4 --reuse ascii --render-font-size 96
  %(prog)s input.mp4 out3.mp4 --reuse final
        """
    )

    parser.add_argument('input', type=str, nargs='?',
                        help='Input video file path')
    parser.add_argument('output', type=str, nargs='?',
                        help='Output ASCII video file path')
    parser.add_argument('--key-color', type=str, default=None,
                        help='Key color in hex format (default: auto-detect)')
    parser.add_argument('--detect-green', action='store_true',
                        help='Force green color detection even if --key-color provided')
    parser.add_argument('--tolerance', type=float, default=0.25,
                        help='Chromakey tolerance (default: 0.25)')
    parser.add_argument('--edge-blending', type=float, default=0.05,
                        help='Chromakey edge blending (default: 0.05)')
    parser.add_argument('--fps', type=float, default=None,
                        help='Override output FPS (default: from input)')
    parser.add_argument('--scale-divisor', type=int, default=8,
                        help='Default ascii width = source width / this '
                             'when --ascii-width not given (default: 8)')
    parser.add_argument('--ascii-width', type=int, default=160,
                        help='ASCII grid width in characters (default: 160)')
    parser.add_argument('--ramp', type=str, default=None,
                        help="'jp2a' or a custom ramp string "
                             "(default: full density ramp)")
    parser.add_argument('--colorized', action='store_true',
                        help='Render characters in their source colors '
                             'instead of monochrome green')
    parser.add_argument('--leave-black-empty', action='store_true',
                        help='Leave black areas empty (no ASCII characters)')
    parser.add_argument('--black-threshold', type=int, default=50,
                        help='Brightness threshold for "black" (default: 50)')
    parser.add_argument('--hist-stretch', action='store_true',
                        help='Global histogram stretch before character mapping')
    parser.add_argument('--no-key', action='store_true',
                        help='Skip chromakey stage entirely')
    parser.add_argument('--sample-region', type=str, default='corners',
                        help='Key color sampling region: corners, center, '
                             'full, or WxH+X+Y (default: corners)')
    parser.add_argument('--render-font-size', type=int, default=64,
                        help='Font rasterization quality (default: 64)')
    parser.add_argument('--target-resolution', type=str, default=None,
                        help='Target WIDTHxHEIGHT, e.g. 1920x1080 '
                             '(default: source dimensions)')
    parser.add_argument('--jobs', type=int, default=None, metavar='N',
                        help='Parallel worker processes (default: cores - 1)')
    parser.add_argument('--keep-temp', action='store_true',
                        help='Keep temporary files; record run in state file')
    parser.add_argument('--silent-progress', action='store_true',
                        help='Suppress progress bars')
    parser.add_argument('--reuse', choices=['extracted', 'ascii', 'final'],
                        default=None,
                        help='Reuse artifacts: "extracted" skips keying+'
                             'extraction, "ascii" also skips conversion, '
                             '"final" reuses rendered frames entirely')
    parser.add_argument('--reuse-dir', type=str, default=None, metavar='PATH',
                        help='Explicit temp dir to reuse (default: last '
                             '--keep-temp run, or $ASCII_VIDEO_TEMP)')
    parser.add_argument('--report', action='store_true',
                        help='Print recorded state of the last run and exit')
    parser.add_argument('--state-file', action='store_true',
                        help='Print the raw state JSON and exit')

    args = parser.parse_args()

    # ---- Standalone state modes ----
    if args.report:
        report_state(load_state())
        sys.exit(0)
    if args.state_file:
        print(json.dumps(load_state(), indent=2))
        sys.exit(0)

    if not args.input or not args.output:
        parser.error("input and output are required "
                     "(unless using --report / --state-file)")

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"Error: Input file '{args.input}' not found")
        sys.exit(1)
    if input_path.suffix.lower() not in ['.mp4', '.mov', '.avi', '.mkv',
                                          '.webm']:
        print("Warning: Uncommon video format detected. May fail.")

    global TQDM_DISABLED
    TQDM_DISABLED = args.silent_progress

    # ---- Resolve reuse ----
    state = load_state()
    reuse_level = args.reuse
    reuse_dir = None
    if reuse_level:
        reuse_dir = resolve_reuse_dir(args.reuse_dir, state)
        if reuse_dir is None:
            print("Error: no reusable temp directory found. Run once with "
                  "--keep-temp first, or set ASCII_VIDEO_TEMP / --reuse-dir.")
            sys.exit(1)
        validate_reuse(reuse_dir, reuse_level)
        frames_dir = reuse_dir / 'frames'
        txt_dir_r = reuse_dir / 'txt_frames'
        img_dir_r = reuse_dir / 'images'
        print(f"  Reusing artifacts from: {reuse_dir}")
        print(f"  Level: {reuse_level}  (frames={count_dir(frames_dir)}, "
              f"ascii={count_dir(txt_dir_r)}, rendered={count_dir(img_dir_r)})")
        skipped = {'extracted': ['keying', 'chromakey', 'extraction'],
                   'ascii':     ['keying', 'chromakey', 'extraction',
                                 'conversion'],
                   'final':     ['keying', 'chromakey', 'extraction',
                                 'conversion', 'rendering']}[reuse_level]
        print(f"  Skipped stages (their CLI options are IGNORED): "
              f"{', '.join(skipped)}")

    run_start = time.time()
    temp_dir = Path(tempfile.mkdtemp(prefix='asciify_'))

    # Begin run-state recording
    state = load_state()
    state.update({
        'input': str(input_path), 'keep_temp': args.keep_temp,
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
        fps = args.fps if args.fps else fps

        # Resolve ramp
        if args.ramp == 'jp2a':
            ramp = JP2A_RAMP
        elif args.ramp:
            ramp = args.ramp
        else:
            ramp = DEFAULT_RAMP

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
        elif args.no_key:
            print("  Skipping key color detection (--no-key)")
            key_color, was_detected = 'N/A', False
        elif args.detect_green or args.key_color is None:
            key_color, was_detected = sample_green_color(
                input_path, region=args.sample_region,
                src_width=width, src_height=height)
        else:
            key_color, was_detected = args.key_color, False

        if reuse_level:
            key_mode = 'none'
        elif args.no_key:
            key_mode = 'none'
        elif was_detected:
            key_mode = 'auto'
        else:
            key_mode = 'manual'
        state['key'] = {'mode': key_mode,
                        'color': None if key_color.startswith('N/A') else key_color,
                        'detected': was_detected,
                        'tolerance': args.tolerance,
                        'edge_blending': args.edge_blending}
        state['stages_completed'] += ['key']
        save_state(state)
        if not reuse_level:
            print(f"  Using chromakey color: {key_color}"
                  + (" (auto-detected)" if was_detected else ""))
        print()

        # ---- Stage 3: Chromakey ----
        if reuse_level:
            print(f"[3/6] Skipping chromakey/extraction (reuse: {reuse_level})")
        elif args.no_key:
            print("[3/6] Skipping chromakey (--no-key)")
            chroma_output = input_path
        else:
            print("[3/6] Applying chromakey and compositing over black...")
            chroma_output = temp_dir / 'chroma_keyed.mp4'
            with tqdm(total=1, desc="  Processing video",
                      disable=TQDM_DISABLED) as pbar:
                chromakey_to_black(input_path, chroma_output, key_color,
                                   args.tolerance, args.edge_blending,
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
            print(f"  Extracting at {args.ascii_width}px wide "
                  f"for 1:1 character mapping")
            frames_dir = temp_dir / 'frames'
            frames = extract_frames(chroma_output, frames_dir, fps,
                                    args.ascii_width)
        state['counts']['frames'] = len(frames)
        state['stages_completed'] += ['extract']
        save_state(state)
        print()

        if not frames:
            raise RuntimeError("No frames extracted/found — cannot continue")

        # ---- Target resolution & grid metrics ----
        if args.target_resolution:
            tw, th = map(int, args.target_resolution.split('x'))
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
            if args.hist_stretch and reuse_level != 'ascii':
                print("  Computing global histogram bounds...")
                stretch = compute_global_stretch(frames)
                print(f"  Stretch range: {stretch[0]}-{stretch[1]} "
                      f"(gain x{255 / max(stretch[1] - stretch[0], 1):.2f})")
            elif reuse_level == 'ascii':
                # stretch applied at conversion time originally; warn
                if args.hist_stretch:
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
                # Grid comes from the reused files, not the current flags
                with open(ascii_files[0]) as f:
                    first = f.read().rstrip('\n').split('\n')
                max_cols = max(len(l) for l in first)
                max_rows = len(first)
                if args.ascii_width != max_cols:
                    print(f"  Note: reused ASCII grid is {max_cols} cols; "
                          f"--ascii-width={args.ascii_width} ignored")
                print(f"  Using {len(ascii_files)} reused ASCII files "
                      f"(grid {max_cols}x{max_rows})")

                cell_w = max(1, tw // max_cols)
                cell_h = max(1, th // max_rows)
                off_x = (tw - cell_w * max_cols) // 2
                off_y = (th - cell_h * max_rows) // 2
                metrics = (max_cols, max_rows, cell_w, cell_h,
                           off_x, off_y, tw, th, args.render_font_size)

                rendered_images = frames_to_rendered_images(
                    frames, img_dir, metrics, ramp, None,
                    leave_black_empty=args.leave_black_empty,
                    black_threshold=args.black_threshold,
                    jobs=args.jobs, ascii_files=ascii_files,
                    colorized=args.colorized)
            else:
                # Fresh (or reuse 'extracted'): grid is analytic
                from PIL import Image
                _, fh = Image.open(frames[0]).size
                max_cols = args.ascii_width
                max_rows = max(1, fh // 2)
                cell_w = max(1, tw // max_cols)
                cell_h = max(1, th // max_rows)
                off_x = (tw - cell_w * max_cols) // 2
                off_y = (th - cell_h * max_rows) // 2
                metrics = (max_cols, max_rows, cell_w, cell_h,
                           off_x, off_y, tw, th, args.render_font_size)

                rendered_images = frames_to_rendered_images(
                    frames, img_dir, metrics, ramp, stretch,
                    leave_black_empty=args.leave_black_empty,
                    black_threshold=args.black_threshold,
                    jobs=args.jobs,
                    txt_dir=(temp_dir / 'txt_frames') if args.keep_temp else None,
                    colorized=args.colorized)

        state['grid'] = {
            'ascii_width': max_cols, 'rows': max_rows,
            'stretch': list(stretch) if stretch else None,
            'ramp': ('jp2a' if ramp == JP2A_RAMP
                     else ('default' if ramp == DEFAULT_RAMP else ramp)),
        }
        state['render'] = {
            'target_width': tw, 'target_height': th,
            'font_size': args.render_font_size,
            'colorized': args.colorized,
            'leave_black_empty': args.leave_black_empty,
            'black_threshold': args.black_threshold,
        }
        state['counts']['ascii_files'] = (count_dir(reuse_dir / 'txt_frames')
                                          if reuse_level in ('ascii',)
                                          else state['counts']['frames'])
        state['counts']['rendered_images'] = len(rendered_images)
        state['stages_completed'] += ['ascii', 'render']
        save_state(state)
        print(f"  Rendered {len(rendered_images)} images")
        print()

        # ---- Stage 7: Assemble ----
        print("[7/6] Assembling final video...")
        # ---- Stage 7: Assemble ----
        if reuse_level == 'final':
            # Resolution may differ from the run that created these frames
            validate_reuse_frame_sizes(reuse_dir, state, tw, th)
            assemble_video(rendered_images, output_path, fps, input_path,
                           has_audio, tw, th, check_frames=True)
        else:
            # Normal run (or non-final reuse): frames were just rendered at target
            assemble_video(rendered_images, output_path, fps, input_path,
                           has_audio, tw, th, check_frames=False)
        print(f"  Final output: {output_path}")
        print()

        print("="*60)
        print("CONVERSION COMPLETE!")
        print("="*60)
        state['output'] = str(output_path)
        state['completed'] = True
        state['duration_s'] = round(time.time() - run_start, 1)
        state['stages_completed'] += ['assemble']
        if args.keep_temp:
            state['temp_dir'] = str(temp_dir)
            print(f"\n  Temp directory (kept): {temp_dir}")
            print(f"  State file: {STATE_FILE}")
        else:
            state['temp_dir'] = None
        save_state(state)
        print()

    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
        if not args.keep_temp:
            cleanup_temp(temp_dir)
        save_state(state)
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        if not args.keep_temp:
            print("Cleaning up temporary files...")
            cleanup_temp(temp_dir)
        save_state(state)
        sys.exit(1)
    finally:
        # Never clean a reused directory; only this run's own temp dir
        if not args.keep_temp:
            cleanup_temp(temp_dir)


if __name__ == '__main__':
    main()
