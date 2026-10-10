# Installation

## Requirements

- **Python 3.10 or newer**
- **FFmpeg**, providing both the `ffmpeg` and `ffprobe` commands on your `PATH`

asciify-video drives FFmpeg as an external program; it is not installed by `pip`.

Linux is the tested platform. Windows is untested but probably supported.

```bash
# Debian / Ubuntu
sudo apt-get install ffmpeg

# macOS (Homebrew)
brew install ffmpeg

# Windows
winget install ffmpeg
```

The FFmpeg build needs the `chromakey` filter and the `libx264` encoder, which
every mainstream build includes. Transparent output additionally needs
`libvpx-vp9`, and uses `libopus` for audio when available (falling back to
Vorbis). To check:

```bash
ffmpeg -hide_banner -encoders | grep -E "libx264|libvpx-vp9|libopus"
```

## Install the package

```bash
pip install asciify-video
```

This installs the `asciify_video` command along with its Python dependencies,
[Pillow](https://pillow.readthedocs.io/) and [tqdm](https://tqdm.github.io/).

To install from a source checkout instead:

```bash
git clone https://github.com/ulenarofmondarth/asciify-video.git
cd asciify-video
pip install .
```

## Verify

```bash
asciify_video --help
```

`python -m asciify_video` is equivalent to the `asciify_video` command, which is
useful when the scripts directory is not on your `PATH`.

## Fonts

Glyphs are drawn with a monospace TrueType font found on the system: DejaVu
Sans Mono or Liberation Mono on Linux, Consolas on Windows, Courier New, Menlo
or SF Mono on macOS. If none of those exist, any TrueType font in the system
font directories is used, and finally Pillow's built-in font. No configuration
is needed, but installing DejaVu Sans Mono gives the most consistent results
across machines.

## Development install

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

The `dev` extra adds the test, lint, type-check and documentation tools. Tests
that need a real `ffmpeg` are skipped automatically when it is not installed.
To build this documentation locally:

```bash
make -C docs html
```
