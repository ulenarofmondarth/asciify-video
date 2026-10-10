# The conversion pipeline

{func}`~asciify_video.pipeline.run_conversion` runs seven numbered stages.
Stages 5 and 6 are fused into one parallel step.

```text
1 probe --> 2 key colour --> 3 chromakey --> 4 extract --> 5+6 convert+render --> 7 assemble
 ffprobe     sample frames     ffmpeg          ffmpeg        Python, parallel       ffmpeg
```

## Stage by stage

### 1. Probe

`ffprobe` reports the source width, height, frame rate and whether there is an
audio stream. If `--fps` is given it replaces the source rate from here on.

### 2. Key colour

Unless a key colour is supplied (or keying is disabled), ten frames spread
over the middle half of the clip are decoded at 160 pixels wide. Within the
sampling region, pixels count as backdrop when green exceeds both red and blue
by more than 30 and mean brightness is between 30 and 220. The average of
those pixels is the key colour. The first and last quarters of the clip are
skipped because they often contain fades or an empty stage.

Detection never fails the run. If nothing qualifies, or anything goes wrong,
pure green (`0x00FF00`) is used and a warning is printed.

### 3. Chromakey

FFmpeg's `chromakey` filter removes the key colour and the result is overlaid
on a black frame of the source size, encoded as near-lossless H.264 with the
audio copied. Compositing over black means the later luminance mapping sees
the removed background as the darkest value.

With `--no-key` this stage is skipped and the input itself is used.

### 4. Extract

Every frame is written as a PNG, scaled so its width equals the number of
character columns. One pixel column then corresponds to one character column,
so no further horizontal resampling happens in Python.

The number of columns is `--ascii-width` if given, otherwise the output width
divided by `--scale-divisor`.

### 5 and 6. Convert and render

Each frame is turned into text and drawn to a PNG at the output size. This is
the only CPU-bound Python work, and it runs in a process pool. The details are
in {doc}`rendering`.

### 7. Assemble

FFmpeg encodes the rendered PNGs at the output frame rate and muxes the audio
from the original input. Opaque output is H.264 with AAC in MP4. Output with
`--background-alpha` below 1 is VP9 with an alpha channel and Opus (or Vorbis)
in WebM.

For the alpha case the PNG decoder's output is forced to `rgba` on the *input*
side of the command. Letting FFmpeg negotiate the pixel format has been seen
to hang the VP9 encoder.

## Progress recording

After each stage the pipeline appends the stage name to `stages_completed` in
the run state and saves the state file. An interrupted or failed run therefore
leaves an accurate record of how far it got, which `asciify_video --report` shows.

## Reuse

With `--keep-temp`, the temp directory survives and its path is recorded. A
later run with `--reuse LEVEL` reads from it and skips the stages that made
those files:

| Level | Stages run | Reads |
|-------|-----------|-------|
| *(fresh)* | 1 2 3 4 5 6 7 | the input |
| `extracted` | 1 5 6 7 | `frames/` |
| `ascii` | 1 6 7 | `frames/` and `txt_frames/` |
| `final` | 1 7 | `images/` |

Stage 1 is special. When the state file describes the directory being reused,
the recorded probe is restored instead of running `ffprobe`. This matters for
the frame rate: the recorded rate is the one the kept frames were extracted
at, which is not necessarily the source rate.

At the `final` level the kept frames may not match the requested output size,
so one frame is measured and a scale filter is added during assembly if
needed.

A reuse run still creates its own temp directory for whatever it produces, and
deletes it afterwards. The reused directory stays recorded in the state file
so that further reuse runs can find it.

## Failure handling

`run_conversion` lets exceptions propagate. Before they do, the state file is
saved and the run's own temp directory is removed (unless `--keep-temp`). The
CLI catches the exception, prints it, and exits with status 1.
