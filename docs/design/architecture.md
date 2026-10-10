# Architecture

## Layers

The package has two layers with a strict boundary between them.

```text
                +---------------------------+
   argv  --->   |  cli                      |   parse, validate, normalize
                +-------------+-------------+
                              | ConversionConfig
                              v
                +---------------------------+
                |  pipeline.run_conversion  |   orchestrate the stages
                +--+------+------+------+---+
                   |      |      |      |
                   v      v      v      v
               ffmpeg  keying  render  state / reuse
```

- **The CLI layer** ({mod}`asciify_video.cli`) owns everything about the
  command line: argument definitions, option validation, the WebM rename rule,
  locating a reuse directory, and turning failures into exit codes.
- **The core** (every other module) owns video processing. It never imports
  `argparse`, never reads `sys.argv`, and reports failure by raising.

The only thing that crosses the boundary is a
{class}`~asciify_video.config.ConversionConfig`. Because the core assumes the
config is already normalized, there is exactly one place where each option is
validated, and the core can be driven from Python with no CLI involved.

This boundary is enforced by tests rather than convention:
`tests/unit/test_architecture.py` parses every module and fails if a core
module imports `argparse` or defines a `main()`.

## Modules

| Module | Responsibility |
|--------|----------------|
| {mod}`~asciify_video.constants` | Default values, character ramps, and the tables mapping flags to stages |
| {mod}`~asciify_video.config` | `ConversionConfig` and colour parsing |
| {mod}`~asciify_video.ffmpeg` | Every `ffmpeg` / `ffprobe` invocation: probe, chromakey, extract, assemble |
| {mod}`~asciify_video.keying` | Detecting the green-screen colour from sampled frames |
| {mod}`~asciify_video.fonts` | Finding a usable monospace font |
| {mod}`~asciify_video.render` | Converting frames to ASCII and drawing them, in parallel |
| {mod}`~asciify_video.state` | The JSON record of the last run |
| {mod}`~asciify_video.reuse` | Finding and validating kept artifacts |
| {mod}`~asciify_video.pipeline` | Running the stages in order and recording progress |
| {mod}`~asciify_video.cli` | The `asciify_video` command |

Dependencies point one way. `constants` depends on nothing; `config`, `state`
and `ffmpeg` depend only on it; `keying`, `render` and `reuse` build on those;
`pipeline` composes all of them; and `cli` sits on top.

## External programs

All video decoding and encoding is done by FFmpeg, run as a subprocess. The
package never links against FFmpeg libraries or parses video data itself; the
only pixel data handled in Python is PNG frames (through Pillow) and one raw
RGB frame at a time during key-colour detection.

Every invocation goes through
{func}`~asciify_video.ffmpeg.run_command`, with two exceptions that read
binary or best-effort output directly: frame sampling for key detection, and
the check for the `libopus` encoder.

## Data on disk

Each run works in a fresh temp directory:

```text
asciify_XXXXXXXX/
├── chroma_keyed.mp4       stage 3: subject composited over black
├── frames/                stage 4: frame_000001.png ... at grid width
├── txt_frames/            stages 5-6: frame_000000.txt ... (only with --keep-temp)
└── images/                stages 5-6: rendered_000001.png ... at output size
```

The directory is deleted when the run ends, successfully or not, unless
`--keep-temp` is set. A directory that is being *reused* is never deleted.

Text files are numbered from zero in frame order while frames and rendered
images keep FFmpeg's numbering from one, so `frame_000000.txt` belongs to
`frame_000001.png`. Reuse pairs them by sorted position, not by number.

Separately, one small JSON file records the most recent run; see
{doc}`state-file`.

## Testing approach

- **Unit tests** replace FFmpeg with fakes and assert on the exact command
  lines built. Conversion and rendering run for real on tiny generated images.
- **Pipeline tests** run {func}`~asciify_video.pipeline.run_conversion` end to
  end with only the FFmpeg-backed functions faked, covering fresh runs and
  every reuse level.
- **Integration tests** generate a one-second green-screen clip and convert it
  with the real `ffmpeg`. They are skipped when FFmpeg is not installed.

Tests never touch the real state file: a suite-wide fixture redirects it to a
temporary directory.
