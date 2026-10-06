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
- **Everything is documented** via JSON state files and `--report`

## Features

| Feature | Description |
|---------|-------------|
| **Auto green detection** | Samples corner regions to find optimal key color |
| **Manual key override** | Specify exact hex color with `--key-color` |
| **Chromakey-to-black** | Composites keyed subject over black background |
| **Direct luminance mapping** | Custom Python rasterizer (no external `jp2a` dependency) |
| **Parallel processing** | Multi-core conversion and rendering via `ProcessPoolExecutor` |
| **Histogram stretching** | Global luminance remapping (`--hist-stretch`) prevents per-frame brightness pumping |
| **Colorized mode** | Sample character colors from source frames (`--colorized`) |
| **Target resolution** | Consistent output geometry regardless of input resolution |
| **Black masking** | Leave dark areas empty (`--leave-black-empty --black-threshold`) |
| **Artifact reuse** | Skip costly stages by reusing intermediates (`--reuse`) |
| **JSON state tracking** | Run metadata persisted to `~/.cache/asciify/state.json` |
| **Progress reporting** | `--report` shows last run's state; `--state-file` dumps raw JSON |

## Requirements

### System dependencies

- **Python 3.8+**
- **FFmpeg** (with chromakey and libx264 support)
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
5. Render ASCII at source resolution
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

## Advanced Usage

### Artifact reuse (iteration workflow)

The `--keep-temp` flag preserves intermediate artifacts for rapid iteration:

```bash
# First run: keep all intermediates
./asciify.py input.mp4 output1.mp4 --keep-temp

# Later: tweak only render settings (fonts, colors, masking)
./asciify.py input.mp4 output2.mp4 --reuse ascii --render-font-size 96

# Even later: tweak only encode settings (FPS, CRF, audio)
./asciify.py input.mp4 output3.mp4 --reuse final --fps 60
```

#### Reuse levels

| Level | Skips stages | Reuses from disk | Typical iteration |
|-------|--------------|------------------|-------------------|
| `extracted` | keying, chromakey, extraction | frames/*.png | Fix key color and retry |
| `ascii` | keying, chromakey, extraction, conversion | txt_frames/*.txt, frames/*.png | Tweak rendering (font, colors, masking) |
| `final` | all stages above + rendering | images/rendered_*.png | Tune encoder (FPS, CRF, audio bitrate) |

### State inspection

```bash
# Human-readable summary of last run
./asciify.py --report

# Raw JSON dump
./asciify.py --state-file

# Both work standalone; no input/output args needed
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
| `output` | *(required)* | Output ASCII video file path |
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
| `--leave-black-empty` | off | Leave dark areas as true emptiness |
| `--black-threshold` | 50 | Brightness threshold for "black" (0-255) |
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
(meta)  (chroma)   (PNG)      (fused parallel)  (images)  (MP4)
```

### Fused parallel convert+render

Unlike earlier designs that wrote intermediate `.txt` files between stages, the current pipeline fuses conversion and rendering into a single worker function:

```
Worker 1: PNG --> ASCII (in memory) --> Rendered PNG
Worker 2: PNG --> ASCII (in memory) --> Rendered PNG
Worker 3: PNG --> ASCII (in memory) --> Rendered PNG
```

Intermediate text files exist **only** when `--keep-temp` is specified, primarily for debugging and `--reuse ascii` compatibility.

### Analytic grid calculation

The character grid is computed analytically from `--target-resolution` and `--ascii-width`, eliminating the need for pre-scanning all ASCII files:

```
max_cols = --ascii-width (user setting)
max_rows = frame_height / 2 (from first frame)
cell_w = --target_width / max_cols
cell_h = --target_height / max_rows
```

This guarantees **identical character dimensions** across all videos rendered with the same target settings.

### State file schema

Stored at `~/.cache/asciify/state.json`:

```json
{
  "version": 1,
  "updated": "2026-10-05T21:40:12",
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
    "colorized": false,
    "leave_black_empty": false,
    "black_threshold": 50
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

### Font loading failure

`asciify.py` searches standard paths on Linux/macOS/Windows and falls back to scalable defaults. If you get font errors, check:

```bash
fc-list : monospace                                      # Linux
find /System/Library/Fonts -name "*.ttf" | grep -i mono  # macOS
dir C:\Windows\Fonts\*mono*                             # Windows
```

### Vertical striping in output

This was a known bug in the removed `jp2a` tool. The current version uses a pure-Python rasterizer. If striping appears, it indicates either:

1. Outdated installation (ensure you're using v3+)
2. Corrupted temp files (delete `~/.cache/asciify` and re-run)

### "Cannot write empty image" crash

The script handles blank frames gracefully. If you still encounter this, verify:

1. The input video isn't entirely black/dark
2. `--black-threshold` isn't too aggressive (try lowering it)
3. Chromakey isn't removing too much subject matter (adjust `--tolerance`)

## Performance Notes

FFmpeg stages (chromakey, extraction, assembly) are single-command and not parallelized; the Python convert+render stage scales with `--jobs`. Parallelization benefits grow with:

- Higher resolution (more pixels per frame)
- Longer duration (more frames)

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
- [ ] `--target-resolution` consistency across inputs
- [ ] `--reuse` levels validating and skipping correctly
- [ ] `--keep-temp` creating and recording state
- [ ] `--report` displaying accurate last-run info
- [ ] Blank frame handling (empty subjects)
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
