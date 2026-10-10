# Iterating with artifact reuse

Keying and frame extraction are the slow, FFmpeg-bound part of a run, and they
rarely need repeating while you tune how the result looks. `--keep-temp` keeps
the intermediate files of a run, and `--reuse` starts a later run from them.

## The workflow

```bash
# 1. One full run, keeping the intermediates
asciify_video input.mp4 v1.mp4 --keep-temp

# 2. Change how it is rendered, without re-keying or re-extracting
asciify_video input.mp4 v2.mp4 --reuse ascii --font-color '#FFB000'

# 3. Change only the encode
asciify_video input.mp4 v3.mp4 --reuse final --fps 30
```

Any number of `--reuse` runs can follow one `--keep-temp` run. The kept
directory is recorded in the state file and stays recorded, so `--reuse` finds
it without being told where it is.

## Reuse levels

| Level | Starts from | Stages skipped | Use it to change |
|-------|-------------|----------------|------------------|
| `extracted` | `frames/*.png` | key detection, chromakey, extraction | grid size, ramp, histogram stretch, and everything below |
| `ascii` | `txt_frames/*.txt` and `frames/*.png` | also conversion to text | font colour, colorized mode, background, black masking, output size |
| `final` | `images/rendered_*.png` | also rendering | frame rate, output size (frames are rescaled), encoder preset |

At the `ascii` level the frames are still needed: they supply the per-cell
colours for `--colorized` and the brightness mask for `--leave-black-empty`.

## Options that stop having an effect

A reuse level freezes everything upstream of it. `asciify_video` prints which
options are inert and marks any that you passed:

```text
  Reusing artifacts from: /tmp/asciify_x1y2
  Level: final  (frames=108, ascii=108, rendered=108)
  Stages skipped: keying, conversion, rendering
  No effect at this level: --ascii-width, --background-alpha, ...
    !! --font-color was passed but is IGNORED (--reuse final)
  Still honored: --fps, --target-resolution (frames rescaled at assembly), audio mapping
```

If an option you need is being ignored, use a less aggressive level or run
without `--reuse`.

## Which directory is reused

The directory is chosen from the first of these that is set:

1. `--reuse-dir PATH`
2. the `ASCII_VIDEO_TEMP` environment variable
3. the directory recorded in the state file

It must contain the artifacts the level needs, otherwise the run stops with an
error naming the missing directory.

## Frame rate and source properties

When the state file describes the directory being reused, the source size,
frame rate and audio flag are taken from it rather than probed again. The
recorded frame rate is the rate the kept frames were extracted at, so a clip
first converted with `--fps 12` is reassembled at 12 fps unless you pass
`--fps` again.

## Inspecting the last run

```bash
asciify_video --report        # readable summary
asciify_video --state-file    # raw JSON
```

```text
State file: /home/user/.cache/asciify/state.json
Updated:    2026-10-06T14:22:31
Input:      input.mp4
Temp dir:   /tmp/asciify_x1y2  (present)
Completed:  yes
Stages:     probe, key, extract, ascii, render, assemble
Source:     1920x1080 @ 24.00 fps, audio=yes
Keying:     0x00B140 (auto-detected), tol=0.25, blend=0.05
Grid:       240 cols x 68 rows, stretch=off, ramp='default'
Render:     1920x1080, font=64, color=#FFB000, black-empty=off
Background: #000000, alpha=1.00
Artifacts:  frames=108, ascii=108, rendered=108
Last output: output.mp4
```

## Cleaning up

A kept directory is never deleted automatically. Its location is shown by
`asciify_video --report`; delete it when you have finished iterating.

Combining `--reuse` with `--keep-temp` keeps the new run's directory and
records that one instead. It holds only what the new run produced, so it may
not be reusable at every level: a directory made by `--reuse extracted
--keep-temp` has rendered images and text but no extracted frames.
