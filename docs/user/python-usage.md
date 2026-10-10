# Using asciify-video from Python

The `asciify_video` command is a thin wrapper around two public names:
{class}`~asciify_video.config.ConversionConfig` describes a run, and
{func}`~asciify_video.pipeline.run_conversion` executes it.

```python
from pathlib import Path

from asciify_video import ConversionConfig, run_conversion

cfg = ConversionConfig(
    input=Path("input.mp4"),
    output=Path("output.mp4"),
    colorized=True,
    hist_stretch=True,
    silent_progress=True,
)
run_conversion(cfg)
```

## Values must already be normalized

`run_conversion` performs no validation. The command line does that work
before building the config, so when you build one yourself:

- Colours are RGB tuples in `font_color_rgb` and `background_rgb`. Use
  {func}`~asciify_video.config.color_as_rgb` to parse a hex string. The
  `font_color` and `background_color` string fields are only copied into the
  state record.
- `background_alpha` must be between 0 and 1.
- If `background_alpha` is below 1, `output` must end in `.webm`.
- `scale_divisor` and `ascii_width` (when set) must be positive.
- `reuse_dir` must be set, and contain the right artifacts, whenever
  `reuse_level` is set. {func}`~asciify_video.reuse.validate_reuse` checks
  this.

```python
from asciify_video.config import color_as_rgb

cfg = ConversionConfig(
    input=Path("input.mp4"),
    output=Path("overlay.webm"),
    font_color="#FFB000",
    font_color_rgb=color_as_rgb("#FFB000"),
    background_alpha=0.0,
)
```

## Errors

Failures raise exceptions rather than exiting: `RuntimeError` when FFmpeg
fails or no frames are produced, `ValueError` when the input has no video
stream, and `OSError` when FFmpeg cannot be started. The run's temp directory
is removed before the exception propagates, unless `keep_temp` is set.

## Things to be aware of

- **Progress goes to stdout.** `run_conversion` prints its stage-by-stage
  report with `print`. `silent_progress=True` only removes the progress bars.
  Redirect stdout if you need silence.
- **The state file is shared.** Every run writes
  `~/.cache/asciify/state.json`. Two runs at the same time will overwrite each
  other's record. Set `XDG_CACHE_HOME` before importing the package to move it.
- **Worker processes.** Rendering uses a process pool, so on platforms that
  spawn rather than fork (Windows, macOS) the calling script needs the usual
  `if __name__ == "__main__":` guard.

## Running a single stage

The stage functions are importable on their own, for example to detect a key
colour without converting anything:

```python
from asciify_video.ffmpeg import probe_video
from asciify_video.keying import sample_green_color

info = probe_video("input.mp4")
color, detected = sample_green_color(
    "input.mp4", src_width=info["width"], src_height=info["height"],
    show_progress=False,
)
```

See the {doc}`../api/index` for every module.
