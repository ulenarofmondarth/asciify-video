# asciify

Convert green-screen video to ASCII art video with consistent output geometry, multi-core processing, and intelligent artifact reuse.

![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)
![Platform](https://img.shields.io/badge/platform-linux%20%7C%20macos%20%7C%20windows-lightgray.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)

## Overview

`asciify.py` takes green-screen video footage and produces an ASCII art version where:

- **Character geometry is consistent** across different source resolutions (via `--target-resolution`)
- **Vertical stripe artifacts are eliminated** (custom Python rasterizer replaces buggy jp2a)
- **Performance is optimized** via ProcessPoolExecutor parallelization
- **Iteration is fast** via artifact reuse (`--reuse extracted|ascii|final`)
- **Backgrounds are flexible** — any color, fully transparent, or anywhere in between
- **Font colors are customizable** — choose any CSS hex color for characters
- **Everything is documented** via JSON state files and `--report`

## Features

| Feature | Description |
|---------|-------------|
| **Auto green detection** | Samples corner regions to find optimal key color |
| **Manual key override** | Specify exact hex color with `--key-color` |
| **Chromakey-to-background** | Composites keyed subject over a selectable background |
| **Background color & alpha** | Any CSS hex color; opacity 0-1, including full transparency (`--background-color`, `--background-alpha`) |
| **Font color selection** | Choose any CSS hex color for characters (`--font-color`, default: `#64FF64`) |
| **Automatic WebM selection** | Alpha output automatically targets `.webm`, renaming the output file with a prominent notice |
| **VP9 speed control** | Tune alpha encode speed with `--vpx-preset` |
| **Mutual exclusivity enforcement** | `--font-color` and `--colorized` cannot both be specified (clear error + abort) |
| **Direct luminance mapping** | Custom Python rasterizer (no external `jp2a` dependency) |
| **Parallel processing** | Multi-core conversion and rendering via `ProcessPoolExecutor` |
| **Histogram stretching** | Global luminance remapping (`--hist-stretch`) prevents per-frame brightness pumping |
| **Colorized mode** | Sample character colors from source frames (`--colorized`) |
| **Target resolution** | Consistent output geometry regardless of input resolution |
| **Black masking** | Leave dark areas empty (`--leave-black-empty --black-threshold`) |
| **Artifact reuse** | Skip costly stages by reusing intermediates (`--reuse`) |
| **Reuse-aware messaging** | Flags CLI options that have no effect at the chosen reuse level |
| **JSON state tracking** | Run metadata persisted to `~/.cache/asciify/state.json` |
| **Progress reporting** | `--report` shows last run's state; `--state-file` dumps raw JSON |

## Requirements

### System dependencies

- **Python 3.8+**
- **FFmpeg** (with chromakey, libx264, libvpx-vp9, and ideally libopus support)
- **ffprobe** (shipped with FFmpeg)

Install via your package manager:

```bash
# Ubuntu/Debian
sudo apt-get install ffmpeg python3-pip

# macOS (Homebrew)
brew install ffmpeg

# Windows
# Download from https://ffmpeg.org/download.html or use winget/choco
winget install ffmpeg
```

Verify alpha support in your build:

```bash
ffmpeg -encoders | grep vpx
# Expected: libvpx and libvpx-vp9 entries

ffmpeg -encoders | grep opus
# Expected: libopus (audio in alpha mode falls back to Vorbis if absent)
```

### Python dependencies

```bash
pip install pillow tqdm
```

## Quick Start

### Basic usage

```bash
./asciify.py input.mp4 output.mp4
```

This will:

1. Auto-detect green key color from corners
2. Chromakey subject onto black background
3. Extract frames scaled to 160 characters wide
4. Convert each frame to ASCII (160 cols x calculated rows)
5. Render ASCII with green (#64FF64) characters
6. Assemble final video

### With manual key color

```bash
./asciify.py input.mp4 output.mp4 --key-color 0x00B140
```

### With target resolution (consistent geometry)

```bash
./asciify.py video1.mp4 out1.mp4 --target-resolution 1920x1080
./asciify.py video2.mp4 out2.mp4 --target-resolution 1920x1080
# Both outputs now have identical character dimensions
```

### Pre-keyed footage (skip chromakey)

```bash
./asciify.py input.mp4 output.mp4 --no-key
```

### Colorized output

```bash
./asciify.py input.mp4 output.mp4 --colorized
```

## Background Color & Transparency

The background behind the ASCII characters is selectable in both color and opacity:

```bash
# Opaque white background (standard MP4)
./asciify.py input.mp4 out.mp4 --background-color '#FFFFFF' --background-alpha 1

# Opaque custom color
./asciify.py input.mp4 out.mp4 --background-color '#102030' --background-alpha 1

# Semi-transparent background (WebM)
./asciify.py input.mp4 out --background-color '#000000' --background-alpha 0.5

# Fully transparent background (WebM) - ideal for overlays
./asciify.py input.mp4 out --background-alpha 0
```

### Automatic WebM selection

Alpha transparency requires the VP9/WebM container. When `--background-alpha` is below 1 and the output filename does not already end in `.webm`, the script renames it for you and prints a prominent notice before anything else runs:

```
! ---------------------------------------------------------- !
!              NOTE: alpha output requires WebM              !
! ---------------------------------------------------------- !
!  Output filename changed:
!    out.mp4
!    -> out.webm
! ---------------------------------------------------------- !
!  Reason: --background-alpha 0.5 produces a
!  transparent video, which requires the VP9/WebM
!  container. Use --background-alpha 1 to force MP4.
! ---------------------------------------------------------- !
```

If the renamed target already exists, the script writes to `<name>_new.webm` instead of overwriting it.

### Output format rules

| Setting | Format | Audio | Notes |
|---------|--------|-------|-------|
| `--background-alpha 1` | MP4 (H.264) | AAC, preserved | Standard opaque video |
| `--background-alpha 0-0.99` | WebM (VP9 + alpha) | Opus (Vorbis fallback), preserved | `yuva420p` pixel format |
| `--background-alpha 0` | WebM (VP9 + alpha) | Opus (Vorbis fallback), preserved | Pure transparency for overlay use |

### VP9 encode speed

Alpha encoding with VP9 can be slow. Control the speed/quality trade-off:

```bash
./asciify.py input.mp4 out --background-alpha 0 --vpx-preset ultrafast  # fastest (default)
./asciify.py input.mp4 out --background-alpha 0 --vpx-preset medium    # slower, better quality
./asciify.py input.mp4 out --background-alpha 0 --vpx-preset slow      # best quality, ~10x slower
```

Presets map to VP9 `cpu-used` values from 8 (ultrafast) down to 0 (slow).

## Font Color Selection

Characters render in a uniform color (default: `#64FF64`, the classic terminal green):

```bash
# Classic green (default, unchanged output)
./asciify.py in.mp4 out.mp4

# Amber terminal look
./asciify.py in.mp4 out.mp4 --font-color '#FFB000'

# Cyan on dark gray
./asciify.py in.mp4 out.mp4 --font-color '#00FFFF' \
    --background-color '#101820' --background-alpha 1

# White text
./asciify.py in.mp4 out.mp4 --font-color '#FFFFFF' --background-color '#000000' --background-alpha 1
```

### Incompatibility with colorized mode

`--font-color` and `--colorized` are mutually exclusive. The script detects this conflict upfront and aborts immediately with a clear error message:

```
$ ./asciify.py in.mp4 out.mp4 --font-color '#FF0000' --colorized
Error: --font-color and --colorized are mutually exclusive.
       --colorized samples each character's color from the source frame,
       leaving no role for a uniform --font-color.
```

This hard error prevents wasting time on a full render that would violate the intended behavior.

## Advanced Usage

### Artifact reuse (iteration workflow)

The `--keep-temp` flag preserves intermediate artifacts for rapid iteration:

```bash
# First run: keep all intermediates
./asciify.py input.mp4 output1.mp4 --keep-temp

# Later: tweak only render settings (fonts, colors, backgrounds, masking)
./asciify.py input.mp4 output2.mp4 --reuse ascii --render-font-size 96

# Even later: tweak only encode settings (FPS, resolution, audio)
./asciify.py input.mp4 output3.mp4 --reuse final --fps 60
```

#### Reuse levels

| Level | Skips stages | Reuses from disk | Typical iteration |
|-------|--------------|------------------|-------------------|
| `extracted` | keying, chromakey, extraction | frames/*.png | Fix key color and retry |
| `ascii` | + conversion | txt_frames/*.txt, frames/*.png | Tweak rendering (font color, backgrounds, masking) |
| `final` | + rendering | images/rendered_*.png | Tune encoder (FPS, resolution, audio) |

#### What still works at each level

Each reuse level freezes everything upstream of it, so some CLI options become inert. The script tells you exactly which ones:

```
$ ./asciify.py input.mp4 out.mp4 --reuse final --background-alpha 0.5
  Reusing artifacts from: /var/folders/.../asciify_x1y2
  Level: final  (frames=108, ascii=108, rendered=108)
  Stages skipped: keying, conversion, rendering
  No effect at this level: --ascii-width, --background-alpha, --background-color,
    --black-threshold, --colorized, --font-color, --detect-green, --edge-blending,
    --hist-stretch, --jobs, --key-color, --leave-black-empty, --no-key,
    --ramp, --render-font-size, --sample-region, --scale-divisor, --tolerance
    !! --font-color was passed but is IGNORED (--reuse final)
  Still honored: --fps, --target-resolution (frames rescaled at assembly), audio mapping
  Note: background color/alpha, font, and masking are baked into the reused frames
```

Summary:

- **`extracted`**: conversion and rendering options all work (`--ascii-width`, `--ramp`, `--hist-stretch`, fonts, backgrounds, font color)
- **`ascii`**: rendering options still work — including `--font-color`, `--background-color`, `--background-alpha`, `--colorized`, and `--leave-black-empty` — since rendering re-runs from the reused text. The character grid comes from the stored text files, so `--ascii-width` is advisory only.
- **`final`**: background color/alpha, font color, font size, and masking options are baked into the reused frames and have no effect. `--fps`, `--target-resolution` (assembly rescales frames), and audio mapping are still honored.

### State inspection

```bash
# Human-readable summary of last run
./asciify.py --report

# Raw JSON dump
./asciify.py --state-file

# Both work standalone; no input/output args needed
```

Example `--report` output:

```
State file: /home/user/.cache/asciify/state.json
Updated:    2026-10-06T14:22:31
Input:      input.mp4
Temp dir:   /var/folders/.../asciify_x1y2  (present)
Completed:  yes
Stages:     probe, key, extract, ascii, render, assemble
Source:     1920x1080 @ 24.00 fps, audio=yes
Keying:     0x00B140 (auto-detected), tol=0.25, blend=0.05
Grid:       160 cols x 135 rows, stretch=off, ramp='default'
Render:     1920x1080, font=64, color=#FFB000, black-empty=off
Background: #000000, alpha=1.00
Artifacts:  frames=108, ascii=108, rendered=108
Last output: output.mp4
```

### Environment override for reuse directory

```bash
export ASCII_VIDEO_TEMP=/path/to/previous/temp
./asciify.py input.mp4 out.mp4 --reuse ascii

# Or point at an explicit directory:
./asciify.py input.mp4 out.mp4 --reuse ascii --reuse-dir /explicit/path
```

### Parallel processing tuning

```bash
./asciify.py input.mp4 output.mp4 --jobs 4
# Default: number_of_cores - 1
```

### Histogram stretching

```bash
./asciify.py input.mp4 output.mp4 --hist-stretch
# Remaps luminance globally before character mapping
# Prevents brightness "pumping" between frames
```

## CLI Reference

| Flag | Default | Description |
|------|---------|-------------|
| `input` | *(required)* | Input video file path |
| `output` | *(required)* | Output ASCII video file path (auto-renamed to `.webm` when alpha < 1) |
| `--key-color` | auto-detect | Key color in hex (e.g., `0x00B140`) |
| `--detect-green` | off | Force green detection even if `--key-color` given |
| `--tolerance` | 0.25 | Chromakey tolerance (0-1) |
| `--edge-blending` | 0.05 | Chromakey edge blending (0-1) |
| `--no-key` | off | Skip chromakey stage entirely |
| `--fps` | from source | Override output FPS |
| `--scale-divisor` | 8 | Default ASCII width = source_width / divisor |
| `--ascii-width` | 160 | ASCII grid width in characters |
| `--ramp` | default | `'jp2a'` or custom ramp string |
| `--colorized` | off | Render characters in source colors |
| `--font-color` | `#64FF64` | Character color, CSS hex (incompatible with `--colorized`) |
| `--leave-black-empty` | off | Leave dark areas as true emptiness |
| `--black-threshold` | 50 | Brightness threshold for "black" (0-255) |
| `--background-color` | `#000000` | Background color, CSS hex (`#RRGGBB`) |
| `--background-alpha` | 0 | Background opacity 0-1 (below 1 selects WebM) |
| `--vpx-preset` | `ultrafast` | VP9 encode speed for alpha: `ultrafast`, `veryfast`, `faster`, `fast`, `medium`, `slow` |
| `--hist-stretch` | off | Global histogram stretch before mapping |
| `--sample-region` | `corners` | Sampling region: `corners`, `center`, `full`, or `WxH+X+Y` |
| `--render-font-size` | 64 | Font rasterization quality |
| `--target-resolution` | source dims | Target output (e.g., `1920x1080`) |
| `--jobs` | cores - 1 | Parallel worker processes |
| `--keep-temp` | off | Keep temporary files; record run state |
| `--silent-progress` | off | Suppress tqdm progress bars |
| `--reuse` | none | `extracted`, `ascii`, or `final` |
| `--reuse-dir` | auto | Explicit temp dir path |
| `--report` | off | Print last run's state; exit |
| `--state-file` | off | Print raw state JSON; exit |

## Architecture Highlights

### Pipeline stages

```
Probe --> Keying --> Extract --> ASCII Convert --> Render --> Assemble
(meta)  (chroma)   (PNG)      (fused parallel)  (images)  (MP4/WebM)
```

### Fused parallel convert+render

The pipeline fuses conversion and rendering into a single worker function:

```
Worker 1: PNG --> ASCII (in memory) --> Rendered PNG
Worker 2: PNG --> ASCII (in memory) --> Rendered PNG
Worker 3: PNG --> ASCII (in memory) --> Rendered PNG
```

Intermediate text files exist **only** when `--keep-temp` is specified, primarily for debugging and `--reuse ascii` compatibility.

### Analytic grid calculation

The character grid is computed analytically from `--target-resolution` and `--ascii-width`:

```
max_cols = --ascii-width (user setting)
max_rows = frame_height / 2 (from first frame)
cell_w = --target_width / max_cols
cell_h = --target_height / max_rows
```

This guarantees **identical character dimensions** across all videos rendered with the same target settings.

### Alpha-aware rendering and assembly

Rendered frames are RGBA images: the background layer carries the user-selected color at the user-selected alpha, while all glyphs remain fully opaque in either:

- The uniform `--font-color` tint (monochrome mode), or
- Per-cell colors sampled from the source (colorized mode)

During assembly:

- **Opaque output** (`--background-alpha 1`): H.264/MP4 with AAC audio.
- **Alpha output** (below 1): PNGs are fed to ffmpeg with the input pixel format forced to RGBA (preventing a format-negotiation hang in some builds), encoded as VP9 `yuva420p` WebM with a configurable `cpu-used` speed, realtime deadline, and row-based multithreading enabled. Audio is re-encoded as Opus, falling back to Vorbis if the build lacks `libopus`.

### State file schema

Stored at `~/.cache/asciify/state.json`:

```json
{
  "version": 1,
  "updated": "2026-10-06T14:22:31",
  "input": "/path/to/input.mp4",
  "temp_dir": "/var/folders/.../asciify_xyz",
  "keep_temp": true,
  "completed": true,
  "stages_completed": ["probe", "key", "extract", "ascii", "render", "assemble"],
  "probe": {
    "width": 1920,
    "height": 1080,
    "fps": 24.0,
    "has_audio": true
  },
  "key": {
    "mode": "auto",
    "color": "0x00B140",
    "detected": true,
    "tolerance": 0.25,
    "edge_blending": 0.05
  },
  "grid": {
    "ascii_width": 160,
    "rows": 135,
    "stretch": null,
    "ramp": "default"
  },
  "render": {
    "target_width": 1920,
    "target_height": 1080,
    "font_size": 64,
    "font_color": "#FFB000",
    "colorized": false,
    "leave_black_empty": false,
    "black_threshold": 50,
    "bg_color": "#000000",
    "bg_alpha": 1.0
  },
  "counts": {
    "frames": 108,
    "ascii_files": 108,
    "rendered_images": 108
  },
  "output": "/path/to/output.mp4",
  "duration_s": 127.4
}
```

## Troubleshooting

### "No reusable temp directory found" with `--reuse`

Run once with `--keep-temp` first to populate the temp directory:

```bash
./asciify.py input.mp4 output.mp4 --keep-temp
# Then reuse:
./asciify.py input.mp4 output2.mp4 --reuse ascii
```

Alternatively set `$ASCII_VIDEO_TEMP`:

```bash
export ASCII_VIDEO_TEMP=/path/to/previous/temp
```

### "Missing or empty 'frames'" error

The reuse validation checks that all required artifact directories exist and contain files. This typically means:

1. The temp directory is incomplete (run was interrupted)
2. Points to wrong directory (verify `--reuse-dir`)
3. Artifacts were manually deleted

Solution: re-run with `--keep-temp` from scratch, or specify a different `--reuse-dir`.

### `--font-color` and `--colorized` both specified

These options are mutually exclusive — `--colorized` samples each cell's color from the source frame, so a uniform `--font-color` has nothing to control. The script detects this conflict at startup and aborts with a clear explanation. Choose one or the other.

### Alpha encoding hangs or fails

The script checks for `libvpx-vp9` before alpha encoding and exits with guidance if it's missing. If encoding is merely slow, lower the preset:

```bash
./asciify.py input.mp4 out --background-alpha 0 --vpx-preset ultrafast
```

Some ffmpeg builds deadlock feeding RGBA PNGs to libvpx-vp9; asciify forces `-pix_fmt rgba` at the input stage to prevent this. If hangs persist with your build, try a static ffmpeg build (e.g., from johnvansickle.com, Linux) or Homebrew's current build (macOS), which handle alpha pipelines reliably.

To verify an alpha encode after the fact:

```bash
ffprobe -v error -show_entries stream=pix_fmt output.webm
# Look for "yuva420p"
```

### Transparent background renders as black

The alpha channel is present in the file; your player does not support VP9 alpha. Chromium, Firefox, and recent mpv builds display VP9 alpha correctly; QuickTime and some older players show solid black. If you need broad player compatibility, render opaque (`--background-alpha 1`) and composite in post.

### A flag is reported as IGNORED during `--reuse`

Options affecting stages before your reuse point have no effect, since those stages are skipped. The `!!` lines in the reuse banner flag any you actually passed. Move that setting to a lower reuse level, or run without `--reuse`.

### Font loading failure

`asciify.py` searches standard paths on Linux/macOS/Windows and falls back to scalable defaults. If you get font errors, check:

```bash
fc-list : monospace                                      # Linux
find /System/Library/Fonts -name "*.ttf" | grep -i mono  # macOS
dir C:\Windows\Fonts\*mono*                             # Windows
```

### "Cannot write empty image" crash

The script handles blank frames gracefully. If you still encounter this, verify:

1. The input video isn't entirely black/dark
2. `--black-threshold` isn't too aggressive (try lowering it)
3. Chromakey isn't removing too much subject matter (adjust `--tolerance`)

## Performance Notes

FFmpeg stages (chromakey, extraction, assembly) are single-command and not parallelized; the Python convert+render stage scales with `--jobs`. Parallelization benefits grow with higher resolution and longer clips.

For alpha output specifically, VP9 encoding dominates assembly time; `--vpx-preset` trades speed against quality, with `ultrafast` (`cpu-used 8`) completing roughly an order of magnitude faster than `slow` (`cpu-used 0`).

### Memory usage

Peak memory is dominated by:

- Loaded font cache (approx. 1-5 MB for typical glyphs)
- Parallel workers (approx. 100-200 MB total for 4 workers at 1080p)
- Temporary PNG files on disk (approx. 20-50 MB for a 10s clip)

Reduce worker count with `--jobs 2` if hitting memory constraints.

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing`)
3. Commit changes (`git commit -am 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing`)
5. Open a Pull Request

### Development setup

```bash
git clone https://github.com/yourusername/asciify.git
cd asciify
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
venv\Scripts\activate     # Windows
pip install -r requirements.txt
chmod +x asciify.py       # Make executable (Linux/macOS)
```

### Testing checklist

- [ ] Auto green detection on various green backgrounds
- [ ] Manual key override accuracy
- [ ] `--no-key` mode on pre-keyed footage
- [ ] `--hist-stretch` preventing brightness pumping
- [ ] `--colorized` mode producing correct per-cell colors
- [ ] `--font-color` changing character color correctly
- [ ] `--font-color` + `--colorized` conflict detected and aborted with clear error
- [ ] `--target-resolution` consistency across inputs
- [ ] Background colors rendering correctly (opaque)
- [ ] Alpha transparency surviving to final WebM (`yuva420p` verified via ffprobe)
- [ ] Automatic `.webm` rename notice displayed for non-webm output names with alpha < 1
- [ ] Existing output file collision handled (`_new` suffix, no overwrite)
- [ ] Audio preserved in alpha mode (Opus, Vorbis fallback)
- [ ] VP9 speed presets varying encode time as expected
- [ ] `--reuse` levels validating, skipping, and reporting ignored flags correctly
- [ ] `STAGE_FLAG_MAP` correctly registering all rendering flags including `--font-color`
- [ ] `--keep-temp` creating and recording state
- [ ] `--report` displaying accurate last-run info including font color and background fields
- [ ] Blank frame handling (empty subjects, including transparent blanks)
- [ ] Cross-platform font fallback

## License

MIT License -- see LICENSE for details.

## Credits

- Original concept inspired by the jp2a command-line utility
- Architecture evolved iteratively from v1 (jp2a-based) to v3 (pure Python)

## Support

- Report issues: https://github.com/yourusername/asciify/issues
- Full flag documentation: `./asciify.py --help`
- State troubleshooting: check `~/.cache/asciify/state.json`
