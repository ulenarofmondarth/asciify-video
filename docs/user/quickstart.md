# Quick start

## Convert a green-screen clip

```bash
asciify_video input.mp4 output.mp4
```

With no options this will:

1. Detect the green key colour by sampling the corners of several frames.
2. Key the subject out and composite it over black.
3. Extract frames at the character-grid width.
4. Convert each frame to ASCII and render it, using all but one CPU core.
5. Assemble the rendered frames into `output.mp4`, keeping the original audio.

The result has the same resolution and frame rate as the input, drawn in
terminal green (`#64FF64`) on black.

## Common variations

Footage that is already keyed, or was never on a green screen:

```bash
asciify_video input.mp4 output.mp4 --no-key
```

A key colour you already know:

```bash
asciify_video input.mp4 output.mp4 --key-color 0x00B140
```

Characters in the colours of the source video:

```bash
asciify_video input.mp4 output.mp4 --colorized
```

A transparent background, for overlaying on other video:

```bash
asciify_video input.mp4 overlay.webm --background-alpha 0
```

## Controlling the character grid

The grid width is the output width divided by `--scale-divisor` (default 8), so
each character cell is 8 pixels wide: 160 columns for 1280-wide output, 240 for
1920-wide. A smaller divisor gives more, smaller characters:

```bash
asciify_video input.mp4 output.mp4 --scale-divisor 6
```

To set the number of columns directly, use `--ascii-width`; it takes priority
over `--scale-divisor`:

```bash
asciify_video input.mp4 output.mp4 --ascii-width 200
```

The number of rows follows from the video's aspect ratio.

## Matching several videos

`--target-resolution` sets the output size. Because the grid is derived from
the output size, clips with different source resolutions come out with
identical character dimensions:

```bash
asciify_video phone_clip.mp4  a.mp4 --target-resolution 1920x1080
asciify_video camera_clip.mp4 b.mp4 --target-resolution 1920x1080
```

## Steadier brightness

`--hist-stretch` stretches luminance to use the whole character ramp. The
stretch is computed once over the whole clip, which avoids the frame-to-frame
brightness "pumping" that per-frame normalization causes:

```bash
asciify_video input.mp4 output.mp4 --hist-stretch
```

## Speed and memory

Conversion and rendering run in parallel worker processes. The default is one
fewer than the number of CPU cores; lower it if memory is tight:

```bash
asciify_video input.mp4 output.mp4 --jobs 2
```

The FFmpeg stages (keying, extraction, assembly) are each a single FFmpeg
command and are not affected by `--jobs`.

## Next steps

- {doc}`appearance` covers colours, backgrounds, transparency and masking.
- {doc}`reuse-workflow` shows how to iterate on settings without redoing the
  slow stages.
- {doc}`cli-reference` lists every option.
