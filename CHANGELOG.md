# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

First packaged release. Earlier versions were a pair of standalone scripts
(`asciify.py` and `asciify_cli.py`).

### Added

- Installable package `asciify-video` with an `asciify_video` console command
  (also `python -m asciify_video`), replacing the `./asciify.py` script.
- Public Python API: `asciify_video.ConversionConfig` and
  `asciify_video.run_conversion`, fully type-annotated (`py.typed`).
- `--scale-divisor` now takes effect: when `--ascii-width` is not given the
  grid width is the output width divided by the divisor.
- Sphinx documentation: user guide, design notes and API reference.
- Continuous integration and tag-driven PyPI releases on GitHub Actions.

### Changed

- **Default grid width** is now output width / 8 instead of a fixed 160
  columns. This is unchanged for 1280-wide output and gives 240 columns for
  1920-wide output. Pass `--ascii-width 160` for the old grid.
- A `--reuse` run takes the source size and frame rate from the state file
  instead of probing the input again. The frame rate is the one the kept
  frames were extracted at.
- The directory being reused stays recorded in the state file, so several
  `--reuse` runs can follow one `--keep-temp` run without `--reuse-dir`.
- Python 3.10 or newer is required.

### Fixed

- Transparent (WebM) output failed with "Unrecognized option 'realtime'".
  The VP9 command now uses `-deadline realtime` with `-row-mt 1`.
- The final encode failed on Windows, whose ffmpeg builds lack
  `-pattern_type glob`. Frames are now read as a numbered sequence.
- `--reuse` reported ignored flags from `sys.argv` rather than from the
  arguments passed to `main()`.
- `--reuse` with an incomplete directory printed a traceback instead of an
  error message.
- Non-positive `--ascii-width` and `--scale-divisor` values are rejected.
