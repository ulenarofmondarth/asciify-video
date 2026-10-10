# Conversion and rendering

Turning a frame into drawn text happens in two steps inside one worker
process. The work for a frame is described by a
{class}`~asciify_video.render.FrameTask`; the grid geometry by a
{class}`~asciify_video.render.GridMetrics`.

## Grid geometry

```text
cols   = --ascii-width, or output_width // --scale-divisor
rows   = extracted_frame_height // 2      (1920x1080 at 240 columns: 136 // 2 = 68)
cell_w = output_width  // cols
cell_h = output_height // rows
```

The extracted frame is `cols` pixels wide with its height following the aspect
ratio, so halving the height gives cells about twice as tall as they are wide,
matching the proportions of monospace type.

Cells are whole pixels. Any remainder is split evenly into margins that centre
the grid on the canvas.

Because every value derives from the output size and the grid dimensions,
never from the source resolution, two clips rendered at the same
`--target-resolution` have identical character cells.

## Stage A: luminance to characters

1. The extracted frame is resized to exactly `cols` x `rows` with a box
   filter, so each pixel is the average of one cell.
2. That image is converted to luminance.
3. If histogram stretching is on, luminance is remapped linearly so the global
   bounds become 0 and 255.
4. Each value selects a character: `ramp[int(value * len(ramp) / 256)]`.

The resize is done in RGB *before* taking luminance. Averaging in colour space
and then weighting the channels gives the perceptually correct cell
brightness; averaging pre-computed luminance is close but not the same, and
the RGB image is needed anyway for colorized output.

### Histogram stretching

The bounds are computed once for the whole clip by
{func}`~asciify_video.render.compute_global_stretch`: every tenth frame is
sampled, and the 0.5th and 99.5th percentiles of all sampled pixels become the
low and high bounds. Using clip-wide bounds keeps brightness stable from frame
to frame. If the range is narrower than 16 levels the stretch is skipped,
since it would mostly amplify noise.

## Stage B: characters to pixels

Text is not drawn with a text-layout engine. Each distinct character is
rasterized once at `--render-font-size`, cropped to its ink bounding box, and
scaled to exactly fill a cell. Glyphs therefore tile the grid with no gaps and
no dependence on the font's own advance width or line height, which is what
makes the output geometry independent of which font is installed.

`--render-font-size` controls the resolution of that master glyph, not the
size of characters in the output. Higher values give sharper edges when cells
are large.

There are two drawing paths:

- **Monochrome.** Each character is tinted once with the font colour and the
  tinted image is pasted into every cell showing it.
- **Colorized.** Glyph masks are pasted into a single coverage mask, a
  one-pixel-per-cell colour image is scaled up with nearest-neighbour to form
  a layer of flat cell colours, and the layer is composited through the mask
  in one operation. This avoids tinting a glyph per cell.

With `--leave-black-empty`, cells whose luminance is below the threshold are
skipped in either path.

The canvas is RGBA. The background carries the chosen colour at the chosen
alpha, and glyphs are always opaque. A frame with no visible characters is
written as bare background without loading a font.

## Parallelism

Frames are independent, so
{func}`~asciify_video.render.frames_to_rendered_images` submits one task per
frame to a `ProcessPoolExecutor`. Processes rather than threads are used
because the work is pure-Python pixel manipulation that holds the GIL.

Each worker loads the font and builds its glyph cache per frame. A cache holds
at most one entry per ramp character, so this is cheap next to the per-cell
work.

Conversion and rendering are *fused*: a worker converts a frame to text and
renders it immediately, returning the text to the parent. The text is only
written to disk, by the parent, when `--keep-temp` is set.

## Reusing saved text

With `--reuse ascii`, stage A is replaced by reading the saved text file. The
frame is still loaded, because it supplies the cell colours and the black
mask. The grid size is taken from the text itself, so `--ascii-width` and
`--scale-divisor` have no effect at that level.
