# Troubleshooting

## "no reusable temp directory found"

`--reuse` needs the intermediate files of an earlier run. Make one with
`--keep-temp` first:

```bash
asciify_video input.mp4 output.mp4 --keep-temp
asciify_video input.mp4 output2.mp4 --reuse ascii
```

If the directory exists but is not the one recorded in the state file, point
at it with `--reuse-dir PATH` or the `ASCII_VIDEO_TEMP` environment variable.
`asciify_video --report` shows the recorded directory and whether it still exists.

## "--reuse LEVEL: missing or empty '...'"

The directory lacks the artifacts that level needs. Usually the earlier run
was interrupted, the path is wrong, or the files were deleted. The `ascii`
level in particular needs `txt_frames/`, which is only written by a
`--keep-temp` run. Use a lower level (`extracted`), or re-run with
`--keep-temp`.

## An option is reported as IGNORED

The option feeds a stage that the chosen `--reuse` level skips. Use a level
that re-runs that stage, or drop `--reuse`. See {doc}`reuse-workflow`.

## The subject is partly keyed out, or the green is not removed

Automatic detection averages the green it finds in the frame corners. If the
corners are not backdrop, sample elsewhere or give the colour yourself:

```bash
asciify_video in.mp4 out.mp4 --sample-region full
asciify_video in.mp4 out.mp4 --sample-region 400x300+0+0     # WxH+X+Y in source pixels
asciify_video in.mp4 out.mp4 --key-color 0x00B140
```

Then adjust how much is removed. Raise `--tolerance` to remove more shades of
the key colour, lower it if parts of the subject disappear. `--edge-blending`
softens the cut-out edge.

When detection finds no clearly green pixels it says so and falls back to pure
green (`0x00FF00`).

## Transparent output fails to encode

Transparent output needs the `libvpx-vp9` encoder. Check that your FFmpeg has
it:

```bash
ffmpeg -hide_banner -encoders | grep libvpx-vp9
```

If it is slow rather than failing, that is VP9 encoding; the default
`--vpx-preset ultrafast` is already the fastest setting.

## The transparent background plays as black

The alpha channel is in the file but the player ignores it. Chromium, Firefox
and recent `mpv` builds show VP9 transparency. If you need wide player
support, render opaque and composite in an editor.

## The output looks too dark, too sparse or too dense

- Too dark or flat: add `--hist-stretch`.
- Too few characters: lower `--scale-divisor` or raise `--ascii-width`.
- Faint clutter in dark areas: add `--leave-black-empty`, and raise
  `--black-threshold` to remove more.
- Too much removed by masking: lower `--black-threshold`.

## Characters look wrong or uneven

A fallback font is probably in use. Install DejaVu Sans Mono (the
`fonts-dejavu-core` package on Debian and Ubuntu). To see which monospace
fonts are available:

```bash
fc-list : family | grep -i mono        # Linux
```

## "Command failed: ffmpeg ..."

FFmpeg exited with an error; its own message is printed just above, after
`STDERR:`. Common causes are an unreadable input file, an output directory
that does not exist, and an FFmpeg build without a required encoder.

## Running out of memory

Each worker process holds its own frame and glyph cache. Reduce the number of
workers with `--jobs 2`.
