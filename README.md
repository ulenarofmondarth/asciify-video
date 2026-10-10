# asciify-video

Convert green-screen video into ASCII-art video, with consistent character geometry, multi-core rendering, optional transparency, and fast iteration through artifact reuse.

[![CI](https://github.com/ulenarofmondarth/asciify-video/actions/workflows/ci.yml/badge.svg)](https://github.com/ulenarofmondarth/asciify-video/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-blue.svg)

## Quick Start

```bash
pip install asciify-video
asciify_video input.mp4 output.mp4
```

`ffmpeg` and `ffprobe` must already be installed and on your `PATH`.

With no options this detects the green key colour, keys the subject over black, converts every frame to ASCII, and writes a video with the same size, frame rate and audio as the input, drawn in terminal green.

## Features

- **Automatic green detection**, or a key colour you supply (`--key-color`); `--no-key` for footage that is already keyed
- **Consistent geometry**: clips of different source resolutions rendered at one `--target-resolution` have identical character cells
- **Parallel conversion and rendering** across CPU cores (`--jobs`)
- **Colour control**: any character colour (`--font-color`), or colours sampled from the source (`--colorized`)
- **Backgrounds**: any colour, any opacity, including fully transparent WebM for overlays (`--background-color`, `--background-alpha`)
- **Steady brightness** from a clip-wide histogram stretch (`--hist-stretch`)
- **Black masking** to leave dark areas empty (`--leave-black-empty`)
- **Artifact reuse**: keep a run's intermediates and re-render or re-encode without repeating the slow stages (`--keep-temp`, `--reuse`)
- **Run reporting**: every run is recorded and can be inspected with `--report`
- **Python API**, fully type-annotated, for use without the command line

## Installation

Requirements:

- Python 3.10 or newer
- FFmpeg (`ffmpeg` and `ffprobe`), with `libx264`; transparent output also needs `libvpx-vp9`

Linux is the tested platform. Windows is untested but probably supported.

```bash
# Debian / Ubuntu
sudo apt-get install ffmpeg

# macOS (Homebrew)
brew install ffmpeg

# Windows
winget install ffmpeg
```

Install from PyPI:

```bash
pip install asciify-video
```

Or from source:

```bash
git clone https://github.com/ulenarofmondarth/asciify-video.git
cd asciify-video
pip install .
```

This provides the `asciify_video` command; `python -m asciify_video` is equivalent.

## Configuration

Everything is configured with command-line options; there is no configuration file. The most used options:

| Option | Default | Purpose |
|--------|---------|---------|
| `--key-color` | detected | Key colour as hex, e.g. `0x00B140` |
| `--no-key` | off | Skip chromakeying |
| `--scale-divisor` | 8 | Grid width = output width / divisor |
| `--ascii-width` | from divisor | Grid width in characters; overrides `--scale-divisor` |
| `--target-resolution` | source size | Output size, e.g. `1920x1080` |
| `--font-color` | `#64FF64` | Character colour (not with `--colorized`) |
| `--colorized` | off | Take character colours from the source |
| `--background-color` | `#000000` | Background colour |
| `--background-alpha` | 1 | Background opacity, 0-1; below 1 produces WebM |
| `--hist-stretch` | off | Clip-wide histogram stretch |
| `--jobs` | cores - 1 | Worker processes |
| `--keep-temp` / `--reuse` | off | Keep and reuse intermediate files |

`asciify_video --help` lists every option, as does the [CLI reference](https://asciify-video.readthedocs.io/en/latest/user/cli-reference.html).

Environment variables:

| Variable | Purpose |
|----------|---------|
| `ASCII_VIDEO_TEMP` | Directory to reuse with `--reuse` when `--reuse-dir` is not given |
| `XDG_CACHE_HOME` | Base directory for the run-state file, `$XDG_CACHE_HOME/asciify/state.json` (default `~/.cache`) |

## Usage

```bash
# Footage that is already keyed
asciify_video input.mp4 output.mp4 --no-key

# Characters in the source colours, with steadier brightness
asciify_video input.mp4 output.mp4 --colorized --hist-stretch

# Amber characters on a dark blue background
asciify_video input.mp4 output.mp4 --font-color '#FFB000' --background-color '#101820'

# Transparent background for overlaying (written as WebM)
asciify_video input.mp4 overlay.webm --background-alpha 0

# Two clips with matching character size
asciify_video clip_a.mp4 a.mp4 --target-resolution 1920x1080
asciify_video clip_b.mp4 b.mp4 --target-resolution 1920x1080
```

Iterate without repeating the slow stages:

```bash
asciify_video input.mp4 v1.mp4 --keep-temp                           # full run, keep intermediates
asciify_video input.mp4 v2.mp4 --reuse ascii --font-color '#FFB000'  # re-render only
asciify_video input.mp4 v3.mp4 --reuse final --fps 30                # re-encode only
asciify_video --report                                               # describe the last run
```

From Python:

```python
from pathlib import Path

from asciify_video import ConversionConfig, run_conversion

run_conversion(ConversionConfig(
    input=Path("input.mp4"),
    output=Path("output.mp4"),
    colorized=True,
))
```

The [user guide](https://asciify-video.readthedocs.io/) covers appearance options, transparency, the reuse workflow and troubleshooting in full.

> **Changed from the original script.** The default grid is now output width / 8 rather than a fixed 160 columns: still 160 for 1280-wide output, 240 for 1920-wide. Pass `--ascii-width 160` for the old grid. The command is now `asciify_video` rather than `./asciify.py`.

## API Reference

Full API documentation, generated from the source, is at <https://asciify-video.readthedocs.io/en/latest/api/>. The same site has design notes on the pipeline, rendering and the state file.

To build the documentation locally:

```bash
pip install -e ".[docs]"
make -C docs html
```

## Testing

```bash
pip install -e ".[dev]"
pytest
```

The suite fails below 90% coverage. Unit tests fake FFmpeg and need nothing installed; the integration tests convert a generated clip with the real `ffmpeg` and are skipped when it is absent.

```bash
ruff check .                  # lint, including docstring rules
mypy                          # strict type check
pre-commit run --all-files    # everything the commit hooks run
```

## Contributing

Issues and pull requests are welcome at <https://github.com/ulenarofmondarth/asciify-video>.

- Branch from `main` and use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `refactor:`).
- Code is PEP 8 at 100 columns with type hints and Google-style docstrings on everything public; `ruff` and `mypy` (strict) must pass.
- Add tests for new behaviour and keep coverage at or above 90%.
- Keep the layers apart: argument parsing and validation belong in `asciify_video.cli`, video processing everywhere else. Tests enforce this.
- Run `pre-commit install` once to check each commit locally.

Releases are published to PyPI by pushing a `vX.Y.Z` tag that matches the version in `pyproject.toml`.

## License

MIT. See [LICENSE](https://github.com/ulenarofmondarth/asciify-video/blob/main/LICENSE).

The original concept was inspired by the `jp2a` command-line tool.

## Changelog

See [CHANGELOG.md](https://github.com/ulenarofmondarth/asciify-video/blob/main/CHANGELOG.md).
