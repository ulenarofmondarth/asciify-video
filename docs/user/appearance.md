# Colours, backgrounds and transparency

## Character colour

Characters are drawn in one colour, terminal green by default. Choose another
with `--font-color`, given as a CSS hex colour:

```bash
asciify_video in.mp4 out.mp4 --font-color '#FFB000'      # amber
asciify_video in.mp4 out.mp4 --font-color '#FFFFFF'      # white
```

Alternatively, `--colorized` takes each character's colour from the matching
area of the source frame:

```bash
asciify_video in.mp4 out.mp4 --colorized
```

The two are mutually exclusive, because `--colorized` leaves nothing for a
uniform colour to control. Passing both stops immediately with an error rather
than rendering something you did not ask for.

## Character ramp

The ramp is the list of characters used from darkest to brightest. The default
is a 70-character ramp for fine tonal gradation. `--ramp jp2a` selects the
shorter ramp used by the `jp2a` tool, and any other value is used as a custom
ramp, darkest character first:

```bash
asciify_video in.mp4 out.mp4 --ramp jp2a
asciify_video in.mp4 out.mp4 --ramp ' .:-=+*#%@'
```

## Leaving dark areas empty

By default every cell gets a character, so dark areas are filled with the
darkest non-blank characters of the ramp. `--leave-black-empty` draws nothing
in cells whose brightness is below `--black-threshold` (0-255, default 50):

```bash
asciify_video in.mp4 out.mp4 --leave-black-empty --black-threshold 40
```

This matters most with a transparent or coloured background, where faint
characters around the subject would otherwise be visible.

## Background colour

```bash
asciify_video in.mp4 out.mp4 --background-color '#102030'
asciify_video in.mp4 out.mp4 --font-color '#00FFFF' --background-color '#101820'
```

## Transparency

`--background-alpha` sets the background opacity from 0 (fully transparent) to
1 (opaque, the default). Characters are always fully opaque.

```bash
asciify_video in.mp4 overlay.webm --background-alpha 0       # transparent
asciify_video in.mp4 tinted.webm --background-alpha 0.5      # half-transparent black
```

### Output format

| `--background-alpha` | Container | Video | Audio |
|----------------------|-----------|-------|-------|
| 1 (default) | MP4 | H.264 | AAC |
| below 1 | WebM | VP9 with alpha (`yuva420p`) | Opus, or Vorbis if FFmpeg lacks `libopus` |

Transparency requires WebM. If the alpha is below 1 and the output name does
not end in `.webm`, the name is changed for you and a boxed notice says so:

```text
! ---------------------------------------------------------- !
!              NOTE: alpha output requires WebM              !
! ---------------------------------------------------------- !
!  Output filename changed:
!    out.mp4
!    -> out.webm
! ---------------------------------------------------------- !
```

If the renamed file already exists, the output goes to `<name>_new.webm`
instead, so an unrelated file is never overwritten because of the rename.

### Encoding speed

VP9 encoding dominates the run time of transparent output. `--vpx-preset`
trades speed for quality:

| Preset | libvpx `cpu-used` |
|--------|-------------------|
| `ultrafast` (default) | 8 |
| `veryfast` | 6 |
| `faster` | 4 |
| `fast` | 2 |
| `medium` | 1 |
| `slow` | 0 |

```bash
asciify_video in.mp4 overlay.webm --background-alpha 0 --vpx-preset medium
```

### Checking the result

Not every player shows VP9 transparency. Chromium, Firefox and recent `mpv`
builds do; some players show a black background even though the alpha channel
is present. To confirm the file has an alpha channel:

```bash
ffprobe -v error -select_streams v:0 -show_entries stream_tags=alpha_mode output.webm
```
