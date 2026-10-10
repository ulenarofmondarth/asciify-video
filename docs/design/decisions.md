# Design decisions

The reasoning behind choices that are not obvious from the code.

## Convert luminance in Python instead of calling jp2a

Early versions shelled out to `jp2a` for the image-to-text step. It produced
vertical stripe artifacts and tied the character grid to that tool's own
sizing rules. Mapping luminance to characters is a few lines of Python once
the frame is already at grid resolution, and doing it in-process gives exact
control over the grid, the ramp and histogram stretching. The `jp2a` ramp is
still available as `--ramp jp2a`.

## Scale glyphs to the cell instead of laying out text

Drawing whole lines with a text engine makes the output depend on the font:
advance widths, line height and hinting all vary between fonts and platforms.
Rasterizing each character once and stretching its ink box to the cell makes
the grid exact by construction, and makes a fallback font degrade the look of
individual characters rather than the alignment of the whole frame.

## Derive the grid from the output size

The default grid width is the output width divided by `--scale-divisor`, not a
fixed number of columns and not a function of the source width. This keeps
character cells a constant size in pixels, and means clips of different source
resolutions rendered at one `--target-resolution` match exactly.

## Fuse conversion and rendering

The text for a frame is small and immediately consumed, so each worker
converts a frame and renders it in one go, with no intermediate file. Text
files are written only when `--keep-temp` asks for them, which keeps a normal
run free of per-frame text I/O.

## One global histogram stretch

Normalizing each frame independently makes overall brightness jump as the
content changes. Computing the bounds once across the clip costs one extra
pass over a sample of frames and keeps brightness steady.

## Keep the CLI and the core strictly apart

All option validation lives in the CLI and all processing lives in the core,
joined only by `ConversionConfig`. This lets the core be used as a library and
tested without argument parsing, and gives each rule a single home. The
separation is checked by tests so it cannot erode unnoticed.

## Import Pillow lazily

Pillow is imported inside the functions that use it rather than at module
level. Commands that never touch an image, such as `asciify_video --help` and
`asciify_video --report`, start without loading it.

## Record state after every stage

Saving the state file once at the end would be simpler, but a run that is
interrupted halfway would leave no trace. Saving per stage makes
`asciify_video --report` useful for diagnosing where a run stopped.

## Force RGBA at FFmpeg's input for transparent output

For VP9 with alpha, `-pix_fmt rgba` is given before the input so the PNG
decoder is told what to produce. Leaving the format to negotiation has been
seen to hang the encoder. The encoder options are deliberately minimal for the
same reason.

## Rename to `.webm` rather than fail

Transparent output cannot go in an MP4, and FFmpeg chooses the container from
the file extension. Asking for transparency with an `.mp4` name is an easy
mistake, so the name is corrected with a prominent notice rather than
rejected. If the corrected name already exists, a `_new` suffix avoids
overwriting a file the user did not name.

## Known limitations

- Progress and status are written with `print`, so library callers cannot
  route them through `logging`.
- The state file holds one record and is not locked.
- The font is reloaded in each worker for every frame.
